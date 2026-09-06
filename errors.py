"""Application-specific exceptions and safe error formatting for LogOpen."""


class LogOpenError(Exception):
    """Base class for expected, user-facing LogOpen failures."""


class EventCollectionError(LogOpenError):
    """Raised when a configured Windows Event Log cannot be read."""


class LLMAnalysisError(LogOpenError):
    """Raised when optional LLM analysis cannot be completed."""


class ReportExportError(LogOpenError):
    """Raised when an incident report cannot be written safely."""


def describe_error(error):
    """Return a useful single-line description, even for empty exceptions."""

    message = " ".join(str(error).split())
    return message or error.__class__.__name__
