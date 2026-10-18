"""Application exceptions and single-line formatting for non-secret errors."""


class LogOpenError(Exception):
    """Base class for expected, user-facing LogOpen failures."""


class EventCollectionError(LogOpenError):
    """Raised when a configured Windows Event Log cannot be read."""


class LLMAnalysisError(LogOpenError):
    """Raised when optional LLM analysis cannot be completed."""


class ReportExportError(LogOpenError):
    """Raised when an incident report cannot be written safely."""


def describe_error(error):
    """Return collapsed exception text, or its class name if the text is empty.

    This only normalizes whitespace; it does not redact secrets. Credential and
    OpenAI boundaries use fixed messages instead of passing their errors here.
    """

    message = " ".join(str(error).split())
    return message or error.__class__.__name__
