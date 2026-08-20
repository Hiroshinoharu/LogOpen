"""Terminal reporting helpers."""

import sys

from incident_detection import build_incident


def clean_message(message):
    """Remove empty lines from an event message before printing."""

    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return "No message available."
    return "\n".join(lines)


def _print_incident_mapping(title, values, out, separator="x"):
    print(title, file=out)
    for key, value in values.items():
        print(f"    {key} {separator} {value}", file=out)


def _print_incident_events(events, out):
    print("  Messages:", file=out)
    for event_index, event in enumerate(events, start=1):
        print(
            (
                f"    Event {event_index} "
                f"({event['level']} | {event['provider']} | ID {event['event_id']}):"
            ),
            file=out,
        )
        for line in clean_message(event["message"]).splitlines():
            print(f"      {line}", file=out)


def _print_incident_summary(index, summary, out):
    print(f"Incident {index}:", file=out)
    print(f"  Summary: {summary['summary_text']}", file=out)
    print(f"  Log Type: {summary['log_type']}", file=out)
    print(f"  Computer Name: {summary['computer_name']}", file=out)
    print(f"  Providers: {summary['providers']}", file=out)
    _print_incident_mapping(
        "  Provider Classifications:",
        summary["provider_classifications"],
        out,
        "->",
    )
    _print_incident_mapping("  Provider Counts:", summary["providers_counts"], out)
    print(
        f"  Incident Classification: {summary['incident_classification']}",
        file=out,
    )
    _print_incident_mapping(
        "  Event Classifications:",
        summary["event_classification_counts"],
        out,
    )
    print(f"  Event IDs: {summary['event_ids']}", file=out)
    _print_incident_mapping("  Event ID Counts:", summary["event_ids_counts"], out)
    print(f"  Highest Severity: {summary['highest_severity']}", file=out)
    print(f"  Time Generated Start: {summary['time_generated_start']}", file=out)
    print(f"  Time Generated End: {summary['time_generated_end']}", file=out)
    print(f"  Levels: {summary['levels']}", file=out)
    print(f"  Number of Events in Incident: {summary['event_count']}", file=out)
    print(f"  Incident Duration: {summary['incident_duration']}", file=out)
    _print_incident_events(summary["events"], out)
    print(file=out)


def display_incident_reports(incidents, out=None):
    """Print plain incident summaries."""

    if out is None:
        out = sys.stdout

    if not incidents:
        print("No incidents to display.", file=out)
        return

    for index, incident in enumerate(incidents, start=1):
        incident_summary = build_incident(incident)
        if incident_summary is None:
            continue

        _print_incident_summary(index, incident_summary, out)
