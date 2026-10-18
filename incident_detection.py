"""Groups related events into incidents and builds their summary dictionaries."""

import re
from collections import Counter

from config import EVENT_SIMILARITY_THRESHOLD, INCIDENT_BUNDLE_TIMEDELTA
from incident_classification import classify_event, classify_incident
from incident_scoring import calculate_incident_score, get_incident_priority

SEVERITY_RANK = {
    "Critical": 4,
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
    """Join string values with commas/and; use unknown providers for empty input."""

    if not values:
        return "unknown providers"
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _format_duration(duration):
    """Format a timedelta as hours, minutes, and seconds in natural language.

    Fractional seconds are truncated and negative durations display as zero.
    """

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
    """Turn scoring-reason strings into a readable clause for summary_text.

    Known reasons are reworded; other reasons have their first letter lowered.
    Empty input yields a fixed explanation that no additional factors applied.
    """

    if not reasons:
        return "no additional scoring factors applied"

    readable_reasons = {
        "Error-level incident detected": "error-level events were present",
        "Warning-level incident detected": "warning-level events were present",
        "High event count (>10)": "more than 10 related events were grouped together",
        "Moderate event count (5-9)": "5 to 9 related events were grouped together",
        "Low event count (3-4)": "3 to 4 related events were grouped together",
        "Incident lasted between 1 and 5 minutes": (
            "the incident lasted between 1 and 5 minutes"
        ),
        "Incident lasted between 5 and 15 minutes": (
            "the incident lasted between 5 and 15 minutes"
        ),
        "Incident lasted over 15 minutes": "the incident lasted over 15 minutes",
    }
    formatted_reasons = [
        readable_reasons.get(reason, reason[:1].lower() + reason[1:])
        for reason in reasons
    ]
    return _join_readable(formatted_reasons)


def _build_summary_text(incident_summary):
    """Return a deterministic paragraph from a fully populated incident summary.

    Uses classification, priority, source logs, computer, provider labels,
    duration, count, and score reasons. It neither calls AI nor changes data.
    """

    provider_labels = sorted(
        incident_summary["provider_classifications"].values()
    )
    event_count = incident_summary["event_count"]
    event_label = "event" if event_count == 1 else "events"
    duration_label = _format_duration(incident_summary["incident_duration"])
    score_reasons = _format_score_reasons(
        incident_summary["incident_score_reasons"]
    )
    log_types = incident_summary.get("log_types") or [incident_summary["log_type"]]
    log_label = f"{_join_readable(log_types)} {'log' if len(log_types) == 1 else 'logs'}"

    return (
        f"{incident_summary['incident_priority']}-priority "
        f"{incident_summary['incident_classification']} incident in the "
        f"{log_label} on "
        f"{incident_summary['computer_name']} involving "
        f"{_join_readable(provider_labels)}. "
        f"It contains {event_count} {event_label} and lasted {duration_label}. "
        f"Score {incident_summary['incident_score']} because {score_reasons}."
    )


def refresh_incident_summary(incident_summary):
    """Recalculate score, priority, reasons, breakdown, and summary_text in place.

    Args:
        incident_summary: A dictionary built by build_incident, optionally
            enriched with recurrence counts.

    Returns:
        dict: The same dictionary, with all score-derived fields refreshed.

    Call again after recurrence enrichment so the score includes repetition.
    """

    score, reasons, breakdown = calculate_incident_score(incident_summary)
    incident_summary["incident_score"] = score
    incident_summary["incident_priority"] = get_incident_priority(score)
    incident_summary["incident_score_reasons"] = reasons
    incident_summary["incident_score_breakdown"] = breakdown
    incident_summary["summary_text"] = _build_summary_text(incident_summary)
    return incident_summary


def normalize_component_name(value):
    """Lowercase a string, replace hyphens/underscores, and collapse whitespace."""

    return " ".join(
        value.lower().replace("_", " ").replace("-", " ").split()
    )


def extract_event_components(event):
    """Return normalized component names found in provider and message text.

    Uses known aliases and executable-name matches to link related events
    across providers/logs. This is a text heuristic, not installed-software
    discovery. The input event dictionary is read without modification.
    """

    text = f"{event['provider']} {event['message']}".lower()
    components = set()

    for canonical_name, aliases in COMPONENT_ALIASES.items():
        if any(alias in text for alias in aliases):
            components.add(canonical_name)

    for executable_name in EXECUTABLE_PATTERN.findall(text):
        components.add(normalize_component_name(executable_name))

    return components


def component_similarity_score(previous_event, current_event):
    """Return +3 for shared components, -3 for disjoint known ones, or zero.

    Zero means at least one event has no extractable component evidence.
    """

    previous_components = extract_event_components(previous_event)
    current_components = extract_event_components(current_event)

    if not previous_components or not current_components:
        return 0

    if previous_components & current_components:
        return 3

    return -3


def event_similarity_score(previous_event, current_event):
    """Return a nonnegative relationship score for two event dictionaries.

    Different computers score zero. A shared provider adds two points and a
    matching ID within that provider adds two more. Component evidence adds
    or subtracts three; timestamps within the configured window add one.
    bundle_incidents separately enforces the time window as a hard cutoff.
    """

    if previous_event.get("computer_name") != current_event.get("computer_name"):
        return 0

    score = 0

    if previous_event["provider"] == current_event["provider"]:
        score += 2

        # A shared numeric ID alone does not establish a relationship between
        # different providers. Cross-provider matches require shared components.
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


def bundle_incidents(events, *, cancel_event=None):
    """Group related events chronologically, including interleaved sequences.

    Args:
        events: Parsed event dictionaries with mutually comparable timestamps.
        cancel_event: Optional threading.Event-like token checked between
            events; cancellation returns the groups built so far.

    Returns:
        list[list[dict]]: Groups in creation/start order, each chronological.
        Input order is unchanged and event dictionaries are shared, not copied.

    A provider/component index identifies candidate groups on the same
    computer. Compare each candidate's last event within the configured time
    gap, then require EVENT_SIMILARITY_THRESHOLD. Ties prefer higher similarity,
    a newer last event, then the earlier-created group. The gap applies between
    successive events, so a whole incident may span longer than that window.
    """

    if not events:
        return []

    sorted_events = sorted(events, key=lambda event: event["time_generated"])
    incidents = []
    indexes = {}

    def keys(event):
        """Build computer-scoped provider/component index keys for one event."""
        computer = event.get("computer_name")
        return [(computer, "provider", event["provider"])] + [
            (computer, "component", component)
            for component in sorted(extract_event_components(event))
        ]

    def unindex(index):
        """Remove a group's last-event memberships from the candidate indexes.

        The group and its events remain in the output; only future lookup changes.
        """
        for key in keys(incidents[index][-1]):
            indexes[key].discard(index)
            if not indexes[key]:
                del indexes[key]

    for event in sorted_events:
        if cancel_event is not None and cancel_event.is_set():
            break
        event_keys = keys(event)
        candidates = set()
        for key in event_keys:
            candidates.update(indexes.get(key, ()))
        matches = []
        for index in sorted(candidates):
            previous = incidents[index][-1]
            if event["time_generated"] - previous["time_generated"] > INCIDENT_BUNDLE_TIMEDELTA:
                unindex(index)
                continue
            score = event_similarity_score(previous, event)
            if score >= EVENT_SIMILARITY_THRESHOLD:
                matches.append((score, previous["time_generated"], -index, index))
        if matches:
            index = max(matches)[-1]
            unindex(index)
            incidents[index].append(event)
        else:
            index = len(incidents)
            incidents.append([event])
        for key in event_keys:
            indexes.setdefault(key, set()).add(index)
    return incidents


def build_incident(events):
    """Summarize one chronological event group and calculate its initial score.

    Args:
        events: A chronological list of related parsed events, normally from
            bundle_incidents. The first/last entries determine the time span.

    Returns:
        dict | None: Counts, provider/ID sets, source log names, timestamps,
        duration, severity, classification, score fields, and summary_text;
        None for an empty group. The events field retains the original list.

    The most common event classification wins (first encountered on ties).
    When detection rules matched, classification is chosen among the strongest
    assessed events so informational follow-ups cannot hide a stronger finding.
    log_types preserves individual source names; log_type is a display label.
    Recurrence and optional AI fields are added later by the main workflow.
    """

    if not events:
        return None

    highest_severity_event = max(
        events,
        key=lambda event: max(SEVERITY_RANK.get(event["level"], 0),
                              SEVERITY_RANK.get(event.get("detection_severity"), 0)),
    )

    event_id_counts = Counter(event["event_id"] for event in events)
    provider_counts = Counter(event["provider"] for event in events)
    event_classification_counts = Counter(
        classify_event(event) for event in events
    )

    most_common_classification = event_classification_counts.most_common(1)
    if most_common_classification:
        most_common_classification = most_common_classification[0][0]
    # A stream of informational follow-up actions must not conceal a stronger
    # detection in the same incident (for example Defender detection + actions).
    assessed_severity = max((highest_severity_event["level"], highest_severity_event.get("detection_severity", "Unknown")),
                            key=lambda level: SEVERITY_RANK.get(level, 0))
    if any(event.get("detection_rules") for event in events):
        strongest = [event for event in events
                     if max(SEVERITY_RANK.get(event["level"], 0), SEVERITY_RANK.get(event.get("detection_severity"), 0))
                     == SEVERITY_RANK.get(assessed_severity, 0)]
        most_common_classification = Counter(classify_event(event) for event in strongest).most_common(1)[0][0]
    
    # Build the incident summary dictionary
    first_event = events[0]
    last_event = events[-1]
    log_types = sorted({event["log_type"] for event in events})
    incident_summary = {
        "log_type": ", ".join(log_types),
        "log_types": log_types,
        "computer_name": first_event["computer_name"],
        "providers": sorted({event["provider"] for event in events}),
        "providers_counts": provider_counts,
        "event_ids": sorted({event["event_id"] for event in events}),
        "event_ids_counts": event_id_counts,
        "time_generated_start": first_event["time_generated"],
        "time_generated_end": last_event["time_generated"],
        "levels": sorted({event["level"] for event in events}),
        "highest_severity": assessed_severity,
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
