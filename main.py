"""Read recent Windows event log entries and filter them for inspection."""

from datetime import datetime, timedelta

import win32evtlog
import win32evtlogutil

def log_event_level(event):
    """Return a human-readable severity label for a Windows event log record."""

    if event.EventType == win32evtlog.EVENTLOG_AUDIT_FAILURE:
        return "Failure Audit"
    elif event.EventType == win32evtlog.EVENTLOG_AUDIT_SUCCESS:
        return "Success Audit"
    elif event.EventType == win32evtlog.EVENTLOG_INFORMATION_TYPE:
        return "Information"
    elif event.EventType == win32evtlog.EVENTLOG_WARNING_TYPE:
        return "Warning"
    elif event.EventType == win32evtlog.EVENTLOG_ERROR_TYPE:
        return "Error"
    else:
        return "Unknown"

def parse_event(log_type, event):
    """Convert a raw event log record into a dictionary of useful fields."""

    return{
        "log_type": log_type,
        "computer_name": event.ComputerName,
        "provider": event.SourceName,
        "event_id": event.EventID & 0x1FFFFFFF,
        "time_generated": event.TimeGenerated,
        "level": log_event_level(event),
        "message": win32evtlogutil.SafeFormatMessage(event, log_type)
    }

def get_recent_events(limit):
    """Read up to ``limit`` recent events from the configured Windows log."""

    log_type = get_log_type()
    hand = win32evtlog.OpenEventLog("",log_type)
    flags = (
        win32evtlog.EVENTLOG_SEQUENTIAL_READ  |
        win32evtlog.EVENTLOG_BACKWARDS_READ
        )
    
    parsed_events = []
    count = 0
    while count  < limit:
        events = win32evtlog.ReadEventLog(hand, flags,0)
        if events:
            for event in events:
                parsed_events.append(parse_event(log_type, event))
                
                count  +=  1
                
                if count >= limit:
                    break
        else:
            break

    win32evtlog.CloseEventLog(hand)
    return parsed_events

def get_log_type():
    """Return the Windows event log channel used by this script."""

    log_type = 'System'
    return log_type

def  filter_events(events, filter_criteria):
    """Return events whose ``level`` value matches one of the requested levels."""

    filtered_events = []
    for event in events:
        if event["level"] in filter_criteria:
            filtered_events.append(event)
    return filtered_events

def  filter_events_by_time(events, hours):
    """Return events generated within the last ``hours`` hours."""
    # Determine the timezone of the events based on the first event's time_generated field
    timezone = events[0]["time_generated"].tzinfo if events else None
    # Calculate the time threshold for filtering events
    time_threshold = datetime.now(tz=timezone) - timedelta(hours=hours)
    filtered_events = []
    for event in events:
        if event["time_generated"] > time_threshold:
            filtered_events.append(event)
    return filtered_events

def  main():    
    """Print recent warning and error events from the selected Windows log."""

    events = get_recent_events(limit = 500)
    problem_events = filter_events_by_time(events, 24)
    problem_events = filter_events(problem_events, ["Error", "Warning"])
    print(f"Found {len(problem_events)} recent warning or error events in the last 24 hours:")
    for event in problem_events:
        print(event)

if __name__ == '__main__':
    main()
