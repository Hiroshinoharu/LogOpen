"""Persist terminal choices and supply the shell policy for new suggestions."""

from dataclasses import dataclass

from PyQt6.QtCore import QSettings

import config
from settings import app_settings

SHELL_LABELS = {"powershell": "PowerShell", "cmd": "Command Prompt", "bash": "Bash (WSL)"}
HOST_LABELS = {"console": "Standard terminal window", "windows_terminal": "Windows Terminal"}


@dataclass(frozen=True)
class TerminalSettings:
    shell: str = "powershell"
    host: str = "console"


class TerminalPreferences:
    """Keep the selected shell and terminal host separate from AI credentials."""

    def __init__(self, storage=None):
        self._storage = storage if storage is not None else app_settings._create_qsettings()

    def _value(self, key, default=None):
        self._storage.sync()
        if self._storage.status() != QSettings.Status.NoError:
            return default
        return self._storage.value("terminal/" + key, default)

    def saved_shell(self):
        try:
            value = self._value("shell")
            return value if isinstance(value, str) and value in SHELL_LABELS else None
        except Exception:
            return None

    def load(self):
        default = config.PREFERRED_DIAGNOSTIC_SHELL
        if not isinstance(default, str) or default not in SHELL_LABELS:
            default = "powershell"
        try:
            host = self._value("host", "console")
            if not isinstance(host, str) or host not in HOST_LABELS:
                host = "console"
        except Exception:
            host = "console"
        return TerminalSettings(self.saved_shell() or default, host)

    def save(self, preferences):
        if preferences.shell not in SHELL_LABELS or preferences.host not in HOST_LABELS:
            raise ValueError("Choose a supported terminal and command format.")
        try:
            self._storage.setValue("terminal/shell", preferences.shell)
            self._storage.setValue("terminal/host", preferences.host)
            self._storage.sync()
            if self._storage.status() != QSettings.Status.NoError:
                raise app_settings.SettingsError("Could not save terminal preferences.")
        except Exception:
            raise app_settings.SettingsError("Could not save terminal preferences.") from None


def diagnostic_shell_policy():
    """An explicit user choice overrides config defaults for newly generated commands."""
    selected = TerminalPreferences().saved_shell()
    if selected:
        return (selected,), selected
    return config.ALLOWED_DIAGNOSTIC_SHELLS, config.PREFERRED_DIAGNOSTIC_SHELL
