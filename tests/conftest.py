"""Keep tests away from real user preferences, credentials, and API traffic."""

import socket
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QSettings

from settings import app_settings, credential_store


@pytest.fixture(autouse=True)
def isolated_ai_settings(monkeypatch, tmp_path):
    settings_path = str(tmp_path / "preferences.ini")
    monkeypatch.setattr(app_settings, "_create_qsettings", lambda: QSettings(
        settings_path, QSettings.Format.IniFormat,
    ))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    backend = Mock(CRED_TYPE_GENERIC=1, CRED_PERSIST_LOCAL_MACHINE=2)
    missing = OSError("Test credential not found")
    missing.winerror = 1168
    backend.CredRead.side_effect = missing
    backend.CredDelete.side_effect = missing
    monkeypatch.setattr(credential_store, "_backend", lambda: backend)

    def no_network(*_args, **_kwargs):
        raise AssertionError("Unit tests must mock network requests")

    monkeypatch.setattr(socket.socket, "connect", no_network)
