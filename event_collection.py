"""Windows Event Log collection helpers."""

import win32evtlog
import win32evtlogutil

from config import LOG_TYPE


def log_event_level(event):
    """Return a human-readable severity label for a Windows event log record."""

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

    return {
        "log_type": log_type,
        "computer_name": event.ComputerName,
        "provider": event.SourceName,
        "event_id": event.EventID & 0x1FFFFFFF,
        "time_generated": event.TimeGenerated,
        "level": log_event_level(event),
        "message": win32evtlogutil.SafeFormatMessage(event, log_type),
    }


def get_log_type():
    """Return the Windows event log channel used by this script."""

    return LOG_TYPE


def get_recent_events(limit):
    """Read up to ``limit`` recent events from the configured Windows log."""

    hand = None
    try:
        log_type = get_log_type()
        hand = win32evtlog.OpenEventLog("", log_type)
        flags = (
            win32evtlog.EVENTLOG_SEQUENTIAL_READ
            | win32evtlog.EVENTLOG_BACKWARDS_READ
        )

        parsed_events = []
        count = 0
        while count < limit:
            events = win32evtlog.ReadEventLog(hand, flags, 0)
            if not events:
                break

            for event in events:
                parsed_events.append(parse_event(log_type, event))
                count += 1
                if count >= limit:
                    break
    finally:
        if hand is not None:
            win32evtlog.CloseEventLog(hand)

    return parsed_events
