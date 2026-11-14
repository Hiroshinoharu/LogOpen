"""Keyboard semantics and font scaling for evidence readers and summary actions."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QBoxLayout, QLabel, QPushButton, QScrollArea, QTableWidget

from ui.appearance import themed_stylesheet
from ui.case_tools import CompareScansDialog, SupportReportDialog
from models.scan_result import ScanResult
from ui.guidance import QuickStartDialog
from ui.incident_detail import IncidentDetailWidget
from ui.incident_detail import SourceEventsWidget
from ui.insights import MetricCard
from ui.table_layout import RoomyTable
from ui.timeline import TimelineWidget


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


def test_summary_actions_use_button_release_semantics(app):
    """Assistive technology gets a button; Space and mouse presses can be cancelled."""
    card = MetricCard()
    assert isinstance(card, QPushButton)
    card.setAccessibleName("Review high priority problems")
    card.resize(300, 120)
    card.show()
    card.setFocus()
    app.processEvents()
    activated = QSignalSpy(card.activated)
    QTest.keyPress(card, Qt.Key.Key_Space)
    assert len(activated) == 0
    QTest.keyRelease(card, Qt.Key.Key_Space)
    assert len(activated) == 1
    QTest.mousePress(card, Qt.MouseButton.LeftButton)
    assert len(activated) == 1
    QTest.mouseRelease(card, Qt.MouseButton.LeftButton)
    assert len(activated) == 2
    QTest.keyClick(card, Qt.Key.Key_Return)
    assert len(activated) == 3
    card.setEnabled(False)
    card.click()
    assert len(activated) == 3
    card.close()


def test_table_rows_and_headers_follow_font_enlargement(app):
    table = QTableWidget(2, 2)
    table.setHorizontalHeaderLabels(["Priority", "What happened"])
    roomy = RoomyTable(table, [120, 320])
    table.show()
    font = QFont(table.font())
    font.setPixelSize(40)
    table.setFont(font)
    app.processEvents()
    assert table.rowHeight(0) >= table.fontMetrics().height() + 20
    assert table.horizontalHeader().height() >= table.horizontalHeader().fontMetrics().height() + 20
    assert roomy.widths[0] >= table.fontMetrics().horizontalAdvance("Priority") + 48
    table.close()


def test_timeline_search_keeps_its_label_after_typing(app):
    timeline = TimelineWidget()
    timeline.search.setText("DNS")
    labels = [label for label in timeline.findChildren(QLabel) if label.buddy() is timeline.search]
    assert labels and labels[0].text() == "&Search timeline"
    assert timeline.search.accessibleName() == "Search timeline"
    assert timeline.table.accessibleName()
    timeline.close()


def test_enlarged_detail_actions_stack_and_guide_scrolls(app):
    base = Path("ui/style.qss").read_text(encoding="utf-8")
    style = themed_stylesheet(base, "light", "largest")
    detail = IncidentDetailWidget()
    detail.setStyleSheet(style)
    detail.resize(760, 620)
    detail.show()
    app.processEvents()
    assert detail.review_actions.direction() == QBoxLayout.Direction.TopToBottom
    assert detail.ai_actions.direction() == QBoxLayout.Direction.TopToBottom
    assert detail.note_editor.maximumHeight() > 100
    guide = QuickStartDialog()
    guide.setStyleSheet(style)
    guide.resize(510, 420)
    guide.show()
    app.processEvents()
    scroll = guide.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.verticalScrollBar().maximum() > 0
    guide.close()
    detail.close()


def test_support_report_masking_controls_fit_enlarged_text(app):
    store = Mock()
    store.checklist.return_value = []
    dialog = SupportReportDialog([], ScanResult(), store)
    base = Path("ui/style.qss").read_text(encoding="utf-8")
    dialog.setStyleSheet(themed_stylesheet(base, "light", "largest"))
    dialog.resize(800, 620)
    dialog.show()
    app.processEvents()
    assert dialog.minimumSizeHint().width() <= 800
    assert dialog.width() == 800 and dialog.height() == 620
    scroll = dialog.findChild(QScrollArea)
    assert scroll.verticalScrollBar().maximum() > 0
    choices = (dialog.mask_user, dialog.mask_computer, dialog.mask_paths, dialog.notes)
    for previous, following in zip(choices, choices[1:]):
        assert previous.geometry().bottom() < following.geometry().top()
    scroll.ensureWidgetVisible(dialog.preview)
    app.processEvents()
    caret = dialog.preview.viewport().mapTo(scroll.viewport(), dialog.preview.cursorRect().center())
    assert scroll.viewport().rect().contains(caret)
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    center = dialog.preview.mapTo(scroll.viewport(), dialog.preview.rect().center())
    assert scroll.viewport().rect().contains(center)
    assert any(label.buddy() is dialog.extra for label in dialog.findChildren(QLabel))
    dialog.close()


def test_source_reader_reflows_to_avoid_clipping_in_short_viewport(app):
    sources = SourceEventsWidget()
    base = Path("ui/style.qss").read_text(encoding="utf-8")
    sources.setStyleSheet(themed_stylesheet(base, "dark", "normal"))
    sources.set_events([dict(event_id=10010, provider="DCOM", level="Error", log_type="System",
                             time_generated="2026-09-09T20:07:13", message="A long message. " * 300)])
    sources.resize(752, 255)
    sources.show()
    app.processEvents()
    assert sources.splitter.orientation() == Qt.Orientation.Horizontal
    assert sources.message.mapTo(sources, sources.message.rect().bottomRight()).y() < sources.height()
    sources.resize(752, 650)
    app.processEvents()
    assert sources.splitter.orientation() == Qt.Orientation.Vertical
    sources.close()


def test_comparison_long_scopes_fit_and_keep_exact_selection_visible(app):
    store = Mock()
    now = datetime.now(timezone.utc)
    logs = ["Microsoft-Windows-Diagnostics-Performance/Operational", "System", "Application"]
    store.list_scans.return_value = [
        dict(id=index, created_at=now - timedelta(days=index), log_types=logs, hours=24, complete=True)
        for index in (1, 2)
    ]
    dialog = CompareScansDialog(ScanResult(), store)
    base = Path("ui/style.qss").read_text(encoding="utf-8")
    dialog.setStyleSheet(themed_stylesheet(base, "light", "largest"))
    dialog.resize(800, 620)
    dialog.show()
    app.processEvents()
    assert dialog.width() == 800 and dialog.height() == 620
    assert dialog.minimumSizeHint().width() <= 800
    dialog.baseline.setCurrentIndex(1)
    assert dialog.baseline.currentData() == 2
    assert dialog.baseline_details.text() == dialog.baseline.currentText()
    assert logs[0] in dialog.baseline_details.text()
    scroll = dialog.findChild(QScrollArea)
    assert scroll.horizontalScrollBar().maximum() == 0
    dialog.close()


def test_detail_value_labels_keep_content_as_their_native_accessible_name(app):
    detail = IncidentDetailWidget()
    detail.set_incident(dict(summary_text="The application stopped responding.", incident_score=65,
                             llm_analysis=dict(explanation="A specific saved explanation.")))
    labels = [detail.summary, detail.score, detail.builtin_steps, detail.ai_status,
              detail.ai_model_hint, *detail.fields.values(), *detail.score_fields.values(),
              *detail.ai_fields.values()]
    assert detail.summary.text() == "The application stopped responding."
    assert detail.ai_fields["explanation"].text() == "A specific saved explanation."
    assert all(not label.accessibleName() and label.accessibleDescription() for label in labels)
    detail.close()
