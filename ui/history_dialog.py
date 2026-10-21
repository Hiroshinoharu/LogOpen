"""Browse saved scans and inspect evidence-based comparisons without rescanning."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView,
    QLabel, QTableWidget, QVBoxLayout,
)

from ui.table_sorting import SortableTableItem, numeric_key, timestamp_key
from ui.table_layout import RoomyTable, expand_window
from ui.icons import decorate_buttons


class HistoryDialog(QDialog):
    """Choose a persisted scan by timestamp, scope, and coverage status."""

    def __init__(self, scans, parent=None):
        """Render history rows using IDs that stay attached when sorted."""
        super().__init__(parent)
        self.setWindowTitle("Scan history")
        expand_window(self, 1500, 900)
        self.scan_id = None
        layout = QVBoxLayout(self)
        hint = QLabel("Open an earlier scan to inspect its evidence and comparison. "
                      "Partial scans are retained, but cannot establish that an issue disappeared.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(len(scans), 4)
        self.table.setHorizontalHeaderLabels(["Saved", "Scope", "Incidents", "Coverage"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, scan in enumerate(scans):
            values = (str(scan["created_at"]),
                      f"{', '.join(scan.get('log_types') or [])} · {scan.get('hours', '?')}h",
                      str(scan["incident_count"]), "Complete" if scan["complete"] else "Incomplete")
            keys = (timestamp_key(scan["created_at"]), values[1].casefold(),
                    numeric_key(scan["incident_count"]), values[3])
            for column, (value, key) in enumerate(zip(values, keys)):
                item = SortableTableItem(value, key)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, scan["id"])
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.roomy_table = RoomyTable(self.table, [280, 650, 120, 150], flexible=1)
        self.roomy_table.fit_contents()
        self.table.sortItems(0, Qt.SortOrder.DescendingOrder)
        self.table.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.table)
        self.message = QLabel("No saved scans yet." if not scans else "")
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if scans:
            self.table.setCurrentCell(0, 0)
        decorate_buttons(self)

    def accept(self, *_args):
        """Return the selected database ID; ignore Open when no row is selected."""
        row = self.table.currentRow()
        if row < 0:
            return
        self.scan_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        super().accept()


class ComparisonDialog(QDialog):
    """Show new, continuing, and no-longer-observed issues from comparable scans."""

    def __init__(self, comparison, parent=None):
        """Render issue lists; absence is described as observation, not proof of repair."""
        super().__init__(parent)
        self.setWindowTitle("Scan comparison")
        expand_window(self, 1400, 850)
        layout = QVBoxLayout(self)
        text = ("Compared with the previous complete scan of the same scope. "
                "No longer observed means absent from this scan; it does not prove a fix."
                if comparison.get("comparable") else
                "No comparable complete baseline is available. Run another complete scan "
                "using the same logs and time window to compare changes.")
        hint = QLabel(text)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        entries = [(label, incident) for key, label in (
            ("new", "New"), ("persistent", "Persistent"),
            ("no_longer_observed", "No longer observed"),
        ) for incident in comparison.get(key, [])]
        table = QTableWidget(len(entries), 4)
        table.setHorizontalHeaderLabels(["Change", "Classification", "Computer", "Priority"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, (label, incident) in enumerate(entries):
            for column, value in enumerate((label, incident.get("incident_classification", "Unknown"),
                                             incident.get("computer_name", "Unknown"),
                                             incident.get("incident_priority", "Unknown"))):
                table.setItem(row, column, SortableTableItem(str(value), str(value).casefold()))
        table.setSortingEnabled(True)
        self.roomy_table = RoomyTable(table, [190, 500, 280, 130], flexible=1)
        self.roomy_table.fit_contents()
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        decorate_buttons(self)
