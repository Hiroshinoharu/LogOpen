"""Unit tests for incident detection helpers."""

from datetime import datetime, timedelta
import unittest

from incident_detection import (
    build_incident,
    bundle_incidents,
    event_similarity_score,
)
from tests.helpers import make_event


class IncidentDetectionTests(unittest.TestCase):
    def test_event_similarity_score_counts_provider_id_and_time(self):
        base_time = datetime(2026, 8, 19, 12, 0, 0)
        previous_event = make_event(
            provider="DCOM",
            event_id=10016,
            time_generated=base_time,
        )
        current_event = make_event(
            provider="DCOM",
            event_id=10016,
            time_generated=base_time + timedelta(seconds=30),
        )

        self.assertEqual(event_similarity_score(previous_event, current_event), 5)

    def test_bundle_incidents_groups_similar_events_only(self):
        base_time = datetime(2026, 8, 19, 12, 0, 0)
        events = [
            make_event(
                provider="DCOM",
                event_id=10016,
                time_generated=base_time,
            ),
            make_event(
                provider="DCOM",
                event_id=10010,
                time_generated=base_time + timedelta(seconds=30),
                level="Error",
            ),
            make_event(
                provider="Tcpip",
                event_id=4266,
                time_generated=base_time + timedelta(seconds=45),
            ),
        ]

        incidents = bundle_incidents(events)

        self.assertEqual(len(incidents), 2)
        self.assertEqual(incidents[0], events[:2])
        self.assertEqual(incidents[1], [events[2]])

    def test_build_incident_creates_summary_fields(self):
        base_time = datetime(2026, 8, 19, 12, 0, 0)
        events = [
            make_event(
                provider="Microsoft-Windows-DNS-Client",
                event_id=1014,
                time_generated=base_time,
                level="Warning",
                message="Name resolution timed out.",
            ),
            make_event(
                provider="Microsoft-Windows-DNS-Client",
                event_id=1014,
                time_generated=base_time + timedelta(seconds=20),
                level="Warning",
                message="Name resolution timed out again.",
            ),
            make_event(
                provider="DCOM",
                event_id=10010,
                time_generated=base_time + timedelta(seconds=40),
                level="Error",
                message="The server did not register with DCOM.",
            ),
        ]

        summary = build_incident(events)

        self.assertEqual(summary["highest_severity"], "Error")
        self.assertEqual(summary["incident_classification"], "DNS Resolution Timeout")
        self.assertEqual(
            summary["event_classification_counts"]["DNS Resolution Timeout"],
            2,
        )
        self.assertEqual(summary["providers_counts"]["Microsoft-Windows-DNS-Client"], 2)
        self.assertEqual(summary["event_ids_counts"][1014], 2)
        self.assertEqual(summary["incident_duration"], timedelta(seconds=40))


if __name__ == "__main__":
    unittest.main()
