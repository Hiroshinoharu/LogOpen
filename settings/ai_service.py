"""Small OpenAI client boundary shared by analysis and connection testing."""

from settings.credential_store import CredentialStoreError, resolve_openai_api_key


class MissingCredentialsError(RuntimeError):
    def __init__(self):
        super().__init__("No API key configured. Save a key in Settings or set OPENAI_API_KEY.")


def create_openai_client(*, timeout=60.0, max_retries=2):
    key = resolve_openai_api_key()
    if not key:
        raise MissingCredentialsError()
    from openai import OpenAI
    # User credentials are only sent to OpenAI, even if OPENAI_BASE_URL is set.
    return OpenAI(api_key=key, base_url="https://api.openai.com/v1",
                  timeout=timeout, max_retries=max_retries)


def check_openai_connection(model):
    """Fetch model metadata, with no inference or incident data; never raise."""
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
