"""Create the current app's desktop shortcut using the Windows desktop location."""

from pathlib import Path
import subprocess
import sys


class ShortcutError(RuntimeError):
    """A fixed, user-facing shortcut failure."""


def shortcut_details():
    """Use the installed executable, or a windowless source launcher when developing."""
    root = Path(__file__).resolve().parent
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return str(executable), "", str(executable.parent), f"{executable},0"
    windowless = executable.with_name("pythonw.exe")
    if windowless.is_file():
        executable = windowless
    return (str(executable), subprocess.list2cmdline([str(root / "desktop.py")]),
            str(root), f"{root / 'assets' / 'logopen.ico'},0")


def create_desktop_shortcut(*, shell=None):
    """Create/update LogOpen.lnk in the real Windows desktop, including OneDrive desktops."""
    if sys.platform != "win32":
        raise ShortcutError("Desktop shortcuts are supported on Windows.")
    com = None
    try:
        if shell is None:
            import pythoncom
            from win32com.client import Dispatch
            pythoncom.CoInitialize()
            com = pythoncom
            shell = Dispatch("WScript.Shell")
        folder = shell.SpecialFolders("Desktop")
        desktop = Path(folder) if isinstance(folder, str) and folder else None
        if desktop is None or not desktop.is_absolute() or not desktop.is_dir():
            raise ShortcutError("Your Windows desktop folder could not be found.")
        destination = desktop / "LogOpen.lnk"
        target, arguments, directory, icon = shortcut_details()
        shortcut = shell.CreateShortcut(str(destination))
        shortcut.TargetPath = target
        shortcut.Arguments = arguments
        shortcut.WorkingDirectory = directory
        shortcut.IconLocation = icon
        shortcut.Description = "LogOpen — make sense of your Windows problems"
        shortcut.Save()
        return destination
    except ShortcutError:
        raise
    except Exception:
        raise ShortcutError("Could not create the shortcut. Check that your desktop folder is writable.") from None
    finally:
        if com is not None:
            com.CoUninitialize()
