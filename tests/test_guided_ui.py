"""Check navigation and recovery paths in the beginner-friendly desktop flow."""

import os
from dataclasses import replace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from detection_rules import PERFORMANCE, UPDATE, WLAN
from models.scan_options import ScanOptions
from ui.main_window import MainWindow
from ui.scan_controls import ScanOptionsDialog


@pytest.fixture
def desktop():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.motion.set_enabled(False)
    window.show()
    app.processEvents()
    yield window, app
    if window._settings_dialog is not None:
        window._settings_dialog.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def problem(**changes):
    return {
        "incident_classification": "DNS Resolution Timeout", "incident_priority": "High",
        "incident_score": 65, "event_count": 4, "summary_text": "Four recorded network failures.",
        **changes,
    }


def test_guided_navigation_opens_ai_setup_without_requesting_analysis(desktop, monkeypatch):
    """Reading next steps and opening setup must not silently send an AI request."""
    window, app = desktop
    analyze = Mock()
    monkeypatch.setattr("ui.workspace.analyse_incident_with_llm_result", analyze)
    assert window.journey.current_step == 0
    assert window.filters_widget.isHidden()
    incident = problem()
    window._show_scan_results([incident])
    assert window.journey.current_step == 1
    window.incident_table.setCurrentCell(0, 0)
    window.detail_button.click()
    assert window.journey.current_step == 2
    detail = window.incident_detail
    assert detail.technical_details.content.isHidden()
    detail.technical_details.toggle_button.click()
    assert not detail.technical_details.content.isHidden()
    detail.next_steps_button.click()
    assert detail.tabs.currentIndex() == 1
    detail.ai_settings_button.click()
    assert window._settings_dialog.isVisible()
    window._settings_dialog.close_button.click()
    app.processEvents()
    assert window._settings_dialog is None
    assert detail.tabs.currentIndex() == 1
    detail.back_button.click()
    assert window.page_stack.currentWidget() is window.dashboard
    assert window.journey.current_step == 1
    assert window._selected_incident() is incident
    window.detail_button.click()
    assert detail.technical_details.content.isHidden()
    analyze.assert_not_called()


def test_show_all_problems_recovers_search_and_hidden_results(desktop):
    """An empty view offers a working way back even when every problem is hidden."""
    window, _app = desktop
    incidents = [problem(), problem(incident_classification="Application error", review_state="ignored")]
    window._show_scan_results(incidents)
    window.search_input.setText("no matching text")
    window._apply_filters()
    assert not window.filter_feedback.isHidden()
    assert window._selected_incidents(visible=True) == []
    window.clear_filters_button.click()
    assert window.search_input.text() == ""
    assert window._selected_incidents(visible=True) == incidents
    assert window.filter_feedback.isHidden()
    window._show_scan_results([incidents[1]])
    window.review_filter.setCurrentIndex(0)
    assert not window.filter_feedback.isHidden()
    window.clear_filters_button.click()
    assert window._selected_incidents(visible=True) == [incidents[1]]


def test_collapsing_advanced_scan_options_preserves_edits(desktop):
    """Advanced controls retain their values when collapsed and saved."""
    window, _app = desktop
    options = ScanOptions(log_types=("System",), hours=72, max_records=5000)
    dialog = ScanOptionsDialog(options, window)
    try:
        assert dialog.advanced.content.isHidden()
        dialog.advanced.toggle_button.click()
        dialog.logs.setText("System, Application")
        dialog.limit.setValue(1000)
        dialog.advanced.toggle_button.click()
        dialog.accept()
        assert dialog.options.log_types == ("System", "Application")
        assert dialog.options.max_records == 1000
        assert dialog.options.hours == 72
    finally:
        dialog.close()
        dialog.deleteLater()


def test_start_here_tracks_priority_sorting_filters_and_review_decisions(desktop):
    """Recommend a visible active source object, then move on when its family is reviewed."""
    window, _app = desktop
    low = problem(incident_classification="Low", incident_priority="Low", incident_score=20)
    reviewed = problem(incident_classification="Already reviewed", incident_priority="Critical",
                       incident_score=99, review_state="acknowledged")
    high = problem()
    critical = problem(incident_classification="Windows Update Installation Failed",
                       incident_priority="Critical", incident_score=85)
    window.history_store.set_state(reviewed, "acknowledged")
    window._show_scan_results([low, reviewed, high, critical])
    window.incident_table.sortItems(1, Qt.SortOrder.AscendingOrder)
    assert window._start_here_incident is critical
    window.start_here_button.click()
    assert window.incident_detail.incident is critical
    assert window.page_stack.currentWidget() is window.incident_detail
    window._review_incident("acknowledged")
    assert window._start_here_incident is high
    window._review_incident("ignored")
    assert window._start_here_incident is high
    window.search_input.setText("Low")
    window._apply_filters()
    assert window._start_here_incident is low
    window.search_input.setText("nothing matches")
    window._apply_filters()
    assert window.start_here_card.isHidden()
    window._clear_filters()
    window._show_scan_results([])
    assert window.start_here_card.isHidden()
    assert window._start_here_incident is None


def test_start_here_uses_score_for_ties_and_has_a_reviewed_empty_state(desktop):
    """Scores break equal-priority ties; reviewed and hidden evidence is never recommended."""
    window, _app = desktop
    first = problem(incident_score=60)
    second = problem(incident_score=75)
    window._show_scan_results([first, second])
    assert window._start_here_incident is second
    first["review_state"] = "acknowledged"
    second["review_state"] = "ignored"
    window._apply_filters()
    assert window._start_here_incident is None
    assert "No unreviewed" in window.start_here_title.text()
    assert window.start_here_button.isHidden()


@pytest.mark.parametrize("profile,channels", [
    ("network", ("System", WLAN, "Microsoft-Windows-DNS-Client/Operational")),
    ("apps", ("Application", "System")),
    ("startup", ("System", "Application", PERFORMANCE)),
    ("updates", ("System", UPDATE)),
])
def test_symptom_choice_stages_and_persists_scope_without_starting_work(desktop, monkeypatch, profile, channels):
    """Friendly choices only change source scope; retain limits, rules, and AI preferences."""
    window, _app = desktop
    scan = Mock()
    monkeypatch.setattr("ui.main_window.scan_system", scan)
    window.scan_options = replace(window.scan_options, hours=72, max_records=1234, automatic_ai=False,
                                  rules_json='[{"id":"test","channel":"*","provider":"Example","event_ids":[1],"label":"Example","severity":"Warning","enabled":true}]')
    window.scan_goal.setCurrentIndex(window.scan_goal.findData(profile))
    assert window.scan_options.log_types == channels
    assert window.scan_options.profile == profile
    assert window.scan_options.hours == 72
    assert window.scan_options.max_records == 1234
    assert not window.scan_options.automatic_ai
    assert '"id":"test"' in window.scan_options.rules_json
    assert window.scan_goal_hint.text()
    second = MainWindow()
    try:
        assert second.scan_options == window.scan_options
        assert second.scan_goal.currentData() == profile
    finally:
        second.close()
        second.deleteLater()
    scan.assert_not_called()


def test_built_in_checks_work_offline_and_clear_between_incidents(desktop, monkeypatch):
    """Local checks stay available without AI, and unknown labels get an honest fallback."""
    window, _app = desktop
    analyze = Mock()
    monkeypatch.setattr("ui.workspace.analyse_incident_with_llm_result", analyze)
    detail = window.incident_detail
    detail.set_incident(problem(llm_analysis_status="disabled"))
    assert "Network & internet" in detail.builtin_steps.text()
    assert detail.builtin_steps.textFormat() == Qt.TextFormat.PlainText
    detail.set_incident(problem(incident_classification="Windows Update Installation Failed"))
    assert "Update history" in detail.builtin_steps.text()
    assert "Network & internet" not in detail.builtin_steps.text()
    detail.set_incident(problem(incident_classification="Custom <rule>"))
    assert "provider, event ID" in detail.builtin_steps.text()
    detail.set_incident(None)
    assert detail.builtin_steps.text() == ""
    analyze.assert_not_called()
