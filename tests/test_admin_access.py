"""Exercise elevation without launching privileged processes or showing real UAC."""

from pathlib import Path
from threading import Event
from unittest.mock import Mock
import os

import pytest
import pywintypes

import admin_access
from tests.test_guided_ui import desktop
from tests.test_windows_events import native, xml
from ui.settings_dialog import SettingsDialog
from errors import EventLogAccessError
import windows_events


@pytest.mark.parametrize("elevated", [0, 1])
def test_status_uses_current_process_token_and_closes_handle(monkeypatch, elevated):
    import win32security
    token = Mock()
    monkeypatch.setattr(win32security, "OpenProcessToken", Mock(return_value=token))
    read = Mock(return_value=elevated)
    monkeypatch.setattr(win32security, "GetTokenInformation", read)
    assert admin_access.elevation_status() is bool(elevated)
    read.assert_called_once_with(token, win32security.TokenElevation)
    token.Close.assert_called_once_with()


def test_failed_status_probe_is_unknown_not_administrator(monkeypatch):
    import win32security
    monkeypatch.setattr(win32security, "OpenProcessToken", Mock(side_effect=pywintypes.error(5, "OpenProcessToken", "denied")))
    assert admin_access.elevation_status() is None
    assert admin_access.access_label(None) == "Access status unavailable"


@pytest.mark.parametrize("frozen", [False, True])
def test_admin_restart_targets_only_own_app_and_restores_environment(monkeypatch, tmp_path, frozen):
    executable = tmp_path / "App with spaces" / ("LogOpen.exe" if frozen else "python.exe")
    executable.parent.mkdir()
    executable.touch()
    if not frozen:
        executable.with_name("pythonw.exe").touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(admin_access.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(admin_access.sys, "executable", str(executable))
    monkeypatch.setenv("PYINSTALLER_RESET_ENVIRONMENT", "old-value")
    calls = []
    monkeypatch.setattr(admin_access, "_shell_launch", lambda *args: calls.append((args, os.environ.get("PYINSTALLER_RESET_ENVIRONMENT"))))
    assert admin_access.relaunch_as_administrator(123)[0]
    (target, arguments, directory, hwnd), marker = calls[0]
    assert target == str(executable if frozen else executable.with_name("pythonw.exe"))
    assert arguments == ([] if frozen else [str(Path(admin_access.__file__).resolve().parent / "desktop.py")])
    assert directory == str(tmp_path) and hwnd == 123
    assert marker == ("1" if frozen else "old-value")
    assert os.environ["PYINSTALLER_RESET_ENVIRONMENT"] == "old-value"


def test_shell_launch_uses_uac_verb_and_quotes_source_path(monkeypatch):
    import pythoncom
    import win32api
    from win32com.shell import shell
    execute = Mock(return_value={"hProcess": 123})
    initialize, uninitialize, close = Mock(), Mock(), Mock()
    monkeypatch.setattr(shell, "ShellExecuteEx", execute)
    monkeypatch.setattr(pythoncom, "CoInitialize", initialize)
    monkeypatch.setattr(pythoncom, "CoUninitialize", uninitialize)
    monkeypatch.setattr(win32api, "CloseHandle", close)
    admin_access._shell_launch(r"C:\App dir\pythonw.exe", [r"C:\App dir\desktop.py"], r"C:\App dir", 456)
    args = execute.call_args.kwargs
    assert args["lpVerb"] == "runas"
    assert args["lpParameters"] == '"C:\\App dir\\desktop.py"'
    assert args["lpFile"] == r"C:\App dir\pythonw.exe"
    assert args["hwnd"] == 456
    initialize.assert_called_once()
    uninitialize.assert_called_once()
    close.assert_called_once_with(123)


@pytest.mark.parametrize("code,word", [(1223, "cancelled"), (5, "Could not start")])
def test_uac_cancellation_and_denial_are_safe_results(monkeypatch, code, word):
    monkeypatch.setattr(admin_access.sys, "frozen", True, raising=False)
    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)
    monkeypatch.setattr(admin_access, "_shell_launch", Mock(side_effect=pywintypes.error(code, "ShellExecuteEx", "private details")))
    success, message = admin_access.relaunch_as_administrator()
    assert not success and word in message and "private details" not in message
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in os.environ


def test_restart_waits_for_monitor_and_cancellation_keeps_results(desktop, monkeypatch):
    window, app = desktop
    window._elevated = False
    item = {"incident_classification": "Example", "incident_priority": "Low", "incident_score": 1}
    window._show_scan_results([item])
    window.incident_table.setCurrentCell(0, 0)
    window._open_incident_detail()
    window.incident_detail.note_editor.setPlainText("Unsaved observation")
    window.monitor.active = True
    window.monitor.thread = object()
    window.monitor.token = Event()
    launch = Mock(return_value=(False, "Administrator request cancelled."))
    monkeypatch.setattr("ui.main_window.relaunch_as_administrator", launch)
    window._restart_as_administrator()
    assert window.monitor.token.is_set() and window.isVisible()
    launch.assert_not_called()
    assert window.history_store.issue_preferences(item)["note"] == "Unsaved observation"
    window.monitor.thread = None
    window.monitor._finished()
    app.processEvents()
    launch.assert_called_once()
    assert window.isVisible() and not window._close_pending
    assert window.incident_detail.incident is item


def test_successful_admin_restart_closes_old_window(desktop, monkeypatch):
    window, _ = desktop
    window._elevated = False
    launch = Mock(return_value=(True, "Started"))
    monkeypatch.setattr("ui.main_window.relaunch_as_administrator", launch)
    window.admin_action.trigger()
    launch.assert_called_once()
    assert not window.isVisible()


def test_elevated_app_never_requests_elevation_again(desktop, monkeypatch):
    window, _ = desktop
    window._elevated = True
    launch = Mock()
    monkeypatch.setattr("ui.main_window.relaunch_as_administrator", launch)
    window._restart_as_administrator()
    launch.assert_not_called()
    monkeypatch.setattr("ui.settings_dialog.elevation_status", lambda: True)
    dialog = SettingsDialog(window)
    try:
        assert dialog.access_status.text() == "Administrator access"
        assert not dialog.admin_restart_button.isEnabled()
        dialog._set_testing(True)
        dialog._set_testing(False)
        assert not dialog.admin_restart_button.isEnabled()
    finally:
        dialog.close()


def test_settings_saves_before_admin_signal_and_blocks_failed_save(desktop, monkeypatch):
    window, _ = desktop
    monkeypatch.setattr("ui.settings_dialog.elevation_status", lambda: False)
    dialog = SettingsDialog(window)
    requested = Mock()
    dialog.admin_restart_requested.connect(requested)
    save = Mock(return_value=False)
    monkeypatch.setattr(dialog, "_save_settings", save)
    try:
        dialog._restart_as_administrator()
        requested.assert_not_called()
        save.return_value = True
        dialog._restart_as_administrator()
        requested.assert_called_once()
    finally:
        dialog.close()


def test_permission_denied_has_actionable_guidance_and_preserves_partial_events(native):
    denied = pywintypes.error(5, "EvtQuery", "Access is denied.")
    native.EvtQuery.side_effect = denied
    with pytest.raises(EventLogAccessError, match="Restart as administrator"):
        windows_events.collect_events("Restricted", 10)
    native.EvtQuery.side_effect = None
    native.EvtNext.side_effect = [(Mock(),), denied]
    native.EvtRender.return_value = xml()
    result = windows_events.collect_events("Restricted", 10)
    assert len(result) == 1 and not result.complete and result.stop_reason == "access_denied"
    assert "If already elevated" in result.warnings[0]


def test_denied_channel_does_not_discard_readable_log_coverage(monkeypatch):
    import main
    from models.scan_options import ScanOptions
    from event_collection import CollectedEvents
    monkeypatch.setattr(main, "get_recent_events", Mock(side_effect=[CollectedEvents(), EventLogAccessError("Restart as administrator")]))
    monkeypatch.setattr(main, "export_incidents_to_json", Mock())
    result = main.scan_system(ScanOptions(log_types=("System", "Restricted"), automatic_ai=False))
    assert not result.complete
    assert result.coverage["System"]["complete"]
    assert result.coverage["Restricted"]["stop_reason"] == "access_denied"
    assert "Restart as administrator" in result.warnings[0]
