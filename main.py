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
    select_incidents_for_llm,
    should_analyse_incident_with_llm,
)
from models.scan_result import ScanResult
from terminal_reporting import display_incident_reports
from settings.app_settings import AppSettings


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


def add_llm_analyses(incident_summaries):
    """Attach optional analysis and an explicit AI outcome to summaries in place.

    Loads preferences once, preserves existing analyses, clears stale error
    fields, then selects eligible incidents by score and the saved scan limit.
    Every incident gets llm_analysis_status and llm_analysis_message; failures
    also get llm_analysis_error. Successful models become JSON-compatible dicts.

    May make billable API calls and write status output. Unexpected per-incident
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
        elif should_analyse_incident_with_llm(incident, settings=settings):
            status, message = "not_selected", "Not selected because the AI analysis limit for this scan was reached."
        else:
            status, message = "not_selected", "This incident does not meet the AI analysis criteria."
        incident["llm_analysis_status"] = status
        incident["llm_analysis_message"] = message
    if not settings.enabled:
        _write_status("AI analysis is disabled. Deterministic analysis is complete.")
        return
    selected_incidents = select_incidents_for_llm(incident_summaries, settings=settings)

    for incident in selected_incidents:
        try:
            outcome = analyse_incident_with_llm_result(incident, settings=settings)
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


def _run_workflow() -> ScanResult:
    """Run collection through export, preserving usable results from partial scans.

    Reads configured logs within the lookback/cap, filters warnings/errors,
    groups events, builds summaries, adds recurrence, and refreshes scores.
    Prints local reports, adds optional AI, then exports reports/incidents.json
    relative to the current working directory and prints available AI prose.

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

    _validate_log_types(config.LOG_TYPES)
    problem_events = []
    collection_errors = []
    incident_summaries = ScanResult()

    for log_type in config.LOG_TYPES:
        try:
            events = get_recent_events(
                log_type, limit=config.MAX_RECORDS_PER_LOG,
                hours=config.SCAN_WINDOW_HOURS, levels=("Error", "Warning"),
            )
            coverage = events.coverage() if hasattr(events, "coverage") else {"complete": True}
            coverage["window_hours"] = config.SCAN_WINDOW_HOURS
            incident_summaries.coverage[log_type] = coverage
            if not coverage["complete"]:
                incident_summaries.complete = False
            for message in getattr(events, "warnings", ()):
                incident_summaries.warnings.append(message)
                _write_status(f"Warning: {message}", error=True)
            events.sort(key=lambda event: event["time_generated"])
            log_problem_events = filter_events_by_time(events, config.SCAN_WINDOW_HOURS)
            log_problem_events = filter_events(
                log_problem_events,
                ["Error", "Warning"],
            )
            problem_events.extend(log_problem_events)
        except (EventCollectionError, ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            error_message = f"{log_type}: {describe_error(exc)}"
            collection_errors.append(error_message)
            incident_summaries.complete = False
            incident_summaries.coverage[log_type] = {
                "complete": False, "stop_reason": "collection_error",
                "window_hours": config.SCAN_WINDOW_HOURS,
            }
            incident_summaries.warnings.append(error_message)
            _write_status(
                f"Warning: could not process the {log_type} log: "
                f"{describe_error(exc)}",
                error=True,
            )

    if len(collection_errors) == len(config.LOG_TYPES):
        raise RuntimeError(
            "none of the configured event logs could be processed ("
            + "; ".join(collection_errors)
            + ")"
        )

    problem_events.sort(key=lambda event: event["time_generated"])
    incidents = bundle_incidents(problem_events)

    for incident_number, incident in enumerate(incidents, start=1):
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

    apply_recurrence_metadata(incident_summaries)
    for summary in incident_summaries:
        refresh_incident_summary(summary)

    log_types_label = format_log_types_label(config.LOG_TYPES)
    print(
        f"Found {len(problem_events)} recent warning or error events in "
        f"the {log_types_label} in the last {config.SCAN_WINDOW_HOURS} hours:"
    )
    print(
        f"Bundled into {len(incidents)} incidents based on a "
        f"{config.INCIDENT_BUNDLE_TIMEDELTA} time window."
    )
    print()
    display_incident_reports(incident_summaries)

    if not incident_summaries:
        print("\nSkipping LLM analysis because no incidents were found.")
    else:
        add_llm_analyses(incident_summaries)

    # Export after LLM enrichment so the report contains the analysis results.
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

    return incident_summaries


def scan_system() -> ScanResult:
    """Run the full workflow and return a ScanResult for CLI or desktop callers.

    Includes terminal output, Windows log reads, report-file writes, and optional
    AI requests. It is blocking; the desktop calls it from ScanWorker. Partial
    failures are metadata, while fatal workflow errors propagate to the caller.
    """

    return _run_workflow()


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
