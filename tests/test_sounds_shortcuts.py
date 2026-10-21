"""Mute persistence, nonblocking audio, and desktop links without native side effects."""

from array import array
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

import desktop_shortcut
from ui.main_window import MainWindow
from ui.settings_dialog import SettingsDialog
from ui.sounds import SOUNDS, SoundEffects, SoundPreferences


@pytest.fixture
def audio_backend(monkeypatch):
    backend = SimpleNamespace(PlaySound=Mock(), SND_FILENAME=1, SND_ASYNC=2, SND_NODEFAULT=4)
    monkeypatch.setitem(sys.modules, "winsound", backend)
    monkeypatch.setattr("ui.sounds.sys.platform", "win32")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("LOGOPEN_DISABLE_SOUNDS", raising=False)
    return backend


def test_sound_cues_are_short_quiet_pcm_files():
    for path in SOUNDS.values():
        with wave.open(str(path), "rb") as source:
            assert (source.getnchannels(), source.getsampwidth(), source.getframerate()) == (1, 2, 44100)
            assert 0.1 <= source.getnframes() / source.getframerate() <= 0.4
            samples = array("h", source.readframes(source.getnframes()))
            assert 0 < max(abs(sample) for sample in samples) < 32767 * 0.11


def test_playback_is_async_muting_is_immediate_and_duplicates_are_limited(audio_backend):
    preferences = SoundPreferences()
    sounds = SoundEffects(preferences)
    assert sounds.play("start")
    assert not sounds.play("start")
    assert sounds.play("success")  # A fast scan still gets a completion cue.
    assert audio_backend.PlaySound.call_count == 2
    assert audio_backend.PlaySound.call_args.args == (str(SOUNDS["success"]), 7)
    preferences.save(False)
    assert not sounds.play("attention")
    assert not SoundPreferences().enabled()
    assert audio_backend.PlaySound.call_count == 2


@pytest.mark.parametrize("suppression", ["offscreen", "environment"])
def test_automated_runs_never_play_sound(audio_backend, monkeypatch, suppression):
    if suppression == "offscreen":
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    else:
        monkeypatch.setenv("LOGOPEN_DISABLE_SOUNDS", "1")
    assert not SoundEffects().play("success")
    audio_backend.PlaySound.assert_not_called()


def test_unavailable_audio_is_harmless(audio_backend):
    audio_backend.PlaySound.side_effect = RuntimeError("No audio device")
    assert not SoundEffects().play("success")
    assert not SoundEffects().play("unknown")


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_app_sound_toggle_saves_immediately_and_updates_quick_mute(app):
    window = MainWindow()
    try:
        window._open_settings()
        dialog = window._settings_dialog
        dialog.tabs.setCurrentIndex(2)
        dialog.sound_checkbox.setChecked(False)
        assert not SoundPreferences().enabled()
        assert not window.sound_action.isChecked()
        assert not dialog.test_sound_button.isEnabled()
        dialog.close()
        app.processEvents()
        window.sound_action.trigger()
        assert SoundPreferences().enabled()
        window._open_settings()
        assert window._settings_dialog.sound_checkbox.isChecked()
    finally:
        if window._settings_dialog is not None:
            window._settings_dialog.close()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_opening_saved_results_does_not_play_a_scan_completion(app):
    window = MainWindow()
    window.sounds.play = Mock()
    try:
        window._show_scan_results([])
        window.sounds.play.assert_not_called()
        window._scan_thread = object()  # A worker result is delivered before cleanup.
        window._show_scan_results([])
        window.sounds.play.assert_called_once_with("success")
    finally:
        window._scan_thread = None
        window.close()
        window.deleteLater()
        app.processEvents()


def test_shortcut_uses_redirected_desktop_and_packaged_icon(monkeypatch, tmp_path):
    desktop = tmp_path / "OneDrive Desktop"
    desktop.mkdir()
    executable = tmp_path / "LogOpen.exe"
    executable.write_bytes(b"test executable")
    monkeypatch.setattr(desktop_shortcut.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_shortcut.sys, "executable", str(executable))
    shell = Mock()
    shell.SpecialFolders.return_value = str(desktop)
    shortcut = shell.CreateShortcut.return_value
    result = desktop_shortcut.create_desktop_shortcut(shell=shell)
    assert result == desktop / "LogOpen.lnk"
    assert shortcut.TargetPath == str(executable)
    assert shortcut.Arguments == ""
    assert shortcut.IconLocation == f"{executable},0"
    shortcut.Save.assert_called_once_with()


def test_source_shortcut_uses_windowless_python_and_quotes_entry_point(monkeypatch, tmp_path):
    executable = tmp_path / "Python with spaces" / "python.exe"
    executable.parent.mkdir()
    executable.touch()
    windowless = executable.with_name("pythonw.exe")
    windowless.touch()
    monkeypatch.setattr(desktop_shortcut.sys, "frozen", False, raising=False)
    monkeypatch.setattr(desktop_shortcut.sys, "executable", str(executable))
    target, arguments, directory, icon = desktop_shortcut.shortcut_details()
    assert target == str(windowless)
    assert str(Path(directory) / "desktop.py") in arguments
    assert "logopen.ico" in icon


def test_shortcut_error_stays_safe_and_app_button_reports_result(app, monkeypatch):
    shell = Mock()
    shell.SpecialFolders.side_effect = RuntimeError("private Windows details")
    with pytest.raises(desktop_shortcut.ShortcutError, match="writable") as error:
        desktop_shortcut.create_desktop_shortcut(shell=shell)
    assert "private Windows details" not in str(error.value)
    create = Mock(return_value=Path("LogOpen.lnk"))
    monkeypatch.setattr("ui.settings_dialog.create_desktop_shortcut", create)
    dialog = SettingsDialog()
    try:
        dialog.shortcut_button.click()
        create.assert_called_once_with()
        assert "created on your desktop" in dialog.message_label.text()
    finally:
        dialog.close()


def test_desktop_shortcut_is_selected_by_default_in_installer():
    path = Path(__file__).resolve().parents[1] / "installer" / "LogOpen.iss"
    task = next(line for line in path.read_text().splitlines() if line.startswith('Name: "desktopicon";'))
    assert "unchecked" not in task
