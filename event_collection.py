"""Windows Event Log collection helpers."""

import logging

from errors import EventCollectionError, describe_error

try:
    import win32evtlog
    import win32evtlogutil
except ImportError as exc:  # Allows the rest of the application to fail cleanly.
    win32evtlog = None
    win32evtlogutil = None
    _WIN32_IMPORT_ERROR = exc
else:
    _WIN32_IMPORT_ERROR = None


LOGGER = logging.getLogger(__name__)


def _require_event_log_dependencies():
    """Raise a helpful error when pywin32 is unavailable."""

    if _WIN32_IMPORT_ERROR is not None:
        raise EventCollectionError(
            "Windows Event Log support is unavailable. Run LogOpen on Windows "
            "and install pywin32 with 'python -m pip install pywin32'."
        ) from _WIN32_IMPORT_ERROR


def log_event_level(event):
    """Return a human-readable severity label for a Windows event log record."""

    _require_event_log_dependencies()

    if event.EventType == win32evtlog.EVENTLOG_AUDIT_FAILURE:
        return "Failure Audit"
    if event.EventType == win32evtlog.EVENTLOG_AUDIT_SUCCESS:
        return "Success Audit"
    if event.EventType == win32evtlog.EVENTLOG_INFORMATION_TYPE:
        return "Information"
    if event.EventType == win32evtlog.EVENTLOG_WARNING_TYPE:
        return "Warning"
    if event.EventType == win32evtlog.EVENTLOG_ERROR_TYPE:
        return "Error"
    return "Unknown"


def parse_event(log_type, event):
    """Convert a raw event log record into a dictionary of useful fields."""

    _require_event_log_dependencies()
    try:
        parsed_event = {
            "log_type": log_type,
            "computer_name": event.ComputerName,
            "provider": event.SourceName,
            "event_id": event.EventID & 0x1FFFFFFF,
            "time_generated": event.TimeGenerated,
            "level": log_event_level(event),
        }
    except Exception as exc:
        raise EventCollectionError(
            f"Could not parse an event from the {log_type} log: "
            f"{describe_error(exc)}"
        ) from exc

    try:
        parsed_event["message"] = win32evtlogutil.SafeFormatMessage(
            event,
            log_type,
        ) or "No message available."
    except Exception as exc:
        # Message DLLs are not always installed. The structured event is still useful.
        LOGGER.warning(
            "Could not format message for %s event %s: %s",
            log_type,
            parsed_event["event_id"],
            describe_error(exc),
        )
        parsed_event["message"] = "Event message could not be formatted."

    return parsed_event


def get_recent_events(log_type, limit):
    """Read up to ``limit`` recent events from the requested Windows log."""

    _require_event_log_dependencies()
    if not isinstance(log_type, str) or not log_type.strip():
        raise ValueError("log_type must be a non-empty string")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    if limit == 0:
        return []

    hand = None
    try:
        hand = win32evtlog.OpenEventLog("", log_type)
        flags = (
            win32evtlog.EVENTLOG_SEQUENTIAL_READ
            | win32evtlog.EVENTLOG_BACKWARDS_READ
        )

        parsed_events = []
        count = 0
        while count < limit:
            try:
                events = win32evtlog.ReadEventLog(hand, flags, 0)
            except Exception as exc:
                if parsed_events:
                    LOGGER.warning(
                        "Stopped reading the %s log after %s events: %s",
                        log_type,
                        len(parsed_events),
                        describe_error(exc),
                    )
                    break
                raise
            if not events:
                break

            for event in events:
                try:
                    parsed_events.append(parse_event(log_type, event))
                except EventCollectionError as exc:
                    LOGGER.warning("Skipping invalid event: %s", exc)
                count += 1
                if count >= limit:
                    break
    except EventCollectionError:
        raise
    except Exception as exc:
        raise EventCollectionError(
            f"Could not read the {log_type} log: {describe_error(exc)}"
        ) from exc
    finally:
        if hand is not None:
            try:
                win32evtlog.CloseEventLog(hand)
            except Exception as exc:
                # A close failure must not hide successfully collected events or an
                # earlier, more useful read error.
                LOGGER.warning(
                    "Could not close the %s log handle: %s",
                    log_type,
                    describe_error(exc),
                )

    return parsed_events
