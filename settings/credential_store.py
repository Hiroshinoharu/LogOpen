"""Keep the user's OpenAI key in Windows Credential Manager, never in files."""

import os

SERVICE_NAME = "LogOpen"
CREDENTIAL_TARGET = "LogOpen/OpenAI"
_NOT_FOUND = 1168  # Windows ERROR_NOT_FOUND; other errors are storage failures.


class CredentialStoreError(RuntimeError):
    def __init__(self):
        super().__init__("Secure credential storage is unavailable. Check Windows Credential Manager.")


def _backend():
    try:
        import win32cred
        return win32cred
    except ImportError:
        raise CredentialStoreError() from None


def save_openai_api_key(api_key):
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
            "CredentialBlob": api_key.encode("utf-16-le"),
            "Persist": backend.CRED_PERSIST_LOCAL_MACHINE,
            "Comment": f"{SERVICE_NAME} user-provided OpenAI API key",
        }, 0)
    except Exception:
        raise CredentialStoreError() from None


def get_openai_api_key():
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
    try:
        backend = _backend()
        backend.CredDelete(CREDENTIAL_TARGET, backend.CRED_TYPE_GENERIC, 0)
    except Exception as exc:
        if getattr(exc, "winerror", None) != _NOT_FOUND:
            raise CredentialStoreError() from None


def environment_key_configured():
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def resolve_openai_api_key():
    """Prefer the stored key, then the process environment (also if storage fails)."""
    storage_failed = False
    try:
        key = get_openai_api_key()
    except CredentialStoreError:
        storage_failed = True
        key = None
    key = key or os.environ.get("OPENAI_API_KEY", "").strip() or None
    if key is None and storage_failed:
        raise CredentialStoreError()
    return key


def credential_status():
    """Return display text only; never fill widgets with a saved key."""
    try:
        if get_openai_api_key():
            return "API key configured"
    except CredentialStoreError:
        if environment_key_configured():
            return "Secure storage unavailable; using OPENAI_API_KEY from the environment."
        return "Secure credential storage unavailable. AI analysis will be skipped."
    if environment_key_configured():
        return "Using OPENAI_API_KEY from the environment."
    return "No API key configured. AI analysis will be skipped."
