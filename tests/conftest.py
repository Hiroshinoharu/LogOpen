"""Keep tests away from real user preferences, credentials, and API traffic."""

import socket
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QSettings

from settings import app_settings, credential_store
from notification_delivery import NotificationEnvironment


@pytest.fixture(scope="session", autouse=True)
def desktop_font_metrics():
    """Use real Windows glyph metrics for headless readability/layout checks.

    The offscreen Qt plugin can omit system font discovery. Its fallback draws
    boxes and measures every character as an em, which cannot model this UI.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QFontDatabase
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
        path = fonts / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    yield app


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
    # Desktop tests must not depend on the host's current interruption state.
    monkeypatch.setattr("ui.assistance.notification_environment", lambda: NotificationEnvironment(
        "unknown", "Host notification policy is not queried by unit tests.",
    ))
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
