"""Test incident presentation without event collection or AI API calls."""

from copy import deepcopy
from datetime import datetime, timedelta
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    if exc.name != "PyQt6":
        raise
    raise unittest.SkipTest("PyQt6 is required for the incident detail tests") from exc

from ui.incident_detail import IncidentDetailWidget


def make_incident():
    return {
        "incident_classification": "DNS <client> & networking",
        "incident_priority": "High",
        "incident_score": 65,
        "summary_text": "Repeated failures involving <service> & DNS.",
        "log_type": "System",
        "providers": ["DNS Client", "Provider <name>"],
        "event_ids": [1014, 1000],
        "event_count": 4,
        "incident_duration": timedelta(minutes=5, seconds=12),
        "is_recurring": True,
        "recurrence_count_24h": 3,
        "recurrence_count_7d": 4,
        "incident_score_breakdown": {
            "severity": 40, "event_count": 5, "classification_impact": 5,
            "recurrence": 10, "duration": 5,
        },
        "llm_analysis": {
            "explanation": "The events show repeated lookup failures.",
            "likely_causes": ["Temporary DNS unavailability", "A network interruption"],
            "recommended_actions": ["Check connectivity", "Review <adapter> settings"],
            "remediation_notes": ["Verify the result before changing configuration"],
        },
        "events": [
            {"level": "Error", "provider": "DNS Client", "log_type": "System",
             "event_id": 1014, "time_generated": datetime(2026, 9, 8, 12, 0),
             "message": "Name resolution failed for <host>.\nAdditional event details."},
            {"level": "Warning", "provider": "Provider <name>", "event_id": 1000,
             "log_type": "Application", "message": "A second source message."},
        ],
    }


class IncidentDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.widget = IncidentDetailWidget()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()

    def test_displays_metadata_and_supplied_score_breakdown_without_mutation(self):
        incident = make_incident()
        original = deepcopy(incident)
        self.widget.set_incident(incident)
        self.assertIs(self.widget.incident, incident)
        self.assertEqual(self.widget.classification.text(), incident["incident_classification"])
        self.assertEqual(self.widget.classification.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(self.widget.priority.text(), "High priority")
        self.assertEqual(self.widget.score.text(), "65 / 100")
        self.assertEqual(self.widget.summary.text(), incident["summary_text"])
        expected = {
            "log_type": "System", "providers": "DNS Client, Provider <name>",
            "event_ids": "1014, 1000", "event_count": "4", "incident_duration": "0:05:12",
            "is_recurring": "Recurring", "recurrence_count_24h": "3", "recurrence_count_7d": "4",
        }
        for key, value in expected.items():
            self.assertEqual(self.widget.fields[key].text(), value)
        for key, value in incident["incident_score_breakdown"].items():
            self.assertEqual(self.widget.score_fields[key].text(), str(value))
        self.assertEqual(incident, original)

    def test_displays_all_ai_sections_then_clears_them_for_missing_analysis(self):
        self.widget.set_incident(make_incident())
        self.assertTrue(self.widget.ai_missing.isHidden())
        self.assertFalse(self.widget.ai_content.isHidden())
        self.assertIn("repeated lookup failures", self.widget.ai_fields["explanation"].text())
        self.assertEqual(self.widget.ai_fields["likely_causes"].text(),
                         "• Temporary DNS unavailability\n• A network interruption")
        self.assertIn("Review <adapter> settings", self.widget.ai_fields["recommended_actions"].text())
        self.assertIn("Verify the result", self.widget.ai_fields["remediation_notes"].text())

        for analysis in (None, {}):
            with self.subTest(analysis=analysis):
                self.widget.set_incident({"llm_analysis": analysis})
                self.assertFalse(self.widget.ai_missing.isHidden())
                self.assertTrue(self.widget.ai_content.isHidden())
                self.assertEqual(self.widget.ai_missing.text(),
                                 "AI analysis was not generated for this incident.")
                self.assertNotIn("lookup failures", self.widget.ai_fields["explanation"].text())

    def test_missing_fields_and_zero_values_are_distinct(self):
        self.widget.set_incident({})
        self.assertEqual(self.widget.fields["recurrence_count_24h"].text(), "Not available")
        self.assertEqual(self.widget.score_fields["severity"].text(), "Not available")
        self.assertEqual(self.widget.source_events.toPlainText(), "No source events available for this incident.")
        self.widget.set_incident({
            "incident_score": 0, "is_recurring": False, "recurrence_count_24h": 0,
            "recurrence_count_7d": 0, "incident_duration": timedelta(),
            "incident_score_breakdown": {"severity": 0},
        })
        self.assertEqual(self.widget.score.text(), "0 / 100")
        self.assertEqual(self.widget.fields["is_recurring"].text(), "Not recurring")
        self.assertEqual(self.widget.fields["recurrence_count_24h"].text(), "0")
        self.assertEqual(self.widget.fields["recurrence_count_7d"].text(), "0")
        self.assertEqual(self.widget.fields["incident_duration"].text(), "0:00:00")
        self.assertEqual(self.widget.score_fields["severity"].text(), "0")

    def test_source_events_keep_messages_readable_and_scrollable_in_a_small_view(self):
        incident = make_incident()
        incident["events"][0]["message"] += "\n" + "Long source message\n" * 200
        self.widget.set_incident(incident)
        self.widget.resize(420, 300)
        self.widget.show()
        self.app.processEvents()
        text = self.widget.source_events.toPlainText()
        self.assertIn("Event 1 · Error · ID 1014", text)
        self.assertIn("Name resolution failed for <host>.", text)
        self.assertIn("Provider: Provider <name>", text)
        self.assertIn("A second source message.", text)
        self.assertIn("2026-09-08 12:00:00", text)
        self.assertTrue(self.widget.source_events.isReadOnly())
        self.assertGreater(self.widget.source_events.verticalScrollBar().maximum(), 0)
        self.assertGreater(self.widget.scroll_area.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.widget.scroll_area.horizontalScrollBar().maximum(), 0)

        self.widget.scroll_area.verticalScrollBar().setValue(200)
        self.widget.set_incident(None)
        self.assertEqual(self.widget.scroll_area.verticalScrollBar().value(), 0)
        self.assertIsNone(self.widget.incident)
        self.assertNotIn("Long source", self.widget.source_events.toPlainText())


if __name__ == "__main__":
    unittest.main()
