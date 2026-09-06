import json
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import config
from llm_analysis import should_analyse_incident_with_llm
from models.incident_analysis import IncidentAnalysis

INCIDENT_REPORT_PATH = Path(__file__).parents[1] / "reports" / "incidents.json"
with INCIDENT_REPORT_PATH.open(encoding="utf-8") as report_file:
    EXPORTED_INCIDENT = json.load(report_file)[0]


def make_incident(**overrides):
    """Copy an exported incident and apply test-specific values."""

    incident = deepcopy(EXPORTED_INCIDENT)

    # Tests exercise eligibility before a new analysis has been stored.
    incident["llm_analysis"] = None
    incident.update(overrides)
    return incident


def install_openai_mock(monkeypatch, *, output_parsed=None, side_effect=None):
    """Install a mock OpenAI SDK and return the mocked client."""

    class OpenAIError(Exception):
        pass

    class APIConnectionError(OpenAIError):
        pass

    class APITimeoutError(OpenAIError):
        pass

    class APIStatusError(OpenAIError):
        pass

    class AuthenticationError(APIStatusError):
        pass

    class RateLimitError(APIStatusError):
        pass

    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        output_parsed=output_parsed
    )
    client.responses.parse.side_effect = side_effect

    mock_openai = ModuleType("openai")
    mock_openai.OpenAI = Mock(return_value=client)
    mock_openai.OpenAIError = OpenAIError
    mock_openai.APIConnectionError = APIConnectionError
    mock_openai.APITimeoutError = APITimeoutError
    mock_openai.APIStatusError = APIStatusError
    mock_openai.AuthenticationError = AuthenticationError
    mock_openai.RateLimitError = RateLimitError
    monkeypatch.setitem(sys.modules, "openai", mock_openai)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return client, mock_openai


def test_low_priority_incident_is_not_analyzed():
    incident  = make_incident()
    
    assert should_analyse_incident_with_llm(incident) is False

def test_high_priority_incident_is_analyzed(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", True)
    
    incident = make_incident(incident_priority="High")
    
    assert should_analyse_incident_with_llm(incident) is True

def test_critical_priority_incident_is_analyzed(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", True)
    
    incident = make_incident(incident_priority="Critical")
    
    assert should_analyse_incident_with_llm(incident) is True

def test_recurring_incident_is_analyzed(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", True)
    
    incident = make_incident(is_recurring=True)
    assert should_analyse_incident_with_llm(incident) is True

def test_large_incident_is_analyzed(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", True)
    monkeypatch.setattr(config, "LLM_ANALYSIS_MIN_EVENTS", 10)

    incident = make_incident(event_count=10)

    assert should_analyse_incident_with_llm(incident) is True


def test_llm_disable_returns_false(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", False)
    
    incident = make_incident(incident_priority="Critical")
    
    assert should_analyse_incident_with_llm(incident) is False

def test_already_analyzed_incident_returns_false(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", True)

    incident = make_incident(
        incident_priority="Critical",
        llm_analysis={"explanation": "Already analyzed"}
    )

    assert should_analyse_incident_with_llm(incident) is False

def test_selected_incidents_are_sorted_and_limited(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", True)
    monkeypatch.setattr(config, "MAX_LLM_ANALYSES_PER_RUN", 2)

    incidents = [
        make_incident(incident_priority="High", incident_score=20),
        make_incident(incident_priority="Critical", incident_score=90),
        make_incident(incident_priority="High", incident_score=40),
        make_incident(incident_priority="Critical", incident_score=70),
    ]

    from llm_analysis import select_incidents_for_llm

    selected = select_incidents_for_llm(incidents)

    assert len(selected) == 2
    assert selected[0]["incident_score"] == 90
    assert selected[1]["incident_score"] == 70

def test_selected_incidents_with_recurring_and_high_priority(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", True)
    monkeypatch.setattr(config, "MAX_LLM_ANALYSES_PER_RUN", 3)

    incidents = [
        make_incident(incident_priority="Low", is_recurring=True, incident_score=10),
        make_incident(incident_priority="High", is_recurring=False, incident_score=50),
        make_incident(incident_priority="Critical", is_recurring=False, incident_score=80),
        make_incident(incident_priority="Low", is_recurring=False, incident_score=5),
    ]

    from llm_analysis import select_incidents_for_llm

    selected = select_incidents_for_llm(incidents)

    assert len(selected) == 3
    assert selected[0]["incident_score"] == 80  # Critical
    assert selected[1]["incident_score"] == 50  # High
    assert selected[2]["incident_score"] == 10  # Recurring Low

def test_analyse_incident_with_llm_returns_incident_analysis(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", True)
    incident = make_incident(incident_priority="High")
    from llm_analysis import analyse_incident_with_llm

    mocked_output = {
        "explanation": "A test explanation.",
        "likely_causes": ["A test cause."],
        "recommended_actions": ["A test action."],
        "remediation_notes": ["A test note."],
    }
    install_openai_mock(monkeypatch, output_parsed=mocked_output)

    analysis = analyse_incident_with_llm(incident)

    assert isinstance(analysis, IncidentAnalysis)
    assert analysis.explanation == mocked_output["explanation"]
    assert analysis.likely_causes == mocked_output["likely_causes"]
    assert analysis.recommended_actions == mocked_output["recommended_actions"]
    assert analysis.remediation_notes == mocked_output["remediation_notes"]

def test_analyse_incident_with_llm_returns_none_when_output_missing(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", True)
    incident = make_incident(incident_priority="High")
    from llm_analysis import analyse_incident_with_llm
    install_openai_mock(monkeypatch)

    analysis = analyse_incident_with_llm(incident)

    
    assert analysis is None

def test_analyse_incident_with_llm_handles_api_error(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", True)
    incident = make_incident(incident_priority="High")
    from llm_analysis import analyse_incident_with_llm

    client, mock_openai = install_openai_mock(monkeypatch)
    client.responses.parse.side_effect = mock_openai.APIConnectionError()

    analysis = analyse_incident_with_llm(incident)

    assert analysis is None

def test_analyse_incident_with_llm_handles_invalid_output(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", True)
    incident = make_incident(incident_priority="High")
    from llm_analysis import analyse_incident_with_llm
    install_openai_mock(
        monkeypatch,
        output_parsed={"invalid_field": "This is not a valid analysis"},
    )

    analysis = analyse_incident_with_llm(incident)

    assert analysis is None
