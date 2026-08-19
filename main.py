"""Read recent Windows event log entries and filter them for inspection."""

import config
from event_collection import get_recent_events
from event_filtering import filter_events, filter_events_by_time
from incident_detection import bundle_incidents
from json_reporting import export_incidents_to_json
from terminal_reporting import display_incident_reports


def format_log_types_label(log_types):
    """Return a readable label for the configured Windows log types."""

    if not log_types:
        return "configured logs"
    if len(log_types) == 1:
        return f"{log_types[0]} log"
    if len(log_types) == 2:
        return f"{log_types[0]} and {log_types[1]} logs"
    return f"{', '.join(log_types[:-1])}, and {log_types[-1]} logs"


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
    display_incident_reports(incidents)

    # Export incidents to JSON file
    export_incidents_to_json(incidents, "reports/incidents.json")

if __name__ == "__main__":
    main()
