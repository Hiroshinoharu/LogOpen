"""Dialogs for scan scope and evidence filters; no collection occurs here."""

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import (
    QCheckBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout, QComboBox, QPushButton, QHBoxLayout,
)

from models.scan_options import ScanOptions
from detection_rules import PROFILES
from ui.channel_browser import ChannelBrowser
from ui.rule_editor import RuleEditorDialog


class ScanOptionsDialog(QDialog):
    """Validate a requested time window, source logs, and per-log record limit."""

    def __init__(self, options, parent=None):
        """Populate editable controls from a validated ScanOptions snapshot."""
        super().__init__(parent)
        self.setWindowTitle("Scan scope")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.rules_json = options.rules_json
        self.profile = QComboBox()
        for key, (label, _channels) in PROFILES.items():
            self.profile.addItem(label, key)
        self.profile.setCurrentIndex(self.profile.findData(options.profile))
        self.profile.currentIndexChanged.connect(self._apply_profile)
        form.addRow("Profile", self.profile)
        self.logs = QLineEdit(", ".join(options.log_types))
        self.logs.setCursorPosition(0)
        self.logs.setPlaceholderText("System, Application")
        self.logs.setAccessibleName("Windows event logs, separated by commas")
        self.logs.textEdited.connect(self._custom_profile)
        self.hours = QDoubleSpinBox()
        self.hours.setRange(0.1, 24 * 365)
        self.hours.setDecimals(1)
        self.hours.setSuffix(" hours")
        self.hours.setValue(options.hours)
        self.hours.setAccessibleName("Scan lookback in hours")
        self.limit = QSpinBox()
        self.limit.setRange(100, 10_000_000)
        self.limit.setSingleStep(10_000)
        self.limit.setValue(options.max_records)
        form.addRow("Logs", self.logs)
        source_buttons = QHBoxLayout()
        self.browse_button = QPushButton("Browse channels…")
        self.browse_button.clicked.connect(self._browse_channels)
        source_buttons.addWidget(self.browse_button)
        self.rules_button = QPushButton("Detection rules…")
        self.rules_button.clicked.connect(self._edit_rules)
        source_buttons.addWidget(self.rules_button)
        form.addRow(source_buttons)
        form.addRow("Look back", self.hours)
        form.addRow("Record limit per log", self.limit)
        self.automatic_ai = QCheckBox("Allow automatic AI using AI Settings")
        self.automatic_ai.setChecked(options.automatic_ai)
        form.addRow(self.automatic_ai)
        layout.addLayout(form)
        note = QLabel("Choose classic or operational channels. Presets may include logs that are disabled or require additional permissions; "
                      "use Browse channels to check access. Scans include warnings/errors, audit failures, and events matching detection rules. "
                      "Reaching the record limit marks coverage incomplete.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.options = options

    def _apply_profile(self):
        """Populate suggested channels; custom profiles keep the user's current list."""
        names = PROFILES[self.profile.currentData()][1]
        if names:
            self.logs.setText(", ".join(names))
            self.logs.setCursorPosition(0)

    def _custom_profile(self):
        """Mark edited channel selections custom without overwriting their names."""
        self.profile.setCurrentIndex(self.profile.findData("custom"))

    def _browse_channels(self):
        """Apply checked channel names after the browser's worker has stopped."""
        dialog = ChannelBrowser(tuple(name.strip() for name in self.logs.text().split(",") if name.strip()), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.logs.setText(", ".join(dialog.names))
            self.logs.setCursorPosition(0)
            self._custom_profile()

    def _edit_rules(self):
        """Stage validated rules; the outer dialog's Save commits the scan snapshot."""
        dialog = RuleEditorDialog(self.rules_json, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.rules_json = dialog.rules_json

    def accept(self):
        """Accept only valid, deduplicated log names and numeric scan bounds."""
        try:
            names = tuple(dict.fromkeys(name.strip() for name in self.logs.text().split(",") if name.strip()))
            self.options = ScanOptions(log_types=names, hours=self.hours.value(),
                                       max_records=self.limit.value(), profile=self.profile.currentData(),
                                       rules_json=self.rules_json, automatic_ai=self.automatic_ai.isChecked())
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        super().accept()


class FilterDialog(QDialog):
    """Edit provider/classification text filters and an optional inclusive date range."""

    def __init__(self, values, parent=None):
        """Show existing filters; accepting returns them through values()."""
        super().__init__(parent)
        self.setWindowTitle("More filters")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.provider = QLineEdit(values.get("provider", ""))
        self.classification = QLineEdit(values.get("classification", ""))
        self.dates = QCheckBox("Filter by incident start date")
        self.dates.setChecked(values.get("dates", False))
        self.start = QDateEdit(values.get("start", QDate.currentDate().addDays(-7)))
        self.end = QDateEdit(values.get("end", QDate.currentDate()))
        for control in (self.start, self.end):
            control.setCalendarPopup(True)
            control.setDisplayFormat("yyyy-MM-dd")
            control.setEnabled(self.dates.isChecked())
            self.dates.toggled.connect(control.setEnabled)
        form.addRow("Provider contains", self.provider)
        form.addRow("Classification contains", self.classification)
        form.addRow(self.dates)
        form.addRow("From", self.start)
        form.addRow("Through", self.end)
        layout.addLayout(form)
        self.error = QLabel()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel |
                                   QDialogButtonBox.StandardButton.Reset)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(self.reset)
        layout.addWidget(buttons)

    def reset(self):
        """Clear optional text/date filters without closing the dialog."""
        self.provider.clear()
        self.classification.clear()
        self.dates.setChecked(False)

    def values(self):
        """Return the selected text and QDate bounds for local filtering."""
        return dict(provider=self.provider.text().strip(),
                    classification=self.classification.text().strip(),
                    dates=self.dates.isChecked(), start=self.start.date(), end=self.end.date())

    def accept(self):
        """Reject inverted date ranges rather than silently hiding all results."""
        if self.dates.isChecked() and self.start.date() > self.end.date():
            self.error.setText("The start date must be on or before the end date.")
            return
        super().accept()
