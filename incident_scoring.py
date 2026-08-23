from datetime import timedelta

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
    
    return score, reasons
