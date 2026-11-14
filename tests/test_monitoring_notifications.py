"""Live-monitoring feedback and notification suppression remain honest and testable."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QItemSelectionModel, QPoint
from PyQt6.QtWidgets import QApplication

import notification_preferences
import ui.assistance as assistance
from models.scan_result import ScanResult
from monitoring import AlertTracker
from notification_delivery import NotificationEnvironment
from notification_preferences import NotificationPreferences
from tests.test_monitoring_timeline import incident
from tests.test_workspace import wait_until
from ui.main_window import MainWindow


@pytest.fixture
def desktop(monkeypatch):
    """Never query the host's notification policy or show a native banner in tests."""
    monkeypatch.setattr(assistance, "notification_environment", lambda: NotificationEnvironment(
        "unknown", "Windows notification readiness is unverified. Use Test notification.",
    ), raising=False)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.motion.set_enabled(False)
    tray = Mock()
    tray.isVisible.return_value = True
    tray.isSystemTrayAvailable.return_value = True
    tray.supportsMessages.return_value = True
    monkeypatch.setattr(window, "monitor_tray", tray)
    monkeypatch.setattr(window.sounds, "play", Mock())
    yield window, app
    window.monitor.stop()
    wait_until(app, lambda: window.monitor.thread is None)
    window.close()
    window.deleteLater()
    app.processEvents()


def test_live_monitoring_explains_active_overnight_quiet_hours(desktop, monkeypatch):
    window, app = desktop

    class LocalClock(datetime):
        @classmethod
        def now(cls, tz=None):
            stamp = cls(2026, 11, 14, 2, 30)
            return stamp.astimezone(tz) if tz is not None else stamp

    monkeypatch.setattr(notification_preferences, "datetime", LocalClock)
    monkeypatch.setattr(assistance, "datetime", LocalClock, raising=False)
    window._monitor_notifications = True
    window.notification_preferences = NotificationPreferences(quiet=True)
    window.monitor.active = True
    window.monitor.tracker = AlertTracker()
    window.resize(1280, 720)
    window.show()
    window._refresh_monitor_presentation()
    app.processEvents()
    status = window._notification_status_text()
    assert "quiet" in status.casefold()
    assert "08:00" in status
    assert "08:00" in window.monitoring_status.notification_label.text()
    assert window.monitoring_status.notification_label.isVisible()
    assert "monitor" in window.monitoring_status.title_label.text().casefold()


def test_initial_monitor_poll_is_clearly_labeled_as_a_silent_baseline(desktop):
    window, app = desktop
    window.monitor.active = True
    window.monitor.tracker = AlertTracker()
    window.monitor.thread = object()
    try:
        window.resize(1280, 720)
        window.show()
        window._refresh_monitor_presentation()
        app.processEvents()
        text = " ".join((window.monitoring_status.title_label.text(),
                         window.monitoring_status.detail_label.text())).casefold()
        assert "baseline" in text
        assert "silent" in text or "no alert" in text or "no notification" in text
        title = window.monitoring_status.title_label.text().casefold()
        assert "baseline" in title or ("first scan" in title and "silent" in title)
    finally:
        window.monitor.thread = None


def test_test_notification_bypasses_app_filters_without_saving_preferences(desktop, monkeypatch):
    window, _app = desktop
    monkeypatch.setattr(assistance, "notification_environment", lambda: NotificationEnvironment(
        "ready", "Windows permits a notification request; display is not confirmed.", 5,
    ))
    window.notification_preferences = NotificationPreferences(
        priorities=(), quiet=True, quiet_start=0, quiet_end=0, batch_seconds=600,
    )
    window._monitor_notifications = False
    before = window.notification_preferences
    saved_preference = window._ui_settings.value("monitor/desktop_notifications", None)
    assert window._test_notification()
    window.monitor_tray.showMessage.assert_called_once()
    assert window.notification_preferences == before
    assert window._monitor_notifications is False
    assert window._ui_settings.value("monitor/desktop_notifications", None) == saved_preference
    feedback = window._notification_feedback.casefold()
    assert "requested" in feedback
    assert "not confirmed" in feedback


def test_unknown_windows_readiness_never_claims_a_confirmed_banner(desktop):
    window, _app = desktop
    assert window._test_notification()
    window.monitor_tray.showMessage.assert_called_once()
    feedback = window._notification_feedback.casefold()
    assert "requested" in feedback
    assert "not confirmed" in feedback
    assert "delivered" not in feedback


def test_test_notification_respects_windows_suppression(desktop, monkeypatch):
    window, _app = desktop
    monkeypatch.setattr(assistance, "notification_environment", lambda: NotificationEnvironment(
        "suppressed", "Windows presentation mode is suppressing pop-up notifications.", 4,
    ))
    assert not window._test_notification()
    window.monitor_tray.showMessage.assert_not_called()
    assert "windows" in window._notification_feedback.casefold()
    assert "suppress" in window._notification_feedback.casefold()


@pytest.mark.parametrize("size", ["normal", "larger"])
def test_monitoring_and_quiet_status_remain_visible_with_results_and_details(desktop, size):
    window, app = desktop
    result = ScanResult([incident(datetime.now(timezone.utc), record=number) for number in range(8)])
    result.complete = False
    result.warnings = ["Security: access denied", "Application: record cap reached"]
    result.comparison = dict(comparable=True, new_count=1, persistent_count=1)
    window.monitor.active = True
    window.monitor.tracker = AlertTracker()
    window.monitor.tracker.observe(ScanResult())
    window.monitor.last_result = result
    window._monitor_notifications = True
    window.notification_preferences = NotificationPreferences(quiet=True, quiet_start=0, quiet_end=0)
    window._set_appearance(size=size, save=False)
    window.show()
    for width, height in ((1280, 720), (800, 620)):
        window.resize(width, height)
        window._monitor_completed(result, [])
        window._refresh_monitor_presentation()
        for _ in range(4):
            app.processEvents()
        banner = window.monitoring_status
        assert banner.isVisible()
        for label in (banner.title_label, banner.notification_label):
            assert label.isVisible()
            assert label.mapTo(window, label.rect().topLeft()).y() >= 0
            assert label.mapTo(window, label.rect().bottomRight()).y() < window.height()
        assert "quiet" in banner.notification_label.text().casefold()
        assert window.incident_table.viewport().height() >= 75, (size, width, height)
        bottom = window.incident_table.mapTo(window, QPoint(0, window.incident_table.height())).y()
        assert bottom < window.height(), (size, width, height, bottom)

        window.incident_table.setCurrentCell(
            0, 0, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
        )
        assert window._selected_incident() is not None
        window._open_incident_detail()
        assert window.page_stack.currentWidget() is window.incident_detail, "Detail did not open synchronously"
        for _ in range(4):
            app.processEvents()
        assert window.page_stack.currentWidget() is window.incident_detail
        assert banner.isVisible() and banner.notification_label.isVisible()
        window._show_incident_list()


def test_pending_real_alert_is_suppressed_when_quiet_hours_begin(desktop, monkeypatch):
    """A delayed batch must respect quiet hours at delivery as well as collection."""
    window, _app = desktop
    current = [datetime(2026, 11, 14, 21, 59)]

    class LocalClock:
        @staticmethod
        def now():
            return current[0]

    monkeypatch.setattr(notification_preferences, "datetime", LocalClock)
    tray = Mock()
    tray.isVisible.return_value = True
    monkeypatch.setattr(window, "monitor_tray", tray)
    sound = Mock()
    monkeypatch.setattr(window.sounds, "play", sound)
    window.monitor.active = True
    window._monitor_notifications = True
    window.notification_preferences = NotificationPreferences(quiet=True, batch_seconds=120)
    fresh = incident(datetime.now(timezone.utc), record=100)
    window._queue_notifications([fresh])
    assert window._notification_timer.isActive()
    assert window._notification_batch

    current[0] = datetime(2026, 11, 14, 22, 1)
    window._flush_notification_batch()
    tray.showMessage.assert_not_called()
    sound.assert_not_called()
    assert not window._notification_batch
    assert not window._notification_timer.isActive()
