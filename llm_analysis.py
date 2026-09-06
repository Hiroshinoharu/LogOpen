from __future__ import annotations

import os
import sys
from typing import Any

import config
from incident_classification import classify_event
from models.incident_analysis import IncidentAnalysis
from pydantic import ValidationError


def _format_mapping(values):
    """Format incident metadata collections into stable, readable text."""

    if not values:
        return "None"
    if isinstance(values, dict):
        return ", ".join(f"{key}: {value}" for key, value in values.items())
    if isinstance(values, str):
        return values
    return "; ".join(str(value) for value in values)


def _clean_message(message):
    """Normalize and bound event messages before including them in LLM context."""

    if message is None:
        return "No message available."
    if not isinstance(message, str):
        message = str(message)
    cleaned = " ".join(line.strip() for line in message.splitlines() if line.strip())
    if not cleaned:
        return "No message available."
    if len(cleaned) <= config.MAX_MESSAGE_LENGTH:
        return cleaned
    return f"{cleaned[:config.MAX_MESSAGE_LENGTH].rstrip()}... [truncated]"


def _format_events(events):
    """Render representative source events as labelled, LLM-friendly text."""

    if not events:
        return "No source events available."

    formatted_events = []
    for index, event in enumerate(events[:config.MAX_CONTEXT_EVENTS], start=1):
        if not isinstance(event, dict):
            formatted_events.append(f"Event {index}: invalid event data omitted.")
            continue
        try:
            classification = event.get("classification") or classify_event(event)
        except (AttributeError, KeyError, TypeError, ValueError):
            classification = "Unknown"
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

    remaining_events = len(events) - config.MAX_CONTEXT_EVENTS
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
    if not isinstance(incident, dict):
        raise TypeError("incident must be a dictionary")

    events = incident.get("events") or []
    levels = incident.get("levels") or []
    providers = incident.get("providers") or []
    event_ids = incident.get("event_ids") or []

    return "\n".join(
        (
            "INCIDENT OVERVIEW",
            f"Summary: {incident.get('summary_text', 'Not available')}",
            f"Log Type: {incident.get('log_type')}",
            f"Computer Name: {incident.get('computer_name')}",
            f"Time Window: {incident.get('time_generated_start')} to {incident.get('time_generated_end')}",
            f"Duration: {incident.get('incident_duration')}",
            f"Event Count: {incident.get('event_count', len(events))}",
            f"Levels Present: {', '.join(str(level) for level in levels) or 'None'}",
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
            f"Providers: {', '.join(str(provider) for provider in providers) or 'None'}",
            f"Provider Counts: {_format_mapping(incident.get('providers_counts'))}",
            f"Event IDs: {', '.join(str(event_id) for event_id in event_ids) or 'None'}",
            f"Event ID Counts: {_format_mapping(incident.get('event_ids_counts'))}",
            "",
            "SOURCE EVENTS",
            _format_events(events),
        )
    )


def _coerce_incident_analysis(parsed_output: Any) -> IncidentAnalysis | None:
    """Validate structured model output before returning it to callers."""

    if isinstance(parsed_output, IncidentAnalysis):
        return parsed_output
    if isinstance(parsed_output, dict):
        return IncidentAnalysis.model_validate(parsed_output)
    return None


def _log_llm_failure(message):
    """Report an optional LLM analysis failure without disrupting processing."""

    try:
        print(f"Warning: LLM analysis skipped: {message}", file=sys.stderr)
    except (BrokenPipeError, OSError):
        pass


def should_analyse_incident_with_llm(incident):
    """
    Determine whether an incident should be analysed with a large language model (LLM).

    Args:
        incident (dict): The incident data to evaluate.

    Returns:
        bool: True if the incident should be analysed, False otherwise.
    """
    if not isinstance(incident, dict) or not config.ENABLE_LLM_ANALYSIS:
        return False

    if incident.get("llm_analysis") is not None:
        return False

    if incident.get("incident_priority") in {"High", "Critical"}:
        return True

    if incident.get("is_recurring"):
        return True

    return incident.get("event_count", 0) >= config.LLM_ANALYSIS_MIN_EVENTS

def select_incidents_for_llm(incidents):
    """
    Select incidents that meet the criteria for LLM analysis.

    Args:
        incidents (list): A list of incident dictionaries.
    """
    # Filter incidents based on the criteria defined in should_analyse_incident_with_llm
    selected_incidents = []
    for incident in  incidents:
        if should_analyse_incident_with_llm(incident):
            selected_incidents.append(incident)
    
    # Sort the selected incidents by incident_score in descending order and limit to MAX_LLM_ANALYSES_PER_RUN
    selected_incidents.sort(key=lambda incident: incident.get("incident_score", 0), reverse=True)
    return selected_incidents[:config.MAX_LLM_ANALYSES_PER_RUN]


def analyse_incident_with_llm(incident):
    """
    Analyse an incident using a large language model (LLM) to provide insights and recommendations.

    Args:
        incident (dict): The incident data to be analysed.

    Returns:
        IncidentAnalysis | None: The validated structured analysis, if available.
    """
    if not isinstance(incident, dict):
        _log_llm_failure("incident data is invalid.")
        return None
    analysis_model = IncidentAnalysis
    if not os.getenv("OPENAI_API_KEY"):
        _log_llm_failure("OpenAI API credentials are not configured.")
        return None

    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            OpenAI,
            OpenAIError,
            RateLimitError,
        )
    except ImportError:
        _log_llm_failure("the OpenAI SDK is not installed.")
        return None

    try:
        context = build_incident_context(incident)
        client = OpenAI()
        response = client.responses.parse(
            model=config.DEFAULT_ANALYSIS_MODEL,
            instructions=config.ANALYSIS_INSTRUCTIONS,
            input=f"Analyze the following LogOpen incident:\n{context}",
            text_format=analysis_model,
        )
    except AuthenticationError:
        _log_llm_failure("OpenAI authentication failed.")
        return None
    except (APIConnectionError, APITimeoutError):
        _log_llm_failure("the OpenAI request could not be completed.")
        return None
    except RateLimitError:
        _log_llm_failure("the OpenAI rate limit was reached.")
        return None
    except APIStatusError:
        _log_llm_failure("the OpenAI API returned an error response.")
        return None
    except OpenAIError:
        _log_llm_failure("the OpenAI SDK could not complete the request.")
        return None
    except (AttributeError, TypeError):
        _log_llm_failure("incident data could not be formatted for analysis.")
        return None

    try:
        analysis = _coerce_incident_analysis(
            getattr(response, "output_parsed", None)
        )
    except ValidationError:
        _log_llm_failure("the structured LLM output did not match the analysis schema.")
        return None

    if analysis is None:
        _log_llm_failure("the LLM response did not contain valid structured output.")
    return analysis
