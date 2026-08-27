"""Unit tests for incident analysis models and LLM integration boundaries."""

import importlib
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from models.incident_analysis import IncidentAnalysis


@pytest.fixture
def incident():
    return {
        "incident_type": "Repeated failed logins",
        "source": "Security",
        "count": 5,
    }


def test_incident_analysis_accepts_complete_analysis():
    analysis = IncidentAnalysis(
        explanation="Several failed sign-in attempts were detected.",
        likely_causes=["Incorrect credentials", "Brute-force attempt"],
        recommended_actions=["Review the account activity"],
        remediation_notes=["Reset the password if compromise is suspected"],
    )

    assert analysis.likely_causes == ["Incorrect credentials", "Brute-force attempt"]
    assert analysis.recommended_actions == ["Review the account activity"]


def test_incident_analysis_requires_all_analysis_fields():
    with pytest.raises(ValidationError):
        IncidentAnalysis(
            explanation="An explanation",
            likely_causes=["A cause"],
            recommended_actions=["An action"],
        )


def test_llm_analysis_import_uses_mocked_openai_dependency(monkeypatch, incident):
    mock_client = Mock()
    mock_openai = ModuleType("openai")
    mock_openai.OpenAI = Mock(return_value=mock_client)
    monkeypatch.setitem(sys.modules, "openai", mock_openai)
    sys.modules.pop("llm_analysis", None)

    llm_analysis = importlib.import_module("llm_analysis")
    result = llm_analysis.analyse_incident_with_llm(incident)

    mock_openai.OpenAI.assert_called_once_with()
    mock_client.models.list.assert_not_called()
    assert result is None
