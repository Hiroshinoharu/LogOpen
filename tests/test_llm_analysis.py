import json
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

import config
from llm_analysis import should_analyse_incident_with_llm
from models.incident_analysis import DiagnosticStep, IncidentAnalysis

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

    client = MagicMock()
    client.__enter__.return_value = client
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
        "diagnostic_steps": [
            {"description": "Inspect the affected service.", "command": "Get-Service", "shell": "powershell", "risk_level": "safe"},
            {"description": "Review related events.", "command": None, "shell": None, "risk_level": "safe"},
        ],
    }
    client, _ = install_openai_mock(monkeypatch, output_parsed=mocked_output)

    analysis = analyse_incident_with_llm(incident)

    assert isinstance(analysis, IncidentAnalysis)
    assert analysis.explanation == mocked_output["explanation"]
    assert analysis.likely_causes == mocked_output["likely_causes"]
    assert analysis.recommended_actions == mocked_output["recommended_actions"]
    assert analysis.remediation_notes == mocked_output["remediation_notes"]
    assert all(isinstance(step, DiagnosticStep) for step in analysis.diagnostic_steps)
    assert [step.model_dump() for step in analysis.diagnostic_steps] == mocked_output["diagnostic_steps"]
    assert client.responses.parse.call_args.kwargs["text_format"] is IncidentAnalysis
    assert "Allowed shells: powershell, cmd." in client.responses.parse.call_args.kwargs["instructions"]
    assert "Preferred shell: powershell." in client.responses.parse.call_args.kwargs["instructions"]


@pytest.mark.parametrize("shell,command", [("cmd", "ver"), ("bash", "pwd")])
def test_configured_shell_preferences_reach_llm_requests(monkeypatch, shell, command):
    from llm_analysis import analyse_incident_with_llm

    monkeypatch.setattr(config, "ALLOWED_DIAGNOSTIC_SHELLS", (shell,))
    monkeypatch.setattr(config, "PREFERRED_DIAGNOSTIC_SHELL", shell)
    client, _ = install_openai_mock(monkeypatch, output_parsed={
        "explanation": "Inspect the environment.", "likely_causes": [],
        "recommended_actions": [], "remediation_notes": [],
        "diagnostic_steps": [{"description": "Check the environment.", "command": command,
                              "shell": shell, "risk_level": "safe"}],
    })
    analysis = analyse_incident_with_llm(make_incident())

    assert analysis.diagnostic_steps[0].shell == shell
    instructions = client.responses.parse.call_args.kwargs["instructions"]
    assert f"Allowed shells: {shell}." in instructions
    assert f"Preferred shell: {shell}." in instructions


def test_provider_identity_rules_reach_the_llm_request_without_changing_incident_data(monkeypatch):
    from llm_analysis import analyse_incident_with_llm

    incident = {"log_type": "System", "providers": ["DCOM"], "event_ids": [10010]}
    original = deepcopy(incident)
    client, _ = install_openai_mock(monkeypatch, output_parsed={
        "explanation": "Review the incident evidence.", "likely_causes": [],
        "recommended_actions": [], "remediation_notes": [], "diagnostic_steps": [],
    })

    assert analyse_incident_with_llm(incident) is not None
    client.responses.parse.assert_called_once()
    request = client.responses.parse.call_args.kwargs
    instructions = " ".join(request["instructions"].split())
    for requirement in (
        "Do not assume that a LogOpen provider/display label is the registered Windows Event Provider name accepted by Get-WinEvent.",
        "Do not turn display labels, normalized provider names, classifications, or other LogOpen-derived labels into ProviderName parameters or FilterHashtable values unless supplied evidence explicitly establishes the exact registered provider name.",
        "When the exact registered provider name is uncertain, omit ProviderName from Get-WinEvent filters.",
        "Prefer fields directly supported by incident evidence, such as LogName and Event ID (Id).",
        "Do not guess a replacement provider name or apply a hard-coded provider mapping.",
        "ProviderName may still be selected as an output property",
    ):
        assert requirement in instructions
    assert "Get-WinEvent -FilterHashtable @{LogName='System'; Id=10010} | Select-Object -First 50 TimeCreated, Id, ProviderName, LevelDisplayName, Message" in instructions
    assert "Do not add ProviderName='DCOM' to that filter." in instructions
    assert "do not reuse those values for unrelated incidents" in instructions
    assert "Providers: DCOM" in request["input"]
    assert "Event IDs: 10010" in request["input"]
    assert incident == original


def test_provider_guidance_preserves_existing_diagnostic_safety_and_risk_rules():
    from llm_analysis import build_analysis_instructions

    instructions = " ".join(build_analysis_instructions().split())
    for requirement in (
        "Do not invent commands or parameters.",
        "Prefer safe, read-only diagnostic checks before actions that modify the system.",
        "Order diagnostic_steps from lowest risk to highest risk where practical.",
        'Assign exactly one risk_level to every step: - "safe":',
        '- "safe": Read-only inspection or diagnostic actions that should not modify system state and normally do not require elevated privileges.',
        '- "caution": Non-destructive actions that may temporarily affect functionality or state, such as restarting a service, process, application, or clearing a cache; also diagnostic operations requiring additional privileges or care.',
        '- "system_change": Assign "system_change" only when the diagnostic step itself directly modifies persistent system state.',
        'Planning, escalation, validation, reviewing configuration, or recommending that an administrator investigate should not be marked as "system_change" unless the step explicitly performs a persistent change.',
        "Do not automatically execute or imply that LogOpen has executed any command.",
        "The diagnostic steps are recommendations for the user to review.",
    ):
        assert requirement in instructions


@pytest.mark.parametrize("requirements", [
    pytest.param((
        "Follow the principle of least privilege: start with the narrowest useful diagnostic command that works in a standard, non-elevated Windows PowerShell or terminal session.",
        "Prefer a non-elevated alternative when it provides sufficient evidence.",
        "Do not add administrator-only switches, system-wide scopes, or elevated operations when a non-elevated command can gather sufficient diagnostic information.",
        "do not use Get-AppxPackage -AllUsers when querying the current user's packages with Get-AppxPackage is sufficient.",
        "Expand to system-wide or administrator-level investigation only when the incident evidence provides a reason to do so.",
        "Never assume that LogOpen or the user's terminal is running as Administrator.",
        "If a command requires administrator privileges, explicitly state this in the diagnostic step description and explain why elevation is necessary for the diagnostic objective.",
        "Only recommend elevation when it is genuinely necessary.",
    ), id="least-privilege-and-elevation"),
    pytest.param((
        "Be generated only when reasonably confident that the command, parameters, filters, and syntax are valid for the supplied evidence.",
        "Never derive parameters from unverified contextual clues or become artificially specific through unverified assumptions.",
        "When uncertain about a parameter, prefer a simpler valid diagnostic command or omit the command entirely.",
        "A step with command=null and shell=null is preferable to a plausible-looking but unverified command.",
        "Base the response only on the supplied incident evidence.",
        "Clearly distinguish confirmed facts from hypotheses.",
        "Base every step on evidence from the supplied incident.",
        "Distinguish facts directly established by the incident, reasonable hypotheses, and information that still needs to be gathered.",
        "Do not claim that a command proves a root cause unless its result would actually establish that conclusion.",
        "Do not claim that a diagnostic step will fix the incident unless the supplied evidence supports that conclusion.",
    ), id="command-reliability-and-grounding"),
])
def test_command_safety_guidance_reaches_the_llm_request(monkeypatch, requirements):
    from llm_analysis import analyse_incident_with_llm

    client, _ = install_openai_mock(monkeypatch, output_parsed={
        "explanation": "Gather further evidence.", "likely_causes": [],
        "recommended_actions": [], "remediation_notes": [], "diagnostic_steps": [],
    })

    assert analyse_incident_with_llm({"log_type": "Application"}) is not None
    client.responses.parse.assert_called_once()
    instructions = " ".join(client.responses.parse.call_args.kwargs["instructions"].split())
    for requirement in requirements:
        assert requirement in instructions


def test_diagnostic_order_prioritizes_evidence_and_justified_interventions():
    from llm_analysis import build_analysis_instructions

    instructions = " ".join(build_analysis_instructions().split())
    expected_sequence = (
        "1. Safe, non-elevated evidence gathering.",
        "2. Additional investigation and correlation.",
        "3. Elevated diagnostics only if justified.",
        "4. Temporary or interventional actions only if justified.",
        "5. Persistent system changes only when the incident evidence strongly supports them.",
    )
    assert " ".join(expected_sequence) in instructions
    assert "Do not recommend a persistent system change merely because it is a commonly suggested fix for an Event ID." in instructions
    assert "When evidence is incomplete or ambiguous, prefer steps that gather more information before recommending system changes." in instructions


@pytest.mark.parametrize("shell", [None, "bash"])
def test_generated_commands_require_an_allowed_shell(monkeypatch, capsys, shell):
    from llm_analysis import analyse_incident_with_llm

    monkeypatch.setattr(config, "ALLOWED_DIAGNOSTIC_SHELLS", ("powershell", "cmd"))
    monkeypatch.setattr(config, "PREFERRED_DIAGNOSTIC_SHELL", "powershell")
    install_openai_mock(monkeypatch, output_parsed={
        "explanation": "Inspect the environment.", "likely_causes": [],
        "recommended_actions": [], "remediation_notes": [],
        "diagnostic_steps": [{"description": "Check the environment.", "command": "pwd",
                              "shell": shell, "risk_level": "safe"}],
    })

    assert analyse_incident_with_llm(make_incident()) is None
    assert "missing or disallowed shell" in capsys.readouterr().err


@pytest.mark.parametrize("allowed,preferred", [
    ([], "powershell"), ("powershell", "powershell"),
    (("terminal",), "terminal"), (("powershell",), "cmd"),
])
def test_invalid_shell_configuration_skips_the_api_call(monkeypatch, capsys, allowed, preferred):
    from llm_analysis import analyse_incident_with_llm

    monkeypatch.setattr(config, "ALLOWED_DIAGNOSTIC_SHELLS", allowed)
    monkeypatch.setattr(config, "PREFERRED_DIAGNOSTIC_SHELL", preferred)
    client, sdk = install_openai_mock(monkeypatch)

    assert analyse_incident_with_llm(make_incident()) is None
    sdk.OpenAI.assert_not_called()
    client.responses.parse.assert_not_called()
    assert "invalid diagnostic shell configuration" in capsys.readouterr().err


def test_analyse_incident_with_llm_rejects_invalid_diagnostic_steps(monkeypatch, capsys):
    from llm_analysis import analyse_incident_with_llm

    install_openai_mock(monkeypatch, output_parsed={
        "explanation": "A test explanation.",
        "likely_causes": [],
        "recommended_actions": [],
        "remediation_notes": [],
        "diagnostic_steps": [{"description": "Inspect the service.", "risk_level": "unknown"}],
    })

    assert analyse_incident_with_llm(make_incident(incident_priority="High")) is None
    assert "did not match the analysis schema" in capsys.readouterr().err

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
