"""Deterministic incident scoring with explainable category contributions.

Severity, event volume, duration, recurrence, and classification contribute
points. The final total is capped at 100; the breakdown retains raw points.
These heuristics prioritize investigation rather than proving a root cause.
"""

from datetime import timedelta

MAX_INCIDENT_SCORE = 100
PRIORITY_THRESHOLDS = (
    (80, "Critical"),
    (60, "High"),
    (40, "Medium"),
    (0, "Low"),
)

CLASSIFICATION_WEIGHTS = {
    "Application Hang": 10,
    "UDP Ephemeral Port Exhaustion": 15,
    "Defender Service Crash": 20,
    "TPM Hardware Error": 20,
    "DNS Resolution Timeout": 5,
    "DCOM Permission Warning": 0,
}

def get_incident_priority(score):
    """Map a nonnegative score to Low, Medium, High, or Critical.

    Thresholds are 0, 40, 60, and 80 respectively. The scorer supplies values
    capped at 100; this helper does not validate or clamp its input.
    """
    return next(
        priority
        for threshold, priority in PRIORITY_THRESHOLDS
        if score >= threshold
    )


def _add_score(breakdown, reasons, category, points, reason):
    """Add nonzero points to an existing breakdown category and append its reason.

    Mutates both containers; zero-point contributions leave them unchanged.
    """

    if points:
        breakdown[category] += points
        reasons.append(reason)


def _get_duration_score(duration):
    """Return (points, reason) for a timedelta, with None when no points apply.

    Under one minute scores zero; one through five minutes scores five; over
    five through fifteen scores ten; over fifteen scores fifteen.
    """

    if duration < timedelta(minutes=1):
        return 0, None
    if duration <= timedelta(minutes=5):
        return 5, "Incident lasted between 1 and 5 minutes"
    if duration <= timedelta(minutes=15):
        return 10, "Incident lasted between 5 and 15 minutes"
    return 15, "Incident lasted over 15 minutes"


def calculate_incident_score(incident):
    """Return a capped score, explanation list, and uncapped category breakdown.

    Args:
        incident: Summary containing highest_severity, event_count,
            incident_duration (timedelta), and incident_classification.
            Optional recurrence_count_24h/recurrence_count_7d default to one.

    Returns:
        tuple[int, list[str], dict[str, int]]: Total up to 100, reasons for
        nonzero contributions, and points for severity, event_count,
        classification_impact, recurrence, and duration.

    Both recurrence bonuses can apply. Breakdown values can sum above 100
    because only the final score is capped. The incident is not modified.
    """

    reasons = []
    breakdown = {
        "severity": 0,
        "event_count": 0,
        "classification_impact": 0,
        "recurrence": 0,
        "duration": 0,
    }

    # Score based on highest severity
    if incident["highest_severity"] == "Error":
        _add_score(
            breakdown,
            reasons,
            "severity",
            40,
            "Error-level incident detected",
        )
    elif incident["highest_severity"] == "Warning":
        _add_score(
            breakdown,
            reasons,
            "severity",
            20,
            "Warning-level incident detected",
        )

    if incident["event_count"] >= 10:
        _add_score(
            breakdown,
            reasons,
            "event_count",
            30,
            "High event count (10+)",
        )
    elif incident["event_count"] >= 5:
        _add_score(
            breakdown,
            reasons,
            "event_count",
            15,
            "Moderate event count (5-9)",
        )
    elif incident["event_count"] >= 3:
        _add_score(
            breakdown,
            reasons,
            "event_count",
            5,
            "Low event count (3-4)",
        )

    duration_score, duration_reason = _get_duration_score(
        incident["incident_duration"]
    )
    _add_score(
        breakdown,
        reasons,
        "duration",
        duration_score,
        duration_reason,
    )

    recurrence_count_24h = incident.get("recurrence_count_24h", 1)
    recurrence_count_7d = incident.get("recurrence_count_7d", 1)

    if recurrence_count_24h >= 3:
        _add_score(
            breakdown,
            reasons,
            "recurrence",
            10,
            "Classification occurred "
            f"{recurrence_count_24h} times in the last 24 hours",
        )

    if recurrence_count_7d >= 5:
        _add_score(
            breakdown,
            reasons,
            "recurrence",
            20,
            "Classification occurred "
            f"{recurrence_count_7d} times in the last 7 days",
        )

    classification = incident["incident_classification"]

    impact_score = CLASSIFICATION_WEIGHTS.get(classification, 0)

    if impact_score > 0:
        _add_score(
            breakdown,
            reasons,
            "classification_impact",
            impact_score,
            f"Incident classification '{classification}' "
            f"has an impact score of {impact_score}",
        )

    score = min(sum(breakdown.values()), MAX_INCIDENT_SCORE)
    return score, reasons, breakdown
