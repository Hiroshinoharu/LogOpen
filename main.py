"""Run the LogOpen workflow from Windows Event Log collection to reporting."""

import config
from event_collection import get_recent_events
from event_filtering import filter_events, filter_events_by_time
from incident_detection import (
    build_incident,
    bundle_incidents,
    refresh_incident_summary,
)
from incident_recurrence import apply_recurrence_metadata
from json_reporting import export_incidents_to_json
from terminal_reporting import display_incident_reports
from llm_analysis import analyse_incident_with_llm


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


def add_llm_analyses(incident_summaries):
    """Add serializable LLM analysis to each incident summary."""

    for incident in incident_summaries:
        try:
            analysis = analyse_incident_with_llm(incident)
        except Exception as exc:
            incident["llm_analysis"] = None
            incident["llm_analysis_error"] = str(exc)
            continue

        incident["llm_analysis"] = (
            analysis.model_dump(mode="json") if analysis is not None else None
        )


def main():
    """Print recent warning and error events from the configured Windows logs."""

    problem_events = []

    for log_type in config.LOG_TYPES:
        events = get_recent_events(log_type, limit=500)
        events.sort(key=lambda event: event["time_generated"])
        log_problem_events = filter_events_by_time(events, 24)
        log_problem_events = filter_events(log_problem_events, ["Error", "Warning"])
        problem_events.extend(log_problem_events)
    problem_events.sort(key=lambda event: event["time_generated"])
    incidents = bundle_incidents(problem_events)
    
    incident_summaries = [
        build_incident(incident) for incident in incidents
    ]
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

if __name__ == "__main__":
    main()
