"""Small OpenAI client boundary shared by analysis and connection testing."""

from settings.credential_store import CredentialStoreError, resolve_openai_api_key


class MissingCredentialsError(RuntimeError):
    """Signal that neither secure storage nor the environment supplied a key."""
    def __init__(self):
        """Initialize a fixed missing-key message suitable for user-facing handling."""
        super().__init__("No API key configured. Save a key in Settings or set OPENAI_API_KEY.")


def create_openai_client(*, timeout=60.0, max_retries=2):
    """Resolve credentials and construct an OpenAI client for the official endpoint.

    Args:
        timeout: Request timeout in seconds (60 by default).
        max_retries: SDK retry allowance (two by default).

    Returns:
        OpenAI: A client the caller must close, normally using a with block.

    Raises:
        MissingCredentialsError: Neither key source is configured.
        CredentialStoreError: Secure storage failed and no environment key exists.
        ImportError: The optional OpenAI SDK is unavailable.

    Does not issue an API request itself. SDK construction errors propagate;
    callers translate them into safe messages at the service boundary.
    """
    key = resolve_openai_api_key()
    if not key:
        raise MissingCredentialsError()
    from openai import OpenAI
    # User credentials are only sent to OpenAI, even if OPENAI_BASE_URL is set.
    return OpenAI(api_key=key, base_url="https://api.openai.com/v1",
                  timeout=timeout, max_retries=max_retries)


def check_openai_connection(model):
    """Check credentials and model metadata access without running incident analysis.

    Args:
        model: Model identifier to validate and retrieve from the API.

    Returns:
        tuple[bool, str]: Success flag and display-safe explanation. Expected
        and unexpected Exception failures become false results, not raw errors.

    Uses a ten-second timeout and zero retries, closes the client, and sends
    no incident data. It can be invoked while AI is disabled. Metadata access
    does not establish that a later inference request will succeed.
    """
    try:
        from openai import (
            APIConnectionError, APIStatusError, APITimeoutError,
            AuthenticationError, RateLimitError,
        )
    except ImportError:
        return False, "The OpenAI SDK is not installed."
    try:
        from settings.app_settings import validate_model
        model = validate_model(model)
        with create_openai_client(timeout=10.0, max_retries=0) as client:
            client.models.retrieve(model)
        return True, "Connection successful. Authentication and model access verified."
    except MissingCredentialsError:
        return False, "No API key configured. Save a key or set OPENAI_API_KEY."
    except CredentialStoreError:
        return False, "Secure credential storage unavailable. Check Windows Credential Manager."
    except AuthenticationError:
        return False, "Authentication failed. Check or replace your OpenAI API key."
    except (APIConnectionError, APITimeoutError):
        return False, "Could not connect to OpenAI. Check your network and try again."
    except RateLimitError:
        return False, "OpenAI rate limit reached. Try again later."
    except APIStatusError as exc:
        if exc.status_code in (403, 404):
            return False, "Model unavailable or access denied. Check the model and key permissions."
        return False, "OpenAI returned an error. Try again later."
    except Exception:
        return False, "Connection test failed. Check your AI settings and try again."
