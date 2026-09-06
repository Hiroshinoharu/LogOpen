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
    """Determine the priority level of an incident based on its score.

    Args:
        score (int): The calculated score of the incident.

    Returns:
        str: A string representing the priority level ("High", "Medium", or "Low").
    """
    return next(
        priority
        for threshold, priority in PRIORITY_THRESHOLDS
        if score >= threshold
    )


def _add_score(breakdown, reasons, category, points, reason):
    """Record one scoring contribution and its explanation."""

    if points:
        breakdown[category] += points
        reasons.append(reason)


def _get_duration_score(duration):
    """Return the score contribution and reason for an incident duration."""

    if duration < timedelta(minutes=1):
        return 0, None
    if duration <= timedelta(minutes=5):
        return 5, "Incident lasted between 1 and 5 minutes"
    if duration <= timedelta(minutes=15):
        return 10, "Incident lasted between 5 and 15 minutes"
    return 15, "Incident lasted over 15 minutes"


def calculate_incident_score(incident):
    """Calculate a capped score, reasons, and category breakdown for an incident."""

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
