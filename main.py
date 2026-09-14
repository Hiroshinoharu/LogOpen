"""Run the LogOpen workflow from Windows Event Log collection to reporting."""

import sys
from typing import Any

import config
from errors import describe_error
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
    analyse_incident_with_llm,
    select_incidents_for_llm,
)
from terminal_reporting import display_incident_reports
from settings.app_settings import AppSettings


def format_log_types_label(log_types):
    """Return a readable label for the configured Windows log types."""

    if not log_types:
        return "configured logs"
    if len(log_types) == 1:
        return f"{log_types[0]} log"
    if len(log_types) == 2:
        return f"{log_types[0]} and {log_types[1]} logs"
    return f"{', '.join(log_types[:-1])}, and {log_types[-1]} logs"


def display_llm_analysis(analysis):
    """Print a validated LLM analysis in a readable format."""

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
    """Write a status message without risking a second error during cleanup."""

    try:
        print(message, file=sys.stderr if error else sys.stdout)
    except (BrokenPipeError, OSError):
        pass


def add_llm_analyses(incident_summaries):
    """Add serializable LLM analysis to selected incident summaries."""
    
    settings = AppSettings().load()
    if not settings.enabled:
        _write_status("AI analysis is disabled. Deterministic analysis is complete.")
        return
    selected_incidents = select_incidents_for_llm(incident_summaries, settings=settings)

    for incident in selected_incidents:
        try:
            analysis = analyse_incident_with_llm(incident, settings=settings)
            incident["llm_analysis"] = (
                analysis.model_dump(mode="json")
                if analysis is not None
                else None
            )
        except Exception:
            incident["llm_analysis"] = None
            # SDK/validation exceptions may contain credentials or request data.
            incident["llm_analysis_error"] = "AI analysis could not be completed. Check AI Settings."
            _write_status(
                "Warning: AI analysis was skipped for an incident: "
                f"{incident['llm_analysis_error']}",
                error=True,
            )
            continue


def _validate_log_types(log_types):
    """Validate the configured log list before starting collection."""

    if isinstance(log_types, str) or not isinstance(log_types, (list, tuple)):
        raise ValueError("config.LOG_TYPES must be a list or tuple of log names")
    if not log_types:
        raise ValueError("config.LOG_TYPES must contain at least one log name")
    if any(not isinstance(name, str) or not name.strip() for name in log_types):
        raise ValueError("each configured log name must be a non-empty string")


def _run_workflow() -> list[dict[str, Any]]:
    """Report recent Windows warning and error events and return their incidents."""

    _validate_log_types(config.LOG_TYPES)
    problem_events = []
    collection_errors = []

    for log_type in config.LOG_TYPES:
        try:
            events = get_recent_events(log_type, limit=500)
            events.sort(key=lambda event: event["time_generated"])
            log_problem_events = filter_events_by_time(events, 24)
            log_problem_events = filter_events(
                log_problem_events,
                ["Error", "Warning"],
            )
            problem_events.extend(log_problem_events)
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
            error_message = f"{log_type}: {describe_error(exc)}"
            collection_errors.append(error_message)
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

    incident_summaries: list[dict[str, Any]] = []
    for incident_number, incident in enumerate(incidents, start=1):
        try:
            summary = build_incident(incident)
        except Exception as exc:
            _write_status(
                f"Warning: incident {incident_number} was skipped because it "
                f"could not be summarized: {describe_error(exc)}",
                error=True,
            )
            continue
        if summary is not None:
            incident_summaries.append(summary)

    apply_recurrence_metadata(incident_summaries)
    for summary in incident_summaries:
        refresh_incident_summary(summary)

    log_types_label = format_log_types_label(config.LOG_TYPES)
    print(
        f"Found {len(problem_events)} recent warning or error events in "
        f"the {log_types_label} in the last 24 hours:"
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
    export_incidents_to_json(incident_summaries, "reports/incidents.json")

    for incident in incident_summaries:
        if incident is None:
            continue
        analysis_data = incident.get("llm_analysis")
        if analysis_data is not None:
            display_llm_analysis(analysis_data)

    return incident_summaries


def scan_system() -> list[dict[str, Any]]:
    """Return incident summaries, allowing scan failures to reach the caller."""

    return _run_workflow()


def main() -> int:
    """Run LogOpen and translate failures into concise user-facing messages."""

    try:
        scan_system()
        return 0
    except KeyboardInterrupt:
        _write_status("LogOpen was cancelled by the user.", error=True)
        return 130
    except BrokenPipeError:
        return 0
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
        _write_status(
            f"Error: LogOpen could not complete: {describe_error(exc)}",
            error=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
