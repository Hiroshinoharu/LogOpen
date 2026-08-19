"""Unit tests for event filtering helpers."""

from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from event_filtering import filter_events, filter_events_by_time
from tests.helpers import make_event


class FilterEventsTests(unittest.TestCase):
    def test_filter_events_keeps_requested_levels(self):
        events = [
            make_event(level="Warning"),
            make_event(level="Error"),
            make_event(level="Information"),
        ]

        filtered = filter_events(events, ["Warning", "Error"])

        self.assertEqual(filtered, events[:2])

    def test_filter_events_by_time_uses_requested_window(self):
        fixed_now = datetime(2026, 8, 19, 12, 0, 0)
        events = [
            make_event(time_generated=fixed_now - timedelta(hours=1)),
            make_event(time_generated=fixed_now - timedelta(hours=30)),
        ]

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return fixed_now
                return fixed_now.replace(tzinfo=tz)

        with patch("event_filtering.datetime", FixedDateTime):
            filtered = filter_events_by_time(events, 24)

        self.assertEqual(filtered, [events[0]])


if __name__ == "__main__":
    unittest.main()
