"""Filters collected event dictionaries by severity and recency."""

from datetime import datetime, timedelta


def filter_events(events, filter_criteria):
    """Return a new list of events whose level belongs to filter_criteria.

    Input order and dictionary identities are preserved. Every event must have
    a level key; filtering neither copies event dictionaries nor mutates them.
    """

    return [
        event
        for event in events
        if event["level"] in filter_criteria
    ]


def filter_events_by_time(events, hours):
    """Return events newer than now minus hours, preserving input order.

    Each event needs a time_generated datetime. The first event's timezone is
    used for now, so timestamps must be mutually comparable. The boundary is
    exclusive; there is no upper bound rejecting future timestamps. An empty
    input returns an empty list, and event dictionaries are shared unchanged.
    """

    timezone = events[0]["time_generated"].tzinfo if events else None
    time_threshold = datetime.now(tz=timezone) - timedelta(hours=hours)
    return [
        event
        for event in events
        if event["time_generated"] > time_threshold
    ]
