"""Review/edit a diagnostic report, then save or copy it for voluntary sharing."""
from copy import deepcopy
from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QPlainTextEdit, QCheckBox, QPushButton,
                            QFileDialog, QApplication, QFrame, QScrollArea, QWidget)
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
        scroll = QScrollArea(objectName="settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(objectName="settingsContent")
        fields = QVBoxLayout(content)
        fields.addWidget(body("Describe what happened and how to reproduce it. Review the report before sharing. "
                              "Masking can miss sensitive details. Nothing is uploaded automatically."))
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("What did you do? What did you expect? What happened instead?")
        self.description.setAccessibleName("Problem description")
        self.description.setMinimumHeight(100)
        self.description.setMaximumHeight(200)
        description_label = body("What happened?")
        description_label.setBuddy(self.description)
        fields.addWidget(description_label)
        fields.addWidget(self.description)
        self.include = QCheckBox("Include selected incident evidence")
        self.include.setAccessibleDescription("Uses masked incident evidence and excludes your local notes. Review the preview before sharing.")
        self.include.setEnabled(bool(self.incidents))
        fields.addWidget(self.include)
        rebuild = QPushButton("Build report preview (replaces edits)")
        rebuild.clicked.connect(self.rebuild)
        fields.addWidget(rebuild)
        self.preview = QPlainTextEdit()
        self.preview.setAccessibleName("Editable problem report; copied or saved exactly as shown")
        self.preview.setMinimumHeight(220)
        preview_label = body("Review and edit your report")
        preview_label.setBuddy(self.preview)
        fields.addWidget(preview_label)
        fields.addWidget(self.preview, 1)
        self.feedback = body("")
        self.feedback.setAccessibleDescription("Problem report result")
        fields.addWidget(self.feedback)
        copy = QPushButton("Copy reviewed report")
        copy.clicked.connect(self.copy)
        save = QPushButton("Save reviewed report…")
        save.clicked.connect(self.save)
        issue = QPushButton("Open GitHub issue form")
        issue.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/Hiroshinoharu/LogOpen/issues/new/choose")))
        for button in (copy, save, issue):
            fields.addWidget(button)
        fields.addWidget(body("The issue form opens without attaching your report. Paste or attach it yourself only after review."))
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
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
