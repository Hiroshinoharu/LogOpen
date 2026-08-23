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

    def test_event_similarity_score_for_unrelated_close_events(self):
        base_time = datetime(2026, 8, 19, 12, 0, 0)
        previous_event = make_event(
            provider="DCOM",
            event_id=10016,
            time_generated=base_time,
            message="DCOM permission warning.",
        )
        current_event = make_event(
            provider="Tcpip",
            event_id=4266,
            time_generated=base_time + timedelta(seconds=10),
            message="TCP/IP failed to establish an outgoing connection.",
        )

        self.assertEqual(event_similarity_score(previous_event, current_event), 1)

    def test_event_similarity_score_correlates_cross_log_defender_events(self):
        base_time = datetime(2026, 8, 19, 12, 0, 0)
        previous_event = make_event(
            log_type="Application",
            provider="Application Error",
            event_id=1000,
            time_generated=base_time,
            message="Faulting application name: MsMpEng.exe",
        )
        current_event = make_event(
            log_type="System",
            provider="Service Control Manager",
            event_id=7031,
            time_generated=base_time + timedelta(seconds=5),
            message=(
                "The Microsoft Defender Antivirus Service service "
                "terminated unexpectedly."
            ),
        )

        self.assertEqual(event_similarity_score(previous_event, current_event), 4)
        self.assertEqual(
            bundle_incidents([previous_event, current_event]),
            [[previous_event, current_event]],
        )

    def test_bundle_incidents_keeps_different_components_separate(self):
        base_time = datetime(2026, 8, 19, 12, 0, 0)
        first_event = make_event(
            log_type="Application",
            provider="Application Error",
            event_id=1000,
            time_generated=base_time,
            message="Faulting application name: MsMpEng.exe",
        )
        second_event = make_event(
            log_type="Application",
            provider="Application Error",
            event_id=1000,
            time_generated=base_time + timedelta(seconds=10),
            message="Faulting application name: SearchIndexer.exe",
        )

        self.assertEqual(event_similarity_score(first_event, second_event), 2)
        self.assertEqual(
            bundle_incidents([first_event, second_event]),
            [[first_event], [second_event]],
        )

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
        self.assertEqual(summary["incident_score"], 50)
        self.assertEqual(summary["incident_priority"], "Medium")
        self.assertEqual(
            summary["incident_score_reasons"],
            [
                "Error-level incident detected",
                "Low event count (3-4)",
                "Incident classification 'DNS Resolution Timeout' has an impact score of 5",
            ],
        )
        self.assertEqual(
            summary["event_classification_counts"]["DNS Resolution Timeout"],
            2,
        )
        self.assertEqual(summary["providers_counts"]["Microsoft-Windows-DNS-Client"], 2)
        self.assertEqual(summary["event_ids_counts"][1014], 2)
        self.assertEqual(summary["incident_duration"], timedelta(seconds=40))
        self.assertEqual(
            summary["summary_text"],
            "Medium-priority DNS Resolution Timeout incident in the "
            "System log on Test-PC involving DNS Client and Distributed COM. "
            "It contains 3 events and lasted 40 seconds. "
            "Score 50 because error-level events were present, "
            "3 to 4 related events were grouped together, and "
            "incident classification 'DNS Resolution Timeout' has an "
            "impact score of 5.",
        )

    def test_build_incident_summary_text_formats_multiple_score_reasons(self):
        base_time = datetime(2026, 8, 19, 12, 0, 0)
        events = [
            make_event(
                provider="DCOM",
                event_id=10016,
                time_generated=base_time,
                level="Warning",
                message="Permission warning one.",
            ),
            make_event(
                provider="DCOM",
                event_id=10016,
                time_generated=base_time + timedelta(minutes=5),
                level="Warning",
                message="Permission warning two.",
            ),
            make_event(
                provider="DCOM",
                event_id=10016,
                time_generated=base_time + timedelta(minutes=15),
                level="Warning",
                message="Permission warning three.",
            ),
            make_event(
                provider="DCOM",
                event_id=10016,
                time_generated=base_time + timedelta(minutes=45),
                level="Warning",
                message="Permission warning four.",
            ),
            make_event(
                provider="DCOM",
                event_id=10016,
                time_generated=base_time + timedelta(hours=1, minutes=5),
                level="Warning",
                message="Permission warning five.",
            ),
        ]

        summary = build_incident(events)

        self.assertEqual(summary["incident_score"], 55)
        self.assertEqual(summary["incident_priority"], "Medium")
        self.assertEqual(
            summary["incident_score_reasons"],
            [
                "Warning-level incident detected",
                "Moderate event count (5-9)",
                "Incident duration > 1 hour",
            ],
        )
        self.assertEqual(
            summary["summary_text"],
            "Medium-priority DCOM Permission Warning incident in the "
            "System log on Test-PC involving Distributed COM. "
            "It contains 5 events and lasted 1 hour and 5 minutes. "
            "Score 55 because warning-level events were present, "
            "5 to 9 related events were grouped together, and "
            "the incident lasted longer than 1 hour.",
        )


if __name__ == "__main__":
    unittest.main()
