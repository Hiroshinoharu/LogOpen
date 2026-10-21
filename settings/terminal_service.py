"""Open an interactive terminal without passing any suggested command to it."""

import ctypes
from pathlib import Path
import shutil
import subprocess
import sys

from settings.terminal_settings import HOST_LABELS, SHELL_LABELS, TerminalPreferences


def open_terminal(*, shell=None, preferences=None):
    """Open the selected terminal, or match an older command's recorded shell.

    Returns display-safe (success, message). All executable arguments come from
    fixed choices. Report text and generated commands never enter this function.
    """
    preferences = preferences if preferences is not None else TerminalPreferences().load()
    selected = shell if shell is not None else preferences.shell
    if not isinstance(selected, str) or selected not in SHELL_LABELS or preferences.host not in HOST_LABELS:
        return False, "Choose a supported terminal in Settings."
    if sys.platform != "win32":
        return False, "Opening terminals is supported on Windows."
    candidates, arguments = {
        "powershell": (("pwsh.exe", "powershell.exe"), ["-NoLogo", "-NoProfile"]),
        "cmd": (("cmd.exe",), ["/D"]),
        "bash": (("wsl.exe",), ["--exec", "bash", "--noprofile", "--norc", "-i"]),
    }[selected]
    executable = next((path for name in candidates if (path := shutil.which(name))), None)
    if executable is None:
        return False, f"{SHELL_LABELS[selected]} is unavailable. Install it or choose another terminal in Settings."
    command = [executable, *arguments]
    if preferences.host == "windows_terminal":
        host = shutil.which("wt.exe")
        if host is None:
            return False, "Windows Terminal is unavailable. Choose Standard terminal window in Settings."
        command = [host, "-w", "new", "new-tab", *command]
    try:
        # External Windows tools must not inherit PyInstaller's bundled DLL path.
        # Restore it immediately after CreateProcess, including on launch failure.
        frozen = getattr(sys, "frozen", False)
        if frozen:
            ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            subprocess.Popen(command, cwd=str(Path.home()), creationflags=subprocess.CREATE_NEW_CONSOLE)
        finally:
            if frozen:
                ctypes.windll.kernel32.SetDllDirectoryW(getattr(sys, "_MEIPASS", None))
    except OSError:
        return False, "Could not open the terminal. Check your terminal choice in Settings."
    return True, f"Opened {SHELL_LABELS[selected]}. Copy a command, review it, and paste it when ready."
