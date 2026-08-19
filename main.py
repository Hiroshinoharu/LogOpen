"""Read recent Windows event log entries and filter them for inspection."""

from config import INCIDENT_BUNDLE_TIMEDELTA
from event_collection import get_log_type, get_recent_events
from event_filtering import filter_events, filter_events_by_time
from incident_detection import bundle_incidents
from json_reporting import export_incidents_to_json
from terminal_reporting import display_incident_reports


def main():
    """Print recent warning and error events from the selected Windows log."""

    events = get_recent_events(limit=500)
    events.sort(key=lambda event: event["time_generated"])
    problem_events = filter_events_by_time(events, 24)
    problem_events = filter_events(problem_events, ["Error", "Warning"])
    incidents = bundle_incidents(problem_events)
    print(
        f"Found {len(problem_events)} recent warning or error events in "
        f"the {get_log_type()} log in the last 24 hours:"
    )
    print(
        f"Bundled into {len(incidents)} incidents based on a "
        f"{INCIDENT_BUNDLE_TIMEDELTA} time window."
    )
    print()
    display_incident_reports(incidents)

    # Export incidents to JSON file
    export_incidents_to_json(incidents, "reports/incidents.json")

if __name__ == "__main__":
    main()
