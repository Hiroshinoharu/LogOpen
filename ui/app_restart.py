"""Launch a fresh desktop instance for source runs and packaged applications."""

import os
from pathlib import Path
import subprocess
import sys


def relaunch_app():
    """Start LogOpen without a shell; return False if the process cannot start.

    Source runs use the absolute desktop entry point so a changed working
    directory is harmless. Frozen runs start the installed executable as an
    independent PyInstaller instance that can outlive the current process.
    """
    command = [sys.executable]
    environment = os.environ.copy()
    if getattr(sys, "frozen", False):
        # https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#independent-subprocess
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    else:
        command.append(str(Path(__file__).resolve().parents[1] / "desktop.py"))
    try:
        subprocess.Popen(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except OSError:
        return False
    return True
