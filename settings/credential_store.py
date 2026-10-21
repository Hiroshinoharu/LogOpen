"""Store the OpenAI key in Windows Credential Manager and resolve its fallback.

The generic credential target is LogOpen/OpenAI for the current Windows user.
Saved keys take precedence over OPENAI_API_KEY in the process, user, then system environment.
No plaintext file fallback is used. Backend exceptions are replaced with a
fixed message because their contents may include sensitive data.
"""

import os

SERVICE_NAME = "LogOpen"
CREDENTIAL_TARGET = "LogOpen/OpenAI"
_NOT_FOUND = 1168  # Windows ERROR_NOT_FOUND; other errors are storage failures.


class CredentialStoreError(RuntimeError):
    """Credential storage failure with fixed text suitable for display to the user."""
    def __init__(self):
        """Construct the safe storage error without accepting backend exception text."""
        super().__init__("Secure credential storage is unavailable. Check Windows Credential Manager.")


def _backend():
    """Load the pywin32 interface to Windows Credential Manager on demand.

    Returns:
        module: win32cred, providing credential read/write/delete operations.

    Raises:
        CredentialStoreError: win32cred cannot be imported.

    Lazy import keeps module import usable without pywin32 and lets tests
    substitute an isolated backend. Import failure details are not exposed.
    """
    try:
        import win32cred
        return win32cred
    except ImportError:
        raise CredentialStoreError() from None


def save_openai_api_key(api_key):
    """Validate and immediately save or replace the user's OpenAI credential.

    Args:
        api_key: Nonblank ASCII text, at most 2048 characters after trimming,
            without embedded whitespace or control characters below code 33.

    Raises:
        ValueError: The supplied value fails local input validation.
        CredentialStoreError: The Windows backend cannot write the credential.

    Passes Unicode text to CredWrite; pywin32 handles its Windows encoding.
    Persistence is local-machine scoped for this user, including later logons.
    Returns None and performs no authentication/network check.
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("Enter an OpenAI API key first.")
    api_key = api_key.strip()
    if len(api_key) > 2048 or not api_key.isascii() or any(char.isspace() or ord(char) < 33 for char in api_key):
        raise ValueError("Enter a valid OpenAI API key without whitespace.")
    try:
        backend = _backend()
        backend.CredWrite({
            "Type": backend.CRED_TYPE_GENERIC,
            "TargetName": CREDENTIAL_TARGET,
            "UserName": "OpenAI",
            # pywin32 expects Unicode here and performs the Windows encoding.
            # CredRead may return bytes, which get_openai_api_key decodes below.
            "CredentialBlob": api_key,
            "Persist": backend.CRED_PERSIST_LOCAL_MACHINE,
            "Comment": f"{SERVICE_NAME} user-provided OpenAI API key",
        }, 0)
    except Exception:
        raise CredentialStoreError() from None


def get_openai_api_key():
    """Read and return the saved secret, or None if absent/blank.

    CredRead may return Unicode or UTF-16-LE bytes; both are normalized to a
    stripped string. Only Windows error 1168 means not found. Other read/decode
    failures raise CredentialStoreError with fixed text. Callers must not put
    the returned secret into UI labels, logs, or exported incident data.
    """
    try:
        backend = _backend()
        credential = backend.CredRead(CREDENTIAL_TARGET, backend.CRED_TYPE_GENERIC, 0)
        key = credential["CredentialBlob"]
        if isinstance(key, bytes):
            key = key.decode("utf-16-le")
        if not isinstance(key, str):
            raise CredentialStoreError()
        return key.strip() or None
    except Exception as exc:
        if getattr(exc, "winerror", None) == _NOT_FOUND:
            return None
        raise CredentialStoreError() from None


def delete_openai_api_key():
    """Delete the saved credential immediately; a missing entry is a no-op.

    Returns None. Other backend failures raise CredentialStoreError.
    OPENAI_API_KEY is unchanged and can still supply a fallback credential.
    """
    try:
        backend = _backend()
        backend.CredDelete(CREDENTIAL_TARGET, backend.CRED_TYPE_GENERIC, 0)
    except Exception as exc:
        if getattr(exc, "winerror", None) != _NOT_FOUND:
            raise CredentialStoreError() from None


def _windows_environment_key(scope):
    """Read only OPENAI_API_KEY from Windows' persisted environment, without caching.

    A key added since launch is usable without restarting. Missing/inaccessible
    values are ignored; no registry changes or environment enumeration occur.
    """
    try:
        import winreg
        root, path = ((winreg.HKEY_CURRENT_USER, "Environment") if scope == "user" else
                      (winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"))
        with winreg.OpenKey(root, path) as handle:
            value, kind = winreg.QueryValueEx(handle, "OPENAI_API_KEY")
        if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str):
            return value.strip() or None
    except (ImportError, OSError, ValueError):
        pass
    return None


def _environment_key():
    """Return (secret, source) in process/user/system order; never expose the secret."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key, "the environment"
    for scope in ("user", "system"):
        key = _windows_environment_key(scope)
        if key:
            return key, f"Windows {scope} environment settings"
    return None, None


def environment_key_configured():
    """Check process and persisted Windows environment key availability."""
    return _environment_key()[0] is not None


def resolve_openai_api_key():
    """Return the saved key, falling back to process/user/system environment settings.

    A missing stored key or unavailable secure storage allows OPENAI_API_KEY.
    If neither source supplies a key, return None unless storage failed, in
    which case raise CredentialStoreError. This chooses a credential without
    authenticating it; a rejected saved key does not trigger an API retry using
    the environment key.
    """
    storage_failed = False
    try:
        key = get_openai_api_key()
    except CredentialStoreError:
        storage_failed = True
        key = None
    key = key or _environment_key()[0]
    if key is None and storage_failed:
        raise CredentialStoreError()
    return key


def credential_status():
    """Return display-safe text describing configured, missing, or unavailable keys.

    Reads credential availability and checks the environment fallback, but
    returns neither the secret nor backend error details. Used to refresh UI
    labels while leaving the password field empty.
    """
    storage_failed = False
    try:
        if get_openai_api_key():
            return "API key configured"
    except CredentialStoreError:
        storage_failed = True
    key, source = _environment_key()
    if key:
        prefix = "Secure storage unavailable; using" if storage_failed else "Using"
        return f"{prefix} OPENAI_API_KEY from {source}."
    if storage_failed:
        return "Secure credential storage unavailable. AI analysis will be skipped."
    return "No API key configured. AI analysis will be skipped."
