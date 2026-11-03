"""Verify persisted troubleshooting, private report preparation and alert controls."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from PyQt6.QtWidgets import QDialog

from history_store import HistoryStore, incident_key
from models.scan_result import ScanResult
from notification_preferences import NotificationPreferences
from support_report import redact_support, support_text
from tests.test_guided_ui import desktop
from tests.test_productivity import occurrence
from ui.case_tools import (ChecklistDialog, CompareScansDialog, IssueHistoryDialog,
                           SupportReportDialog, simple_explanation)
from ui.notification_options import health_text, NotificationOptionsDialog


def scoped(*items):
    result = ScanResult(items)
    result.scan_options = dict(log_types=["System"], hours=24, max_records=1000)
    result.coverage = {"System": {"complete": True, "records_read": 10, "stop_reason": "exhausted"}}
    return result


def test_checklist_and_actions_follow_issue_without_mutating_evidence(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    item = occurrence(datetime.now(timezone.utc))
    evidence = deepcopy(item["events"])
    steps = [dict(id="network-status", text="Read connection status", done=True, result="Connected")]
    store.save_checklist(item, steps)
    store.save_checklist(item, steps)  # Re-saving unchanged steps must not invent activity.
    store.save_note(item, "Started after update")
    original = store.issue_preferences(item)
    store.set_state(item, "acknowledged")
    store.snooze(item)
    store.restore_preferences([original])
    store.record_activity(item, "Check again result", "No fresh evidence in this window; not proof of a fix.")
    result = scoped(item)
    store.save_scan(result)
    later = occurrence(datetime.now(timezone.utc) + timedelta(minutes=10), 2)
    assert store.checklist(later) == steps
    timeline = store.issue_timeline(later)
    kinds = [entry["kind"] for entry in timeline]
    assert kinds.count("Troubleshooting check") == 1
    assert {"Note saved", "Review decision", "Hidden temporarily", "Undo review change", "Check again result", "Observed in scan"} <= set(kinds)
    assert item["events"] == evidence
    assert store.checklist({**later, "computer_name": "Other PC"}) == []
    with pytest.raises(ValueError):
        store.save_checklist(item, [steps[0], steps[0]])
    with pytest.raises(ValueError):
        store.save_checklist(item, [{**steps[0], "result": "x" * 4001}])
    assert store.checklist(item) == steps


def test_checklist_dialog_reopens_saved_results_and_custom_checks(desktop):
    window, _ = desktop
    item = occurrence(datetime.now(timezone.utc))
    first = ChecklistDialog(item, window.history_store, window)
    try:
        first.rows[0][1].setChecked(True)
        first.rows[0][2].setPlainText("Checked the event time; app works now.")
        first.custom.setText("Ask support about error 123")
        first.add_custom()
        first.save()
        assert "saved locally" in first.feedback.text()
        second = ChecklistDialog(deepcopy(item), window.history_store, window)
        try:
            assert second.rows[0][1].isChecked()
            assert "app works now" in second.rows[0][2].toPlainText()
            assert second.rows[-1][0]["text"] == "Ask support about error 123"
            assert "1 of 4" in second.progress.text()
        finally:
            second.close()
        history = IssueHistoryDialog(item, window.history_store, window)
        try:
            assert history.table.rowCount() == 4
        finally:
            history.close()
    finally:
        first.close()


def test_comparison_can_choose_baseline_and_counts_families(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    now = datetime.now(timezone.utc)
    old = scoped(occurrence(now, 1), occurrence(now + timedelta(seconds=1), 2))
    store.save_scan(old)
    other = scoped(occurrence(now, 3, providers=["Other"]))
    store.save_scan(other)
    current = scoped(occurrence(now, 4), occurrence(now, 5), occurrence(now, 6, providers=["New"]))
    comparison = store.compare_scan(current, previous_scan_id=old.scan_id)
    assert comparison["previous_scan_id"] == old.scan_id
    assert comparison["persistent_count"] == comparison["new_count"] == 1
    assert comparison["no_longer_observed_count"] == 0
    absent = scoped()
    assert store.compare_scan(absent, previous_scan_id=old.scan_id)["no_longer_observed_count"] == 1
    absent.complete = False
    assert not store.compare_scan(absent, previous_scan_id=old.scan_id)["no_longer_observed"]
    absent.complete = True
    absent.scan_options["hours"] = 12
    assert not store.compare_scan(absent, previous_scan_id=old.scan_id)["comparable"]


def test_baseline_picker_keeps_saved_comparison_intact(desktop, monkeypatch):
    window, _ = desktop
    old = scoped(occurrence(datetime.now(timezone.utc)))
    old.created_at -= timedelta(hours=2)
    window.history_store.save_scan(old)
    current = scoped()
    current.comparison = {"reason": "Saved automatic comparison"}
    before = deepcopy(current.comparison)
    displayed = []
    monkeypatch.setattr("ui.case_tools.ComparisonDialog", lambda comparison, parent: Mock(exec=lambda: displayed.append(comparison)))
    dialog = CompareScansDialog(current, window.history_store, window)
    try:
        assert dialog.baseline.count() == 1
        dialog.compare()
        assert displayed[0]["no_longer_observed_count"] == 1
        assert current.comparison == before
    finally:
        dialog.close()


def test_simple_explanation_is_evidence_limited_and_offline():
    item = {"incident_classification": "DNS Resolution Timeout"}
    parts = dict(simple_explanation(item))
    assert "network address" in parts["What happened"]
    assert "might" in parts["What you might notice"]
    assert "Settings" in parts["Best first check"]
    assert "historical" in parts["What this cannot tell you"]
    assert "detection rule" in dict(simple_explanation({}))["What happened"]


def test_support_redacts_names_paths_and_manual_text_without_touching_source():
    item = occurrence(datetime.now(timezone.utc), computer_name="OFFICE-PC", local_note="PRIVATE NOTE")
    item["events"][0].update(computer_name="OFFICE-PC", username="alice", message=
        'OFFICE-PC reported C:\\Users\\alice\\My App\\app.exe\nAccount Name: alice\nContact alice@example.com\nTicket ABC-123')
    original = deepcopy(item)
    steps = {incident_key(item): [dict(text="Read status on OFFICE-PC", done=True, result="Ticket ABC-123") ]}
    text = support_text([item], scoped(item), steps)
    assert "PRIVATE NOTE" not in text
    masked = redact_support(text, [item], extra=["ABC-123"])
    assert "OFFICE-PC" not in masked and "alice" not in masked and "C:\\Users" not in masked
    assert "ABC-123" not in masked and "[path]" in masked and "[computer]" in masked
    assert "[Done]" in masked and "Event 10" in masked
    assert redact_support(text, [item], usernames=False, computers=False, paths=False) == text
    assert item == original


def test_support_report_saves_exact_reviewed_preview(desktop, monkeypatch, tmp_path):
    window, _ = desktop
    item = occurrence(datetime.now(timezone.utc))
    report = SupportReportDialog([item], scoped(item), window.history_store, window)
    destination = tmp_path / "reviewed.txt"
    monkeypatch.setattr("ui.case_tools.QFileDialog.getSaveFileName", lambda *args: (str(destination), "Text report (*.txt)"))
    try:
        report.preview.setPlainText("Reviewed text only\n<literal content>\n")
        report.save()
        assert destination.read_text(encoding="utf-8") == report.preview.toPlainText()
        assert "Nothing was sent" in report.feedback.text()
    finally:
        report.close()


@pytest.mark.parametrize("hour,minute,quiet", [(21, 59, False), (22, 0, True), (0, 0, True), (7, 59, True), (8, 0, False)])
def test_overnight_quiet_hours_boundaries(hour, minute, quiet):
    preferences = NotificationPreferences(quiet=True)
    assert preferences.is_quiet(datetime(2026, 11, 3, hour, minute)) is quiet


def test_preferences_validation_and_same_day_quiet_hours():
    preferences = NotificationPreferences(priorities=("High",), quiet=True, quiet_start=600, quiet_end=720, batch_seconds=60)
    assert NotificationPreferences.from_json(preferences.to_json()) == preferences
    assert not preferences.allows({"incident_priority": "Low"}, datetime(2026, 11, 3, 9))
    assert preferences.allows({"incident_priority": "High"}, datetime(2026, 11, 3, 9))
    assert not preferences.allows({"incident_priority": "High"}, datetime(2026, 11, 3, 11))
    assert replace(preferences, quiet_start=720).is_quiet(datetime(2026, 11, 3, 9))
    for invalid in ("null", "[]", '{"batch_seconds":-1}', '{"priorities":"High"}', '{"quiet":"false"}'):
        assert NotificationPreferences.from_json(invalid) == NotificationPreferences()


def test_alert_filtering_does_not_stop_refresh_and_batches_recheck_reviews(desktop, monkeypatch):
    window, _ = desktop
    sound, tray = Mock(), Mock()
    tray.isVisible.return_value = True
    monkeypatch.setattr(window.sounds, "play", sound)
    monkeypatch.setattr(window, "monitor_tray", tray)
    window._monitor_notifications = True
    low = occurrence(datetime.now(timezone.utc), incident_priority="Low")
    high = occurrence(datetime.now(timezone.utc), 2, incident_priority="High", providers=["Other"])
    window.notification_preferences = NotificationPreferences(priorities=("High",))
    result = scoped(low)
    window._monitor_completed(result, [low])
    assert window._current_result is result
    assert not window.monitor_notice.isHidden()
    sound.assert_not_called()
    window.notification_preferences = NotificationPreferences(quiet=True, quiet_start=0, quiet_end=0)
    window._monitor_completed(scoped(high), [high])
    sound.assert_not_called()
    assert not window._notification_batch
    window.notification_preferences = NotificationPreferences(batch_seconds=60)
    window.monitor.active = True
    window._queue_notifications([low])
    window._queue_notifications([high])
    assert window._notification_timer.isActive()
    assert len(window._notification_batch) == 2
    window.history_store.set_state(low, "acknowledged")
    window._flush_notification_batch()
    sound.assert_called_once_with("attention")
    assert "1 problem" in tray.showMessage.call_args.args[1]
    window._queue_notifications([high])
    window.monitor.stop()
    assert not window._notification_batch and not window._notification_timer.isActive()


def test_notification_preferences_persist_and_health_tracks_failure_recovery(desktop, monkeypatch):
    window, _ = desktop
    def accept(dialog):
        dialog.quiet.setChecked(True)
        dialog.batch.setValue(90)
        dialog.priorities["Low"].setChecked(False)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(NotificationOptionsDialog, "exec", accept)
    window._edit_notifications()
    saved = NotificationPreferences.from_json(window._ui_settings.value("monitor/notification_preferences"))
    assert saved.quiet and saved.batch_seconds == 90 and "Low" not in saved.priorities
    monitor = window.monitor
    assert health_text(monitor)[0] == "Off"
    monitor.active = True
    monitor.tracker = Mock(observe=lambda result: [])
    result = scoped()
    monitor._result(result)
    success = monitor.last_success
    assert success is not None and health_text(monitor)[0] == "Ready"
    monitor._error("Access denied")
    assert "Access denied" in health_text(monitor)[1]
    partial = scoped()
    partial.complete = False
    partial.coverage["System"] = dict(complete=False, stop_reason="access_denied")
    monitor._result(partial)
    assert monitor.last_success == success
    assert health_text(monitor)[0] == "Coverage gaps"
    assert "access_denied" in health_text(monitor)[1]
    monitor.waiting = True
    assert "Waiting until" in health_text(monitor)[1]
    monitor.waiting = False
    monitor.interval = 30_000
    monitor._finished()
    assert monitor.next_due is not None
    monitor.stop()
    assert monitor.next_due is None and health_text(monitor)[0] == "Off"
