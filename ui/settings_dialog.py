"""AI preferences dialog; credentials stay in the settings service layer."""

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSpinBox, QTabWidget, QComboBox, QVBoxLayout, QWidget,
)

from settings.app_settings import AISettings, AppSettings, MIN_ANALYSES, MAX_ANALYSES
from settings import credential_store
from settings.ai_service import check_openai_connection
from ui.guidance import DisclosureSection
from ui.icons import decorate_buttons, icon
from settings.terminal_settings import HOST_LABELS, SHELL_LABELS, TerminalPreferences, TerminalSettings
from settings.terminal_service import open_terminal


def _label(text, name="mutedLabel"):
    """Create a named, wrapping QLabel with literal text rendering."""
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _button(text, slot, *, primary=False):
    """Create a styled button, disable implicit default activation, and connect its slot."""
    button = QPushButton(text)
    button.setObjectName("scanButton" if primary else "secondaryButton")
    button.setMinimumHeight(38)
    button.setAutoDefault(False)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(slot)
    return button


class ConnectionTestWorker(QObject):
    """Run a model-metadata connection check away from the GUI thread.

    Emits result(bool, str) for presentation and finished for thread cleanup.
    Stores a model name; credential resolution stays inside the service layer.
    """
    result = pyqtSignal(bool, str)
    finished = pyqtSignal()

    def __init__(self, model):
        """Store the model to check and initialize the worker without a QObject parent."""
        super().__init__()
        self.model = model  # Only a model identifier crosses the thread boundary.

    @pyqtSlot()
    def run(self):
        """Call the connection service and emit its result, always followed by finished.

        Unexpected exceptions become a fixed failure message rather than raw error
        text. The worker accesses no widgets.
        """
        try:
            success, message = check_openai_connection(self.model)
            self.result.emit(success, message)
        except Exception:
            self.result.emit(False, "Connection test failed. Please try again.")
        finally:
            self.finished.emit()


class SettingsDialog(QDialog):
    """Edit non-secret AI preferences and invoke immediate secure-key operations.

    Save Settings persists preferences and any typed key, while Save Key/Remove
    Key take effect immediately. A connection test saves valid preferences before
    starting its worker. Closing clears typed secret text and waits for any worker
    to finish.
    """
    restart_requested = pyqtSignal()

    def __init__(self, parent=None, *, app_settings=None):
        """Build settings controls and load preferences plus credential-status text.

        parent owns the dialog; app_settings may inject an AppSettings-compatible
        service for tests. The password field starts empty even if a key is saved.
        Opening the dialog does not call the API.
        """
        super().__init__(parent)
        self._settings = app_settings if app_settings is not None else AppSettings()
        self._terminal_settings = TerminalPreferences()
        self._test_thread = None
        self._test_worker = None
        self._close_pending = False
        self._close_result = QDialog.DialogCode.Rejected
        self.setObjectName("settingsDialog")
        self.setWindowTitle("LogOpen — Settings")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(580, 820)
        self.setMinimumSize(440, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(_label("Settings", "pageTitle"))
        layout.addWidget(_label("Choose optional AI help and the terminal you prefer for suggested commands."))
        self.tabs = QTabWidget()
        self.tabs.setObjectName("detailTabs")
        layout.addWidget(self.tabs, 1)

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
        fields.addWidget(_label("1. Choose whether to use AI", "sectionTitle"))
        self.enabled_checkbox = QCheckBox("Use AI to explain problems")
        fields.addWidget(self.enabled_checkbox)
        fields.addWidget(_label(
            "When enabled, selected problem details are sent to OpenAI using your API key. "
            "OpenAI API charges may apply."
        ))

        key_label = _label("2. Add your OpenAI API key", "sectionTitle")
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
        self.save_key_button.setIcon(icon("key"))
        self.remove_key_button = _button("Remove API Key", self._remove_key)
        key_buttons.addWidget(self.save_key_button)
        key_buttons.addWidget(self.remove_key_button)
        key_buttons.addStretch()
        fields.addLayout(key_buttons)
        self.key_status = _label("")
        self.key_status.setAccessibleName("API key status")
        fields.addWidget(self.key_status)
        fields.addWidget(_label(
            "Stored securely for your Windows account. Save Settings also saves a new key. No restart is needed."
        ))

        self.advanced = DisclosureSection("Advanced AI options")
        model_label = _label("Model", "sectionTitle")
        self.advanced.body.addWidget(model_label)
        self.model_input = QLineEdit()
        self.model_input.setMaxLength(200)
        self.model_input.setAccessibleName("OpenAI model")
        model_label.setBuddy(self.model_input)
        self.advanced.body.addWidget(self.model_input)
        limit_label = _label("Maximum AI analyses per scan", "sectionTitle")
        self.advanced.body.addWidget(limit_label)
        self.max_analyses_input = QSpinBox()
        self.max_analyses_input.setRange(MIN_ANALYSES, MAX_ANALYSES)
        self.max_analyses_input.setAccessibleName("Maximum AI analyses per scan")
        limit_label.setBuddy(self.max_analyses_input)
        self.advanced.body.addWidget(self.max_analyses_input)
        self.advanced.body.addWidget(_label(
            f"{MIN_ANALYSES}–{MAX_ANALYSES} analyses per scan. Preferences take effect on the next scan."
        ))
        self.advanced.body.addWidget(_label(
            "You can keep the default model and limit. Removing a saved key still allows "
            "OPENAI_API_KEY from the environment; disable AI to stop analysis."
        ))
        fields.addWidget(self.advanced)
        fields.addWidget(_label("3. Check that your key works", "sectionTitle"))
        self.test_button = _button("Test Connection", self._test_connection)
        self.test_button.setIcon(icon("check"))
        fields.addWidget(self.test_button)
        fields.addWidget(_label(
            "Save your key first, then check that OpenAI accepts it. This check does not send problem details."
        ))
        self.restart_button = _button("Restart App", self._restart_app)
        self.restart_button.setIcon(icon("restart"))
        fields.addWidget(self.restart_button)
        fields.addWidget(_label("Save settings and reopen LogOpen. Live monitoring will stop."))
        fields.addStretch()
        scroll.setWidget(content)
        self.tabs.addTab(scroll, icon("spark"), "AI help")

        terminal_tab = QWidget()
        terminal_layout = QVBoxLayout(terminal_tab)
        terminal_layout.setContentsMargins(0, 16, 0, 0)
        terminal_layout.setSpacing(14)
        terminal_layout.addWidget(_label("Your preferred terminal", "sectionTitle"))
        terminal_layout.addWidget(_label("Choose the command format for new AI suggestions and where to open a terminal."))
        shell_label = _label("Commands for", "detailFieldTitle")
        terminal_layout.addWidget(shell_label)
        self.shell_combo = QComboBox()
        self.shell_combo.setAccessibleName("Preferred command format")
        for value, label in SHELL_LABELS.items():
            self.shell_combo.addItem(icon("terminal"), label, value)
        shell_label.setBuddy(self.shell_combo)
        terminal_layout.addWidget(self.shell_combo)
        host_label = _label("Open in", "detailFieldTitle")
        terminal_layout.addWidget(host_label)
        self.terminal_host_combo = QComboBox()
        self.terminal_host_combo.setAccessibleName("Preferred terminal window")
        for value, label in HOST_LABELS.items():
            self.terminal_host_combo.addItem(icon("terminal"), label, value)
        host_label.setBuddy(self.terminal_host_combo)
        terminal_layout.addWidget(self.terminal_host_combo)
        terminal_layout.addWidget(_label("PowerShell is a good default on Windows. Bash requires WSL with Bash installed. Windows Terminal must be installed to use that window option."))
        self.open_terminal_button = _button("Open selected terminal", self._open_terminal)
        terminal_layout.addWidget(self.open_terminal_button)
        terminal_layout.addWidget(_label("This opens an empty terminal. Copy and review a suggested command before pasting it. Existing saved commands keep their original format."))
        terminal_layout.addStretch()
        self.tabs.addTab(terminal_tab, icon("terminal"), "Terminal")

        self.message_label = _label("", "settingsMessage")
        self.message_label.setMinimumHeight(36)
        self.message_label.setAccessibleName("Settings result")
        layout.addWidget(self.message_label)
        footer = QHBoxLayout()
        footer.addStretch()
        self.close_button = _button("Close", self.reject)
        self.save_settings_button = _button("Save Settings", self._save_settings, primary=True)
        self.save_settings_button.setIcon(icon("save", "#0b3029"))
        footer.addWidget(self.close_button)
        footer.addWidget(self.save_settings_button)
        layout.addLayout(footer)

        preferences = self._settings.load()
        self.enabled_checkbox.setChecked(preferences.enabled)
        self.model_input.setText(preferences.model)
        self.max_analyses_input.setValue(preferences.max_analyses)
        terminal = self._terminal_settings.load()
        self.shell_combo.setCurrentIndex(self.shell_combo.findData(terminal.shell))
        self.terminal_host_combo.setCurrentIndex(self.terminal_host_combo.findData(terminal.host))
        self._refresh_key_status()
        decorate_buttons(self)

    def _open_terminal(self):
        success, message = open_terminal(preferences=TerminalSettings(
            self.shell_combo.currentData(), self.terminal_host_combo.currentData(),
        ))
        self._message(message, success=success)

    def _message(self, message, *, success=False):
        """Set a display-safe feedback message and refresh its success/error styling."""
        self.message_label.setText(message)
        self.message_label.setProperty("success", success)
        self.message_label.style().unpolish(self.message_label)
        self.message_label.style().polish(self.message_label)

    def _refresh_key_status(self):
        """Refresh the availability label through credential_status without filling the key
        input.
        """
        self.key_status.setText(credential_store.credential_status())

    @pyqtSlot()
    def _save_key(self):
        """Save the typed credential immediately and report validation/storage failure.

        Returns whether saving succeeded. Always clears the password input and
        refreshes availability, including after failure. Saving a key does not save
        the other preference controls.
        """
        try:
            credential_store.save_openai_api_key(self.key_input.text())
        except ValueError:
            self._message("Enter a valid OpenAI API key without whitespace.")
            return False
        except Exception:
            self._message("Could not save the key. Check Windows Credential Manager.")
            return False
        else:
            self._message(
                "API key saved securely. No restart needed; retry AI analysis or run a new scan.",
                success=True,
            )
            return True
        finally:
            # Clear on failures too; never repopulate this field from storage.
            self.key_input.clear()
            self._refresh_key_status()

    @pyqtSlot()
    def _remove_key(self):
        """Clear typed text and remove the saved credential, then refresh its status.

        Reports a fixed error on failure; the environment fallback is unchanged.
        """
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
        """Validate controls and persist AISettings, returning whether saving succeeded.

        Displays validation/storage feedback and normalizes the visible model text.
        This does not save the password input or validate access to the model.
        """
        try:
            self._settings.save(AISettings(
                self.enabled_checkbox.isChecked(), self.model_input.text(),
                self.max_analyses_input.value(),
            ))
            self._terminal_settings.save(TerminalSettings(
                self.shell_combo.currentData(), self.terminal_host_combo.currentData(),
            ))
        except ValueError:
            self.advanced.set_expanded(True)
            self._message("Enter a valid model name and analysis limit.")
            return False
        except Exception:
            self._message("Could not save AI settings. Check your user profile permissions.")
            return False
        self.model_input.setText(self.model_input.text().strip())
        self._message("Settings saved. New suggestions will use your selected command format.", success=True)
        return True

    @pyqtSlot()
    def _save_settings(self):
        """Save preferences and any newly typed key, reporting either failure."""
        if not self._save_preferences():
            return False
        if self.key_input.text():
            if not self._save_key():
                return False
            self._message(
                "API key and AI settings saved. No restart needed; retry AI analysis or run a new scan.",
                success=True,
            )
        return True

    @pyqtSlot()
    def _restart_app(self):
        """Save pending settings and ask the host window to restart after closing."""
        if self._test_thread is not None or not self._save_settings():
            return
        self.accept()
        self.restart_requested.emit()

    @pyqtSlot()
    def _test_connection(self):
        """Save preferences and launch one background model-metadata access check.

        Refuses testing with unsaved key text so a different stored key is not tested
        by accident. Disables editing while the worker runs and wires GUI-thread
        result/cleanup slots. No incident inference is requested.
        """
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
        """Toggle edit/test controls and progress text while a connection worker runs."""
        for widget in (self.enabled_checkbox, self.key_input, self.save_key_button,
                       self.remove_key_button, self.model_input, self.max_analyses_input,
                       self.save_settings_button, self.test_button, self.restart_button,
                       self.advanced.toggle_button, self.shell_combo, self.terminal_host_combo,
                       self.open_terminal_button):
            widget.setEnabled(not testing)
        self.test_button.setText("Testing…" if testing else "Test Connection")

    @pyqtSlot(bool, str)
    def _show_test_result(self, success, message):
        """Display a worker connection result on the GUI thread using the supplied success flag."""
        self._message(message, success=success)

    @pyqtSlot()
    def _test_finished(self):
        """Release completed worker/thread references and restore editable controls.

        If closing was requested during the test, finish that deferred dialog result.
        """
        self._test_worker = None
        self._test_thread = None
        self._set_testing(False)
        if self._close_pending:
            self.done(self._close_result)

    def done(self, result):
        """Clear typed secret text and finish, or defer the result until the worker stops.

        Covers acceptance/rejection paths without destroying a running QThread.
        """
        self.key_input.clear()
        if self._test_thread is not None:
            self._close_pending = True
            self._close_result = result
            self._message("Waiting for the connection test to finish before closing…")
            return
        super().done(result)

    def closeEvent(self, event):
        """Clear the password input and defer the Qt close event if a connection test is active."""
        if self._test_thread is not None:
            self.reject()
            event.ignore()
            return
        self.key_input.clear()
        super().closeEvent(event)
