"""Exercise settings with actual Qt event delivery and mocked services."""

import os
import threading
import time
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSlot
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit

from settings.app_settings import AISettings, AppSettings
from settings import credential_store
from ui.main_window import MainWindow
from ui.settings_dialog import SettingsDialog


@pytest.fixture(scope="module")
def app():
    """Reuse or create the Qt application and keep it alive between test windows."""
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def wait_until(app, condition, timeout=3):
    """Pump Qt events until the condition succeeds, failing if the timeout expires."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition(), "Timed out waiting for Qt activity"


@pytest.fixture
def dialog(app):
    """Yield a visible settings dialog and clean it up after its worker thread has stopped."""
    widget = SettingsDialog()
    widget.show()
    yield widget
    wait_until(app, lambda: widget._test_thread is None)
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_mask_save_remove_and_never_redisplay_stored_key(dialog, monkeypatch):
    """Check mask save remove and never redisplay stored key."""
    key = "sk-fake-private-key"
    saved = []
    monkeypatch.setattr(credential_store, "save_openai_api_key", saved.append)
    monkeypatch.setattr(credential_store, "get_openai_api_key", lambda: saved[-1] if saved else None)
    monkeypatch.setattr(credential_store, "delete_openai_api_key", saved.clear)
    assert dialog.key_input.echoMode() == QLineEdit.EchoMode.Password
    dialog.key_input.setText(key)
    dialog.save_key_button.click()
    assert saved == [key]
    assert dialog.key_input.text() == ""
    assert dialog.key_status.text() == "API key configured"
    assert all(key not in label.text() for label in dialog.findChildren(QLabel))
    reopened = SettingsDialog()
    try:
        assert reopened.key_input.text() == ""
        assert reopened.key_status.text() == "API key configured"
    finally:
        reopened.close()
        reopened.deleteLater()
    dialog.remove_key_button.click()
    assert not saved
    assert "No API key" in dialog.key_status.text()


def test_key_storage_failure_stays_safe_and_clears_input(dialog, monkeypatch):
    """Check key storage failure stays safe and clears input."""
    monkeypatch.setattr(credential_store, "save_openai_api_key", Mock(side_effect=RuntimeError("sk-private-error")))
    dialog.key_input.setText("sk-fake-new-key")
    dialog.save_key_button.click()
    assert dialog.key_input.text() == ""
    assert "Could not save" in dialog.message_label.text()
    assert "sk-private-error" not in dialog.message_label.text()
    monkeypatch.setattr(credential_store, "delete_openai_api_key", Mock(side_effect=RuntimeError("sk-private-error")))
    dialog.remove_key_button.click()
    assert "Could not remove" in dialog.message_label.text()


def test_controls_persist_and_validate_preferences(dialog):
    """Check controls persist and validate preferences."""
    dialog.enabled_checkbox.setChecked(False)
    dialog.model_input.setText(" user-model ")
    dialog.max_analyses_input.setValue(8)
    dialog.save_settings_button.click()
    assert AppSettings().load() == AISettings(False, "user-model", 8)
    dialog.model_input.setText("sk-fake-key-in-wrong-field")
    dialog.save_settings_button.click()
    assert AppSettings().load().model == "user-model"
    assert "valid model" in dialog.message_label.text()
    dialog.max_analyses_input.setValue(10000)
    assert dialog.max_analyses_input.value() == 20
    dialog.max_analyses_input.setValue(-10)
    assert dialog.max_analyses_input.value() == 1


def test_unsaved_key_prevents_testing_the_wrong_credential(dialog, monkeypatch):
    """Check unsaved key prevents testing the wrong credential."""
    check = Mock()
    monkeypatch.setattr("ui.settings_dialog.check_openai_connection", check)
    dialog.key_input.setText("sk-fake-unsaved-key")
    dialog.test_button.click()
    assert "Save the new API key" in dialog.message_label.text()
    check.assert_not_called()
    assert dialog._test_thread is None


@pytest.mark.parametrize("result", [(True, "Connection successful."), (False, "Authentication failed.")])
def test_connection_result_reaches_the_ui_and_reenables_controls(dialog, app, monkeypatch, result):
    """Check connection result reaches the ui and reenables controls."""
    monkeypatch.setattr("ui.settings_dialog.check_openai_connection", lambda _model: result)
    dialog.test_button.click()
    wait_until(app, lambda: dialog._test_thread is None)
    assert dialog.message_label.text() == result[1]
    assert dialog.test_button.isEnabled()
    assert dialog.save_key_button.isEnabled()


def test_unexpected_connection_error_does_not_crash_ui(dialog, app, monkeypatch):
    """Check unexpected connection error does not crash ui."""
    check = Mock(side_effect=RuntimeError("sk-private-error"))
    monkeypatch.setattr("ui.settings_dialog.check_openai_connection", check)
    dialog.test_button.click()
    wait_until(app, lambda: dialog._test_thread is None)
    assert dialog.message_label.text() == "Connection test failed. Please try again."
    assert dialog.test_button.isEnabled()


class RecordingDialog(SettingsDialog):
    """Record the threads executing settings-dialog result/cleanup slots."""
    def __init__(self):
        """Initialize the real dialog and lists recording result/cleanup callback threads."""
        self.slot_threads = []
        super().__init__()

    @pyqtSlot(bool, str)
    def _show_test_result(self, success, message):
        """Record the current thread before delegating to the real connection-result slot."""
        self.slot_threads.append(QThread.currentThread())
        super()._show_test_result(success, message)

    @pyqtSlot()
    def _test_finished(self):
        """Record the current thread before delegating to the real connection cleanup slot."""
        assert not self._test_thread.isRunning()
        self.slot_threads.append(QThread.currentThread())
        super()._test_finished()


@pytest.mark.parametrize("close_method", ["escape", "close"])
def test_connection_runs_off_ui_thread_and_closes_safely(app, monkeypatch, close_method):
    """Check connection runs off ui thread and closes safely."""
    release = threading.Event()
    started = threading.Event()
    worker_threads = []

    def check(_model):
        """Record the connection thread and block until the test releases a successful result."""
        worker_threads.append(QThread.currentThread())
        started.set()
        assert release.wait(3)
        return True, "Connection successful."

    check_mock = Mock(side_effect=check)
    monkeypatch.setattr("ui.settings_dialog.check_openai_connection", check_mock)
    widget = RecordingDialog()
    widget.show()
    try:
        widget.test_button.click()
        worker, thread = widget._test_worker, widget._test_thread
        wait_until(app, started.is_set)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        wait_until(app, lambda: bool(ticks))
        assert worker_threads[0] != app.thread()
        assert not release.is_set()
        widget._test_connection()
        assert check_mock.call_count == 1
        if close_method == "escape":
            QTest.keyClick(widget, Qt.Key.Key_Escape)
        else:
            widget.close()
        assert widget.isVisible()
        assert widget._close_pending
        release.set()
        wait_until(app, lambda: widget._test_thread is None)
        wait_until(app, lambda: sip.isdeleted(worker) and sip.isdeleted(thread))
        assert not widget.isVisible()
        assert len(widget.slot_threads) == 2
        assert all(thread == app.thread() for thread in widget.slot_threads)
    finally:
        release.set()
        wait_until(app, lambda: widget._test_thread is None)
        widget.close()
        widget.deleteLater()


def test_settings_button_opens_dialog_and_keeps_incidents(app):
    """Check settings button opens dialog and keeps incidents."""
    window = MainWindow()
    window.show()
    try:
        incidents = [{"incident_classification": "Example", "incident_score": 65}]
        window._show_scan_results(incidents)
        window.settings_button.click()
        assert window._settings_dialog.isVisible()
        window._settings_dialog.close_button.click()
        wait_until(app, lambda: window._settings_dialog is None)
        assert window._incidents == incidents
        assert window.incident_table.item(0, 1).text() == "Example"
    finally:
        window.close()
        window.deleteLater()


def test_closing_parent_waits_for_connection_thread(app, monkeypatch):
    """Check closing parent waits for connection thread."""
    release = threading.Event()
    started = threading.Event()

    def check(_model):
        """Hold a fake connection check open until the test releases it to verify deferred
        parent closing.
        """
        started.set()
        assert release.wait(3)
        return False, "Connection failed."

    monkeypatch.setattr("ui.settings_dialog.check_openai_connection", check)
    window = MainWindow()
    window.show()
    try:
        window.settings_button.click()
        settings = window._settings_dialog
        settings.test_button.click()
        wait_until(app, started.is_set)
        window.close()
        assert window.isVisible()
        assert settings._close_pending
        release.set()
        wait_until(app, lambda: window._settings_dialog is None and not window.isVisible())
    finally:
        release.set()
        wait_until(app, lambda: window._settings_dialog is None or window._settings_dialog._test_thread is None)
        window.close()
        window.deleteLater()
