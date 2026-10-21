"""Terminal preferences affect new suggestions without executing those suggestions."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import pytest

from settings.app_settings import AISettings
from settings.terminal_settings import TerminalPreferences, TerminalSettings, diagnostic_shell_policy
from settings import terminal_service
from llm_analysis import analyse_incident_with_llm_result, build_analysis_instructions


@pytest.mark.parametrize("shell", ["powershell", "cmd", "bash"])
def test_saved_terminal_format_controls_new_ai_instructions_and_validation(monkeypatch, shell):
    TerminalPreferences().save(TerminalSettings(shell, "windows_terminal"))
    assert TerminalPreferences().load() == TerminalSettings(shell, "windows_terminal")
    assert diagnostic_shell_policy() == ((shell,), shell)
    instructions = build_analysis_instructions()
    assert f"Allowed shells: {shell}." in instructions
    assert f"Preferred shell: {shell}." in instructions
    client = MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr("llm_analysis.create_openai_client", lambda: client)
    response = dict(explanation="A recorded problem.", likely_causes=[], recommended_actions=[], remediation_notes=[],
                    diagnostic_steps=[dict(description="Read system information.", command="example", shell=shell, risk_level="safe")])
    client.responses.parse.return_value = SimpleNamespace(output_parsed=response)
    preferences = AISettings(True, "test-model", 3)
    result = analyse_incident_with_llm_result({}, settings=preferences)
    assert result.status == "completed"
    assert f"Preferred shell: {shell}." in client.responses.parse.call_args.kwargs["instructions"]
    response["diagnostic_steps"][0]["shell"] = "cmd" if shell != "cmd" else "powershell"
    assert analyse_incident_with_llm_result({}, settings=preferences).status == "failed"


def test_invalid_stored_terminal_preferences_recover_to_defaults():
    preferences = TerminalPreferences()
    preferences._storage.setValue("terminal/shell", "unsafe;command")
    preferences._storage.setValue("terminal/host", [])
    assert preferences.load() == TerminalSettings()


@pytest.mark.parametrize("shell,executable", [("powershell", "pwsh.exe"), ("cmd", "cmd.exe"), ("bash", "wsl.exe")])
@pytest.mark.parametrize("host", ["console", "windows_terminal"])
def test_open_terminal_uses_fixed_argv_without_any_generated_command(monkeypatch, shell, executable, host):
    monkeypatch.setattr(terminal_service.sys, "platform", "win32")
    monkeypatch.setattr(terminal_service.shutil, "which", lambda name: "C:/Windows/" + name)
    popen = Mock()
    monkeypatch.setattr(terminal_service.subprocess, "Popen", popen)
    success, _message = terminal_service.open_terminal(preferences=TerminalSettings(shell, host))
    assert success
    command = popen.call_args.args[0]
    assert "C:/Windows/" + executable in command
    assert (command[0] == "C:/Windows/wt.exe") is (host == "windows_terminal")
    assert "shell" not in popen.call_args.kwargs
    assert "-Command" not in command and "/C" not in command


def test_unavailable_terminal_does_not_start_another_process(monkeypatch):
    monkeypatch.setattr(terminal_service.sys, "platform", "win32")
    monkeypatch.setattr(terminal_service.shutil, "which", lambda _name: None)
    popen = Mock()
    monkeypatch.setattr(terminal_service.subprocess, "Popen", popen)
    assert not terminal_service.open_terminal(preferences=TerminalSettings("bash"))[0]
    assert not terminal_service.open_terminal(shell="cmd & example")[0]
    popen.assert_not_called()


def test_packaged_terminal_launch_restores_dll_path_after_failure(monkeypatch):
    monkeypatch.setattr(terminal_service.sys, "frozen", True, raising=False)
    monkeypatch.setattr(terminal_service.sys, "_MEIPASS", "C:/LogOpen/_internal", raising=False)
    monkeypatch.setattr(terminal_service.shutil, "which", lambda name: "C:/Windows/" + name)
    directory = Mock(return_value=1)
    monkeypatch.setattr(terminal_service.ctypes.windll.kernel32, "SetDllDirectoryW", directory)
    monkeypatch.setattr(terminal_service.subprocess, "Popen", Mock(side_effect=OSError("Cannot launch")))
    assert not terminal_service.open_terminal(preferences=TerminalSettings())[0]
    assert directory.call_args_list == [call(None), call("C:/LogOpen/_internal")]
