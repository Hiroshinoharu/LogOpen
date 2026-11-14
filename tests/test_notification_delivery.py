"""Native diagnostics remain read-only and never claim that a banner appeared."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import notification_delivery as delivery


def shell_with_state(value, result=0):
    def query(address):
        address._obj.value = value
        return result
    return SimpleNamespace(SHQueryUserNotificationState=Mock(side_effect=query))


@pytest.mark.parametrize("value,expected", [(1, "suppressed"), (2, "suppressed"), (3, "suppressed"),
                                           (4, "suppressed"), (5, "ready"), (6, "suppressed"),
                                           (7, "unknown"), (80, "unknown")])
def test_native_session_state_is_reported_without_sending_or_overriding(monkeypatch, value, expected):
    shell = shell_with_state(value)
    monkeypatch.setattr(delivery.sys, "platform", "win32")
    monkeypatch.setattr(delivery, "_shell32", lambda: shell)
    status = delivery.notification_environment()
    assert status.state == expected
    assert status.native_state == value
    assert status.message
    assert list(vars(shell)) == ["SHQueryUserNotificationState"]
    shell.SHQueryUserNotificationState.assert_called_once()
    if value == 5:
        assert "still depends" in status.message
        assert "Do not disturb" in status.message


@pytest.mark.parametrize("failure", ["hresult", "exception"])
def test_unreadable_windows_state_is_unknown_and_not_ready(monkeypatch, failure):
    shell = shell_with_state(5, -2147467259)
    if failure == "exception":
        shell.SHQueryUserNotificationState.side_effect = OSError("private Windows error")
    monkeypatch.setattr(delivery.sys, "platform", "win32")
    monkeypatch.setattr(delivery, "_shell32", lambda: shell)
    status = delivery.notification_environment()
    assert status.state == "unknown" and status.native_state is None
    assert "private" not in status.message


def test_non_windows_diagnostics_and_identity_do_not_load_native_libraries(monkeypatch):
    monkeypatch.setattr(delivery.sys, "platform", "linux")
    native = Mock(side_effect=AssertionError("Must not load Windows libraries"))
    monkeypatch.setattr(delivery, "_shell32", native)
    assert delivery.notification_environment().state == "unsupported"
    assert not delivery.initialize_app_identity()
    native.assert_not_called()


@pytest.mark.parametrize("result,expected", [(0, True), (-2147467259, False)])
def test_process_identity_uses_the_fixed_installer_identity(monkeypatch, result, expected):
    function = Mock(return_value=result)
    monkeypatch.setattr(delivery.sys, "platform", "win32")
    monkeypatch.setattr(delivery, "_shell32", lambda: SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=function))
    assert delivery.initialize_app_identity() is expected
    function.assert_called_once_with(delivery.APP_ID)
    installer = (Path(__file__).parents[1] / "installer" / "LogOpen.iss").read_text()
    shortcuts = [line for line in installer.splitlines() if 'Filename: "{app}\\LogOpen.exe"' in line and line.startswith("Name:")]
    assert len(shortcuts) == 2
    assert all(f'AppUserModelID: "{delivery.APP_ID}"' in line for line in shortcuts)


def test_identity_failure_cannot_prevent_application_startup(monkeypatch):
    monkeypatch.setattr(delivery.sys, "platform", "win32")
    monkeypatch.setattr(delivery, "_shell32", Mock(side_effect=OSError("Unavailable")))
    assert not delivery.initialize_app_identity()


def test_shortcut_identity_only_attempts_a_real_lnk(monkeypatch, tmp_path):
    monkeypatch.setattr(delivery.sys, "platform", "win32")
    assert not delivery.apply_shortcut_identity(tmp_path / "missing.lnk")
    other = tmp_path / "report.txt"
    other.write_text("not a shortcut")
    assert not delivery.apply_shortcut_identity(other)
