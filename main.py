"""Coordinate collection, deterministic analysis, optional AI, and report export.

scan_system is shared by the CLI and the desktop worker. main translates its
outcome into a process exit code. Importing the module starts neither a scan
nor an API request; execution begins through these entry points.
"""

import sys

import config
from errors import EventCollectionError, LogOpenError, ReportExportError, describe_error
from event_collection import get_recent_events
from event_filtering import filter_events, filter_events_by_time
from incident_detection import (
    build_incident,
    bundle_incidents,
    refresh_incident_summary,
)
from incident_recurrence import apply_recurrence_metadata
from json_reporting import export_incidents_to_json
from llm_analysis import (
    analyse_incident_with_llm_result,
    build_scan_context,
    select_incidents_for_llm,
    should_analyse_incident_with_llm,
)
from models.scan_result import ScanResult
from models.scan_options import ScanOptions
from terminal_reporting import display_incident_reports
from settings.app_settings import AppSettings
from detection_rules import DEFAULT_LEVELS, RuleSet


def format_log_types_label(log_types):
    """Return a readable log-name phrase, with a fallback for an empty sequence."""

    if not log_types:
        return "configured logs"
    if len(log_types) == 1:
        return f"{log_types[0]} log"
    if len(log_types) == 2:
        return f"{log_types[0]} and {log_types[1]} logs"
    return f"{', '.join(log_types[:-1])}, and {log_types[-1]} logs"


def display_llm_analysis(analysis):
    """Print the four prose fields of a validated analysis dictionary to stdout.

    Diagnostic commands are retained in JSON/the desktop view, not printed here.
    Required fields must be present; this function does not validate or call AI.
    """

    print("\nLLM Analysis Results:")
    print("Explanation:", analysis["explanation"])
    print("Likely Causes:", "; ".join(analysis["likely_causes"]) or "None")
    print(
        "Recommended Actions:",
        "; ".join(analysis["recommended_actions"]) or "None",
    )
    print(
        "Remediation Notes:",
        "; ".join(analysis["remediation_notes"]) or "None",
    )


def _write_status(message, *, error=False):
    """Print to stdout, or stderr when error=True, ignoring broken output streams.

    Intended for status/error paths where a second I/O error would hide results.
    """

    try:
        print(message, file=sys.stderr if error else sys.stdout)
    except (BrokenPipeError, OSError):
        pass


def add_llm_analyses(incident_summaries, *, cancel_event=None, progress=None):
    """Attach optional analysis and an explicit AI outcome to summaries in place.

    Loads preferences once, preserves existing analyses, clears stale error
    fields, then selects eligible incidents by score and the saved scan limit.
    Every incident gets llm_analysis_status and llm_analysis_message; failures
    also get llm_analysis_error. Successful models become JSON-compatible dicts.
    Ignored issue families do not consume the automatic analysis budget;
    acknowledged families remain eligible. Manual analysis uses a separate path.

    cancel_event stops before the next request; a running SDK request completes
    normally before cancellation takes effect. progress receives a stage label
    and a percentage on the calling thread. May make billable API calls and
    write status output. Unexpected per-incident
    failures are replaced by fixed messages so processing can continue without
    exposing SDK exception contents. Returns None.
    """
    
    settings = AppSettings().load()
    for incident in incident_summaries:
        incident.pop("llm_analysis_error", None)
        if incident.get("llm_analysis") is not None:
            status, message = "completed", "AI analysis completed."
        elif not settings.enabled:
            status, message = "disabled", "AI analysis is disabled in Settings."
        elif incident.get("review_state") == "ignored":
            status, message = "not_selected", "Automatic AI analysis is skipped for ignored incidents."
        elif should_analyse_incident_with_llm(incident, settings=settings):
            status, message = "not_selected", "Not selected because the AI analysis limit for this scan was reached."
        else:
            status, message = "not_selected", "This incident does not meet the AI analysis criteria."
        incident["llm_analysis_status"] = status
        incident["llm_analysis_message"] = message
    if not settings.enabled:
        _write_status("AI analysis is disabled. Deterministic analysis is complete.")
        return
    selected_incidents = select_incidents_for_llm(
        [incident for incident in incident_summaries if incident.get("review_state") != "ignored"],
        settings=settings,
    )

    for index, incident in enumerate(selected_incidents):
        if cancel_event is not None and cancel_event.is_set():
            for remaining in selected_incidents[index:]:
                remaining["llm_analysis_status"] = "cancelled"
                remaining["llm_analysis_message"] = "AI analysis was skipped because the scan was cancelled."
            break
        if progress is not None:
            progress(f"Analyzing incident {index + 1} of {len(selected_incidents)} with AI…",
                     70 + int(23 * index / max(len(selected_incidents), 1)))
        try:
            outcome = analyse_incident_with_llm_result(
                incident, settings=settings, scan_context=build_scan_context(incident_summaries),
            )
            incident["llm_analysis"] = (
                outcome.analysis.model_dump(mode="json")
                if outcome.analysis is not None
                else None
            )
            incident["llm_analysis_status"] = outcome.status
            incident["llm_analysis_message"] = outcome.message
            if outcome.status == "failed":
                incident["llm_analysis_error"] = outcome.message
        except Exception:
            incident["llm_analysis"] = None
            # SDK/validation exceptions may contain credentials or request data.
            incident["llm_analysis_error"] = "AI analysis could not be completed. Check AI Settings."
            incident["llm_analysis_status"] = "failed"
            incident["llm_analysis_message"] = incident["llm_analysis_error"]
            _write_status(
                "Warning: AI analysis was skipped for an incident: "
                f"{incident['llm_analysis_error']}",
                error=True,
            )
            continue


def _validate_log_types(log_types):
    """Require a nonempty list/tuple of nonblank log-name strings.

    Raises ValueError before collection if config.LOG_TYPES has the wrong shape.
    Validation does not change names or check their availability on this machine.
    """

    if isinstance(log_types, str) or not isinstance(log_types, (list, tuple)):
        raise ValueError("config.LOG_TYPES must be a list or tuple of log names")
    if not log_types:
        raise ValueError("config.LOG_TYPES must contain at least one log name")
    if any(not isinstance(name, str) or not name.strip() for name in log_types):
        raise ValueError("each configured log name must be a non-empty string")


def _run_workflow(options=None, *, progress=None, cancel_event=None, history_store=None) -> ScanResult:
    """Run collection through export, preserving usable results from partial scans.

    Uses ScanOptions (or current config defaults), with optional progress and a
    threading.Event-like cancellation token. Historical recurrence is applied
    before scoring/AI selection. Completed attempts are archived in HistoryStore
    and exported to reports/incidents.json relative to the working directory.
    Cancelled scans retain any summaries already built and never replace the
    latest report or enter history. Storage problems become visible warnings.

    Returns:
        ScanResult: Incidents plus per-log coverage, warnings, completeness,
        and an export_error if writing the report failed.

    Raises:
        ValueError: The configured log list is invalid.
        RuntimeError: No configured log could be collected successfully.

    Recoverable collection/build failures mark results incomplete. Export
    failures preserve incidents and attach a warning; AI failures remain local
    to each incident. Interrupts and unhandled output errors propagate to the
    caller, which decides whether to show a UI error or a CLI exit code.
    """

    if options is None:
        _validate_log_types(config.LOG_TYPES)
        options = ScanOptions()
    if not isinstance(options, ScanOptions):
        raise ValueError("options must be a ScanOptions instance")
    problem_events = []
    collection_errors = []
    incident_summaries = ScanResult()
    incident_summaries.scan_options = options.as_dict()
    if options.collect_all_events:
        incident_summaries.collected_events = []
    rules = RuleSet(options.rules_json)

    def report_progress(message, percent):
        """Notify a caller on this worker thread; progress observers must not raise."""
        if progress is not None:
            progress(message, max(0, min(100, int(percent))))

    def cancelled():
        """Test the caller's cooperative cancellation token without clearing it."""
        return cancel_event is not None and cancel_event.is_set()

    def finish_cancelled():
        """Return incomplete in-memory results without saving a report or history entry."""
        incident_summaries.cancelled = True
        incident_summaries.complete = False
        message = "Scan cancelled. Partial results have not replaced the latest saved scan."
        if message not in incident_summaries.warnings:
            incident_summaries.warnings.append(message)
        for name in options.log_types:
            incident_summaries.coverage.setdefault(name, {
                "complete": False, "stop_reason": "cancelled", "window_hours": options.hours,
            })
        report_progress("Scan cancelled", 100)
        return incident_summaries

    def history_warning(action):
        """Expose a useful history failure without including arbitrary storage payloads."""
        message = f"Scan history could not {action}. Current results are still available."
        incident_summaries.warnings.append(message)
        _write_status(f"Warning: {message}", error=True)

    report_progress("Starting scan…", 0)
    if cancelled():
        return finish_cancelled()

    for log_index, log_type in enumerate(options.log_types):
        if cancelled():
            return finish_cancelled()
        report_progress(f"Reading {log_type}…", int(50 * log_index / len(options.log_types)))
        try:
            collection_options = {"hours": options.hours,
                                  "levels": None if options.collect_all_events else DEFAULT_LEVELS,
                                  "event_matcher": rules.matches}
            if cancel_event is not None:
                collection_options["cancel_event"] = cancel_event
            if progress is not None:
                def collection_progress(records, name=log_type, index=log_index):
                    """Translate per-log record counts into the collection portion of progress."""
                    percent = 50 * (index + min(records / options.max_records, 1)) / len(options.log_types)
                    report_progress(f"Reading {name}: {records:,} records examined…", percent)
                collection_options["progress"] = collection_progress
            events = get_recent_events(
                log_type, limit=options.max_records, **collection_options,
            )
            coverage = events.coverage() if hasattr(events, "coverage") else {"complete": True}
            coverage["window_hours"] = options.hours
            incident_summaries.coverage[log_type] = coverage
            if not coverage["complete"]:
                incident_summaries.complete = False
            for message in getattr(events, "warnings", ()):
                incident_summaries.warnings.append(message)
                _write_status(f"Warning: {message}", error=True)
            events.sort(key=lambda event: event["time_generated"])
            log_problem_events = filter_events_by_time(events, options.hours)
            if options.collect_all_events:
                incident_summaries.collected_events.extend(log_problem_events)
            log_problem_events = [event for event in log_problem_events if rules.apply(event)]
            problem_events.extend(log_problem_events)
        except (EventCollectionError, ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            error_message = f"{log_type}: {describe_error(exc)}"
            collection_errors.append(error_message)
            incident_summaries.complete = False
            incident_summaries.coverage[log_type] = {
                "complete": False, "stop_reason": "collection_error",
                "window_hours": options.hours,
            }
            incident_summaries.warnings.append(error_message)
            _write_status(
                f"Warning: could not process the {log_type} log: "
                f"{describe_error(exc)}",
                error=True,
            )

    if cancelled():
        return finish_cancelled()

    if len(collection_errors) == len(options.log_types):
        raise RuntimeError(
            "none of the configured event logs could be processed ("
            + "; ".join(collection_errors)
            + ")"
        )

    report_progress("Grouping related events…", 50)
    problem_events.sort(key=lambda event: event["time_generated"])
    incidents = (bundle_incidents(problem_events, cancel_event=cancel_event)
                 if cancel_event is not None else bundle_incidents(problem_events))

    for incident_number, incident in enumerate(incidents, start=1):
        if cancelled():
            return finish_cancelled()
        try:
            summary = build_incident(incident)
        except Exception as exc:
            message = (f"Incident {incident_number} was skipped because it "
                       f"could not be summarized: {describe_error(exc)}")
            incident_summaries.complete = False
            incident_summaries.warnings.append(message)
            _write_status(f"Warning: {message}", error=True)
            continue
        if summary is not None:
            incident_summaries.append(summary)
        if incident_number % 100 == 0:
            report_progress(f"Summarizing incidents: {incident_number:,} of {len(incidents):,}…",
                            55 + int(10 * incident_number / max(len(incidents), 1)))

    if cancelled():
        return finish_cancelled()
    report_progress("Checking scan history and recurrence…", 65)
    apply_recurrence_metadata(incident_summaries)
    try:
        if history_store is None:
            from history_store import HistoryStore
            history_store = HistoryStore()
        history_store.enrich_incidents(incident_summaries)
        history_store.apply_states(incident_summaries)
    except Exception:
        history_warning("load recurrence and review states")
    for summary in incident_summaries:
        refresh_incident_summary(summary)
    if history_store is not None:
        try:
            incident_summaries.comparison = history_store.compare_scan(incident_summaries, options)
        except Exception:
            history_warning("compare this scan with previous results")

    log_types_label = format_log_types_label(options.log_types)
    print(
        f"Found {len(problem_events)} recent events matching severity or detection rules in "
        f"the {log_types_label} in the last {options.hours} hours:"
    )
    print(
        f"Bundled into {len(incidents)} incidents based on a "
        f"{config.INCIDENT_BUNDLE_TIMEDELTA} time window."
    )
    print()
    display_incident_reports(incident_summaries)

    if cancelled():
        return finish_cancelled()
    if not options.automatic_ai:
        for incident in incident_summaries:
            incident["llm_analysis_status"] = "not_selected"
            incident["llm_analysis_message"] = "Automatic AI is off for this scan; manual analysis is available."
    elif not incident_summaries:
        print("\nSkipping LLM analysis because no incidents were found.")
    else:
        if cancel_event is None and progress is None:
            add_llm_analyses(incident_summaries)
        else:
            add_llm_analyses(incident_summaries, cancel_event=cancel_event, progress=report_progress)

    if cancelled():
        return finish_cancelled()
    report_progress("Saving scan history…", 95)
    if history_store is not None:
        try:
            history_store.save_scan(incident_summaries, options)
            incident_summaries.history_saved = True
        except Exception:
            history_warning("save this scan")

    # Export after LLM enrichment so the report contains the analysis results.
    report_progress("Saving latest report…", 98)
    try:
        export_incidents_to_json(incident_summaries, "reports/incidents.json")
    except ReportExportError as exc:
        message = f"Results are available, but the JSON report was not saved: {describe_error(exc)}"
        incident_summaries.export_error = message
        incident_summaries.warnings.append(message)
        _write_status(f"Warning: {message}", error=True)

    for incident in incident_summaries:
        if incident is None:
            continue
        analysis_data = incident.get("llm_analysis")
        if analysis_data is not None:
            display_llm_analysis(analysis_data)

    report_progress("Scan complete" if incident_summaries.complete else "Scan finished with incomplete coverage", 100)
    return incident_summaries


def scan_system(options=None, *, progress=None, cancel_event=None, history_store=None) -> ScanResult:
    """Run the full workflow and return a ScanResult for CLI or desktop callers.

    Includes terminal output, Windows log reads, report-file writes, and optional
    AI requests. It is blocking; the desktop calls it from ScanWorker. Partial
    failures are metadata, while fatal workflow errors propagate to the caller.

    Args:
        options: ScanOptions snapshot, or None to read config defaults.
        progress: Optional callback(stage_text, percent) running on this thread.
        cancel_event: Optional threading.Event-like cooperative cancellation.
            A running OS read or AI request must return before it can stop.
        history_store: Optional HistoryStore-compatible backend for tests or
            alternate storage. By default a per-user history store is opened.
    """

    if options is None and progress is None and cancel_event is None and history_store is None:
        return _run_workflow()
    return _run_workflow(options, progress=progress, cancel_event=cancel_event, history_store=history_store)


def main() -> int:
    """Run a CLI scan and return its process exit code.

    Zero means complete collection and successful export, one means an incomplete
    scan, export failure, or handled fatal error, and 130 means interruption.
    Broken pipes return zero to allow terminal pipelines to close early.
    Optional AI failures alone do not fail the scan. The module guard passes
    this return value to SystemExit.
    """

    try:
        result = scan_system()
        if getattr(result, "cancelled", False):
            return 130
        return int(not getattr(result, "complete", True) or bool(getattr(result, "export_error", None)))
    except KeyboardInterrupt:
        _write_status("LogOpen was cancelled by the user.", error=True)
        return 130
    except BrokenPipeError:
        return 0
    except (LogOpenError, ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
        _write_status(
            f"Error: LogOpen could not complete: {describe_error(exc)}",
            error=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
