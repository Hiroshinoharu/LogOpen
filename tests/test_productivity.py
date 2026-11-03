"""Verify evidence-preserving grouping, local review tools, coverage, and focused rechecks."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import Qt

from history_store import HistoryStore
from incident_detection import build_incident
from incident_followup import focused_options, followup_summary
from models.scan_options import ScanOptions
from models.scan_result import ScanResult
from tests.helpers import make_event
from tests.test_guided_ui import desktop
from tests.test_workspace import wait_until
from ui.insights import CoverageDialog, relative_time
from ui.main_window import MainWindow


def occurrence(stamp, record=1, **changes):
    event = make_event(time_generated=stamp, provider="Example", event_id=10)
    event["record_number"] = record
    result = build_incident([event])
    result.update(changes)
    return result


def test_snooze_expiry_undo_and_notes_preserve_old_database_states(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    item = occurrence(datetime.now(timezone.utc))
    store.set_state(item, "acknowledged")
    store.save_note(item, "Started after an update <literal>.")
    snapshot = store.issue_preferences(item)
    store.snooze(item)
    assert item["review_state"] == "snoozed"
    store.restore_preferences([snapshot])
    store.apply_states([item])
    assert item["review_state"] == "acknowledged"
    assert item["local_note"] == "Started after an update <literal>."
    snapshot["snoozed_until"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    store.restore_preferences([snapshot])
    store.apply_states([item])
    assert item["review_state"] == "acknowledged"
    store.set_state(item, "active")
    assert item["snoozed_until"] is None
    with pytest.raises(ValueError):
        store.save_note(item, "x" * 4001)


def test_grouping_filters_and_exports_keep_every_original_occurrence(desktop):
    window, _app = desktop
    now = datetime.now(timezone.utc)
    first, second = occurrence(now, 1), occurrence(now + timedelta(minutes=2), 2)
    unrelated = occurrence(now, 3, incident_classification="Different problem")
    window._show_scan_results(ScanResult([first, second, unrelated]))
    window.group_action.setChecked(True)
    table = window.incident_table
    assert sum(not table.isRowHidden(row) for row in range(3)) == 2
    assert window._selected_incidents(visible=True) == [first, second, unrelated]
    row = next(row for row in range(3) if "2 occurrences" in table.item(row, 1).text())
    table.setCurrentCell(row, 0)
    assert set(map(id, window._selected_incidents())) == {id(first), id(second)}
    window._toggle_selected_group()
    assert all(not table.isRowHidden(row) for row in range(3))
    window._toggle_selected_group()
    window._review_rows("ignored")
    assert first["review_state"] == second["review_state"] == "ignored"
    assert window._selected_incidents(visible=True) == [unrelated]
    window._undo_review_change()
    assert first["review_state"] == second["review_state"] == "active"
    assert len(window._selected_incidents(visible=True)) == 3


def test_notes_and_temporary_hiding_are_local_and_can_be_undone(desktop):
    window, _app = desktop
    item = occurrence(datetime.now(timezone.utc))
    window._show_scan_results(ScanResult([item]))
    window.incident_table.setCurrentCell(0, 0)
    window._open_incident_detail()
    detail = window.incident_detail
    detail.personal_notes.set_expanded(True)
    detail.note_editor.setPlainText("Started after a driver update.")
    detail.save_note_button.click()
    assert item["local_note"] == detail.note_editor.toPlainText()
    detail.snooze_button.click()
    assert item["review_state"] == "snoozed"
    assert window._selected_incidents(visible=True) == []
    window.undo_button.click()
    assert item["review_state"] == "active"
    assert item["local_note"] == "Started after a driver update."
    assert window._selected_incidents(visible=True) == [item]
    assert not window.undo_review_action.isEnabled()


def test_coverage_reports_unknown_and_partial_sources_without_inventing_completeness(desktop):
    window, _app = desktop
    result = ScanResult()
    result.complete = False
    result.scan_options = dict(log_types=["System", "Security"], hours=24, max_records=100)
    result.coverage = {"System": dict(complete=False, stop_reason="record_limit", records_read=100, matched_events=4)}
    dialog = CoverageDialog(result, window)
    try:
        assert dialog.table.item(0, 3).text() == "Entry limit reached"
        assert "shorter time range" in dialog.table.item(0, 4).text()
        assert dialog.table.item(1, 1).text() == "Partial / unknown"
        assert "unknown" in dialog.table.item(1, 4).text()
    finally:
        dialog.close()


def test_focused_check_preserves_scope_and_distinguishes_fresh_evidence():
    now = datetime.now(timezone.utc)
    old = occurrence(now - timedelta(minutes=10), 1)
    baseline = ScanResult([old])
    baseline.created_at = now - timedelta(minutes=5)
    baseline.scan_options = ScanOptions(("System", "Application"), hours=72, max_records=500).as_dict()
    options = focused_options(old, baseline, ScanOptions())
    assert options.log_types == ("System",)
    assert options.hours == 72 and options.max_records == 500 and not options.automatic_ai
    result = ScanResult([deepcopy(old)])
    result.scan_options = options.as_dict()
    result.coverage = {"System": {"complete": True}}
    assert "only previously observed" in followup_summary(old, baseline, result)
    result.append(occurrence(now, 2))
    assert "1 new matching" in followup_summary(old, baseline, result)
    result.clear()
    assert "no matching activity" in followup_summary(old, baseline, result)
    result.complete = False
    assert "cannot establish" in followup_summary(old, baseline, result)
    result.complete = True
    result.coverage.clear()
    assert "cannot establish" in followup_summary(old, baseline, result)
    result.coverage = {"System": {"complete": True}}
    baseline.scan_options["detection_version"] = 1
    assert "cannot establish" in followup_summary(old, baseline, result)
    baseline.scan_options["detection_version"] = result.scan_options["detection_version"]
    baseline.created_at = now - timedelta(days=7)
    assert "cannot establish" in followup_summary(old, baseline, result)


def test_check_again_uses_worker_without_changing_saved_scan_defaults(desktop, monkeypatch):
    window, app = desktop
    item = occurrence(datetime.now(timezone.utc) - timedelta(minutes=2))
    baseline = ScanResult([item])
    baseline.scan_options = window.scan_options.as_dict()
    window._show_scan_results(baseline)
    window.incident_table.setCurrentCell(0, 0)
    window._open_incident_detail()
    defaults = window.scan_options
    def scan(options, **_kwargs):
        result = ScanResult()
        result.scan_options = options.as_dict()
        result.coverage = {name: {"complete": True} for name in options.log_types}
        return result
    scan_mock = Mock(side_effect=scan)
    monkeypatch.setattr("ui.main_window.scan_system", scan_mock)
    window.incident_detail.check_again_button.click()
    wait_until(app, lambda: window._scan_thread is None)
    used = scan_mock.call_args.args[0]
    assert used.log_types == ("System",) and not used.automatic_ai
    assert window.scan_options is defaults
    assert "Check again:" in window.status_label.text()


def test_preview_chips_and_appearance_work_without_network(desktop):
    window, app = desktop
    item = occurrence(datetime.now(timezone.utc), local_note="My note")
    window._show_scan_results(ScanResult([item]))
    window.resize(1600, 1000)
    app.processEvents()
    window.incident_table.setCurrentCell(0, 0)
    assert not window.preview.isHidden()
    assert item["incident_classification"] == window.preview.title.text()
    assert "My note" in window.preview.body.toPlainText()
    window.search_input.setText("Example")
    window._apply_filters()
    assert not window.filter_chips.isHidden()
    window.filter_chip_layout.itemAt(0).widget().click()
    assert window.search_input.text() == ""
    window.attention_card.activated.emit()
    assert window.priority_filter.currentText() == "High and Critical"
    window.problems_card.activated.emit()
    assert window.priority_filter.currentText() == "All priorities"
    window._set_appearance(theme="light", size="large")
    second = MainWindow()
    try:
        assert second._theme == "light" and second._text_size == "large"
        assert "#f8fafc" in second.styleSheet()
    finally:
        second.close()
        second.deleteLater()


def test_relative_timestamps_preserve_honest_unknown_and_future_states():
    now = datetime.now(timezone.utc)
    assert relative_time(now - timedelta(minutes=12), now) == "12 minutes ago"
    assert relative_time(now + timedelta(hours=1), now) == "In the future"
    assert relative_time("malformed", now) == "Time unavailable"


def test_old_reports_with_no_scan_timestamp_do_not_look_new(desktop):
    window, _app = desktop
    result = ScanResult()
    result.created_at = None
    window._display_saved_result(result, "Imported report")
    window._update_relative_scan_time()
    assert window.last_scan.text() == "Scan time unavailable"


def test_temporarily_hidden_issues_do_not_consume_automatic_ai_budget(monkeypatch):
    import main
    item = occurrence(datetime.now(timezone.utc), review_state="snoozed", incident_priority="Critical")
    analyze = Mock()
    monkeypatch.setattr(main, "analyse_incident_with_llm_result", analyze)
    main.add_llm_analyses(ScanResult([item]))
    analyze.assert_not_called()
    assert item["llm_analysis_status"] == "not_selected"
