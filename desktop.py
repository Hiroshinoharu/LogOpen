"""Launch LogOpen's desktop interface, including a noninteractive bundle check.

The PyInstaller entry point starts no scan automatically. Frozen applications
have no terminal, so missing output streams are replaced with the null device.
Their working directory is a writable per-user data directory, keeping relative
report exports away from the installation directory.
"""

from contextlib import ExitStack
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def _prepare_runtime():
    """Provide output streams and choose a writable directory when frozen.

    Source launches retain the caller's working directory. LOGOPEN_DATA_DIR,
    when present, overrides the packaged app's default LOCALAPPDATA/LogOpen
    directory. Directory and file errors propagate to the startup error handler.
    """
    for stream_name in ("stdout", "stderr"):
        if getattr(sys, stream_name) is None:
            setattr(sys, stream_name, open(os.devnull, "w", encoding="utf-8"))
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        directory = Path(os.environ.get("LOGOPEN_DATA_DIR", base / "LogOpen"))
        directory.mkdir(parents=True, exist_ok=True)
        os.chdir(directory)


def _check_optional_runtime():
    """Check native imports and an offline AI client without using saved secrets.

    Creating the client and accessing its resource objects make no HTTP requests.
    A synthetic key prevents the check from consulting environment credentials.
    """
    import win32cred
    import win32evtlog
    from openai import OpenAI
    from models.incident_analysis import IncidentAnalysis

    assert callable(win32cred.CredRead)
    assert callable(win32evtlog.ReadEventLog)
    assert callable(win32evtlog.EvtQuery)
    assert callable(win32evtlog.EvtRender)
    assert IncidentAnalysis.model_json_schema()["type"] == "object"
    with OpenAI(api_key="logopen-offline-build-check") as client:
        assert callable(client.responses.parse)
        assert callable(client.models.retrieve)


def main():
    """Start Qt and return its exit code, or validate a hidden UI and return zero.

    --smoke-test builds and closes the real window with Qt's offscreen platform
    and temporary application storage. It starts no scan or AI request. This
    checks bundled imports, widgets, and the stylesheet without showing a window.
    """
    smoke_test = "--smoke-test" in sys.argv
    with ExitStack() as resources:
        if smoke_test:
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            directory = resources.enter_context(TemporaryDirectory(prefix="logopen-smoke-"))
            os.environ["LOGOPEN_DATA_DIR"] = directory
        original_directory = Path.cwd()
        try:
            _prepare_runtime()
            from PyQt6.QtWidgets import QApplication
            from ui.main_window import MainWindow

            app = QApplication([argument for argument in sys.argv if argument != "--smoke-test"])
            app.setStyle("Fusion")
            app.setApplicationName("LogOpen")
            app.setOrganizationName("LogOpen")
            window = MainWindow()
            if smoke_test:
                _check_optional_runtime()
                if not window.styleSheet().strip():
                    raise RuntimeError("The packaged application stylesheet is missing.")
                app.processEvents()
                window.close()
                app.processEvents()
                return 0
            window.show()
            return app.exec()
        finally:
            # Windows cannot remove a temporary directory while it is the CWD.
            if smoke_test:
                os.chdir(original_directory)


if __name__ == "__main__":
    try:
        result = main()
    except Exception:
        if "--smoke-test" not in sys.argv:
            raise
        # Avoid PyInstaller's interactive failure dialog during automated checks.
        import traceback
        traceback.print_exc()
        result = 1
    raise SystemExit(result)
