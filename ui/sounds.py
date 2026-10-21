"""Short, asynchronous Windows sound cues with a saved mute preference."""

import os
from pathlib import Path
import sys
import time

from PyQt6.QtCore import QSettings

from settings import app_settings

SOUNDS = {name: Path(__file__).resolve().parents[1] / "assets" / "sounds" / f"{name}.wav"
          for name in ("start", "success", "attention")}


class SoundPreferences:
    """Store only the enabled flag; sounds use deliberately quiet source audio."""

    def __init__(self, storage=None):
        self._storage = storage if storage is not None else app_settings._create_qsettings()

    def enabled(self):
        try:
            self._storage.sync()
            if self._storage.status() != QSettings.Status.NoError:
                return False
            value = self._storage.value("ui/sounds", True)
            return value is True or (type(value) is int and value == 1) or (
                isinstance(value, str) and value.strip().lower() in ("true", "1")
            )
        except Exception:
            return False

    def save(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Choose whether to play sound effects.")
        try:
            self._storage.setValue("ui/sounds", enabled)
            self._storage.sync()
            if self._storage.status() != QSettings.Status.NoError:
                raise app_settings.SettingsError("Could not save sound preferences.")
        except Exception:
            raise app_settings.SettingsError("Could not save sound preferences.") from None


class SoundEffects:
    """Play without blocking the GUI or repeating the same cue in quick succession."""

    def __init__(self, preferences=None):
        self.preferences = preferences if preferences is not None else SoundPreferences()
        self._last_played = 0.0
        self._last_cue = None

    def play(self, name):
        if (sys.platform != "win32" or os.environ.get("QT_QPA_PLATFORM") == "offscreen"
                or os.environ.get("LOGOPEN_DISABLE_SOUNDS") == "1" or not self.preferences.enabled()):
            return False
        path = SOUNDS.get(name)
        if path is None or not path.is_file():
            return False
        now = time.monotonic()
        if name == self._last_cue and now - self._last_played < 0.35:
            return False
        try:
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except (ImportError, OSError, RuntimeError):
            return False
        self._last_played = now
        self._last_cue = name
        return True
