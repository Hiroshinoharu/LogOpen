"""Exercise monitoring deduplication, background lifecycle, profiles, and timeline identity."""

from datetime import datetime, timedelta, timezone
import os
import threading
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import QApplication, QDialog
from detection_rules import DEFENDER, PROFILES
from incident_detection import build_incident
from models.scan_options import ScanOptions
from models.scan_result import ScanResult
from monitoring import AlertTracker
from tests.helpers import make_event
from tests.test_workspace import wait_until
from ui.channel_browser import ChannelBrowser
from ui.main_window import MainWindow
from ui.scan_controls import ScanOptionsDialog
from ui.timeline import TimelineWidget
import ui.monitoring as monitor_ui


@pytest.fixture
def desktop():
    """Own an offscreen desktop and cancel all monitoring work before widget destruction."""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.motion.set_enabled(False)
    yield window, app
    window.monitor.stop()
    wait_until(app, lambda: window.monitor.thread is None)
    window.close()
    window.deleteLater()
    app.processEvents()


def incident(stamp, record=1, state="active"):
    """Build high-priority synthetic evidence with a stable native record identity."""
    event = make_event(time_generated=stamp, level="Critical")
    event["record_number"] = record
    result = build_incident([event])
    result["review_state"] = state
    return result


def test_monitor_baseline_repeat_review_and_new_evidence():
    """Baseline and repeated windows remain silent; only new active high-priority evidence alerts."""
    start = datetime(2026, 10, 18, tzinfo=timezone.utc)
    tracker = AlertTracker(start)
    assert not tracker.observe(ScanResult([incident(start - timedelta(hours=1))]))
    new = incident(start + timedelta(seconds=5), record=2)
    assert tracker.observe(ScanResult([new])) == [new]
    assert not tracker.observe(ScanResult([new]))
    ignored = incident(start + timedelta(seconds=6), record=3, state="ignored")
    assert not tracker.observe(ScanResult([ignored]))
    ignored["review_state"] = "active"
    assert not tracker.observe(ScanResult([ignored]))
    assert not tracker.observe(ScanResult([incident(start - timedelta(minutes=1), record=4)]))
    next_event = incident(start + timedelta(seconds=7), record=5)
    assert tracker.observe(ScanResult([next_event])) == [next_event]


def test_cancelled_monitor_scan_cannot_establish_baseline():
    """A cancelled attempt cannot suppress the first real baseline or emit notifications."""
    tracker = AlertTracker()
    cancelled = ScanResult()
    cancelled.cancelled = True
    assert not tracker.observe(cancelled) and not tracker.initialized


def test_monitor_runs_off_gui_thread_without_ai_and_preserves_reading(desktop, monkeypatch):
    """Polling snapshots scope, suppresses AI, archives through the worker, and leaves the current view alone."""
    window, app = desktop
    old = ScanResult([incident(datetime.now(timezone.utc))])
    window._show_scan_results(old)
    calls = []

    def scan(options, **kwargs):
        """Record thread/scope and return a new independently saved-looking result."""
        calls.append((options, QThread.currentThread(), kwargs))
        return ScanResult()

    monkeypatch.setattr(monitor_ui, "scan_system", scan)
    window.monitor.start(ScanOptions((DEFENDER,), automatic_ai=True), 30)
    wait_until(app, lambda: window._latest_monitor_result is not None and window.monitor.thread is None)
    assert len(calls) == 1 and not calls[0][0].automatic_ai
    assert calls[0][0].log_types == (DEFENDER,) and calls[0][1] is not app.thread()
    assert window._current_result is old
    assert window.monitor.timer.isActive()
    window._view_monitor_result()
    assert window._current_result is window._latest_monitor_result


@pytest.mark.parametrize("restart", [False, True])
def test_close_cancels_monitor_worker_before_destroying_window(desktop, monkeypatch, restart):
    """Closing during polling cooperatively stops the worker and waits for native cleanup."""
    window, app = desktop
    started = threading.Event()

    def scan(_options, **kwargs):
        """Wait for the actual cancellation token delivered by closing the desktop."""
        started.set()
        assert kwargs["cancel_event"].wait(3)
        result = ScanResult()
        result.cancelled = True
        return result

    monkeypatch.setattr(monitor_ui, "scan_system", scan)
    relaunch = Mock(side_effect=lambda: window.monitor.thread is None)
    monkeypatch.setattr("ui.main_window.relaunch_app", relaunch)
    window.show()
    window.monitor.start(ScanOptions(), 30)
    wait_until(app, started.is_set)
    if restart:
        window._restart_app()
    else:
        window.close()
    assert window._close_pending
    wait_until(app, lambda: window.monitor.thread is None and not window.isVisible())
    assert not window.monitor.active and not window.monitor.timer.isActive()
    assert relaunch.call_count == int(restart)


def test_monitor_defers_when_foreground_is_busy(desktop, monkeypatch):
    """A scheduled poll cannot start while another scan or modal action owns the UI."""
    window, app = desktop
    scan = Mock()
    monkeypatch.setattr(monitor_ui, "scan_system", scan)
    monkeypatch.setattr(window.monitor, "can_scan", lambda: False)
    window.monitor.start(ScanOptions(), 30)
    window.monitor._tick()
    app.processEvents()
    scan.assert_not_called()
    assert window.monitor.thread is None and window.monitor.timer.isActive()


def test_timeline_orders_mixed_offsets_and_preserves_sorted_selection(desktop):
    """Across-channel chronology follows absolute time; sorting still opens the correct raw payload."""
    window, app = desktop
    timeline = TimelineWidget(window)
    earlier = make_event(time_generated=datetime(2026, 10, 18, 12, tzinfo=timezone(timedelta(hours=1))),
                         log_type="Application", event_id=9, message="earlier <literal>")
    later = make_event(time_generated=datetime(2026, 10, 18, 11, 1, tzinfo=timezone.utc),
                       log_type=DEFENDER, event_id=2, message="later")
    timeline.set_incidents([{"events": [later, earlier], "incident_classification": "Example"}])
    assert timeline.table.item(0, 4).text() == "9"
    assert timeline.table.item(1, 1).text() == "60.000"
    timeline.table.sortItems(4, Qt.SortOrder.AscendingOrder)
    timeline.table.setCurrentCell(0, 0)
    assert '"message": "later"' in timeline.evidence.toPlainText()
    timeline.search.setText("earlier")
    assert timeline.table.isRowHidden(0) and not timeline.table.isRowHidden(1)
    timeline.set_incidents([])
    assert timeline.table.rowCount() == 0 and not timeline.evidence.toPlainText()


def test_profiles_and_custom_rules_are_saved_in_scope(desktop):
    """Profile choices populate exact channel paths and valid custom edits survive Save."""
    window, _app = desktop
    dialog = ScanOptionsDialog(ScanOptions(), window)
    dialog.profile.setCurrentIndex(dialog.profile.findData("security"))
    assert tuple(dialog.logs.text().split(", ")) == PROFILES["security"][1]
    dialog.automatic_ai.setChecked(False)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.options.profile == "security" and not dialog.options.automatic_ai


def test_channel_browser_keeps_checked_channels_through_search(desktop, monkeypatch):
    """Discovery and access updates retain checkbox identity while table rows are sorted and filtered."""
    window, app = desktop
    channels = [dict(name="System", kind="Administrative", enabled=True, access="Not checked", description="System events"),
                dict(name=DEFENDER, kind="Operational", enabled=False, access="Not checked", description="Protection events")]
    monkeypatch.setattr("ui.channel_browser.list_channels", lambda _token: channels)
    monkeypatch.setattr("ui.channel_browser.check_channels", lambda names, _token: {name: "Readable" for name in names})
    browser = ChannelBrowser(["System"], window)
    wait_until(app, lambda: browser._thread is None)
    browser.search.setText("Protection")
    assert browser.selected_names() == ("System",)
    browser._check_selected()
    wait_until(app, lambda: browser._thread is None)
    assert browser.selected_names() == ("System",)
    browser.accept()
    assert browser.names == ("System",)
    browser.deleteLater()
