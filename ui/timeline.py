"""Chronological source-evidence views across incidents and Windows channels."""

import json
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView,
                            QLabel, QLineEdit, QPlainTextEdit, QSplitter, QTableWidget,
                            QVBoxLayout, QWidget)
from ui.table_sorting import SortableTableItem, numeric_key, timestamp_key
from ui.table_layout import RoomyTable, expand_window


class TimelineWidget(QWidget):
    """Browse ordered source events while preserving channel and incident provenance."""

    def __init__(self, parent=None):
        """Build a searchable, sortable timeline and literal evidence reader."""
        super().__init__(parent)
        self.entries = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel("Events ordered by timestamp across channels. Time proximity alone does not establish a cause. "
                      "Times below use this computer's local timezone; original timestamps are in the evidence pane.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter timeline by channel, provider, incident, or message…")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Time", "Gap (s)", "Channel", "Provider", "Event ID", "Windows level", "Incident"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(2, 180)
        self.table.setColumnWidth(3, 180)
        self.roomy_table = RoomyTable(self.table, [280, 110, 460, 350, 110, 150, 320], flexible=2)
        self.table.itemSelectionChanged.connect(self._show_evidence)
        self.table.model().layoutChanged.connect(self._filter)
        splitter.addWidget(self.table)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setAccessibleName("Timeline source evidence")
        splitter.addWidget(self.evidence)
        splitter.setSizes([350, 200])
        layout.addWidget(splitter, 1)

    def set_incidents(self, incidents):
        """Flatten retained evidence and sort by instant, keeping stable source objects.

        Gaps are measured between chronological events in the supplied dataset,
        including entries later hidden by filtering. Missing timestamps sort first.
        """
        self.entries = [(event, incident.get("incident_classification", "Unclassified"))
                        for incident in incidents for event in (incident.get("events") or []) if isinstance(event, dict)]
        self.entries.sort(key=lambda pair: timestamp_key(pair[0].get("time_generated")))
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.entries))
        previous = None
        for row, (event, label) in enumerate(self.entries):
            stamp = event.get("time_generated")
            key = timestamp_key(stamp)
            gap = key[1] - previous if previous is not None and key[0] else 0
            if key[0]:
                previous = key[1]
            displayed = stamp.astimezone().isoformat(sep=" ", timespec="milliseconds") if hasattr(stamp, "astimezone") else str(stamp or "Unknown")
            values = [(displayed, key), (f"{gap:.3f}", numeric_key(gap)),
                      (str(event.get("log_type", "")), str(event.get("log_type", "")).casefold()),
                      (str(event.get("provider", "")), str(event.get("provider", "")).casefold()),
                      (str(event.get("event_id", "")), numeric_key(event.get("event_id"))),
                      (str(event.get("level", "Unknown")), str(event.get("level", "Unknown"))), (label, label.casefold())]
            for col, (text, sort_key) in enumerate(values):
                item = SortableTableItem(text, sort_key)
                item.setData(Qt.ItemDataRole.UserRole, row)
                item.setToolTip(text)
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        self.table.setSortingEnabled(True)
        self.roomy_table.fit_contents()
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.evidence.clear()
        self._filter()
        if self.entries:
            self.table.selectRow(0)

    def _show_evidence(self):
        """Resolve sorted rows to their original evidence; render payload as plain text."""
        row = self.table.currentRow()
        if row < 0 or self.table.item(row, 0) is None:
            self.evidence.clear()
            return
        index = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        event, label = self.entries[index]
        self.evidence.setPlainText(label + "\n\n" + json.dumps(event, ensure_ascii=False, indent=2, default=str))

    def _filter(self):
        """Match literal evidence text without confusing row identity after sorting."""
        query = self.search.text().casefold()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                pair = self.entries[item.data(Qt.ItemDataRole.UserRole)]
                self.table.setRowHidden(row, query not in str(pair).casefold())


class TimelineDialog(QDialog):
    """Show all supplied incidents' evidence in one timeline without rescanning."""

    def __init__(self, incidents, parent=None):
        """Snapshot the visible incident selection into a resizable evidence window."""
        super().__init__(parent)
        self.setWindowTitle("Incident timeline")
        expand_window(self, 1700, 1000)
        layout = QVBoxLayout(self)
        self.timeline = TimelineWidget()
        self.timeline.set_incidents(incidents)
        layout.addWidget(self.timeline)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
