"""Review/edit a diagnostic report, then save or copy it for voluntary sharing."""
from copy import deepcopy
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit, QCheckBox, QPushButton, QFileDialog, QApplication
from feedback_report import problem_report
from report_io import _atomic_write
from ui.case_tools import body, finish


class FeedbackDialog(QDialog):
    def __init__(self, status="", incidents=(), parent=None):
        super().__init__(parent)
        self.status, self.incidents = status, deepcopy(list(incidents))
        self.setWindowTitle("Report a LogOpen problem")
        self.resize(760, 700)
        layout = QVBoxLayout(self)
        layout.addWidget(body("Describe what happened and how to reproduce it. Review the report before sharing. "
                              "Masking can miss sensitive details. Nothing is uploaded automatically."))
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("What did you do? What did you expect? What happened instead?")
        self.description.setAccessibleName("Problem description")
        self.description.setMaximumHeight(100)
        layout.addWidget(self.description)
        self.include = QCheckBox("Include selected incident evidence (masked; excludes local notes)")
        self.include.setEnabled(bool(self.incidents))
        layout.addWidget(self.include)
        rebuild = QPushButton("Build report preview (replaces edits)")
        rebuild.clicked.connect(self.rebuild)
        layout.addWidget(rebuild)
        self.preview = QPlainTextEdit()
        self.preview.setAccessibleName("Editable problem report; copied or saved exactly as shown")
        layout.addWidget(self.preview, 1)
        self.feedback = body("")
        layout.addWidget(self.feedback)
        copy = QPushButton("Copy reviewed report")
        copy.clicked.connect(self.copy)
        save = QPushButton("Save reviewed report…")
        save.clicked.connect(self.save)
        issue = QPushButton("Open GitHub issue form")
        issue.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/Hiroshinoharu/LogOpen/issues/new/choose")))
        for button in (copy, save, issue):
            layout.addWidget(button)
        layout.addWidget(body("The issue form opens without attaching your report. Paste or attach it yourself only after review."))
        finish(self, layout)
        self.rebuild()

    def rebuild(self):
        self.preview.setPlainText(problem_report(self.description.toPlainText(), self.status, self.incidents,
                                                include_incidents=self.include.isChecked()))
        self.feedback.setText("Review the generated text below before copying or saving.")

    def copy(self):
        QApplication.clipboard().setText(self.preview.toPlainText())
        self.feedback.setText("Reviewed report copied to the clipboard. Nothing was uploaded.")

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save reviewed problem report", "LogOpen-problem.txt", "Text report (*.txt)")
        if not path:
            return
        try:
            _atomic_write(path, self.preview.toPlainText())
            self.feedback.setText("Reviewed report saved locally.")
        except OSError:
            self.feedback.setText("Could not save the report. Check the selected location.")
