"""Model metadata and Windows environment discovery without real keys or network."""

import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import openai
import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QComboBox

from settings import credential_store as credentials
from settings.ai_service import create_openai_client, list_openai_models
from settings.app_settings import AISettings, AppSettings
from tests.test_settings_dialog import app, dialog, wait_until
from ui.settings_dialog import SettingsDialog


# Capture the implementation before the autouse fixture blocks real registry reads.
read_windows_key = credentials._windows_environment_key


@pytest.fixture
def registry(monkeypatch):
    values = {}
    native = SimpleNamespace(HKEY_CURRENT_USER=1, HKEY_LOCAL_MACHINE=2, REG_SZ=1, REG_EXPAND_SZ=2)

    def open_key(root, path):
        expected = "Environment" if root == 1 else r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
        assert path == expected
        context = MagicMock()
        context.__enter__.return_value = root
        return context

    def query(root, name):
        assert name == "OPENAI_API_KEY"  # Never enumerate unrelated secrets.
        if root not in values:
            raise FileNotFoundError()
        return values[root]

    native.OpenKey = Mock(side_effect=open_key)
    native.QueryValueEx = Mock(side_effect=query)
    monkeypatch.setitem(sys.modules, "winreg", native)
    monkeypatch.setattr(credentials, "_windows_environment_key", read_windows_key)
    return values, native


def test_system_key_added_after_launch_is_used_by_next_client(registry, monkeypatch):
    values, _native = registry
    assert credentials.resolve_openai_api_key() is None
    values[2] = ("  fake-system-key  ", 1)
    assert credentials.resolve_openai_api_key() == "fake-system-key"
    assert "Windows system" in credentials.credential_status()
    constructor = Mock()
    monkeypatch.setattr(openai, "OpenAI", constructor)
    create_openai_client().close()
    assert constructor.call_args.kwargs["api_key"] == "fake-system-key"
    assert "fake-system-key" not in credentials.credential_status()


def test_saved_process_user_system_precedence(registry, monkeypatch):
    values, _native = registry
    values.update({1: ("user-key", 1), 2: ("system-key", 1)})
    assert credentials.resolve_openai_api_key() == "user-key"
    monkeypatch.setenv("OPENAI_API_KEY", "process-key")
    assert credentials.resolve_openai_api_key() == "process-key"
    monkeypatch.setattr(credentials, "get_openai_api_key", lambda: "saved-key")
    assert credentials.resolve_openai_api_key() == "saved-key"


@pytest.mark.parametrize("value", [("  ", 1), (42, 4), (b"not-text", 3)])
def test_unusable_user_value_falls_back_to_system(registry, value):
    values, _native = registry
    values.update({1: value, 2: ("system-key", 1)})
    assert credentials.resolve_openai_api_key() == "system-key"


def test_registry_and_credential_failures_are_safe(registry, monkeypatch):
    _values, native = registry
    native.QueryValueEx.side_effect = PermissionError("private-registry-details")
    assert credentials.resolve_openai_api_key() is None
    assert "private" not in credentials.credential_status()
    native.QueryValueEx.side_effect = None
    native.QueryValueEx.return_value = ("fake-key", 1)
    monkeypatch.setattr(credentials, "get_openai_api_key", Mock(side_effect=credentials.CredentialStoreError()))
    assert credentials.resolve_openai_api_key() == "fake-key"
    assert "Secure storage unavailable" in credentials.credential_status()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    result = MagicMock()
    result.__enter__.return_value = result
    constructor = Mock(return_value=result)
    monkeypatch.setattr(openai, "OpenAI", constructor)
    return result, constructor


def test_models_are_metadata_only_filtered_deduplicated_and_closed(client):
    sdk, constructor = client
    sdk.models.list.return_value = [SimpleNamespace(id=name) for name in (
        "gpt-6-luna", "gpt-6-luna", "gpt-6.1-sol", "o3", "ft:gpt-4.1:org:custom:id",
        "text-embedding-3-small", "gpt-image-2", "gpt-audio", "gpt-4o-transcribe",
        "gpt-6-codex", "gpt-5-search-api", "gpt-4o-mini-tts", "gpt-realtime", "sk-secret", "bad id",
    )]
    models, message = list_openai_models()
    assert models == ["ft:gpt-4.1:org:custom:id", "gpt-6-luna", "gpt-6.1-sol", "o3"]
    assert "Loaded 4" in message
    sdk.models.list.assert_called_once_with()
    sdk.responses.parse.assert_not_called()
    sdk.__exit__.assert_called_once()
    assert constructor.call_args.kwargs == dict(api_key="fake-key", base_url="https://api.openai.com/v1", timeout=10.0, max_retries=0)


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_model_list_errors_do_not_expose_credentials(client, status):
    response = SimpleNamespace(status_code=status, request=Mock(), headers={})
    error_class = {401: openai.AuthenticationError, 429: openai.RateLimitError}.get(status, openai.APIStatusError)
    client[0].models.list.side_effect = error_class("private-fake-key", response=response, body=None)
    models, message = list_openai_models()
    assert models == [] and "private" not in message and "fake-key" not in message


def test_missing_key_never_constructs_model_client(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(openai, "OpenAI", constructor)
    assert "No API key" in list_openai_models()[1]
    constructor.assert_not_called()


def test_dropdown_keeps_custom_selection_without_network_on_open(app, monkeypatch):
    AppSettings().save(AISettings(False, "my-custom-model", 2))
    request = Mock(return_value=(["gpt-6-luna", "gpt-6.1-sol"], "Loaded models."))
    monkeypatch.setattr("ui.settings_dialog.list_openai_models", request)
    widget = SettingsDialog()
    try:
        request.assert_not_called()
        assert isinstance(widget.model_input, QComboBox)
        assert widget.model_input.isEditable()
        assert widget.model_input.currentText() == "my-custom-model"
        widget.load_models_button.click()
        wait_until(app, lambda: widget._test_thread is None)
        assert widget.model_input.currentText() == "my-custom-model"
        widget.model_input.setCurrentIndex(widget.model_input.findText("gpt-6-luna"))
        widget.save_settings_button.click()
        assert AppSettings().load().model == "gpt-6-luna"
    finally:
        wait_until(app, lambda: widget._test_thread is None)
        widget.close()
        widget.deleteLater()


def test_unsaved_key_blocks_loading_models(dialog, monkeypatch):
    request = Mock()
    monkeypatch.setattr("ui.settings_dialog.list_openai_models", request)
    dialog.key_input.setText("fake-unsaved-key")
    dialog.load_models_button.click()
    request.assert_not_called()
    assert "Save the new API key" in dialog.message_label.text()


def test_detection_button_finds_new_key_without_redisplaying_it(dialog, registry):
    values, _native = registry
    values[1] = ("fake-private-user-key", 1)
    dialog.detect_key_button.click()
    assert "Windows user" in dialog.key_status.text()
    assert "fake-private-user-key" not in dialog.key_status.text()
    assert not dialog.key_input.text()


def test_model_load_runs_off_gui_thread_and_defers_close(dialog, app, monkeypatch):
    started, release = threading.Event(), threading.Event()
    threads = []

    def load():
        threads.append(QThread.currentThread())
        started.set()
        assert release.wait(3)
        return [], "Could not load models."

    monkeypatch.setattr("ui.settings_dialog.list_openai_models", load)
    selected = dialog.model_input.currentText()
    try:
        dialog.load_models_button.click()
        wait_until(app, started.is_set)
        assert not dialog.test_button.isEnabled() and not dialog.restart_button.isEnabled()
        assert threads[0] != app.thread()
        dialog.close()
        assert dialog.isVisible() and dialog._close_pending
    finally:
        release.set()
        wait_until(app, lambda: dialog._test_thread is None)
    assert not dialog.isVisible()
    assert dialog.model_input.currentText() == selected
