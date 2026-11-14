"""Explicit sharing consent and scoped, confirmed local data removal."""
from dataclasses import replace
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QCheckBox, QPushButton, QMessageBox, QScrollArea, QWidget
from settings.privacy import PrivacyPreferences, PrivacyOptions
from settings.app_settings import AppSettings
from settings.credential_store import delete_openai_api_key
from ui.case_tools import body, finish

PRIVACY_TEXT = (
    "Scans read Windows event logs. History, AI results, notes and checks are stored locally in LogOpen's "
    "history database. Saved API keys use Windows Credential Manager.\n\n"
    "Manual AI requests show a preview before sending. Automatic AI sends selected incident summaries, "
    "event messages and scan coverage to OpenAI using your key, without a preview for each incident. "
    "Local notes are excluded. Masking can miss sensitive details. AI charges may apply. "
    "LogOpen requests no stored response, but OpenAI's retention and caching policies still apply.\n\n"
    "Opening Settings with a key checks model availability at OpenAI without sending incidents. "
    "Update checks/downloads contact GitHub. LogOpen does not automatically upload crash or support reports.\n\n"
    "Uninstall preserves local data and credentials. Clearing history below removes its scans, notes, "
    "review states and checklists. Reports you exported or imported, including reports/incidents.json, "
    "remain at their chosen locations; remove those separately. Windows event logs are never deleted."
)


class PrivacyDialog(QDialog):
    history_cleared = pyqtSignal()
    ai_disabled = pyqtSignal()

    def __init__(self, store, busy, parent=None):
        super().__init__(parent)
        self.store, self.busy = store, busy
        self.preferences = PrivacyPreferences()
        options = self.preferences.load()
        self.setWindowTitle("Privacy and local data")
        self.resize(650, 740)
        layout = QVBoxLayout(self)
        scroll = QScrollArea(objectName="settingsScroll")
        scroll.setWidgetResizable(True)
        content = QWidget(objectName="settingsContent")
        fields = QVBoxLayout(content)
        fields.addWidget(body(PRIVACY_TEXT))
        fields.addWidget(body(f"History location: {store.path}"))
        self.mask = QCheckBox("Mask personal details by default")
        self.mask.setAccessibleDescription("Mask common identifiers in AI requests. Masking can miss sensitive details.")
        self.mask.setChecked(options.mask_identifiers)
        self.automatic = QCheckBox("Allow automatic AI sharing")
        self.automatic.setAccessibleDescription("Send selected incident details without showing an individual preview. API charges may apply.")
        self.automatic.setChecked(options.automatic_sharing)
        fields.addWidget(self.mask)
        fields.addWidget(self.automatic)
        fields.addWidget(body("Automatic sharing sends selected incident details without an individual preview. API charges may apply."))
        save = QPushButton("Save privacy preferences")
        save.clicked.connect(self.save_preferences)
        fields.addWidget(save)
        self.clear_history = QCheckBox("Delete local history")
        self.clear_history.setAccessibleDescription("Delete all local scan history, notes and checks. Exported reports are kept.")
        self.clear_key = QCheckBox("Remove saved API key")
        self.clear_key.setAccessibleDescription("Remove LogOpen's saved API key and disable AI. Environment keys are unchanged.")
        fields.addWidget(self.clear_history)
        fields.addWidget(body("Deletes all local scan history, notes and checks. Exported reports stay in their chosen locations."))
        fields.addWidget(self.clear_key)
        fields.addWidget(body("Removes LogOpen's saved API key and disables AI. Environment keys are unchanged."))
        self.clear_button = QPushButton("Delete selected data…")
        self.clear_button.clicked.connect(self.delete_selected)
        fields.addWidget(self.clear_button)
        self.feedback = body("")
        self.feedback.setAccessibleDescription("Privacy preferences and data removal result")
        fields.addWidget(self.feedback)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        finish(self, layout)

    def save_preferences(self):
        try:
            self.preferences.save(PrivacyOptions(self.mask.isChecked(), self.automatic.isChecked()))
        except RuntimeError:
            self.feedback.setText("Could not save privacy preferences.")
            return
        self.feedback.setText("Privacy preferences saved for future requests. Requests already in progress are unchanged.")

    def delete_selected(self):
        if self.busy():
            self.feedback.setText("Stop monitoring and wait for scans or AI requests to finish before deleting data.")
            return
        history, key = self.clear_history.isChecked(), self.clear_key.isChecked()
        if not (history or key):
            self.feedback.setText("Select the data you want to delete.")
            return
        names = [label for selected, label in ((history, "all local history, notes and checks"), (key, "the saved API key (and disable AI)")) if selected]
        if QMessageBox.question(self, "Confirm deletion", "Delete " + " and ".join(names) + "? This cannot be undone.",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        if self.busy():
            self.feedback.setText("A task started. Stop monitoring and wait before deleting data.")
            return
        messages = []
        if history:
            try:
                self.store.clear_all()
                self.history_cleared.emit()
                messages.append("Local history deleted. Exported reports remain on disk.")
                self.clear_history.setChecked(False)
            except RuntimeError:
                messages.append("Could not delete local history.")
        if key:
            try:
                settings = AppSettings()
                settings.save(replace(settings.load(), enabled=False))
                self.ai_disabled.emit()
                self.preferences.save(PrivacyOptions(self.mask.isChecked(), False))
                self.automatic.setChecked(False)
                delete_openai_api_key()
                self.clear_key.setChecked(False)
                messages.append("Saved key removed; AI disabled. Environment keys are unchanged.")
            except Exception:
                messages.append("Could not complete key removal. Check AI settings and Windows Credential Manager.")
        self.feedback.setText(" ".join(messages))
