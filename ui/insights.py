"""Evidence coverage and small incident previews using local data only."""

from datetime import datetime, timezone
from html import escape

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFrame, QLabel,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ui.guidance import problem_explanation, problem_next_steps
from ui.table_layout import RoomyTable, expand_window


class MetricCard(QFrame):
    """Allow summary cards to act like labeled keyboard-accessible filter controls."""

    activated = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


def relative_time(value, now=None):
    """Render an age without discarding the original timestamp used by tooltips and sorting."""
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        stamp = stamp.astimezone() if stamp.tzinfo is None else stamp
        now = now or datetime.now(timezone.utc)
        seconds = (now - stamp).total_seconds()
        if seconds < -60:
            return "In the future"
        if seconds < 60:
            return "Just now"
        if seconds < 3600:
            count, unit = int(seconds // 60), "minute"
        elif seconds < 86400:
            count, unit = int(seconds // 3600), "hour"
        else:
            count, unit = int(seconds // 86400), "day"
        return f"{count} {unit}{'' if count == 1 else 's'} ago"
    except (ValueError, TypeError, OverflowError):
        return "Time unavailable"


class CoverageDialog(QDialog):
    """Show exactly what a saved or current scan checked and why collection stopped."""

    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.setWindowTitle("What was checked?")
        expand_window(self, 1150, 650)
        layout = QVBoxLayout(self)
        scope = getattr(result, "scan_options", {}) or {}
        coverage = getattr(result, "coverage", {}) or {}
        selected_logs = scope.get("log_types") or []
        selected_logs = [selected_logs] if isinstance(selected_logs, str) else selected_logs
        names = list(dict.fromkeys([*selected_logs, *coverage]))
        state = "Cancelled" if getattr(result, "cancelled", False) else (
            "Complete" if names and getattr(result, "complete", False) and all(
                coverage.get(name, {}).get("complete") is True for name in names
            ) else "Partial / unknown")
        heading = QLabel(f"{state} · Lookback: {scope.get('hours', 'unknown')} hours · Entry limit per log: {scope.get('max_records', 'unknown')}")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.table = QTableWidget(len(names), 5)
        self.table.setHorizontalHeaderLabels(["Windows log", "Coverage", "Read / retained", "Why collection stopped", "What to do next"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        reasons = {
            "time_boundary": ("Reached the time window", "The requested time boundary was reached."),
            "end_of_log": ("Reached the end of the log", "All available records in this query were read."),
            "record_limit": ("Entry limit reached", "Choose a shorter time range or raise the entry limit in Scan options."),
            "cancelled": ("Cancelled", "Run another scan to complete this scope."),
            "read_error": ("Log read failed", "Review the scan warnings for the access or read error, then retry."),
            "access_denied": ("Permission required", "Choose File → Restart as administrator, approve Windows UAC, then scan again. If already elevated, ask your administrator about this log's access policy."),
            "collection_error": ("Log unavailable or could not be processed", "Check the scan warnings and read access in Choose Windows logs."),
        }
        for row, name in enumerate(names):
            metadata = coverage.get(name, {})
            reason, action = reasons.get(metadata.get("stop_reason"), ("Not recorded", "Coverage is unknown; run a new scan to establish it."))
            values = (str(name), "Complete" if metadata.get("complete") is True else "Partial / unknown",
                      f"{metadata.get('records_read', '?')} / {metadata.get('matched_events', '?')}", reason, action)
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip("<qt>" + escape(value) + "</qt>")
                self.table.setItem(row, col, item)
        self.roomy = RoomyTable(self.table, [290, 150, 130, 240, 410], flexible=4)
        self.roomy.fit_contents()
        layout.addWidget(self.table)
        notes = QLabel("\n".join(getattr(result, "warnings", [])) or (
            "This report has no coverage metadata. Empty results do not establish a complete scan." if not coverage else
            "Complete describes this scan's selected logs and time window. It does not establish that the whole PC is healthy."))
        notes.setTextFormat(Qt.TextFormat.PlainText)
        notes.setWordWrap(True)
        layout.addWidget(notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class IncidentPreview(QFrame):
    """Summarize the selected occurrence without creating a second AI or review context."""

    open_requested = pyqtSignal()
    expand_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("previewPanel")
        self.setMinimumWidth(250)
        layout = QVBoxLayout(self)
        self.title = QLabel("Select a problem to preview")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setAccessibleName("Selected problem preview")
        layout.addWidget(self.body, 1)
        self.group_button = QPushButton("Show occurrences", objectName="secondaryButton")
        self.group_button.clicked.connect(self.expand_requested.emit)
        layout.addWidget(self.group_button)
        self.open_button = QPushButton("Open full details", objectName="secondaryButton")
        self.open_button.clicked.connect(self.open_requested.emit)
        layout.addWidget(self.open_button)
        self.set_incident(None)

    def set_incident(self, incident, group_count=1, expanded=False):
        """Render literal evidence and local checks, including any unsent personal note."""
        self.open_button.setEnabled(incident is not None)
        self.group_button.setVisible(group_count > 1)
        self.group_button.setText("Collapse occurrences" if expanded else f"Show {group_count} occurrences")
        if incident is None:
            self.title.setText("Select a problem to preview")
            self.body.clear()
            return
        classification = str(incident.get("incident_classification", "Unclassified problem"))
        self.title.setText(classification)
        stamp = incident.get("time_generated_end") or incident.get("time_generated_start")
        parts = [f"{incident.get('incident_priority', 'Unknown')} priority · {relative_time(stamp)}",
                 f"{group_count} occurrence(s) in this view" if group_count > 1 else "",
                 problem_explanation(classification), str(incident.get("summary_text", "")),
                 "Checks you can try", *[f"{index}. {step}" for index, step in enumerate(problem_next_steps(classification), 1)]]
        if incident.get("local_note"):
            parts += ["Your note", str(incident["local_note"])]
        self.body.setPlainText("\n\n".join(part for part in parts if part))
        self.body.setToolTip(f"Exact event time: {stamp}")
