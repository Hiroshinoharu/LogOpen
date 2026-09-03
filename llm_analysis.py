from typing import Any

from incident_classification import classify_event
from models.incident_analysis import IncidentAnalysis

MAX_CONTEXT_EVENTS = 25
MAX_MESSAGE_LENGTH = 1_500
DEFAULT_ANALYSIS_MODEL = "gpt-5.6-luna"

ANALYSIS_INSTRUCTIONS = """
You are an expert in troubleshooting PC issues, particularly Windows operating systems.
You are also an expert in analyzing and interpreting event logs, system logs, and other
diagnostic information to identify potential issues and recommend resolutions.

Analyze the incident context and return a structured response containing exactly these
four fields:
- explanation: A clear explanation of what the incident indicates and its likely impact.
- likely_causes: A list of the most likely root causes, ordered from most to least plausible
  based on the supplied evidence.
- recommended_actions: A prioritized list of practical diagnostic or corrective actions.
- remediation_notes: Additional cautions, verification steps, and relevant follow-up notes.

Base the response only on the supplied incident evidence.

Clearly distinguish confirmed facts from hypotheses.

Do not invent:
- Event IDs
- providers
- services
- applications
- registry keys
- commands
- configuration values
- causes that are unsupported by the supplied evidence

If the evidence is insufficient to determine a specific cause, explicitly say that the
cause is uncertain rather than guessing.

Prefer safe, non-destructive diagnostic steps before recommending configuration changes
or other potentially disruptive actions.

When suggesting remediation:
- prioritize verification and diagnosis first
- explain why an action may help
- avoid destructive actions unless strongly justified by the supplied evidence
- mention when administrator privileges, backups, or additional investigation may be needed

Keep the explanation concise and technically accurate.
Do not repeat the raw incident data unnecessarily.
"""


def _format_mapping(values):
    """Format incident metadata collections into stable, readable text."""

    if not values:
        return "None"
    if isinstance(values, dict):
        return ", ".join(f"{key}: {value}" for key, value in values.items())
    return "; ".join(str(value) for value in values)


def _clean_message(message):
    """Normalize and bound event messages before including them in LLM context."""

    cleaned = " ".join(line.strip() for line in message.splitlines() if line.strip())
    if not cleaned:
        return "No message available."
    if len(cleaned) <= MAX_MESSAGE_LENGTH:
        return cleaned
    return f"{cleaned[:MAX_MESSAGE_LENGTH].rstrip()}... [truncated]"


def _format_events(events):
    """Render representative source events as labelled, LLM-friendly text."""

    if not events:
        return "No source events available."

    formatted_events = []
    for index, event in enumerate(events[:MAX_CONTEXT_EVENTS], start=1):
        classification = event.get("classification") or classify_event(event)
        formatted_events.append(
            "\n".join(
                (
                    f"Event {index}:",
                    f"  Time: {event.get('time_generated')}",
                    f"  Level: {event.get('level')}",
                    f"  Provider: {event.get('provider')}",
                    f"  Event ID: {event.get('event_id')}",
                    f"  Classification: {classification}",
                    f"  Message: {_clean_message(event.get('message', ''))}",
                )
            )
        )

    remaining_events = len(events) - MAX_CONTEXT_EVENTS
    if remaining_events > 0:
        formatted_events.append(
            f"{remaining_events} additional events omitted; use the aggregate counts above."
        )
    return "\n\n".join(formatted_events)


def build_incident_context(incident):
    """
    Build a context string for the incident to be used as input for the LLM.

    Args:
        incident (dict): The incident data to build context from.
        
    Returns:
        str: A formatted string containing the incident context.
    """
    return "\n".join(
        (
            "INCIDENT OVERVIEW",
            f"Summary: {incident.get('summary_text', 'Not available')}",
            f"Log Type: {incident.get('log_type')}",
            f"Computer Name: {incident.get('computer_name')}",
            f"Time Window: {incident.get('time_generated_start')} to {incident.get('time_generated_end')}",
            f"Duration: {incident.get('incident_duration')}",
            f"Event Count: {incident.get('event_count', len(incident.get('events', [])))}",
            f"Levels Present: {', '.join(incident.get('levels', [])) or 'None'}",
            f"Highest Severity: {incident.get('highest_severity')}",
            "",
            "ASSESSMENT",
            f"Priority: {incident.get('incident_priority', 'Not scored')}",
            f"Score: {incident.get('incident_score', 'Not scored')}",
            f"Score Reasons: {_format_mapping(incident.get('incident_score_reasons'))}",
            f"Score Breakdown: {_format_mapping(incident.get('incident_score_breakdown'))}",
            f"Incident Classification: {incident.get('incident_classification')}",
            f"Event Classification Counts: {_format_mapping(incident.get('event_classification_counts'))}",
            f"Provider Classifications: {_format_mapping(incident.get('provider_classifications'))}",
            "",
            "RECURRENCE",
            f"Recurring: {incident.get('is_recurring', False)}",
            f"Occurrences in 24 Hours: {incident.get('recurrence_count_24h', 0)}",
            f"Occurrences in 7 Days: {incident.get('recurrence_count_7d', 0)}",
            "",
            "EVENT AGGREGATES",
            f"Providers: {', '.join(incident.get('providers', [])) or 'None'}",
            f"Provider Counts: {_format_mapping(incident.get('providers_counts'))}",
            f"Event IDs: {', '.join(str(event_id) for event_id in incident.get('event_ids', [])) or 'None'}",
            f"Event ID Counts: {_format_mapping(incident.get('event_ids_counts'))}",
            "",
            "SOURCE EVENTS",
            _format_events(incident.get('events', [])),
        )
    )

def _coerce_incident_analysis(parsed_output: Any) -> IncidentAnalysis | None:
    """Validate structured model output before returning it to callers."""

    if isinstance(parsed_output, IncidentAnalysis):
        return parsed_output
    if isinstance(parsed_output, dict):
        return IncidentAnalysis.model_validate(parsed_output)
    return None


def analyse_incident_with_llm(incident):
    """
    Analyse an incident using a large language model (LLM) to provide insights and recommendations.

    Args:
        incident (dict): The incident data to be analysed.
        
    Returns:
        IncidentAnalysis | None: The validated structured analysis, if available.
    """
    try:
        from openai import OpenAI
    except ImportError:
        return None

    context = build_incident_context(incident)
    client = OpenAI()

    response = client.responses.parse(
        model=DEFAULT_ANALYSIS_MODEL,
        instructions=ANALYSIS_INSTRUCTIONS,
        input=f"Analyze the following LogOpen incident:\n{context}",
        text_format=IncidentAnalysis,
    )

    return _coerce_incident_analysis(getattr(response, "output_parsed", None))
