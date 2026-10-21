"""Thread lifecycle, terminal navigation, and icon coverage for desktop actions."""

import os
from pathlib import Path
import threading
import time
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication, QFileDialog, QPushButton, QTabWidget

from settings.terminal_settings import TerminalPreferences, TerminalSettings
from ui.incident_detail import DiagnosticStepWidget
from ui.main_window import MainWindow
from ui.settings_dialog import SettingsDialog
from ui.update_dialog import UpdateDialog
from update_service import DownloadCancelled, Release, ReleaseAsset


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def wait_until(app, condition):
    deadline = time.monotonic() + 4
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition(), "Qt operation did not finish"


@pytest.fixture
def release():
    return Release("1.2.3", "Release notes <literal>", (
        ReleaseAsset("LogOpen-1.2.3-Setup-x64.exe", "https://github.com/example", 25, "a" * 64),
    ))


def test_update_check_and_download_run_off_ui_thread_without_installing(app, monkeypatch, tmp_path, release):
    monkeypatch.setattr("ui.update_dialog.application_version", lambda: "1.0.0")
    threads = []

    def check():
        threads.append(QThread.currentThread())
        return release

    def download(_asset, destination, *, cancel_event, progress):
        threads.append(QThread.currentThread())
        assert not cancel_event.is_set()
        Path(destination).write_bytes(b"synthetic update")
        progress(25, 25)
        return Path(destination)

    monkeypatch.setattr("ui.update_dialog.latest_release", check)
    monkeypatch.setattr("ui.update_dialog.download_release", download)
    destination = tmp_path / release.assets[0].name
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(destination), ""))
    popen = Mock()
    monkeypatch.setattr("subprocess.Popen", popen)
    dialog = UpdateDialog(auto_check=False)
    dialog.show()
    try:
        dialog.check_button.click()
        wait_until(app, lambda: dialog._thread is None)
        assert dialog.download_button.isEnabled()
        assert dialog.notes.toPlainText() == release.notes
        dialog.download_button.click()
        wait_until(app, lambda: dialog._thread is None)
        assert destination.read_bytes() == b"synthetic update"
        assert dialog.progress.value() == 100
        assert not dialog.folder_button.isHidden()
        assert "verified" in dialog.status.text()
        assert all(thread != app.thread() for thread in threads)
        popen.assert_not_called()
    finally:
        wait_until(app, lambda: dialog._thread is None)
        dialog.close()


@pytest.mark.parametrize("current", ["1.2.3", "2.0.0"])
def test_update_ui_never_offers_a_downgrade(app, monkeypatch, release, current):
    monkeypatch.setattr("ui.update_dialog.application_version", lambda: current)
    monkeypatch.setattr("ui.update_dialog.latest_release", lambda: release)
    dialog = UpdateDialog(auto_check=False)
    try:
        dialog.check_updates()
        wait_until(app, lambda: dialog._thread is None)
        assert not dialog.download_button.isEnabled()
        assert "up to date" in dialog.status.text()
    finally:
        dialog.close()


def test_closing_update_download_cancels_and_waits_for_thread(app, monkeypatch, tmp_path, release):
    monkeypatch.setattr("ui.update_dialog.application_version", lambda: "1.0.0")
    started = threading.Event()

    def download(_asset, _destination, *, cancel_event, progress):
        started.set()
        assert cancel_event.wait(3)
        raise DownloadCancelled()

    monkeypatch.setattr("ui.update_dialog.download_release", download)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(tmp_path / "update.exe"), ""))
    dialog = UpdateDialog(auto_check=False)
    dialog.show()
    try:
        dialog._show_result(release)
        dialog.download_update()
        wait_until(app, started.is_set)
        dialog.close()
        assert dialog._close_pending
        wait_until(app, lambda: dialog._thread is None and not dialog.isVisible())
    finally:
        if dialog._thread is not None:
            dialog._token.set()
            wait_until(app, lambda: dialog._thread is None)
        dialog.close()


def test_main_window_close_waits_for_active_update_check(app, monkeypatch, release):
    monkeypatch.setattr("ui.update_dialog.application_version", lambda: "1.0.0")
    started, stop = threading.Event(), threading.Event()

    def check():
        started.set()
        assert stop.wait(3)
        return release

    monkeypatch.setattr("ui.update_dialog.latest_release", check)
    window = MainWindow()
    window.show()
    try:
        window.updates_button.click()
        wait_until(app, started.is_set)
        window.close()
        assert window.isVisible()
        assert window._updates_dialog._close_pending
        stop.set()
        wait_until(app, lambda: window._updates_dialog is None and not window.isVisible())
    finally:
        stop.set()
        wait_until(app, lambda: window._updates_dialog is None or window._updates_dialog._thread is None)
        window.close()


def test_terminal_controls_save_and_open_unsaved_selection(app, monkeypatch):
    opened = Mock(return_value=(True, "Terminal opened."))
    monkeypatch.setattr("ui.settings_dialog.open_terminal", opened)
    dialog = SettingsDialog()
    try:
        dialog.tabs.setCurrentIndex(1)
        dialog.shell_combo.setCurrentIndex(dialog.shell_combo.findData("cmd"))
        dialog.terminal_host_combo.setCurrentIndex(dialog.terminal_host_combo.findData("windows_terminal"))
        dialog.open_terminal_button.click()
        opened.assert_called_once_with(preferences=TerminalSettings("cmd", "windows_terminal"))
        dialog.save_settings_button.click()
        assert TerminalPreferences().load() == TerminalSettings("cmd", "windows_terminal")
    finally:
        dialog.close()


def test_old_command_opens_its_recorded_shell_without_sending_command(app, monkeypatch):
    TerminalPreferences().save(TerminalSettings("cmd"))
    opened = Mock(return_value=(True, "PowerShell opened."))
    monkeypatch.setattr("ui.incident_detail.open_terminal", opened)
    card = DiagnosticStepWidget(1, dict(description="Read services", command="Get-Service", shell="powershell", risk_level="safe"))
    try:
        card.open_terminal_button.click()
        opened.assert_called_once_with(shell="powershell")
    finally:
        card.close()


def test_all_action_buttons_menus_and_tabs_have_icons(app, monkeypatch):
    from ui.channel_browser import ChannelBrowser
    from ui.guidance import QuickStartDialog
    from ui.history_dialog import ComparisonDialog, HistoryDialog
    from ui.monitoring import MonitorOptionsDialog
    from ui.rule_editor import RuleEditorDialog
    from ui.scan_controls import FilterDialog, ScanOptionsDialog
    from ui.timeline import TimelineDialog
    monkeypatch.setattr(ChannelBrowser, "_start", Mock())
    window = MainWindow()
    dialogs = [SettingsDialog(window), UpdateDialog(window, auto_check=False), QuickStartDialog(window),
               ScanOptionsDialog(window.scan_options, window), FilterDialog({}, window),
               HistoryDialog([], window), ComparisonDialog({}, window), MonitorOptionsDialog(window),
               RuleEditorDialog("[]", window), TimelineDialog([], window), ChannelBrowser((), window)]
    try:
        missing = [button.text() for button in window.findChildren(QPushButton) if button.icon().isNull()]
        assert not missing
        for tabs in window.findChildren(QTabWidget):
            assert all(not tabs.tabIcon(index).isNull() for index in range(tabs.count()))
        for menu in window.menuBar().actions():
            # Qt replaces top-level menu text when an icon is assigned there.
            assert menu.text() and menu.icon().isNull()
            assert all(not action.icon().isNull() for action in menu.menu().actions() if not action.isSeparator())
    finally:
        for dialog in dialogs:
            dialog.close()
        window.close()
