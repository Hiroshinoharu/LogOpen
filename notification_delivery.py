"""Windows notification context and consistent application identity.

Qt tray messages remain best effort: neither a successful API call nor a ready
Windows session proves that a banner appeared. These helpers never change the
user's notification settings or send a message.
"""

from dataclasses import dataclass
from pathlib import Path
import sys


APP_ID = "LogOpen.Desktop"


@dataclass(frozen=True)
class NotificationEnvironment:
    state: str
    message: str
    native_state: int | None = None


def _shell32():
    import ctypes
    return ctypes.WinDLL("shell32", use_last_error=True)


def initialize_app_identity():
    """Identify this desktop process before presenting UI; return whether it succeeded.

    This affects shell grouping/branding. It does not grant permission to show
    notifications, override quiet settings, or register a WinRT toast backend.
    """
    if sys.platform != "win32":
        return False
    try:
        from ctypes import wintypes
        function = _shell32().SetCurrentProcessExplicitAppUserModelID
        function.argtypes = [wintypes.LPCWSTR]
        function.restype = wintypes.HRESULT
        return not (int(function(APP_ID)) & 0x80000000)
    except Exception:
        return False


def notification_environment():
    """Read the Windows interruption state, without interpreting it as delivery.

    SHQueryUserNotificationState does not expose every Windows notification or
    Do not disturb preference. A ready result therefore remains conditional.
    """
    if sys.platform != "win32":
        return NotificationEnvironment("unsupported", "Windows notification diagnostics are unavailable on this platform.")
    try:
        import ctypes
        from ctypes import wintypes
        state = wintypes.UINT()
        function = _shell32().SHQueryUserNotificationState
        function.argtypes = [ctypes.POINTER(wintypes.UINT)]
        function.restype = wintypes.HRESULT
        if int(function(ctypes.byref(state))) & 0x80000000:
            raise OSError("Windows notification state is unavailable")
    except Exception:
        return NotificationEnvironment("unknown", "Windows notification readiness could not be checked. Use Test notification and check Windows notification settings.")
    messages = {
        1: ("suppressed", "Windows reports a locked or inactive session, or a screen saver. Notification banners may be delayed."),
        2: ("suppressed", "Windows reports a full-screen app or presentation settings. Notification banners may be hidden."),
        3: ("suppressed", "Windows reports an exclusive full-screen app. Notification banners may be hidden."),
        4: ("suppressed", "Windows presentation mode is suppressing pop-up notifications."),
        5: ("ready", "Windows reports notifications can be sent. Banner display still depends on Windows notification settings and Do not disturb."),
        6: ("suppressed", "Windows is in its post-setup quiet period. Notification banners may be suppressed."),
        7: ("unknown", "Windows reports another app is active. This state does not confirm whether a notification banner can appear."),
    }
    code, message = messages.get(state.value, ("unknown", "Windows returned an unrecognized notification state. Use Test notification to check banner display."))
    return NotificationEnvironment(code, message, state.value)


def apply_shortcut_identity(path):
    """Give a newly created shortcut the same shell identity as the desktop app.

    Failure is nonfatal: an ordinary launch shortcut still works. No notification
    preference, policy, or existing unrelated shortcut is modified.
    """
    path = Path(path)
    if sys.platform != "win32" or path.suffix.lower() != ".lnk" or not path.is_file():
        return False
    com = None
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32com.shell import shellcon
        pythoncom.CoInitialize()
        com = pythoncom
        store = propsys.SHGetPropertyStoreFromParsingName(
            str(path), None, shellcon.GPS_READWRITE, propsys.IID_IPropertyStore,
        )
        store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID, pythoncom.VT_LPWSTR))
        store.Commit()
        return True
    except Exception:
        return False
    finally:
        if com is not None:
            com.CoUninitialize()
