"""Event filtering helpers."""

from datetime import datetime, timedelta


def filter_events(events, filter_criteria):
    """Return events whose ``level`` value matches one of the requested levels."""

    return [
        event
        for event in events
        if event["level"] in filter_criteria
    ]


def filter_events_by_time(events, hours):
    """Return events generated within the last ``hours`` hours."""

    timezone = events[0]["time_generated"].tzinfo if events else None
    time_threshold = datetime.now(tz=timezone) - timedelta(hours=hours)
    return [
        event
        for event in events
        if event["time_generated"] > time_threshold
    ]
