import json
from copy import deepcopy
from pathlib import Path

import config
from llm_analysis import should_analyse_incident_with_llm


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
