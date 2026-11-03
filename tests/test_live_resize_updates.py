"""Regression coverage for visible live results and explicit update installation."""

from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QFileDialog

from tests.test_guided_ui import desktop
from tests.test_productivity import occurrence
from tests.test_workspace import wait_until
from tests.test_updates import asset
from models.scan_result import ScanResult
from ui.update_dialog import UpdateDialog
from update_service import Release, UpdateError
import update_service


@pytest.mark.parametrize("size", ["normal", "large", "larger"])
def test_live_table_survives_resize_with_warnings_comparison_and_preview(desktop, size):
    window, app = desktop
    window.motion.set_enabled(True)
    window._set_appearance(size=size, save=False)
    result = ScanResult([occurrence(datetime.now(timezone.utc), n) for n in range(8)])
    result.complete = False
    result.warnings = ["Security: access denied", "Application: record cap reached"]
    result.comparison = dict(comparable=True, new_count=1, persistent_count=1)
    for width, height in [(1280, 720), (1920, 1080), (1536, 864), (1366, 768), (1920, 1080), (800, 620)]:
        window.resize(width, height)
        window._monitor_completed(result, [])
        for _ in range(3):
            app.processEvents()
        table = window.incident_table
        bottom = table.mapTo(window.results_panel, QPoint(0, table.height())).y()
        assert table.rowCount() == 8
        assert window.results_stack.currentWidget() is table and table.isVisible()
        assert table.viewport().height() >= 75, (size, width, height)
        assert bottom <= window.results_panel.height(), (size, width, height, bottom, window.results_panel.height())
        assert table.mapTo(window, QPoint(0, table.height())).y() < window.height()
    window.showFullScreen()
    app.processEvents()
    window._monitor_completed(result, [])
    app.processEvents()
    window.showNormal()
    window.resize(1280, 720)
    app.processEvents()
    assert not window.motion.page_effect.isEnabled()
    assert window.incident_table.isVisible()


def test_download_install_handoff_waits_for_thread_and_needs_explicit_action(desktop, monkeypatch, tmp_path):
    window, app = desktop
    package = asset()
    release = Release("1.2.3", "New release", (package,))
    path = tmp_path / package.name
    monkeypatch.setattr("ui.update_dialog.application_version", lambda: "1.0.0")
    monkeypatch.setattr("ui.update_dialog.installer_cache_path", lambda item: path)
    def download(item, destination, **kwargs):
        Path(destination).write_bytes(b"synthetic installer")
        return Path(destination)
    monkeypatch.setattr("ui.update_dialog.download_release", download)
    picker = Mock(side_effect=AssertionError("Streamlined installation must not open a file picker"))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", picker)
    dialog = UpdateDialog(window, auto_check=False)
    received = []
    dialog.install_requested.connect(lambda *args: received.append((args, dialog._thread)))
    try:
        dialog._show_result(release)
        dialog._set_busy(False)
        assert received == []
        dialog.install_button.click()
        wait_until(app, lambda: dialog._thread is None)
        assert received == [((path, package), None)]
        picker.assert_not_called()
    finally:
        dialog.close()


def test_cancel_prevents_automatic_installer_handoff(desktop, monkeypatch, tmp_path):
    window, app = desktop
    package = asset()
    started = Event()
    monkeypatch.setattr("ui.update_dialog.application_version", lambda: "1.0.0")
    monkeypatch.setattr("ui.update_dialog.installer_cache_path", lambda item: tmp_path / item.name)
    def download(item, destination, *, cancel_event, progress):
        started.set()
        assert cancel_event.wait(3)
        # Model a completion racing with Cancel: never hand it to the installer.
        return Path(destination)
    monkeypatch.setattr("ui.update_dialog.download_release", download)
    dialog = UpdateDialog(window, auto_check=False)
    requested = Mock()
    dialog.install_requested.connect(requested)
    try:
        dialog._show_result(Release("1.2.3", "", (package,)))
        dialog.download_and_install()
        wait_until(app, started.is_set)
        dialog.cancel_download()
        wait_until(app, lambda: dialog._thread is None)
        requested.assert_not_called()
    finally:
        dialog.close()


def test_installer_is_reverified_and_launches_without_shell(monkeypatch, tmp_path):
    package = asset()
    path = tmp_path / "update with spaces.exe"
    path.write_bytes(b"synthetic installer")
    launch = Mock()
    monkeypatch.setattr(update_service.sys, "platform", "win32")
    monkeypatch.setattr(update_service.subprocess, "Popen", launch)
    update_service.launch_installer(path, package)
    assert launch.call_args.args == ([str(path.resolve())],)
    assert launch.call_args.kwargs["shell"] is False
    launch.reset_mock()
    path.write_bytes(b"modified installer!")
    with pytest.raises(UpdateError):
        update_service.launch_installer(path, package)
    launch.assert_not_called()
    path.unlink()
    with pytest.raises(UpdateError):
        update_service.launch_installer(path, package)
    with pytest.raises(UpdateError):
        update_service.installer_cache_path(asset(name="../setup.exe"))


def test_install_waits_for_monitor_shutdown_and_failure_keeps_app_open(desktop, monkeypatch):
    window, app = desktop
    window.monitor.active = True
    window.monitor.thread = object()
    window.monitor.token = Event()
    launch = Mock(side_effect=UpdateError("Installer changed; download again."))
    monkeypatch.setattr("ui.main_window.launch_installer", launch)
    window._install_update(Path("test.exe"), asset())
    app.processEvents()
    assert window.monitor.token.is_set()
    assert window.isVisible()
    launch.assert_not_called()
    window.monitor.thread = None
    window.monitor._finished()
    app.processEvents()
    launch.assert_called_once()
    assert window.isVisible() and not window._close_pending
    assert "Installer changed" in window.status_label.text()


def test_successful_installer_handoff_closes_app_without_restarting_old_version(desktop, monkeypatch):
    window, app = desktop
    launch, restart = Mock(), Mock()
    monkeypatch.setattr("ui.main_window.launch_installer", launch)
    monkeypatch.setattr("ui.main_window.relaunch_app", restart)
    package = asset()
    window._install_update(Path("test.exe"), package)
    app.processEvents()
    launch.assert_called_once_with(Path("test.exe"), package)
    restart.assert_not_called()
    assert not window.isVisible()


def test_zip_only_release_cannot_start_installer(desktop, monkeypatch):
    window, _ = desktop
    monkeypatch.setattr("ui.update_dialog.application_version", lambda: "1.0.0")
    dialog = UpdateDialog(window, auto_check=False)
    requested = Mock()
    dialog.install_requested.connect(requested)
    try:
        dialog._show_result(Release("1.2.3", "Portable only", (asset(name="LogOpen-1.2.3-Windows-x64.zip"),)))
        dialog._set_busy(False)
        assert not dialog.install_button.isEnabled()
        assert dialog.download_button.isEnabled()
        dialog.download_and_install()
        requested.assert_not_called()
    finally:
        dialog.close()
