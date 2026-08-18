import win32evtlog
import  win32evtlogutil

def log_event_level(event):
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
    log_type = 'System'
    return log_type

def  main():
    events = get_recent_events(limit = 20)
    for event in events:
        print(event)

if __name__ == '__main__':
    main()