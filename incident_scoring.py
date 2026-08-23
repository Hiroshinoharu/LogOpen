from datetime import timedelta

CLASSIFICATION_WEIGHTS = {
    "Application Hang": 10,
    "UDP Ephemeral Port Exhaustion": 15,
    "Defender Service Crash": 20,
    "TPM Hardware Error": 20,
    "DNS Resolution Timeout": 5,
    "DCOM Permission Warning": 0,
}

def  get_incident_priority(score):
    """Determine the priority level of an incident based on its score.

    Args:
        score (int): The calculated score of the incident.

    Returns:
        str: A string representing the priority level ("High", "Medium", or "Low").
    """
    if score >= 80:
        return "Critical"
    elif score >= 60:
        return "High"
    elif score >= 40:
        return "Medium"
    else:
        return "Low"

def calculate_incident_score(incident):
    """Calculate an incident score from impact, likelihood, and detectability values.

    This function combines the provided factors into a single numeric score that can
    be used to compare and rank incidents.

    Args:
        impact: Numeric value representing how severe the incident is.
        likelihood: Numeric value representing how likely the incident is to occur.
        detectability: Numeric value representing how easy the incident is to detect.

    Returns:
        A numeric score representing the combined effect of impact, likelihood,
        and detectability.
    """
    score = 0
    reasons = []

    # Score based on highest severity
    if incident["highest_severity"] == "Error":
        score += 40
        reasons.append("Error-level incident detected")

    elif incident["highest_severity"] == "Warning":
        score += 20
        reasons.append("Warning-level incident detected")
    
    if incident["event_count"] >= 10:
        score += 30
        reasons.append("High event count (>10)")
    
    elif incident["event_count"] >= 5:
        score += 15
        reasons.append("Moderate event count (5-9)")
    
    elif incident["event_count"] >= 3:
        score += 5
        reasons.append("Low event count (3-4)")
    
    if incident["incident_duration"] > timedelta(hours=1):
        score += 20
        reasons.append("Incident duration > 1 hour")

    recurrence_count_24h = incident.get("recurrence_count_24h", 1)
    recurrence_count_7d = incident.get("recurrence_count_7d", 1)

    if recurrence_count_24h >= 3:
        score += 10
        reasons.append(
            "Classification occurred "
            f"{recurrence_count_24h} times in the last 24 hours"
        )

    if recurrence_count_7d >= 5:
        score += 20
        reasons.append(
            "Classification occurred "
            f"{recurrence_count_7d} times in the last 7 days"
        )

    classification = incident["incident_classification"]

    impact_score = CLASSIFICATION_WEIGHTS.get(classification, 0)

    if impact_score > 0:
        score += impact_score
        reasons.append(f"Incident classification '{classification}' has an impact score of {impact_score}")

    return score, reasons
