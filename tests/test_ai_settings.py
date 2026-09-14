"""Settings, secret storage, and AI integration tests; all API calls are mocked."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import openai
import pytest
from PyQt6.QtCore import QSettings

import config
from settings.app_settings import AISettings, AppSettings, MAX_ANALYSES, SettingsError
from settings import credential_store as credentials
from settings.ai_service import check_openai_connection
from json_reporting import export_incidents_to_json


@pytest.fixture
def credential_backend(monkeypatch):
    values = {}
    backend = Mock(CRED_TYPE_GENERIC=1, CRED_PERSIST_LOCAL_MACHINE=2)

    def read(target, kind, flags):
        assert (target, kind, flags) == (credentials.CREDENTIAL_TARGET, 1, 0)
        if target not in values:
            error = OSError("Credential not found")
            error.winerror = 1168
            raise error
        return values[target]

    def write(value, flags):
        assert flags == 0
        values[value["TargetName"]] = value.copy()

    def delete(target, kind, flags):
        read(target, kind, flags)
        del values[target]

    backend.CredRead.side_effect = read
    backend.CredWrite.side_effect = write
    backend.CredDelete.side_effect = delete
    monkeypatch.setattr(credentials, "_backend", lambda: backend)
    return backend, values


def test_key_round_trip_and_removal(credential_backend):
    backend, values = credential_backend
    assert credentials.get_openai_api_key() is None
    credentials.save_openai_api_key("  sk-fake-user-key  ")
    assert credentials.get_openai_api_key() == "sk-fake-user-key"
    stored = values[credentials.CREDENTIAL_TARGET]
    assert stored["Persist"] == backend.CRED_PERSIST_LOCAL_MACHINE
    assert stored["CredentialBlob"] == "sk-fake-user-key".encode("utf-16-le")
    assert credentials.credential_status() == "API key configured"
    credentials.delete_openai_api_key()
    assert credentials.get_openai_api_key() is None
    credentials.delete_openai_api_key()  # Removing an absent key is harmless.


@pytest.mark.parametrize("stored,environment,expected", [
    (None, None, None), ("stored-user-key", None, "stored-user-key"),
    (None, "environment-key", "environment-key"),
    ("stored-user-key", "environment-key", "stored-user-key"),
    (None, "   ", None),
])
def test_credential_precedence(monkeypatch, credential_backend, stored, environment, expected):
    if stored:
        credentials.save_openai_api_key(stored)
    if environment is not None:
        monkeypatch.setenv("OPENAI_API_KEY", environment)
    assert credentials.resolve_openai_api_key() == expected


@pytest.mark.parametrize("operation", ["save", "read", "delete"])
def test_storage_failures_never_expose_exception_contents(credential_backend, operation):
    backend, _ = credential_backend
    for method in (backend.CredRead, backend.CredWrite, backend.CredDelete):
        method.side_effect = OSError("sensitive-error-sk-fake-user-key")
    with pytest.raises(credentials.CredentialStoreError) as caught:
        if operation == "save":
            credentials.save_openai_api_key("sk-fake-user-key")
        elif operation == "read":
            credentials.get_openai_api_key()
        else:
            credentials.delete_openai_api_key()
    assert "sensitive-error" not in str(caught.value)
    assert "sk-fake" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_unavailable_storage_allows_environment_fallback(monkeypatch, credential_backend):
    backend, _ = credential_backend
    backend.CredRead.side_effect = OSError("unavailable")
    with pytest.raises(credentials.CredentialStoreError):
        credentials.resolve_openai_api_key()
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    assert credentials.resolve_openai_api_key() == "environment-key"
    assert "environment" in credentials.credential_status()


@pytest.mark.parametrize("key", ["", "   ", "key with whitespace", "key\x00", "a" * 2049])
def test_invalid_key_is_not_saved(credential_backend, key):
    backend, _ = credential_backend
    with pytest.raises(ValueError):
        credentials.save_openai_api_key(key)
    backend.CredWrite.assert_not_called()


def test_preferences_persist_separately_from_secrets(tmp_path, credential_backend):
    path = str(tmp_path / "ordinary-settings.ini")
    prefs = AISettings(False, "custom-model", 7)
    credentials.save_openai_api_key("sk-fake-user-key")
    AppSettings(QSettings(path, QSettings.Format.IniFormat)).save(prefs)
    assert AppSettings(QSettings(path, QSettings.Format.IniFormat)).load() == prefs
    text = (tmp_path / "ordinary-settings.ini").read_text()
    assert "sk-fake-user-key" not in text
    assert set(QSettings(path, QSettings.Format.IniFormat).allKeys()) == {
        "ai/enabled", "ai/model", "ai/max_analyses",
    }


def test_config_defaults_and_user_preference_precedence(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LLM_ANALYSIS", False)
    monkeypatch.setattr(config, "DEFAULT_ANALYSIS_MODEL", "existing-model")
    monkeypatch.setattr(config, "MAX_LLM_ANALYSES_PER_RUN", 5)
    assert AppSettings().load() == AISettings(False, "existing-model", 5)
    prefs = AISettings(True, "user-model", 2)
    AppSettings().save(prefs)
    assert AppSettings().load() == prefs


@pytest.mark.parametrize("enabled,model,limit,expected_limit", [
    ("corrupt", "bad model", "oops", 3),
    ("false", "", -5, 1),
    (False, "sk-never-store-a-key-here", 99999, MAX_ANALYSES),
    ([], [], 1.5, 3),
])
def test_malformed_preferences_recover_safely(enabled, model, limit, expected_limit):
    settings = AppSettings()
    settings._storage.setValue("ai/enabled", enabled)
    settings._storage.setValue("ai/model", model)
    settings._storage.setValue("ai/max_analyses", limit)
    assert settings.load() == AISettings(False, config.DEFAULT_ANALYSIS_MODEL, expected_limit)


def test_unavailable_preferences_disable_ai_and_report_save_failure():
    storage = Mock()
    storage.status.return_value = QSettings.Status.AccessError
    settings = AppSettings(storage)
    assert not settings.load().enabled
    with pytest.raises(SettingsError, match="Could not save AI settings"):
        settings.save(AISettings(True, "model", 3))


@pytest.mark.parametrize("preferences", [
    AISettings(True, "model", 0), AISettings(True, "model", 21),
    AISettings(True, "model", 2.5), AISettings(True, "bad model", 3),
    AISettings(True, "sk-fake-key", 3),
])
def test_invalid_preferences_are_rejected_without_writing(preferences):
    storage = Mock()
    with pytest.raises(ValueError):
        AppSettings(storage).save(preferences)
    storage.setValue.assert_not_called()


@pytest.fixture
def api_client(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    client.responses.parse.return_value = SimpleNamespace(output_parsed={
        "explanation": "A test explanation.", "likely_causes": [],
        "recommended_actions": [], "remediation_notes": [], "diagnostic_steps": [],
    })
    constructor = Mock(return_value=client)
    monkeypatch.setattr(openai, "OpenAI", constructor)
    return client, constructor


def test_disabled_ai_skips_selection_and_direct_api_calls(api_client, monkeypatch):
    import main
    from llm_analysis import analyse_incident_with_llm, select_incidents_for_llm
    AppSettings().save(AISettings(False, "test-model", 3))
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    incident = {"incident_priority": "Critical"}
    assert select_incidents_for_llm([incident]) == []
    assert analyse_incident_with_llm(incident) is None
    analyse = Mock()
    monkeypatch.setattr(main, "analyse_incident_with_llm", analyse)
    main.add_llm_analyses([incident])
    analyse.assert_not_called()
    api_client[1].assert_not_called()


def test_saved_model_limit_and_key_reach_llm_but_never_reports(api_client, credential_backend, tmp_path, monkeypatch, capsys):
    import main
    client, constructor = api_client
    credentials.save_openai_api_key("sk-fake-user-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-environment-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://untrusted.invalid")
    AppSettings().save(AISettings(True, "selected-model", 2))
    incidents = [{"incident_priority": "High", "incident_score": score} for score in (10, 90, 60)]
    main.add_llm_analyses(incidents)
    assert client.responses.parse.call_count == 2
    assert "llm_analysis" not in incidents[0]
    assert incidents[1]["llm_analysis"] is not None
    assert incidents[2]["llm_analysis"] is not None
    assert client.responses.parse.call_args.kwargs["model"] == "selected-model"
    assert constructor.call_args.kwargs["api_key"] == "sk-fake-user-secret"
    assert constructor.call_args.kwargs["base_url"] == "https://api.openai.com/v1"
    report = tmp_path / "incidents.json"
    export_incidents_to_json(incidents, report)
    output = capsys.readouterr()
    for secret in ("sk-fake-user-secret", "fake-environment-secret"):
        assert secret not in report.read_text()
        assert secret not in str(client.responses.parse.call_args)
        assert secret not in output.out + output.err


@pytest.mark.parametrize("enabled", [False, True])
def test_deterministic_scan_continues_without_ai_credentials(monkeypatch, api_client, enabled):
    import main
    from tests.helpers import make_event
    AppSettings().save(AISettings(enabled, "test-model", 3))
    monkeypatch.setattr(config, "LOG_TYPES", ["System"])
    monkeypatch.setattr(main, "get_recent_events", lambda *_args, **_kw: [make_event()] * 5)
    monkeypatch.setattr(main, "filter_events_by_time", lambda events, _hours: events)
    export = Mock()
    monkeypatch.setattr(main, "export_incidents_to_json", export)
    incidents = main.scan_system()
    assert incidents and "incident_score" in incidents[0]
    assert "incident_classification" in incidents[0]
    assert incidents[0].get("llm_analysis") is None
    export.assert_called_once()
    api_client[1].assert_not_called()


def test_unexpected_analysis_error_cannot_leak_into_export(monkeypatch, tmp_path, capsys):
    import main
    AppSettings().save(AISettings(True, "test-model", 3))
    monkeypatch.setattr(main, "analyse_incident_with_llm", Mock(side_effect=RuntimeError("sk-fake-secret")))
    incidents = [{"incident_priority": "Critical"}]
    main.add_llm_analyses(incidents)
    report = tmp_path / "incidents.json"
    export_incidents_to_json(incidents, report)
    assert "sk-fake-secret" not in report.read_text()
    assert json.loads(report.read_text())[0]["llm_analysis"] is None
    output = capsys.readouterr()
    assert "sk-fake-secret" not in output.out + output.err


def test_connection_uses_only_metadata_with_short_timeout(api_client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-environment-key")
    client, constructor = api_client
    success, message = check_openai_connection("selected-model")
    assert success and "successful" in message
    constructor.assert_called_once_with(api_key="fake-environment-key",
                                       base_url="https://api.openai.com/v1", timeout=10.0, max_retries=0)
    client.models.retrieve.assert_called_once_with("selected-model")
    client.responses.parse.assert_not_called()
    client.__exit__.assert_called_once()


@pytest.mark.parametrize("status,expected", [(401, "Authentication failed"),
    (403, "access denied"), (404, "Model unavailable"), (429, "rate limit"), (500, "error")])
def test_connection_api_errors_are_safe(api_client, monkeypatch, status, expected):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-secret")
    response = SimpleNamespace(status_code=status, request=Mock(), headers={})
    error_type = {401: openai.AuthenticationError, 429: openai.RateLimitError}.get(status, openai.APIStatusError)
    api_client[0].models.retrieve.side_effect = error_type("sensitive-sk-fake-secret", response=response, body=None)
    success, message = check_openai_connection("test-model")
    assert not success and expected in message
    assert "sk-fake-secret" not in message


@pytest.mark.parametrize("error", [
    openai.APIConnectionError(request=Mock()),
    openai.APITimeoutError(request=Mock()),
    RuntimeError("sensitive-sk-fake-secret"),
])
def test_connection_network_and_unexpected_errors_are_safe(api_client, monkeypatch, error):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-secret")
    api_client[0].models.retrieve.side_effect = error
    success, message = check_openai_connection("test-model")
    assert not success
    assert "sk-fake-secret" not in message


def test_connection_missing_key_does_not_create_a_client(api_client):
    success, message = check_openai_connection("test-model")
    assert not success and "No API key" in message
    api_client[1].assert_not_called()
