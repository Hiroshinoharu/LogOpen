"""Groups related events into incidents and builds their summary dictionaries."""

import re
from collections import Counter

from config import EVENT_SIMILARITY_THRESHOLD, INCIDENT_BUNDLE_TIMEDELTA
from incident_classification import classify_event, classify_incident
from incident_scoring import calculate_incident_score, get_incident_priority

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


def _join_readable(values):
    """Return a natural-language string for a sequence of values."""

    if not values:
        return "unknown providers"
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _format_duration(duration):
    """Return a concise natural-language representation of a timedelta."""

    total_seconds = max(int(duration.total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []

    if hours:
        hour_label = "hour" if hours == 1 else "hours"
        parts.append(f"{hours} {hour_label}")
    if minutes:
        minute_label = "minute" if minutes == 1 else "minutes"
        parts.append(f"{minutes} {minute_label}")
    if seconds or not parts:
        second_label = "second" if seconds == 1 else "seconds"
        parts.append(f"{seconds} {second_label}")

    return _join_readable(parts)


def _format_score_reasons(reasons):
    """Return a readable explanation of the incident score reasons."""

    if not reasons:
        return "no additional scoring factors applied"

    readable_reasons = {
        "Error-level incident detected": "error-level events were present",
        "Warning-level incident detected": "warning-level events were present",
        "High event count (>10)": "more than 10 related events were grouped together",
        "Moderate event count (5-9)": "5 to 9 related events were grouped together",
        "Low event count (3-4)": "3 to 4 related events were grouped together",
        "Incident duration > 1 hour": "the incident lasted longer than 1 hour",
    }
    formatted_reasons = [
        readable_reasons.get(reason, reason[:1].lower() + reason[1:])
        for reason in reasons
    ]
    return _join_readable(formatted_reasons)


def _build_summary_text(incident_summary):
    """Return the human-readable incident summary text."""

    provider_labels = sorted(
        incident_summary["provider_classifications"].values()
    )
    event_count = incident_summary["event_count"]
    event_label = "event" if event_count == 1 else "events"
    duration_label = _format_duration(incident_summary["incident_duration"])
    score_reasons = _format_score_reasons(
        incident_summary["incident_score_reasons"]
    )

    return (
        f"{incident_summary['incident_priority']}-priority "
        f"{incident_summary['incident_classification']} incident in the "
        f"{incident_summary['log_type']} log on "
        f"{incident_summary['computer_name']} involving "
        f"{_join_readable(provider_labels)}. "
        f"It contains {event_count} {event_label} and lasted {duration_label}. "
        f"Score {incident_summary['incident_score']} because {score_reasons}."
    )


def refresh_incident_summary(incident_summary):
    """Refresh score-derived fields after enriching an incident summary."""

    score, reasons = calculate_incident_score(incident_summary)
    incident_summary["incident_score"] = score
    incident_summary["incident_priority"] = get_incident_priority(score)
    incident_summary["incident_score_reasons"] = reasons
    incident_summary["summary_text"] = _build_summary_text(incident_summary)
    return incident_summary


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
    
    # Build the incident summary dictionary
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
    
    return refresh_incident_summary(incident_summary)
