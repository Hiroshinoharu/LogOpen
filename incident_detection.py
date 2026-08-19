"""Incident detection and summarization helpers."""

from collections import Counter
import re

from config import EVENT_SIMILARITY_THRESHOLD, INCIDENT_BUNDLE_TIMEDELTA
from incident_classification import classify_event, classify_incident

SEVERITY_RANK = {
    "Unknown": 0,
    "Information": 1,
    "Success Audit": 1,
    "Warning": 2,
    "Error": 3,
    "Failure Audit": 3,
}

COMPONENT_ALIASES = {
    "microsoft defender antivirus": {
        "microsoft defender antivirus",
        "microsoft defender antivirus service",
        "msmpeng",
        "msmpeng.exe",
        "windefend",
    },
}
EXECUTABLE_PATTERN = re.compile(r"\b[a-z0-9_.-]+\.exe\b")


def normalize_component_name(value):
    """Return a normalized component name for comparison."""

    return " ".join(
        value.lower().replace("_", " ").replace("-", " ").split()
    )


def extract_event_components(event):
    """Extract deterministic component identifiers from provider/message text."""

    text = f"{event['provider']} {event['message']}".lower()
    components = set()

    for canonical_name, aliases in COMPONENT_ALIASES.items():
        if any(alias in text for alias in aliases):
            components.add(canonical_name)

    for executable_name in EXECUTABLE_PATTERN.findall(text):
        components.add(normalize_component_name(executable_name))

    return components


def component_similarity_score(previous_event, current_event):
    """Return a bonus or penalty based on extracted component names."""

    previous_components = extract_event_components(previous_event)
    current_components = extract_event_components(current_event)

    if not previous_components or not current_components:
        return 0

    if previous_components & current_components:
        return 3

    return -3


def event_similarity_score(previous_event, current_event):
    """
    Calculate the similarity score between two events.

    Args:
        previous_event (dict): The previous event dictionary.
        current_event (dict): The current event dictionary.
    """

    score = 0

    if previous_event["provider"] == current_event["provider"]:
        score += 2

    if previous_event["event_id"] == current_event["event_id"]:
        score += 2

    score += component_similarity_score(previous_event, current_event)

    if (
        abs(
            (
                current_event["time_generated"] - previous_event["time_generated"]
            ).total_seconds()
        )
        <= INCIDENT_BUNDLE_TIMEDELTA.total_seconds()
    ):
        score += 1

    return max(score, 0)


def bundle_incidents(events):
    """
    Bundle events into incidents using ``INCIDENT_BUNDLE_TIMEDELTA``.

    Args:
        events (list): A list of event dictionaries.
    """

    if not events:
        return []

    sorted_events = sorted(events, key=lambda event: event["time_generated"])
    incidents = []
    current_incident = [sorted_events[0]]

    for event in sorted_events[1:]:
        time_difference = (
            event["time_generated"] - current_incident[-1]["time_generated"]
        )
        if (
            time_difference <= INCIDENT_BUNDLE_TIMEDELTA
            and event_similarity_score(current_incident[-1], event)
            >= EVENT_SIMILARITY_THRESHOLD
        ):
            current_incident.append(event)
        else:
            incidents.append(current_incident)
            current_incident = [event]

    incidents.append(current_incident)
    return incidents


def build_incident(events):
    """Build a summary of an incident from a list of events."""

    if not events:
        return None

    highest_severity_event = max(
        events,
        key=lambda event: SEVERITY_RANK.get(event["level"], 0),
    )

    event_id_counts = Counter(event["event_id"] for event in events)
    provider_counts = Counter(event["provider"] for event in events)
    event_classification_counts = Counter(
        classify_event(event) for event in events
    )

    most_common_classification = event_classification_counts.most_common(1)
    if most_common_classification:
        most_common_classification = most_common_classification[0][0]

    first_event = events[0]
    last_event = events[-1]
    incident_summary = {
        "log_type": first_event["log_type"],
        "computer_name": first_event["computer_name"],
        "providers": sorted({event["provider"] for event in events}),
        "providers_counts": provider_counts,
        "event_ids": sorted({event["event_id"] for event in events}),
        "event_ids_counts": event_id_counts,
        "time_generated_start": first_event["time_generated"],
        "time_generated_end": last_event["time_generated"],
        "levels": sorted({event["level"] for event in events}),
        "highest_severity": highest_severity_event["level"],
        "incident_classification": most_common_classification,
        "event_classification_counts": event_classification_counts,
        "event_count": len(events),
        "events": events,
        "incident_duration": last_event["time_generated"] - first_event["time_generated"],
    }
    incident_summary["provider_classifications"] = classify_incident(
        incident_summary
    )
    return incident_summary
