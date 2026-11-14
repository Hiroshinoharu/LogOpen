"""Dialogs for scan scope and evidence filters; no collection occurs here."""

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QCheckBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout, QComboBox, QPushButton,
    QScrollArea, QWidget, QFrame,
)

from models.scan_options import ScanOptions
from detection_rules import PROFILES, SCAN_GOALS
from ui.channel_browser import ChannelBrowser
from ui.rule_editor import RuleEditorDialog
from ui.guidance import DisclosureSection
from ui.icons import decorate_buttons, icon


class ScanOptionsDialog(QDialog):
    """Validate a requested time window, source logs, and per-log record limit."""

    def __init__(self, options, parent=None):
        """Populate editable controls from a validated ScanOptions snapshot."""
        super().__init__(parent)
        self.setWindowTitle("Scan options")
        self.setMinimumSize(420, 360)
        self.resize(580, 560)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(objectName="settingsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(objectName="settingsContent")
        fields = QVBoxLayout(content)
        fields.setSpacing(14)
        title = QLabel("Choose what to scan", objectName="sectionTitle")
        fields.addWidget(title)
        intro = QLabel("The defaults are a good place to start. Look further back if your problem happened earlier.")
        intro.setWordWrap(True)
        fields.addWidget(intro)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.rules_json = options.rules_json
        self.profile = QComboBox()
        self.profile.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.profile.setMinimumContentsLength(12)
        self.profile.setAccessibleName("What to check")
        for key, (label, _channels) in PROFILES.items():
            self.profile.addItem(icon("scan"), label, key)
        self.profile.setCurrentIndex(self.profile.findData(options.profile))
        self.profile.currentIndexChanged.connect(self._apply_profile)
        form.addRow("&What to check", self.profile)
        self.profile_hint = QLabel(SCAN_GOALS[options.profile][1], objectName="mutedLabel")
        self.profile_hint.setWordWrap(True)
        form.addRow(self.profile_hint)
        self.logs = QLineEdit(", ".join(options.log_types))
        self.logs.setCursorPosition(0)
        self.logs.setPlaceholderText("System, Application")
        self.logs.setAccessibleName("Windows event logs, separated by commas")
        self.logs.setAccessibleDescription("Choose logs with the browser or enter their names separated by commas.")
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
        self.limit.setAccessibleName("Maximum entries per Windows log")
        self.advanced = DisclosureSection("Advanced scan options")
        advanced_form = QFormLayout()
        advanced_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        advanced_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        advanced_form.addRow("Windows &logs", self.logs)
        source_buttons = QVBoxLayout()
        self.browse_button = QPushButton("Choose Windows logs…")
        self.browse_button.setIcon(icon("folder"))
        self.browse_button.clicked.connect(self._browse_channels)
        source_buttons.addWidget(self.browse_button)
        self.rules_button = QPushButton("Detection rules…")
        self.rules_button.setIcon(icon("settings"))
        self.rules_button.clicked.connect(self._edit_rules)
        source_buttons.addWidget(self.rules_button)
        advanced_form.addRow(source_buttons)
        form.addRow("How far &back", self.hours)
        advanced_form.addRow("Maximum &entries per log", self.limit)
        self.advanced.body.addLayout(advanced_form)
        self.automatic_ai = QCheckBox("Add optional AI explanations")
        self.automatic_ai.setAccessibleDescription("Available when AI is enabled in Settings and automatic sharing is allowed.")
        self.automatic_ai.setChecked(options.automatic_ai)
        form.addRow(self.automatic_ai)
        fields.addLayout(form)
        ai_note = QLabel("AI is optional and uses your OpenAI key. API charges may apply. The Windows log scan works without AI.", objectName="mutedLabel")
        ai_note.setWordWrap(True)
        fields.addWidget(ai_note)
        note = QLabel("These options are for experienced users. Some logs need administrator access or must be enabled in Windows. "
                      "If an entry limit is reached, the results will be marked incomplete.")
        note.setWordWrap(True)
        self.advanced.body.addWidget(note)
        fields.addWidget(self.advanced)
        fields.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        self.error = QLabel(objectName="settingsMessage")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setAccessibleDescription("Scan options error")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.options = options
        decorate_buttons(self)

    def _apply_profile(self):
        """Populate suggested channels; custom profiles keep the user's current list."""
        self.profile_hint.setText(SCAN_GOALS[self.profile.currentData()][1])
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
                                       rules_json=self.rules_json, automatic_ai=self.automatic_ai.isChecked(),
                                       collect_all_events=self.options.collect_all_events)
        except ValueError as exc:
            self.advanced.set_expanded(True)
            self.error.setText(str(exc))
            self.logs.setAccessibleDescription(str(exc))
            self.logs.setFocus()
            self.logs.selectAll()
            self.scroll.ensureWidgetVisible(self.logs)
            return
        super().accept()


class FilterDialog(QDialog):
    """Edit provider/classification text filters and an optional inclusive date range."""

    def __init__(self, values, parent=None):
        """Show existing filters; accepting returns them through values()."""
        super().__init__(parent)
        self.setWindowTitle("More filters")
        self.setMinimumSize(420, 320)
        self.resize(580, 460)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(objectName="settingsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget(objectName="settingsContent")
        fields = QVBoxLayout(content)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.provider = QLineEdit(values.get("provider", ""))
        self.provider.setAccessibleName("Provider contains")
        self.classification = QLineEdit(values.get("classification", ""))
        self.classification.setAccessibleName("Classification contains")
        self.dates = QCheckBox("Filter by start date")
        self.dates.setAccessibleDescription("Show incidents whose start date is within the inclusive date range below.")
        self.dates.setChecked(values.get("dates", False))
        self.start = QDateEdit(values.get("start", QDate.currentDate().addDays(-7)))
        self.end = QDateEdit(values.get("end", QDate.currentDate()))
        self.start.setAccessibleName("Incident start date from")
        self.end.setAccessibleName("Incident start date through")
        for control in (self.start, self.end):
            control.setCalendarPopup(True)
            control.setDisplayFormat("yyyy-MM-dd")
            control.setEnabled(self.dates.isChecked())
            self.dates.toggled.connect(control.setEnabled)
        form.addRow("&Provider contains", self.provider)
        form.addRow("&Classification contains", self.classification)
        form.addRow(self.dates)
        form.addRow("&From", self.start)
        form.addRow("&Through", self.end)
        fields.addLayout(form)
        self.error = QLabel(objectName="settingsMessage")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.setAccessibleDescription("Filter error")
        fields.addWidget(self.error)
        fields.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel |
                                   QDialogButtonBox.StandardButton.Reset)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(self.reset)
        layout.addWidget(buttons)
        decorate_buttons(self)

    def reset(self):
        """Clear optional text/date filters without closing the dialog."""
        self.provider.clear()
        self.classification.clear()
        self.dates.setChecked(False)
        self.error.clear()

    def values(self):
        """Return the selected text and QDate bounds for local filtering."""
        return dict(provider=self.provider.text().strip(),
                    classification=self.classification.text().strip(),
                    dates=self.dates.isChecked(), start=self.start.date(), end=self.end.date())

    def accept(self):
        """Reject inverted date ranges rather than silently hiding all results."""
        if self.dates.isChecked() and self.start.date() > self.end.date():
            self.error.setText("The start date must be on or before the end date.")
            self.start.setAccessibleDescription(self.error.text())
            self.start.setFocus()
            self.scroll.ensureWidgetVisible(self.start)
            return
        super().accept()
