"""Check navigation and recovery paths in the beginner-friendly desktop flow."""

import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

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
