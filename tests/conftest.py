"""Keep tests away from real user preferences, credentials, and API traffic."""

import socket
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QSettings

from settings import app_settings, credential_store


@pytest.fixture(autouse=True)
def isolated_ai_settings(monkeypatch, tmp_path):
    """Isolate every test with temporary preferences, an empty credential backend, and
    blocked network connections.
    """
    settings_path = str(tmp_path / "preferences.ini")
    monkeypatch.setenv("LOGOPEN_DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setattr(app_settings, "_create_qsettings", lambda: QSettings(
        settings_path, QSettings.Format.IniFormat,
    ))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Never read the developer's persisted Windows environment credentials.
    monkeypatch.setattr(credential_store, "_windows_environment_key", lambda _scope: None)
    monkeypatch.setenv("LOGOPEN_DISABLE_SOUNDS", "1")
    backend = Mock(CRED_TYPE_GENERIC=1, CRED_PERSIST_LOCAL_MACHINE=2)
    missing = OSError("Test credential not found")
    missing.winerror = 1168
    backend.CredRead.side_effect = missing
    backend.CredDelete.side_effect = missing
    monkeypatch.setattr(credential_store, "_backend", lambda: backend)

    def no_network(*_args, **_kwargs):
        """Fail the test if code attempts to open a real network connection."""
        raise AssertionError("Unit tests must mock network requests")

    monkeypatch.setattr(socket.socket, "connect", no_network)
