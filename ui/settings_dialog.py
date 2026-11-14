"""AI preferences dialog; credentials stay in the settings service layer."""

import sys
from admin_access import access_label, elevation_status

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSpinBox, QTabWidget, QComboBox, QVBoxLayout, QWidget,
)

from settings.app_settings import AISettings, AppSettings, MIN_ANALYSES, MAX_ANALYSES
from settings import credential_store
from settings.ai_service import check_openai_connection, list_openai_models
from ui.guidance import DisclosureSection
from ui.icons import decorate_buttons, icon
from settings.terminal_settings import HOST_LABELS, SHELL_LABELS, TerminalPreferences, TerminalSettings
from settings.terminal_service import open_terminal
from desktop_shortcut import ShortcutError, create_desktop_shortcut
from ui.sounds import SoundEffects, SoundPreferences
from settings.model_catalog import (MODEL_GUIDES, STARTER_MODELS, PRICE_FLAG_LEGEND,
                                    PRICING_URL, PRICING_CHECKED, price_flag,
                                    model_guide, model_rates, model_tooltip, eur_from_usd,
                                    EUR_ESTIMATE_NOTE, FX_SOURCE_URL)
from ui.model_picker import ModelCostDelegate, add_model_choices
from settings.model_compatibility import SUGGESTED_MODEL, compatibility_hint


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


class ModelListWorker(QObject):
    """Fetch model metadata without blocking Qt or passing secrets through signals."""
    result = pyqtSignal(list, str)
    finished = pyqtSignal()

    @pyqtSlot()
    def run(self):
        try:
            models, message = list_openai_models()
            self.result.emit(models, message)
        except Exception:
            self.result.emit([], "Could not load models. Your current selection is unchanged.")
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
    admin_restart_requested = pyqtSignal()
    sounds_changed = pyqtSignal(bool)
    privacy_requested = pyqtSignal()

    def __init__(self, parent=None, *, app_settings=None):
        """Build settings controls and load preferences plus credential-status text.

        parent owns the dialog; app_settings may inject an AppSettings-compatible
        service for tests. The password field starts empty even if a key is saved.
        First display loads account model metadata in the background when a key
        exists. The full bundled catalog is available immediately, including offline.
        """
        super().__init__(parent)
        self._settings = app_settings if app_settings is not None else AppSettings()
        self._terminal_settings = TerminalPreferences()
        self._sound_preferences = SoundPreferences()
        self._sounds = SoundEffects(self._sound_preferences)
        self._test_thread = None
        self._test_worker = None
        self._loading_models = False
        self._close_pending = False
        self._close_result = QDialog.DialogCode.Rejected
        self._models_open_attempted = False
        self._auto_models_timer = QTimer(self)
        self._auto_models_timer.setSingleShot(True)
        self._auto_models_timer.timeout.connect(self._auto_load_models)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("LogOpen — Settings")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(580, 820)
        self.setMinimumSize(440, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(_label("Settings", "pageTitle"))
        self.tabs = QTabWidget()
        self.tabs.setObjectName("detailTabs")
        self.tabs.setAccessibleName("Settings categories")
        self.tabs.setMinimumHeight(160)
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
        self.enabled_checkbox = QCheckBox("Use optional AI help")
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
        self.key_input.setAccessibleDescription("Hidden as you type. Save a new key or leave empty to keep your existing key.")
        key_label.setBuddy(self.key_input)
        fields.addWidget(self.key_input)
        key_buttons = QHBoxLayout()
        self.save_key_button = _button("Save Key", self._save_key)
        self.save_key_button.setIcon(icon("key"))
        self.remove_key_button = _button("Remove key", self._remove_key)
        self.remove_key_button.setAccessibleName("Remove saved OpenAI API key")
        key_buttons.addWidget(self.save_key_button)
        key_buttons.addWidget(self.remove_key_button)
        key_buttons.addStretch()
        fields.addLayout(key_buttons)
        self.key_status = _label("")
        self.key_status.setAccessibleDescription("API key status")
        fields.addWidget(self.key_status)
        self.detect_key_button = _button("Check this PC for a key", self._detect_key)
        self.detect_key_button.setIcon(icon("key"))
        fields.addWidget(self.detect_key_button)
        fields.addWidget(_label(
            "Checks your saved LogOpen key and OPENAI_API_KEY in Windows. "
            "You can use a detected key or save a new one securely. No restart is needed."
        ))

        model_label = _label("3. Choose an AI model", "sectionTitle")
        fields.addWidget(model_label)
        self.model_input = QComboBox()
        self.model_input.setEditable(True)
        self.model_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_input.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_input.setMinimumContentsLength(16)
        self.model_input.lineEdit().setMaxLength(200)
        self.model_input.setAccessibleName("OpenAI model")
        self.model_input.setAccessibleDescription("Choose from the list or type a compatible model ID. Your saved choice is kept.")
        self.model_input.setToolTip("Choose a model or type a compatible model ID. Your saved choice is kept.")
        self.model_input.setItemDelegate(ModelCostDelegate(self.model_input))
        # Show every bundled model immediately. Account discovery replaces these
        # suggestions with available IDs while retaining the current selection.
        add_model_choices(self.model_input, [*STARTER_MODELS, *MODEL_GUIDES])
        model_label.setBuddy(self.model_input)
        fields.addWidget(self.model_input)
        fields.addWidget(_label(PRICE_FLAG_LEGEND))
        self.model_compatibility = _label("")
        fields.addWidget(self.model_compatibility)
        self.suggested_model_button = _button("Use suggested model", lambda: self.model_input.setCurrentText(SUGGESTED_MODEL))
        fields.addWidget(self.suggested_model_button)
        cost_card = QFrame(objectName="modelCostCard")
        cost_layout = QVBoxLayout(cost_card)
        cost_layout.setContentsMargins(12, 12, 12, 12)
        cost_layout.setSpacing(8)
        self.model_cost_tier = _label("", "modelCostTier")
        self.model_cost_tier.setAccessibleDescription("Selected model cost category")
        self.model_cost_description = _label("")
        self.model_cost_rates = _label("")
        self.model_cost_example = _label("")
        self.model_cost_example.setAccessibleDescription("Illustrative model cost")
        for label in (self.model_cost_tier, self.model_cost_description,
                      self.model_cost_rates, self.model_cost_example):
            cost_layout.addWidget(label)
        self.model_cost_note = _label(
            "Example only, not a bill or spending limit. Actual charges depend on text length, "
            "reasoning, caching, and account settings. Longer context and other service tiers may cost more. "
            "Tokens are small pieces of text."
        )
        cost_layout.addWidget(self.model_cost_note)
        self.pricing_button = _button("View OpenAI pricing", self._open_pricing)
        self.pricing_button.setIcon(icon("info"))
        cost_layout.addWidget(self.pricing_button)
        cost_layout.addWidget(_label(f"Rate guide checked {PRICING_CHECKED} · estimated EUR prices"))
        self.exchange_rate_note = _label(EUR_ESTIMATE_NOTE)
        self.exchange_rate_note.setToolTip(f"Exchange-rate source: {FX_SOURCE_URL}")
        cost_layout.addWidget(self.exchange_rate_note)
        fields.addWidget(cost_card)
        self.load_models_button = _button("Load available models", self._load_models)
        self.load_models_button.setIcon(icon("spark"))
        fields.addWidget(self.load_models_button)
        fields.addWidget(_label(
            "Keep your saved model, choose from the list, or enter a model ID. "
            "The full bundled catalog is shown immediately. With a configured key, "
            "account models load automatically when Settings opens or you save a key. "
            "Load available models refreshes the list without sending problem details. "
            "Model access, compatibility, and pricing vary."
        ))

        self.advanced = DisclosureSection("Advanced AI options")
        limit_label = _label("Maximum AI analyses per scan", "sectionTitle")
        self.advanced.body.addWidget(limit_label)
        self.max_analyses_input = QSpinBox()
        self.max_analyses_input.setRange(MIN_ANALYSES, MAX_ANALYSES)
        self.max_analyses_input.valueChanged.connect(self._refresh_model_cost)
        self.model_input.currentTextChanged.connect(self._refresh_model_cost)
        self.max_analyses_input.setAccessibleName("Maximum AI analyses per scan")
        self.max_analyses_input.setAccessibleDescription(f"Choose between {MIN_ANALYSES} and {MAX_ANALYSES}. This is not a spending limit.")
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
        fields.addWidget(_label("4. Check that your key works", "sectionTitle"))
        self.test_button = _button("Test Connection", self._test_connection)
        self.test_button.setIcon(icon("check"))
        fields.addWidget(self.test_button)
        fields.addWidget(_label(
            "Save your key first, then check that OpenAI accepts it. This check does not send problem details."
        ))
        fields.addStretch()
        scroll.setWidget(content)
        self.tabs.addTab(scroll, icon("spark"), "AI help")

        terminal_scroll = QScrollArea(objectName="settingsScroll")
        terminal_scroll.setWidgetResizable(True)
        terminal_scroll.setFrameShape(QFrame.Shape.NoFrame)
        terminal_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        terminal_tab = QWidget(objectName="settingsContent")
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
        terminal_scroll.setWidget(terminal_tab)
        self.tabs.addTab(terminal_scroll, icon("terminal"), "Terminal")

        app_scroll = QScrollArea()
        app_scroll.setObjectName("settingsScroll")
        app_scroll.setWidgetResizable(True)
        app_scroll.setFrameShape(QFrame.Shape.NoFrame)
        app_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        app_content = QWidget(objectName="settingsContent")
        app_layout = QVBoxLayout(app_content)
        app_layout.setContentsMargins(0, 16, 12, 0)
        app_layout.setSpacing(14)
        reading_options = getattr(parent, "_open_reading_options", None)
        if callable(reading_options):
            app_layout.addWidget(_label("Reading and accessibility", "sectionTitle"))
            app_layout.addWidget(_label("Choose comfortable text, colours, and motion settings."))
            self.reading_options_button = _button("Reading options…", reading_options)
            app_layout.addWidget(self.reading_options_button)
        app_layout.addWidget(_label("Sound effects", "sectionTitle"))
        self.privacy_button = _button("Privacy and local data…", self.privacy_requested.emit)
        app_layout.addWidget(self.privacy_button)
        self.sound_checkbox = QCheckBox("Play subtle sound effects")
        self.sound_checkbox.setChecked(self._sound_preferences.enabled())
        self.sound_checkbox.toggled.connect(self._set_sounds_enabled)
        app_layout.addWidget(self.sound_checkbox)
        app_layout.addWidget(_label(
            "Short cues mark a scan starting, a completed scan or AI explanation, "
            "a finished download, or an error. Sound settings save immediately."
        ))
        self.test_sound_button = _button("Test sound", self._preview_sound)
        self.test_sound_button.setIcon(icon("sound"))
        self.test_sound_button.setEnabled(self.sound_checkbox.isChecked())
        app_layout.addWidget(self.test_sound_button)
        app_layout.addWidget(_label("Desktop shortcut", "sectionTitle"))
        app_layout.addWidget(_label("Put the LogOpen icon on your Windows desktop so the app is easy to find."))
        self.shortcut_button = _button("Create desktop shortcut", self._create_shortcut)
        self.shortcut_button.setIcon(icon("desktop"))
        app_layout.addWidget(self.shortcut_button)
        app_layout.addWidget(_label("Windows permissions", "sectionTitle"))
        self._elevated = elevation_status()
        self.access_status = _label(access_label(self._elevated), "detailFieldTitle")
        app_layout.addWidget(self.access_status)
        app_layout.addWidget(_label(
            "Administrator access can allow deeper event-log troubleshooting, including restricted performance logs. "
            "Windows will ask for permission or administrator credentials. Monitoring stops for the restart; start a new scan afterward. "
            "Scans still only read logs, and suggested commands are never run automatically. "
            "Organization policies can still restrict access. If you enter a different administrator account, its own settings and history will be used."
        ))
        self.admin_restart_button = _button("Restart as administrator…", self._restart_as_administrator)
        self.admin_restart_button.setIcon(icon("key"))
        self.admin_restart_button.setEnabled(sys.platform == "win32" and self._elevated is not True)
        app_layout.addWidget(self.admin_restart_button)
        app_layout.addWidget(_label("Restart LogOpen", "sectionTitle"))
        self.restart_button = _button("Restart App", self._restart_app)
        self.restart_button.setIcon(icon("restart"))
        app_layout.addWidget(self.restart_button)
        app_layout.addWidget(_label("Save settings and reopen LogOpen. Live monitoring will stop."))
        app_layout.addStretch()
        app_scroll.setWidget(app_content)
        self.tabs.addTab(app_scroll, icon("settings"), "App")

        self.message_label = _label("", "settingsMessage")
        self.message_label.setMinimumHeight(36)
        self.message_label.setAccessibleDescription("Settings result")
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
        if self.model_input.findText(preferences.model) < 0:
            add_model_choices(self.model_input, [preferences.model])
        self.model_input.setCurrentText(preferences.model)
        self.max_analyses_input.setValue(preferences.max_analyses)
        terminal = self._terminal_settings.load()
        self.shell_combo.setCurrentIndex(self.shell_combo.findData(terminal.shell))
        self.terminal_host_combo.setCurrentIndex(self.terminal_host_combo.findData(terminal.host))
        self._refresh_key_status()
        self._refresh_model_cost()
        self._message("Bundled models ready. Account availability is unchecked.")
        decorate_buttons(self)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._models_open_attempted:
            self._models_open_attempted = True
            self._auto_models_timer.start(0)

    def _auto_load_models(self):
        """Discover account models after painting; never use an unsaved key."""
        if (not self.isVisible() or self._close_pending or self._test_thread is not None
                or self.key_input.text()):
            return
        try:
            configured = bool(credential_store.resolve_openai_api_key())
        except Exception:
            return  # Keep the offline catalog and credential-status explanation.
        if configured:
            self._load_models()

    def _detect_key(self):
        self._refresh_key_status()
        self._auto_models_timer.start(0)

    def _open_pricing(self):
        guide = model_guide(self.model_input.currentText())
        QDesktopServices.openUrl(QUrl(guide.source_url if guide else PRICING_URL))

    def _refresh_model_cost(self):
        """Show explicit rates or unknown pricing for the currently edited exact ID."""
        model = self.model_input.currentText()
        self.model_compatibility.setText(compatibility_hint(model))
        guide = model_guide(model)
        tier = guide.tier if guide else "Price unknown"
        self.model_cost_tier.setText(price_flag(model))
        self.model_cost_tier.setProperty("tier", tier)
        self.model_cost_tier.style().unpolish(self.model_cost_tier)
        self.model_cost_tier.style().polish(self.model_cost_tier)
        self.model_cost_rates.setVisible(guide is not None)
        self.model_cost_example.setVisible(guide is not None)
        self.model_cost_note.setVisible(guide is not None)
        if guide is None:
            self.model_cost_description.setText(model_tooltip(model))
            self.model_cost_rates.clear()
            self.model_cost_example.clear()
            return
        self.model_cost_description.setText(guide.description)
        self.model_cost_rates.setText(model_rates(model))
        count = self.max_analyses_input.value()
        ratio = guide.example_cost() / MODEL_GUIDES["gpt-6-luna"].example_cost()
        ratio_text = f"{ratio:.1f}".removesuffix(".0")
        comparison = (f" At that size, {ratio_text}× the gpt-6-luna cost."
                      if model.strip() != "gpt-6-luna" else "")
        self.model_cost_example.setText(
            f"Example: 10,000 sent + 2,000 billed output tokens ≈ {eur_from_usd(guide.example_cost())} "
            f"for one analysis; {count} at that size ≈ {eur_from_usd(guide.example_cost(count))}." + comparison
        )

    def _set_sounds_enabled(self, enabled):
        try:
            self._sound_preferences.save(enabled)
        except Exception:
            self.sound_checkbox.blockSignals(True)
            self.sound_checkbox.setChecked(self._sound_preferences.enabled())
            self.sound_checkbox.blockSignals(False)
            self._message("Could not save sound preferences. Check your user profile permissions.")
        else:
            self.sounds_changed.emit(enabled)
            self._message("Sound effects enabled." if enabled else "Sound effects muted.", success=True)
        self.test_sound_button.setEnabled(self.sound_checkbox.isChecked())

    def _preview_sound(self):
        if self._sounds.play("success"):
            self._message("Playing a short preview.", success=True)
        else:
            self._message("Sound preview is unavailable right now. Check your audio output and try again.")

    def _create_shortcut(self):
        try:
            create_desktop_shortcut()
        except ShortcutError as error:
            self._message(str(error))
        else:
            self._message("LogOpen shortcut created on your desktop.", success=True)

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
            self._auto_models_timer.start(0)
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
                self.enabled_checkbox.isChecked(), self.model_input.currentText(),
                self.max_analyses_input.value(),
            ))
            self._terminal_settings.save(TerminalSettings(
                self.shell_combo.currentData(), self.terminal_host_combo.currentData(),
            ))
        except ValueError:
            self.advanced.set_expanded(True)
            self._message("Enter a valid model name and analysis limit.")
            self.tabs.setCurrentIndex(0)
            self.model_input.setAccessibleDescription("Enter a valid model ID using letters, numbers, dots, underscores or hyphens.")
            self.model_input.setFocus()
            self.model_input.lineEdit().selectAll()
            return False
        except Exception:
            self._message("Could not save AI settings. Check your user profile permissions.")
            return False
        self.model_input.setCurrentText(self.model_input.currentText().strip())
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
    def _restart_as_administrator(self):
        """Save settings before asking the host to drain workers and show Windows UAC."""
        if self._test_thread is not None or self._elevated is True or not self._save_settings():
            return
        self.accept()
        self.admin_restart_requested.emit()

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
        self._loading_models = False
        self._message("Testing connection…")
        self._start_ai_worker(ConnectionTestWorker(self.model_input.currentText()), self._show_test_result)

    @pyqtSlot()
    def _load_models(self):
        """Load account candidates automatically or on request; preserve preference edits."""
        self._auto_models_timer.stop()
        if self._test_thread is not None:
            return
        if self.key_input.text():
            self._message("Save the new API key before loading available models.")
            return
        self._refresh_key_status()
        self._loading_models = True
        self._message("Loading available models from OpenAI…")
        self._start_ai_worker(ModelListWorker(), self._show_models)

    @pyqtSlot(list, str)
    def _show_models(self, models, message):
        if models:
            selected = self.model_input.currentText()
            self.model_input.clear()
            add_model_choices(self.model_input, [selected, *models])
            self.model_input.setCurrentText(selected)
        if not models:
            message += " The existing model list and your selection have been kept."
        self._message(message, success=bool(models))

    def _start_ai_worker(self, worker, result_slot):
        """Share the existing deferred-close lifecycle for both metadata operations."""
        self._set_testing(True)
        self._test_thread = QThread(self)
        self._test_worker = worker
        self._test_worker.moveToThread(self._test_thread)
        self._test_thread.started.connect(self._test_worker.run)
        self._test_worker.result.connect(result_slot)
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
                       self.open_terminal_button, self.load_models_button, self.detect_key_button):
            widget.setEnabled(not testing)
        self.suggested_model_button.setEnabled(not testing or self._loading_models)
        self.test_button.setText("Testing…" if testing and not self._loading_models else "Test Connection")
        self.load_models_button.setText("Loading models…" if testing and self._loading_models else "Load available models")
        # Choosing a model remains possible during metadata discovery; the result
        # handler reads the current selection, not the value at request start.
        self.model_input.setEnabled(not testing or self._loading_models)
        self.admin_restart_button.setEnabled(not testing and sys.platform == "win32" and self._elevated is not True)

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
        self._loading_models = False
        self._set_testing(False)
        if self._close_pending:
            self.done(self._close_result)

    def done(self, result):
        """Clear typed secret text and finish, or defer the result until the worker stops.

        Covers acceptance/rejection paths without destroying a running QThread.
        """
        self._auto_models_timer.stop()
        self.key_input.clear()
        if self._test_thread is not None:
            self._close_pending = True
            self._close_result = result
            self._message("Waiting for the OpenAI check to finish before closing…")
            return
        super().done(result)

    def closeEvent(self, event):
        """Clear the password input and defer the Qt close event if a connection test is active."""
        self._auto_models_timer.stop()
        if self._test_thread is not None:
            self.reject()
            event.ignore()
            return
        self.key_input.clear()
        super().closeEvent(event)
