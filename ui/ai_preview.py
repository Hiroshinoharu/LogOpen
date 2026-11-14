"""Review a frozen AI request locally before explicitly sending it."""
from copy import deepcopy
import json
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QCheckBox, QLineEdit, QPlainTextEdit,
                            QTabWidget, QDialogButtonBox, QFrame, QScrollArea, QWidget)
from llm_analysis import prepare_ai_request
from models.incident_analysis import IncidentAnalysis
from settings.privacy import PrivacyPreferences
from settings.model_catalog import model_tooltip
from ui.case_tools import body


class AIPrivacyPreview(QDialog):
    def __init__(self, incident, preferences, scan_context=None, parent=None):
        super().__init__(parent)
        self.incident, self.scan_context = deepcopy(incident), deepcopy(scan_context)
        self.request = None
        self.setWindowTitle("Review information sent to AI")
        self.resize(820, 680)
        layout = QVBoxLayout(self)
        scroll = QScrollArea(objectName="settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(objectName="settingsContent")
        fields = QVBoxLayout(content)
        fields.addWidget(body(f"Destination: OpenAI API · Model: {preferences.model}\n{model_tooltip(preferences.model)}"))
        fields.addWidget(body("Review the exact request text below. Masking is best effort and can miss personal details. "
                              "Your original logs and notes are unchanged. Sending may incur API charges. "
                              "Response storage is disabled; provider retention policies still apply."))
        self.mask = QCheckBox("Mask common personal details")
        self.mask.setAccessibleDescription("Mask usernames, emails, computer names and file paths. Review the preview because masking can miss sensitive details.")
        self.mask.setChecked(PrivacyPreferences().load().mask_identifiers)
        fields.addWidget(self.mask)
        self.extra = QLineEdit()
        self.extra.setPlaceholderText("Additional exact values to mask, separated by semicolons")
        self.extra.setAccessibleName("Additional sensitive values to mask for this request")
        extra_label = body("Additional exact values to mask (separate with semicolons)")
        extra_label.setBuddy(self.extra)
        fields.addWidget(extra_label)
        fields.addWidget(self.extra)
        tabs = QTabWidget(objectName="detailTabs")
        tabs.setAccessibleName("AI request preview sections")
        tabs.setMinimumHeight(240)
        self.preview = QPlainTextEdit()
        self.instructions = QPlainTextEdit()
        self.schema = QPlainTextEdit(json.dumps(IncidentAnalysis.model_json_schema(), indent=2))
        for title, widget in (("Incident information", self.preview), ("AI instructions", self.instructions), ("Response format", self.schema)):
            widget.setReadOnly(True)
            widget.setAccessibleName(title)
            tabs.addTab(widget, title)
        fields.addWidget(tabs, 1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Send reviewed request")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.mask.toggled.connect(self.rebuild)
        self.extra.textChanged.connect(self.rebuild)
        self.rebuild()

    def rebuild(self):
        try:
            self.request = prepare_ai_request(self.incident, scan_context=self.scan_context,
                                               mask_identifiers=self.mask.isChecked(), extra=self.extra.text().split(";"))
            self.preview.setPlainText(self.request["input"])
            self.instructions.setPlainText(self.request["instructions"])
        except Exception:
            self.request = None
            self.preview.setPlainText("Could not prepare the request. Nothing will be sent.")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.request is not None)
