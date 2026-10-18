"""Unit tests for incident classification helpers."""

import unittest

from incident_classification import (
    classify_event,
    classify_incident,
    classify_provider,
)
from tests.helpers import make_event


class IncidentClassificationTests(unittest.TestCase):
    """Behavioral checks for incident classification, using controlled test data."""
    def test_classify_provider_uses_known_mapping(self):
        """Check classify provider uses known mapping."""
        self.assertEqual(classify_provider("Tcpip"), "TCP/IP")

    def test_classify_provider_falls_back_to_cleaned_name(self):
        """Check classify provider falls back to cleaned name."""
        self.assertEqual(
            classify_provider("Microsoft-Windows-TaskScheduler"),
            "TaskScheduler",
        )

    def test_classify_event_uses_message_aware_dcom_rules(self):
        """Check classify event uses message aware dcom rules."""
        event = make_event(
            provider="DCOM",
            event_id=10016,
            message="OpenAI.ChatGPT-Desktop permission warning",
        )

        self.assertEqual(
            classify_event(event),
            "ChatGPT Desktop DCOM Permission Warning",
        )

    def test_classify_event_uses_provider_and_event_id_rules(self):
        """Check classify event uses provider and event id rules."""
        event = make_event(
            provider="Microsoft-Windows-DNS-Client",
            event_id=1014,
        )

        self.assertEqual(classify_event(event), "DNS Resolution Timeout")

    def test_classify_incident_returns_all_provider_labels(self):
        """Check classify incident returns all provider labels."""
        incident = {
            "providers": [
                "DCOM",
                "Microsoft-Windows-DNS-Client",
                "Service Control Manager",
            ]
        }

        self.assertEqual(
            classify_incident(incident),
            {
                "DCOM": "Distributed COM",
                "Microsoft-Windows-DNS-Client": "DNS Client",
                "Service Control Manager": "Service Control Manager",
            },
        )


if __name__ == "__main__":
    unittest.main()
