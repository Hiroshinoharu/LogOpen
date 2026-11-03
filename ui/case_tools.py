"""Local explanations, saved checklists, issue history and reviewable support exports."""

from hashlib import sha256
from uuid import uuid4

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                            QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView,
                            QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
                            QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from history_store import incident_key
from models.scan_result import ScanResult
from report_io import _atomic_write
from support_report import redact_support, support_text
from ui.guidance import problem_explanation, problem_next_steps
from ui.history_dialog import ComparisonDialog
from ui.icons import decorate_buttons


def body(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def finish(dialog, layout):
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    decorate_buttons(dialog)


def simple_explanation(incident):
    category = str(incident.get("incident_classification", ""))
    happened = problem_explanation(category)
    if not happened:
        happened = {
            "Application Error": "Windows recorded that an application failed.",
            "Application Hang": "Windows recorded that an application stopped responding.",
            "Windows Error Reporting": "Windows saved a problem report for an app or component.",
            "UDP Ephemeral Port Exhaustion": "Windows reported running out of temporary network ports.",
        }.get(category, "Windows recorded an event matching this problem's detection rule. Read the original message to see what it reports.")
    if any(word in category.lower() for word in ("dns", "wi-fi", "wlan", "udp")):
        symptom = "You might notice a website or app failing to connect, or a dropped connection. This event alone does not show whether that is still happening."
    elif any(word in category.lower() for word in ("application", "hang", "error reporting")):
        symptom = "An app may close, freeze, or delay shutdown. Match the time and app name to what you experienced."
    elif "Update" in category:
        symptom = "An update may need attention. A later attempt may already have succeeded."
    elif any(word in category for word in ("Logon", "Locked")):
        symptom = "A sign-in may have failed or an account may be locked. This does not establish that someone gained access."
    else:
        symptom = "There may be a related symptom, or no visible effect. Match the event time with what you noticed and check the current status."
    return (("What happened", happened), ("What you might notice", symptom),
            ("Best first check", problem_next_steps(category)[0]),
            ("What this cannot tell you", "A log entry is a historical observation. The priority score helps order your review; it does not prove a cause or describe the PC's current health."))


class SimpleExplanationDialog(QDialog):
    def __init__(self, incident, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Explain this simply")
        self.resize(650, 530)
        layout = QVBoxLayout(self)
        layout.addWidget(body("Built-in explanation · works offline · no AI request"))
        for title, text in simple_explanation(incident):
            layout.addWidget(body(title))
            layout.addWidget(body(text))
        finish(self, layout)


class ChecklistDialog(QDialog):
    def __init__(self, incident, store, parent=None):
        super().__init__(parent)
        self.incident, self.store = incident, store
        self.setWindowTitle("Troubleshooting checklist")
        self.resize(760, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(body("Record what you tried and what happened. Progress follows this issue across scans. Checks are never run automatically; completing them does not mark the problem fixed."))
        self.progress = body("")
        layout.addWidget(self.progress)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self.steps_layout = QVBoxLayout(content)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.rows = []
        saved = store.checklist(incident)
        steps = list(saved)
        ids = {step["id"] for step in saved}
        for text in problem_next_steps(incident.get("incident_classification")):
            key = sha256(text.encode()).hexdigest()
            if key not in ids and len(steps) < 50:
                steps.append(dict(id=key, text=text, done=False, result=""))
        for step in steps:
            self.add_step(step)
        custom = QHBoxLayout()
        self.custom = QLineEdit()
        self.custom.setMaxLength(4000)
        self.custom.setPlaceholderText("Add your own check, including a step from AI guidance")
        self.custom.setAccessibleName("New troubleshooting check")
        custom.addWidget(self.custom, 1)
        add = QPushButton("Add check")
        add.clicked.connect(self.add_custom)
        custom.addWidget(add)
        layout.addLayout(custom)
        self.feedback = body("")
        layout.addWidget(self.feedback)
        save = QPushButton("Save progress")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        finish(self, layout)

    def add_step(self, step):
        checked = QCheckBox("Completed")
        checked.setChecked(step["done"])
        checked.setAccessibleName("Completed: " + step["text"])
        self.steps_layout.addWidget(body(step["text"]))
        self.steps_layout.addWidget(checked)
        result = QPlainTextEdit()
        result.setPlaceholderText("What happened? Record an error code, observation, or next action.")
        result.setAccessibleName("Result: " + step["text"])
        result.setMaximumHeight(85)
        result.setPlainText(step["result"])
        self.steps_layout.addWidget(result)
        self.rows.append((step, checked, result))
        checked.toggled.connect(self.update_progress)
        self.update_progress()

    def update_progress(self):
        done = sum(checked.isChecked() for _, checked, _ in self.rows)
        self.progress.setText(f"{done} of {len(self.rows)} checks completed · Save progress to keep changes")

    def add_custom(self):
        text = self.custom.text().strip()
        if text and len(self.rows) < 50:
            self.add_step(dict(id=str(uuid4()), text=text, done=False, result=""))
            self.custom.clear()
        elif len(self.rows) >= 50:
            self.feedback.setText("This issue already has 50 checks.")

    def save(self):
        steps = [dict(id=step["id"], text=step["text"], done=checked.isChecked(), result=result.toPlainText())
                 for step, checked, result in self.rows]
        try:
            self.store.save_checklist(self.incident, steps)
        except (ValueError, RuntimeError) as exc:
            self.feedback.setText(str(exc))
            return
        self.feedback.setText("Progress saved locally. Results are not sent to AI.")


class IssueHistoryDialog(QDialog):
    def __init__(self, incident, store, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Incident history")
        self.resize(950, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(body("This issue's observations, notes, review decisions and checks. Observations search the latest 500 scans; the latest 500 actions are shown. Overlapping scans can contain the same events. Action history starts when this feature is installed."))
        entries = store.issue_timeline(incident)
        self.table = QTableWidget(len(entries), 3)
        self.table.setHorizontalHeaderLabels(["Time", "Activity", "Details"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 170)
        for row, entry in enumerate(entries):
            for column, field in enumerate(("created_at", "kind", "detail")):
                self.table.setItem(row, column, QTableWidgetItem(str(entry[field])))
        layout.addWidget(self.table, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(170)
        self.table.itemSelectionChanged.connect(lambda: self.detail.setPlainText(
            self.table.item(self.table.currentRow(), 2).text() if self.table.currentRow() >= 0 else ""))
        layout.addWidget(self.detail)
        if entries:
            self.table.setCurrentCell(0, 0)
        else:
            self.detail.setPlainText("No saved observations or actions for this issue yet.")
        finish(self, layout)


class CompareScansDialog(QDialog):
    def __init__(self, result, store, parent=None):
        super().__init__(parent)
        self.result, self.store = result, store
        self.setWindowTitle("Compare scans")
        self.resize(720, 260)
        layout = QVBoxLayout(self)
        layout.addWidget(body(f"Current scan: {result.created_at or 'Unknown time'}. Choose an earlier baseline from the latest 100 saved scans. Only complete scans with matching scope can show no longer detected problems."))
        self.baseline = QComboBox()
        self.baseline.setAccessibleName("Comparison baseline scan")
        for scan in store.list_scans():
            if scan["id"] != result.scan_id and (result.created_at is None or scan["created_at"].timestamp() < result.created_at.timestamp()):
                self.baseline.addItem(f"{scan['created_at'].astimezone():%Y-%m-%d %H:%M:%S} · {', '.join(scan['log_types'])} · {scan['hours']}h · {'Complete' if scan['complete'] else 'Partial'}", scan["id"])
        layout.addWidget(self.baseline)
        self.feedback = body("No earlier saved scans available." if not self.baseline.count() else "")
        layout.addWidget(self.feedback)
        compare = QPushButton("Compare with this scan")
        compare.setEnabled(self.baseline.count() > 0)
        compare.clicked.connect(self.compare)
        layout.addWidget(compare)
        finish(self, layout)

    def compare(self):
        # Do not rewrite the displayed/saved automatic comparison when exploring.
        snapshot = ScanResult(self.result)
        snapshot.__dict__.update(self.result.__dict__)
        try:
            comparison = self.store.compare_scan(snapshot, previous_scan_id=self.baseline.currentData())
        except RuntimeError as exc:
            self.feedback.setText(str(exc))
            return
        ComparisonDialog(comparison, self).exec()


class SupportReportDialog(QDialog):
    def __init__(self, incidents, result, store, parent=None):
        super().__init__(parent)
        self.incidents, self.result = incidents, result
        self.checklists = {incident_key(item): store.checklist(item) for item in incidents}
        self.setWindowTitle("Prepare support report")
        self.resize(900, 720)
        layout = QVBoxLayout(self)
        layout.addWidget(body("Review and edit the exact text before saving. Common names and paths can be masked; automatic masking may miss details in log messages. Nothing is sent or uploaded."))
        options = QHBoxLayout()
        self.mask_user = QCheckBox("Mask usernames / emails")
        self.mask_computer = QCheckBox("Mask computer names")
        self.mask_paths = QCheckBox("Mask file paths")
        self.notes = QCheckBox("Include local note")
        for checkbox in (self.mask_user, self.mask_computer, self.mask_paths):
            checkbox.setChecked(True)
        for checkbox in (self.mask_user, self.mask_computer, self.mask_paths, self.notes):
            options.addWidget(checkbox)
        layout.addLayout(options)
        self.extra = QLineEdit()
        self.extra.setPlaceholderText("Additional text to remove (separate exact values with semicolons)")
        self.extra.setAccessibleName("Additional sensitive text to mask")
        layout.addWidget(self.extra)
        regenerate = QPushButton("Rebuild preview with these options (replaces edits)")
        regenerate.clicked.connect(self.rebuild)
        layout.addWidget(regenerate)
        self.preview = QPlainTextEdit()
        self.preview.setAccessibleName("Editable support report preview; saved exactly as shown")
        layout.addWidget(self.preview, 1)
        self.feedback = body("Troubleshooting results are included. Review them for personal details too.")
        layout.addWidget(self.feedback)
        save = QPushButton("Save reviewed report…")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        finish(self, layout)
        self.rebuild()

    def rebuild(self):
        source = support_text(self.incidents, self.result, self.checklists, include_notes=self.notes.isChecked())
        self.preview.setPlainText(redact_support(source, self.incidents, usernames=self.mask_user.isChecked(),
                                 computers=self.mask_computer.isChecked(), paths=self.mask_paths.isChecked(),
                                 extra=self.extra.text().split(";")))

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save reviewed support report", "LogOpen-support.txt", "Text report (*.txt)")
        if not path:
            return
        try:
            _atomic_write(path, self.preview.toPlainText())
        except OSError:
            self.feedback.setText("Could not save the report. Check the destination and try again.")
            return
        self.feedback.setText("Reviewed report saved. Nothing was sent or uploaded.")
