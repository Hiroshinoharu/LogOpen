"""AI preferences dialog; credentials stay in the settings service layer."""

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from settings.app_settings import AISettings, AppSettings, MIN_ANALYSES, MAX_ANALYSES
from settings import credential_store
from settings.ai_service import check_openai_connection


def _label(text, name="mutedLabel"):
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _button(text, slot, *, primary=False):
    button = QPushButton(text)
    button.setObjectName("scanButton" if primary else "secondaryButton")
    button.setMinimumHeight(38)
    button.setAutoDefault(False)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(slot)
    return button


class ConnectionTestWorker(QObject):
    result = pyqtSignal(bool, str)
    finished = pyqtSignal()

    def __init__(self, model):
        super().__init__()
        self.model = model  # Only a model identifier crosses the thread boundary.

    @pyqtSlot()
    def run(self):
        try:
            success, message = check_openai_connection(self.model)
            self.result.emit(success, message)
        except Exception:
            self.result.emit(False, "Connection test failed. Please try again.")
        finally:
            self.finished.emit()


class SettingsDialog(QDialog):
    def __init__(self, parent=None, *, app_settings=None):
        super().__init__(parent)
        self._settings = app_settings if app_settings is not None else AppSettings()
        self._test_thread = None
        self._test_worker = None
        self._close_pending = False
        self._close_result = QDialog.DialogCode.Rejected
        self.setObjectName("settingsDialog")
        self.setWindowTitle("LogOpen — AI Settings")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(580, 820)
        self.setMinimumSize(440, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(_label("AI Settings", "pageTitle"))
        layout.addWidget(_label("Configure optional AI insights for your incident scans."))

        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("settingsContent")
        fields = QVBoxLayout(content)
        fields.setContentsMargins(0, 0, 12, 0)
        fields.setSpacing(12)
        fields.addWidget(_label("AI Analysis", "sectionTitle"))
        self.enabled_checkbox = QCheckBox("Enable AI Analysis")
        fields.addWidget(self.enabled_checkbox)
        fields.addWidget(_label(
            "When enabled, selected incident details are sent to OpenAI using your API key. "
            "When disabled, local incident analysis continues normally."
        ))

        key_label = _label("OpenAI API Key", "sectionTitle")
        fields.addWidget(key_label)
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setMaxLength(2048)
        self.key_input.setPlaceholderText("Enter a new API key")
        self.key_input.setAccessibleName("OpenAI API Key")
        key_label.setBuddy(self.key_input)
        fields.addWidget(self.key_input)
        key_buttons = QHBoxLayout()
        self.save_key_button = _button("Save Key", self._save_key)
        self.remove_key_button = _button("Remove API Key", self._remove_key)
        key_buttons.addWidget(self.save_key_button)
        key_buttons.addWidget(self.remove_key_button)
        key_buttons.addStretch()
        fields.addLayout(key_buttons)
        self.key_status = _label("")
        self.key_status.setAccessibleName("API key status")
        fields.addWidget(self.key_status)
        fields.addWidget(_label(
            "Saved in Windows Credential Manager. Removing a saved key still allows "
            "OPENAI_API_KEY from the environment; disable AI to stop analysis."
        ))

        model_label = _label("Model", "sectionTitle")
        fields.addWidget(model_label)
        self.model_input = QLineEdit()
        self.model_input.setMaxLength(200)
        self.model_input.setAccessibleName("OpenAI model")
        model_label.setBuddy(self.model_input)
        fields.addWidget(self.model_input)
        limit_label = _label("Maximum AI analyses per scan", "sectionTitle")
        fields.addWidget(limit_label)
        self.max_analyses_input = QSpinBox()
        self.max_analyses_input.setRange(MIN_ANALYSES, MAX_ANALYSES)
        self.max_analyses_input.setAccessibleName("Maximum AI analyses per scan")
        limit_label.setBuddy(self.max_analyses_input)
        fields.addWidget(self.max_analyses_input)
        fields.addWidget(_label(
            f"{MIN_ANALYSES}–{MAX_ANALYSES} analyses per scan. Preferences take effect on the next scan."
        ))
        self.test_button = _button("Test Connection", self._test_connection)
        fields.addWidget(self.test_button)
        fields.addWidget(_label(
            "Saves preferences and checks authentication and model access. "
            "No incident analysis is run. Save a new key before testing."
        ))
        fields.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        self.message_label = _label("", "settingsMessage")
        self.message_label.setMinimumHeight(36)
        self.message_label.setAccessibleName("Settings result")
        layout.addWidget(self.message_label)
        footer = QHBoxLayout()
        footer.addStretch()
        self.close_button = _button("Close", self.reject)
        self.save_settings_button = _button("Save Settings", self._save_preferences, primary=True)
        footer.addWidget(self.close_button)
        footer.addWidget(self.save_settings_button)
        layout.addLayout(footer)

        preferences = self._settings.load()
        self.enabled_checkbox.setChecked(preferences.enabled)
        self.model_input.setText(preferences.model)
        self.max_analyses_input.setValue(preferences.max_analyses)
        self._refresh_key_status()

    def _message(self, message, *, success=False):
        self.message_label.setText(message)
        self.message_label.setProperty("success", success)
        self.message_label.style().unpolish(self.message_label)
        self.message_label.style().polish(self.message_label)

    def _refresh_key_status(self):
        self.key_status.setText(credential_store.credential_status())

    @pyqtSlot()
    def _save_key(self):
        try:
            credential_store.save_openai_api_key(self.key_input.text())
        except ValueError:
            self._message("Enter a valid OpenAI API key without whitespace.")
        except Exception:
            self._message("Could not save the key. Check Windows Credential Manager.")
        else:
            self._message("API key saved securely.", success=True)
        finally:
            # Clear on failures too; never repopulate this field from storage.
            self.key_input.clear()
            self._refresh_key_status()

    @pyqtSlot()
    def _remove_key(self):
        self.key_input.clear()
        try:
            credential_store.delete_openai_api_key()
        except Exception:
            self._message("Could not remove the key. Check Windows Credential Manager.")
        else:
            self._message("Saved API key removed.", success=True)
        self._refresh_key_status()

    @pyqtSlot()
    def _save_preferences(self):
        try:
            self._settings.save(AISettings(
                self.enabled_checkbox.isChecked(), self.model_input.text(),
                self.max_analyses_input.value(),
            ))
        except ValueError:
            self._message("Enter a valid model name and analysis limit.")
            return False
        except Exception:
            self._message("Could not save AI settings. Check your user profile permissions.")
            return False
        self.model_input.setText(self.model_input.text().strip())
        self._message("AI settings saved. They apply to the next scan.", success=True)
        return True

    @pyqtSlot()
    def _test_connection(self):
        if self._test_thread is not None:
            return
        if self.key_input.text():
            self._message("Save the new API key before testing the connection.")
            return
        if not self._save_preferences():
            return
        self._set_testing(True)
        self._message("Testing connection…")
        self._test_thread = QThread(self)
        self._test_worker = ConnectionTestWorker(self.model_input.text())
        self._test_worker.moveToThread(self._test_thread)
        self._test_thread.started.connect(self._test_worker.run)
        self._test_worker.result.connect(self._show_test_result)
        self._test_worker.finished.connect(self._test_thread.quit)
        self._test_thread.finished.connect(self._test_worker.deleteLater)
        self._test_thread.finished.connect(self._test_thread.deleteLater)
        self._test_thread.finished.connect(self._test_finished)
        self._test_thread.start()

    def _set_testing(self, testing):
        for widget in (self.enabled_checkbox, self.key_input, self.save_key_button,
                       self.remove_key_button, self.model_input, self.max_analyses_input,
                       self.save_settings_button, self.test_button):
            widget.setEnabled(not testing)
        self.test_button.setText("Testing…" if testing else "Test Connection")

    @pyqtSlot(bool, str)
    def _show_test_result(self, success, message):
        self._message(message, success=success)

    @pyqtSlot()
    def _test_finished(self):
        self._test_worker = None
        self._test_thread = None
        self._set_testing(False)
        if self._close_pending:
            self.done(self._close_result)

    def done(self, result):
        self.key_input.clear()
        if self._test_thread is not None:
            self._close_pending = True
            self._close_result = result
            self._message("Waiting for the connection test to finish before closing…")
            return
        super().done(result)

    def closeEvent(self, event):
        if self._test_thread is not None:
            self.reject()
            event.ignore()
            return
        self.key_input.clear()
        super().closeEvent(event)
