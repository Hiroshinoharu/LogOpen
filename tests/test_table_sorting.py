"""Verify table ordering and source identity through sorting and repopulation."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.incident_detail import SourceEventsWidget


@pytest.fixture
def app():
    """Keep the Qt application alive while sorting test widgets."""
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("column, expected", [
    (0, [1, 2, 0]), (1, [1, 2, 0]), (2, [1, 2, 0]),
    (3, [1, 2, 0]), (4, [2, 0, 1]),
])
def test_incident_sorting_preserves_identity_and_refresh(app, column, expected):
    """Sort every column both ways and retain the selected source after refresh."""
    incidents = [
        dict(incident_priority="Critical", incident_classification="Zulu", incident_score=100,
             event_count=10, is_recurring=True),
        dict(incident_priority="Low", incident_classification="alpha", incident_score=2,
             event_count=100),
        dict(incident_priority="High", incident_classification="Bravo", incident_score=60,
             event_count=2, is_recurring=False),
    ]
    window = MainWindow()
    try:
        table = window.incident_table
        assert table.isSortingEnabled()
        window._show_scan_results(incidents)
        for order, indices in [(Qt.SortOrder.AscendingOrder, expected),
                               (Qt.SortOrder.DescendingOrder, expected[::-1])]:
            table.sortItems(column, order)
            window._show_scan_results(incidents)
            for row, index in enumerate(indices):
                table.setCurrentCell(row, 0)
                assert window._selected_incident() is incidents[index]
                assert table.item(row, 1).text() == incidents[index]["incident_classification"]
                assert table.item(row, 4).text() == str(incidents[index]["event_count"])
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("column, expected", [
    (0, [2, 1, 0]), (1, [1, 2, 0]), (2, [1, 2, 0]), (3, [1, 2, 0]),
])
def test_source_sorting_keeps_messages_and_complete_rows(app, column, expected):
    """Sort numeric IDs, offset timestamps, provider names, and severity after reload."""
    events = [
        dict(event_id=100, provider="Zulu", level="Error", message="hundred",
             time_generated="2026-10-18T12:00:00+00:00"),
        dict(event_id=2, provider="alpha", level="Information", message="two",
             time_generated="2026-10-18T12:30:00+01:00"),
        dict(event_id=10, provider="Bravo", level="Warning", message="ten",
             time_generated="2026-10-18T11:00:00+00:00"),
    ]
    widget = SourceEventsWidget()
    try:
        assert widget.table.isSortingEnabled()
        widget.set_events(events)
        for order, indices in [(Qt.SortOrder.AscendingOrder, expected),
                               (Qt.SortOrder.DescendingOrder, expected[::-1])]:
            widget.table.sortItems(column, order)
            widget.set_events(events)
            for row, index in enumerate(indices):
                widget.table.setCurrentCell(row, 0)
                assert widget.message.toPlainText() == events[index]["message"]
                assert widget.table.item(row, 2).text() == str(events[index]["event_id"])
        widget.set_events([{}, events[0]])
        widget.table.sortItems(2, Qt.SortOrder.AscendingOrder)
        assert widget.table.item(0, 2).text() == "Not available"
        widget.set_events([])
        assert widget.table.rowCount() == 0
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


def test_header_click_toggles_order(app):
    """Exercise actual header clicks rather than only programmatic sorting."""
    widget = SourceEventsWidget()
    try:
        widget.set_events([dict(event_id=100), dict(event_id=2)])
        widget.show()
        app.processEvents()
        header = widget.table.horizontalHeader()
        point = QPoint(header.sectionViewportPosition(2) + header.sectionSize(2) // 2,
                       header.height() // 2)
        QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=point)
        first_order = [widget.table.item(row, 2).text() for row in range(2)]
        QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=point)
        assert [widget.table.item(row, 2).text() for row in range(2)] == first_order[::-1]
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()
