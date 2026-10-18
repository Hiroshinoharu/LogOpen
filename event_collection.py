"""Read local Windows Event Logs and retain evidence of scan coverage.

pywin32 records become plain event dictionaries for downstream analysis.
Collection keeps partial results when possible and always attempts to close
the native handle. Importing this module does not read any event logs.
"""

import logging
from datetime import datetime, timedelta

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


class CollectedEvents(list):
    """List of parsed event dictionaries with per-log collection metadata.

    Attributes:
        complete: False if a cap, invalid record, or read error reduced coverage.
        records_read: Number of raw records examined, including filtered records.
        oldest_time: Timestamp of the last inspected record, or None initially.
        stop_reason: end_of_log, time_boundary, record_limit, or read_error.
        warnings: Deduplicated messages explaining incomplete coverage.

    An empty list can still represent an incomplete scan; inspect the metadata.
    """

    def __init__(self):
        """Initialize an empty result with complete coverage and no warnings."""
        super().__init__()
        self.complete = True
        self.records_read = 0
        self.oldest_time = None
        self.stop_reason = "end_of_log"
        self.warnings = []

    def coverage(self):
        """Return a new coverage dictionary, including the current matched-event count.

        The timestamp remains a datetime; JSON conversion happens during export.
        Warnings are available separately on this result's warnings attribute.
        """
        return {
            "complete": self.complete,
            "records_read": self.records_read,
            "matched_events": len(self),
            "oldest_time": self.oldest_time,
            "stop_reason": self.stop_reason,
        }

    def warn(self, message):
        """Mark coverage incomplete and append/log the message only if it is new."""
        self.complete = False
        if message not in self.warnings:
            self.warnings.append(message)
            LOGGER.warning(message)


def _require_event_log_dependencies():
    """Raise EventCollectionError when the pywin32 event-log imports failed.

    Delayed validation lets other modules import on unsupported environments.
    """

    if _WIN32_IMPORT_ERROR is not None:
        raise EventCollectionError(
            "Windows Event Log support is unavailable. Run LogOpen on Windows "
            "and install pywin32 with 'python -m pip install pywin32'."
        ) from _WIN32_IMPORT_ERROR


def log_event_level(event):
    """Map a raw record's EventType to a readable severity, or Unknown.

    Requires pywin32; missing dependencies raise EventCollectionError. The
    returned labels are also used by filtering and incident severity scoring.
    """

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
    """Convert one raw Windows record into LogOpen's event dictionary.

    Args:
        log_type: Source log name, such as System or Application.
        event: pywin32 record with ComputerName, SourceName, EventID,
            TimeGenerated, and EventType attributes.

    Returns:
        dict: log_type, computer_name, provider, event_id, time_generated,
        level, and message. The timestamp is retained as a datetime and the
        event ID is masked with 0x1FFFFFFF.

    Raises:
        EventCollectionError: Dependencies or required record fields are invalid.

    Missing message resources produce a logged warning and fallback message;
    they do not discard otherwise usable structured event data.
    """

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


def get_recent_events(log_type, limit, *, hours=None, levels=None):
    """Read a local log backward until its time boundary, EOF, or record cap.

    Args:
        log_type: Nonempty Windows log name.
        limit: Nonnegative maximum number of raw records examined, including
            records rejected by severity filtering. Zero reads nothing.
        hours: Positive lookback window, or None to omit time filtering.
            Records at or before the cutoff are excluded.
        levels: Accepted severity labels, or None to retain every severity.

    Returns:
        CollectedEvents: Matching events in read order, newest first, with
        coverage and warnings even if no events matched. A cap, skipped invalid
        record, or read failure after examining records marks it incomplete.

    Raises:
        ValueError: A log name, limit, or hours argument is invalid.
        EventCollectionError: Dependencies, opening the log, or the initial
            read fail before partial results can be returned.

    The cutoff uses the first record's timezone. Filtering precedes message
    formatting. The native handle is closed in finally; close errors are logged
    without replacing the collected result or an earlier error.
    """

    _require_event_log_dependencies()
    if not isinstance(log_type, str) or not log_type.strip():
        raise ValueError("log_type must be a non-empty string")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    if hours is not None and (isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours <= 0):
        raise ValueError("hours must be a positive number")
    window = timedelta(hours=hours) if hours is not None else None
    parsed_events = CollectedEvents()
    if limit == 0:
        parsed_events.stop_reason = "record_limit"
        parsed_events.warn(f"{log_type}: no records read because the record limit is zero.")
        return parsed_events

    hand = None
    try:
        hand = win32evtlog.OpenEventLog("", log_type)
        flags = (
            win32evtlog.EVENTLOG_SEQUENTIAL_READ
            | win32evtlog.EVENTLOG_BACKWARDS_READ
        )

        time_threshold = None
        stopped = False
        while not stopped:
            try:
                events = win32evtlog.ReadEventLog(hand, flags, 0)
            except Exception as exc:
                if parsed_events.records_read:
                    parsed_events.stop_reason = "read_error"
                    parsed_events.warn(
                        f"{log_type}: scan incomplete; reading stopped after "
                        f"{parsed_events.records_read} records: {describe_error(exc)}"
                    )
                    break
                raise
            if not events:
                break

            for event in events:
                parsed_events.records_read += 1
                try:
                    generated = event.TimeGenerated
                    parsed_events.oldest_time = generated
                    if window is not None:
                        if time_threshold is None:
                            time_threshold = datetime.now(tz=generated.tzinfo) - window
                        if generated <= time_threshold:
                            parsed_events.stop_reason = "time_boundary"
                            stopped = True
                            break
                    # Avoid formatting messages for events we will discard.
                    if levels is None or log_event_level(event) in levels:
                        parsed_events.append(parse_event(log_type, event))
                except (EventCollectionError, AttributeError, TypeError, ValueError):
                    parsed_events.warn(f"{log_type}: scan incomplete; an invalid event was skipped.")
                if parsed_events.records_read >= limit:
                    parsed_events.stop_reason = "record_limit"
                    parsed_events.warn(
                        f"{log_type}: scan incomplete; the {limit:,}-record limit "
                        "was reached before the time boundary or end of the log."
                    )
                    stopped = True
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
