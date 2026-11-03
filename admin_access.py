"""Inspect this process's elevation and request a new instance through Windows UAC."""

import os
from pathlib import Path
import subprocess
import sys


def elevation_status():
    """Return actual token elevation, or None when the status cannot be checked."""
    if sys.platform != "win32":
        return None
    token = None
    try:
        import win32api
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), 8)  # TOKEN_QUERY
        return bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
    except Exception:
        return None
    finally:
        if token is not None:
            token.Close()


def access_label(status):
    """Describe confirmed process rights rather than administrator group membership."""
    return "Administrator access" if status is True else "Standard access" if status is False else "Access status unavailable"


def _shell_launch(executable, arguments, directory, hwnd):
    """Use the documented runas verb, leaving consent and credentials to Windows."""
    import pythoncom
    import win32api
    from win32com.shell import shell
    pythoncom.CoInitialize()
    try:
        result = shell.ShellExecuteEx(
            fMask=0x40 | 0x100, hwnd=hwnd, lpVerb="runas", lpFile=executable,
            lpParameters=subprocess.list2cmdline(arguments), lpDirectory=directory, nShow=1,
        )
        handle = result.get("hProcess")
        if handle is not None:
            win32api.CloseHandle(handle)
    finally:
        pythoncom.CoUninitialize()


def relaunch_as_administrator(hwnd=0):
    """Request an elevated instance; cancel/failure leaves the caller running.

    Launch only our absolute executable/entry point, never a diagnostic command or
    imported text. The caller drains workers first. No ACL, group or UAC settings
    are changed. PyInstaller's temporary restart marker is restored on every path.
    """
    if sys.platform != "win32":
        return False, "Administrator restart is available only on Windows."
    executable = Path(sys.executable)
    frozen = getattr(sys, "frozen", False)
    arguments = []
    if not frozen:
        windowless = executable.with_name("pythonw.exe")
        if windowless.is_file():
            executable = windowless
        arguments = [str(Path(__file__).resolve().parent / "desktop.py")]
    previous_reset = os.environ.get("PYINSTALLER_RESET_ENVIRONMENT")
    try:
        if frozen:
            os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        _shell_launch(str(executable.resolve()), arguments, str(Path.cwd()), hwnd)
        return True, "Windows started the administrator instance."
    except Exception as exc:
        if getattr(exc, "winerror", None) == 1223:
            return False, "Administrator request cancelled. LogOpen remains open with its current permissions. Monitoring is stopped."
        return False, "Could not start LogOpen as administrator. It remains open with its current permissions. Monitoring is stopped. Your organization may restrict elevation."
    finally:
        if frozen:
            if previous_reset is None:
                os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
            else:
                os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = previous_reset
