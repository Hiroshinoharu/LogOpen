"""Check relaunch commands without starting a real second application."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from ui import app_restart


@pytest.mark.parametrize("frozen", [False, True])
def test_restart_targets_desktop_or_installed_executable(monkeypatch, tmp_path, frozen):
    """Relaunch works away from the source directory and resets bundled resources."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_restart.sys, "frozen", frozen, raising=False)
    executable = str(tmp_path / "path with spaces" / ("LogOpen.exe" if frozen else "python.exe"))
    monkeypatch.setattr(app_restart.sys, "executable", executable)
    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)
    popen = Mock()
    monkeypatch.setattr(app_restart.subprocess, "Popen", popen)
    assert app_restart.relaunch_app()
    command = popen.call_args.args[0]
    assert command[0] == executable
    if frozen:
        assert len(command) == 1
        assert popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    else:
        assert Path(command[1]) == Path(app_restart.__file__).resolve().parents[1] / "desktop.py"
        assert "PYINSTALLER_RESET_ENVIRONMENT" not in popen.call_args.kwargs["env"]
    assert popen.call_args.kwargs["creationflags"] == (
        app_restart.subprocess.CREATE_NO_WINDOW if app_restart.sys.platform == "win32" else 0
    )


def test_failed_process_start_returns_false(monkeypatch):
    """Permission or missing-executable errors let the caller keep the app open."""
    monkeypatch.setattr(app_restart.subprocess, "Popen", Mock(side_effect=OSError("Cannot launch")))
    assert not app_restart.relaunch_app()
