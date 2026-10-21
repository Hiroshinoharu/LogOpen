"""Exercise integrated desktop workflows using isolated history and mocked network work."""

import os
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QAbstractAnimation, QDate, QThread, Qt
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog

from incident_detection import build_incident
from json_reporting import export_incidents_to_json
from llm_analysis import AIAnalysisResult
from models.incident_analysis import IncidentAnalysis
from models.scan_options import ScanOptions
from models.scan_result import ScanResult
from report_io import load_report
from settings.app_settings import AISettings, AppSettings
from tests.helpers import make_event
from ui.main_window import MainWindow
from ui.scan_controls import ScanOptionsDialog


def wait_until(app, predicate):
    """Process queued GUI signals until completion, without blocking worker delivery."""
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert predicate(), "Timed out waiting for workspace action"


@pytest.fixture
def window():
    """Own a real desktop and ensure every worker is stopped before destruction."""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    widget = MainWindow()
    widget.motion.set_enabled(False)
    yield widget, app
    widget._cancel_event.set()
    wait_until(app, lambda: widget._scan_thread is None and widget._analysis_thread is None)
    widget.close()
    widget.deleteLater()
    app.processEvents()


def example(provider="Example", hours=0):
    """Build valid local evidence without collecting actual Windows events."""
    return build_incident([make_event(provider=provider, event_id=10,
                                     time_generated=datetime(2026, 10, 18, 12) + timedelta(hours=hours),
                                     message=f"Evidence from {provider}")])


def visible(window):
    """Return source dictionary identities for the rows a user currently sees."""
    return window._selected_incidents(visible=True)


def test_filters_follow_sorted_rows_and_review_state(window):
    """Search messages and combine provider/date/priority/review filters after sorting."""
    widget, app = window
    alpha, zulu = example("Alpha"), example("Zulu", -48)
    alpha["incident_priority"] = "High"
    zulu["incident_priority"] = "Low"
    widget._show_scan_results([zulu, alpha])
    widget.search_input.setText("evidence from alpha")
    widget._apply_filters()
    assert visible(widget) == [alpha]
    widget.incident_table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert visible(widget) == [alpha]
    widget.search_input.clear()
    widget._extra_filters = dict(provider="ALPHA", dates=True,
                                 start=QDate(2026, 10, 18), end=QDate(2026, 10, 18))
    widget._apply_filters()
    assert visible(widget) == [alpha]
    widget.priority_filter.setCurrentText("Low")
    assert visible(widget) == []
    widget._clear_filters()
    widget.incident_table.setCurrentCell(0, 0)
    widget._open_incident_detail()
    widget._review_incident("ignored")
    assert alpha["review_state"] == "ignored"
    assert visible(widget) == [zulu]
    widget.review_filter.setCurrentText("Hidden")
    assert visible(widget) == [alpha]
    widget._review_incident("active")
    assert alpha["review_state"] == "active"
    assert visible(widget) == []
    widget._clear_filters()
    assert len(visible(widget)) == 2


def test_scope_persists_and_validation_keeps_dialog_open(window, monkeypatch):
    """Scope edits survive another window and empty log lists cannot be saved."""
    widget, app = window
    desired = ScanOptions(log_types=("System",), hours=168, max_records=5000)
    def accept(dialog):
        """Supply validated controls as though the user saved the dialog."""
        dialog.options = desired
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ScanOptionsDialog, "exec", accept)
    widget._edit_scan_scope()
    assert widget.scan_options == desired
    second = MainWindow()
    try:
        assert second.scan_options == desired
        dialog = ScanOptionsDialog(desired)
        dialog.logs.setText(" , ")
        dialog.accept()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog.error.text()
        dialog.deleteLater()
    finally:
        second.close()
        second.deleteLater()


def test_progress_and_cancel_reach_scan_worker(window, monkeypatch):
    """The GUI remains responsive and signals cancellation through the supplied token."""
    widget, app = window
    started = threading.Event()
    seen = {}
    def scan(options, *, progress, cancel_event, history_store):
        """Wait for cancellation in a fake worker and return marked partial results."""
        seen["thread"] = QThread.currentThread()
        seen["options"] = options
        progress("Reading test log", 20)
        started.set()
        assert cancel_event.wait(3)
        result = ScanResult([example()])
        result.cancelled = True
        result.complete = False
        return result
    monkeypatch.setattr("ui.main_window.scan_system", scan)
    widget.show()
    widget.run_scan()
    wait_until(app, lambda: started.is_set() and widget.progress_bar.value() == 20)
    assert seen["thread"] != app.thread()
    assert seen["options"] == widget.scan_options
    widget.cancel_button.click()
    wait_until(app, lambda: widget._scan_thread is None)
    assert widget._current_result.cancelled
    assert "cancelled" in widget.status_label.text()
    assert widget.scan_button.isEnabled()
    assert widget.cancel_button.isHidden()


def test_saved_reports_open_without_scan_and_exports_only_selected(window, monkeypatch, tmp_path):
    """Open imported evidence and export a chosen sorted row without mutating history."""
    widget, app = window
    source = tmp_path / "source.json"
    result = ScanResult([example("Zulu"), example("Alpha")])
    export_incidents_to_json(result, source)
    scan = Mock(side_effect=AssertionError("Opening a report must not scan"))
    monkeypatch.setattr("ui.main_window.scan_system", scan)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(source), ""))
    widget._open_report()
    assert len(widget._incidents) == 2
    assert widget._history_scan_id is None
    assert "Opened report" in widget.status_label.text()
    widget.incident_table.sortItems(1, Qt.SortOrder.AscendingOrder)
    widget.incident_table.setCurrentCell(0, 0)
    target = tmp_path / "selected.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(target), "JSON report (*.json)"))
    widget._export_selection()
    restored = load_report(target)
    assert len(restored) == 1
    assert restored[0]["providers"] == ["Alpha"]
    scan.assert_not_called()


def test_history_reopens_comparison_and_review_state(window, monkeypatch):
    """The history picker restores evidence and its comparison without recollection."""
    widget, app = window
    first = ScanResult([example("Old")])
    first.scan_options = ScanOptions().as_dict()
    widget.history_store.save_scan(first)
    current = ScanResult([example("New")])
    current.scan_options = ScanOptions().as_dict()
    widget.history_store.compare_scan(current)
    identity = widget.history_store.save_scan(current)
    widget.history_store.set_state(current[0], "acknowledged")
    def choose(dialog):
        """Select the saved ID through the history dialog contract."""
        dialog.scan_id = identity
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr("ui.workspace.HistoryDialog.exec", choose)
    widget._open_history()
    assert widget._current_result.scan_id == identity
    assert widget._history_scan_id == identity
    assert widget._incidents[0]["review_state"] == "acknowledged"
    assert widget._current_result.comparison["comparable"]
    assert "1 new" in widget.comparison_button.text()


def test_manual_ai_runs_in_worker_updates_history_and_closes_safely(window, monkeypatch):
    """Request selected AI off-thread, save its result, and honor a deferred close."""
    widget, app = window
    result = ScanResult([example()])
    identity = widget.history_store.save_scan(result)
    widget._display_saved_result(widget.history_store.load_scan(identity), "Opened saved scan")
    widget.incident_table.setCurrentCell(0, 0)
    widget._open_incident_detail()
    AppSettings().save(AISettings(True, "example-model", 1))
    started, release = threading.Event(), threading.Event()
    threads = []
    analysis = IncidentAnalysis(explanation="Test analysis", likely_causes=[],
                                recommended_actions=[], remediation_notes=[], diagnostic_steps=[])
    def analyze(incident, *, settings):
        """Simulate a paid boundary with no network and a test-controlled response."""
        threads.append(QThread.currentThread())
        assert settings.model == "example-model"
        started.set()
        assert release.wait(3)
        return AIAnalysisResult("completed", "AI analysis completed.", analysis)
    monkeypatch.setattr("ui.workspace.analyse_incident_with_llm_result", analyze)
    widget.show()
    try:
        widget._analyze_selected()
        wait_until(app, started.is_set)
        assert not widget.scan_button.isEnabled()
        assert not widget.incident_detail.analyze_button.isEnabled()
        widget.close()
        assert widget.isVisible()
        release.set()
        wait_until(app, lambda: widget._analysis_thread is None)
        assert threads[0] != app.thread()
        assert not widget.isVisible()
        assert widget.history_store.load_scan(identity)[0]["llm_analysis"]["explanation"] == "Test analysis"
    finally:
        release.set()


def test_disabled_ai_and_reduced_motion_do_not_start_work(window, monkeypatch):
    """Respect the saved AI flag and stop running animations immediately when disabled."""
    widget, app = window
    widget._show_scan_results([example()])
    widget.incident_table.setCurrentCell(0, 0)
    widget._open_incident_detail()
    AppSettings().save(AISettings(False, "example-model", 1))
    analyze = Mock()
    monkeypatch.setattr("ui.workspace.analyse_incident_with_llm_result", analyze)
    widget._analyze_selected()
    assert widget._analysis_thread is None
    assert "Enable AI" in widget.incident_detail.action_feedback.text()
    analyze.assert_not_called()
    widget.motion.set_enabled(True)
    widget.motion.set_active(True)
    assert widget.motion.pulse.state() == QAbstractAnimation.State.Running
    widget._set_motion_enabled(False)
    assert widget.motion.pulse.state() == QAbstractAnimation.State.Stopped
    assert widget.motion.badge_effect.opacity() == 1.0


def test_compact_window_keeps_result_rows_visible(window):
    """Collapse summary cards at the minimum height so filters do not crowd out data."""
    widget, app = window
    widget._show_scan_results([example(str(index)) for index in range(5)])
    widget.show()
    widget.resize(800, 620)
    app.processEvents()
    assert widget.metrics_panel.isHidden()
    assert widget.incident_table.viewport().height() >= 90
    widget.resize(1140, 860)
    app.processEvents()
    assert not widget.metrics_panel.isHidden()


def test_retry_failure_keeps_previous_analysis_and_does_not_write_imported_history(window, monkeypatch):
    """An imported report ID must not overwrite a locally saved scan on manual retry."""
    widget, app = window
    incident = example()
    incident["llm_analysis"] = {"explanation": "Previous analysis"}
    result = ScanResult([incident])
    widget._show_scan_results(result)
    widget.incident_table.setCurrentCell(0, 0)
    widget._open_incident_detail()
    widget._analysis_target = incident
    update = Mock()
    monkeypatch.setattr(widget.history_store, "update_scan", update)
    widget._show_manual_analysis(AIAnalysisResult("failed", "Connection failed."))
    assert incident["llm_analysis"]["explanation"] == "Previous analysis"
    assert "retained" in widget.incident_detail.action_feedback.text()
    update.assert_not_called()
