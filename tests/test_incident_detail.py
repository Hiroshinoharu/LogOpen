"""Test incident presentation without event collection or AI API calls."""

from copy import deepcopy
from datetime import datetime, timedelta
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtWidgets import QApplication, QPlainTextEdit
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
            "diagnostic_steps": [
                {"description": "Inspect the DNS client service.",
                 "command": "Get-Service -Name Dnscache", "shell": "powershell", "risk_level": "safe"},
                {"description": "Restart the affected service after reviewing its dependencies.",
                 "command": None, "risk_level": "caution"},
                {"description": "Review any proposed configuration change before applying it.",
                 "command": None, "risk_level": "system_change"},
            ],
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
            self.assertEqual(self.widget.score_fields[key].text(), f"{value} pts")
            self.assertEqual(self.widget.score_bars[key].value(), value)
        self.assertEqual(incident, original)

    def test_displays_all_ai_sections_then_clears_them_for_missing_analysis(self):
        self.widget.set_incident(make_incident())
        self.assertTrue(self.widget.ai_missing.isHidden())
        self.assertFalse(self.widget.ai_content.isHidden())
        self.assertIn("repeated lookup failures", self.widget.ai_fields["explanation"].text())
        self.assertEqual(self.widget.ai_fields["likely_causes"].text(),
                         "• Temporary DNS unavailability\n\n• A network interruption")
        self.assertEqual(self.widget.ai_fields["recommended_actions"].text(),
                         "1. Check connectivity\n\n2. Review <adapter> settings")
        self.assertEqual(self.widget.ai_fields["recommended_actions"].textFormat(),
                         Qt.TextFormat.PlainText)
        self.assertIn("Review <adapter> settings", self.widget.ai_fields["recommended_actions"].text())
        self.assertIn("Verify the result", self.widget.ai_fields["remediation_notes"].text())

        for analysis in (None, {}):
            with self.subTest(analysis=analysis):
                self.widget.set_incident({"llm_analysis": analysis})
                self.assertFalse(self.widget.ai_missing.isHidden())
                self.assertTrue(self.widget.ai_content.isHidden())
                self.assertEqual(self.widget.ai_missing_message.text(),
                                 "AI analysis was not generated for this incident.")
                self.assertNotIn("lookup failures", self.widget.ai_fields["explanation"].text())

    def test_missing_fields_and_zero_values_are_distinct(self):
        self.widget.set_incident({})
        self.assertEqual(self.widget.fields["recurrence_count_24h"].text(), "Not available")
        self.assertEqual(self.widget.score_fields["severity"].text(), "Not available")
        self.assertEqual(self.widget.sources.message.toPlainText(), "No source events available for this incident.")
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
        self.assertEqual(self.widget.score_fields["severity"].text(), "0 pts")
        self.assertFalse(self.widget.score_bars["severity"].isHidden())
        self.assertTrue(self.widget.score_bars["duration"].isHidden())

    def test_source_events_keep_messages_readable_and_scrollable_in_a_small_view(self):
        incident = make_incident()
        incident["events"][0]["message"] += "\n" + "Long source message\n" * 200 + "x" * 20000
        self.widget.set_incident(incident)
        self.widget.resize(752, 490)
        self.widget.show()
        self.app.processEvents()
        self.assertGreater(self.widget.scroll_area.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.widget.scroll_area.horizontalScrollBar().maximum(), 0)
        self.widget.tabs.setCurrentIndex(2)
        self.app.processEvents()
        sources = self.widget.sources
        self.assertEqual(sources.message.toPlainText(), incident["events"][0]["message"])
        self.assertEqual(sources.table.rowCount(), 2)
        self.assertEqual(sources.table.item(0, 0).text(), "2026-09-08 12:00:00")
        self.assertEqual(sources.table.item(1, 1).text(), "Provider <name>")
        self.assertIn("Event 1 of 2 · Error · ID 1014", sources.event_context.text())
        self.assertTrue(sources.message.isReadOnly())
        self.assertGreater(sources.message.verticalScrollBar().maximum(), 0)
        self.assertEqual(sources.message.horizontalScrollBar().maximum(), 0)
        self.assertEqual(sources.table.horizontalScrollBar().maximum(), 0)
        sources.message.verticalScrollBar().setValue(200)
        sources.table.setCurrentCell(1, 0)
        self.assertEqual(sources.message.toPlainText(), "A second source message.")
        self.assertIn("Event 2 of 2 · Warning · ID 1000", sources.event_context.text())
        self.assertEqual(sources.message.verticalScrollBar().value(), 0)
        self.widget.scroll_area.verticalScrollBar().setValue(200)
        self.widget.set_incident(None)
        self.assertEqual(self.widget.tabs.currentIndex(), 0)
        self.assertEqual(self.widget.scroll_area.verticalScrollBar().value(), 0)
        self.assertIsNone(self.widget.incident)
        self.assertNotIn("Long source", sources.message.toPlainText())
        self.assertEqual(sources.table.rowCount(), 0)
        self.assertEqual(sources.event_context.text(), "")

    def test_overview_reflows_and_ai_content_scrolls_at_different_window_sizes(self):
        incident = make_incident()
        incident["llm_analysis"]["explanation"] = "An explanation with <literal> tags. " * 400
        self.widget.set_incident(incident)
        self.widget.show()
        for width, height in ((1052, 630), (752, 490), (1232, 750)):
            self.widget.resize(width, height)
            self.widget.tabs.setCurrentIndex(0)
            self.app.processEvents()
            score_position = self.widget.overview_grid.getItemPosition(
                self.widget.overview_grid.indexOf(self.widget.scoring)
            )[:2]
            self.assertEqual(score_position, (0, 1) if width >= 900 else (1, 0))
            self.assertEqual(self.widget.scroll_area.horizontalScrollBar().maximum(), 0)
            self.widget.tabs.setCurrentIndex(1)
            self.app.processEvents()
            self.assertGreater(self.widget.ai_scroll.verticalScrollBar().maximum(), 0)
            self.assertEqual(self.widget.ai_scroll.horizontalScrollBar().maximum(), 0)
            self.assertEqual(self.widget.ai_fields["explanation"].text(),
                             incident["llm_analysis"]["explanation"].strip())

    def test_new_incident_resets_event_selection_and_handles_incomplete_sources(self):
        self.widget.set_incident(make_incident())
        self.widget.sources.table.setCurrentCell(1, 0)
        self.widget.tabs.setCurrentIndex(2)
        self.widget.set_incident({"events": [None, {"event_id": 0}, "invalid"]})
        self.assertEqual(self.widget.tabs.currentIndex(), 0)
        self.assertEqual(self.widget.tabs.tabText(2), "Source events (1)")
        self.assertEqual(self.widget.sources.table.currentRow(), 0)
        self.assertEqual(self.widget.sources.table.item(0, 2).text(), "0")
        self.assertEqual(self.widget.sources.message.toPlainText(), "No message available.")
        self.assertNotIn("Provider <name>", self.widget.sources.event_context.text())

    def test_diagnostic_cards_show_all_steps_risks_and_preserve_ai_sections(self):
        incident = make_incident()
        original = deepcopy(incident)
        self.widget.set_incident(incident)
        self.widget.tabs.setCurrentIndex(1)
        self.widget.show()
        self.app.processEvents()
        self.assertEqual(len(self.widget.diagnostic_cards), 3)
        self.assertTrue(self.widget.diagnostic_empty.isHidden())
        for index, (card, step, badge) in enumerate(zip(
            self.widget.diagnostic_cards, incident["llm_analysis"]["diagnostic_steps"],
            ("SAFE", "CAUTION", "SYSTEM CHANGE"),
        ), start=1):
            self.assertEqual(card.number.text(), f"Step {index}")
            self.assertEqual(card.description.text(), step["description"])
            self.assertEqual(card.risk_badge.text(), badge)
            self.assertEqual(card.risk_badge.property("risk"), step["risk_level"])
            self.assertTrue(card.isVisible())
            self.assertEqual(card.thread(), self.app.thread())
        content = self.widget.ai_content.layout()
        self.assertLess(content.indexOf(self.widget.ai_fields["recommended_actions"].parentWidget()),
                        content.indexOf(self.widget.diagnostic_section))
        self.assertLess(content.indexOf(self.widget.diagnostic_section),
                        content.indexOf(self.widget.ai_fields["remediation_notes"].parentWidget()))
        self.assertTrue(all(label.isVisible() for label in self.widget.ai_fields.values()))
        self.assertEqual(incident, original)

    def test_null_commands_do_not_create_empty_command_controls(self):
        self.widget.set_incident(make_incident())
        for card in self.widget.diagnostic_cards[1:]:
            self.assertIsNone(card.command_box)
            self.assertIsNone(card.copy_button)
            self.assertIsNone(card.shell_label)
            self.assertEqual(card.findChildren(QPlainTextEdit), [])

    def test_missing_or_empty_steps_clear_previous_cards_without_hiding_existing_analysis(self):
        for steps in (None, [], "missing", [None]):
            with self.subTest(steps=steps):
                self.widget.set_incident(make_incident())
                previous_cards = list(self.widget.diagnostic_cards)
                incident = make_incident()
                if steps == "missing":
                    del incident["llm_analysis"]["diagnostic_steps"]
                else:
                    incident["llm_analysis"]["diagnostic_steps"] = steps
                self.widget.set_incident(incident)
                self.assertEqual(self.widget.diagnostic_cards, [])
                self.assertTrue(all(card.isHidden() for card in previous_cards))
                self.assertFalse(self.widget.diagnostic_empty.isHidden())
                self.assertEqual(self.widget.diagnostic_empty.text(),
                                 "No structured diagnostic steps were generated for this incident.")
                self.assertFalse(self.widget.ai_content.isHidden())
                self.assertEqual(self.widget.ai_fields["explanation"].text(),
                                 incident["llm_analysis"]["explanation"])
        self.widget.set_incident(make_incident())
        self.widget.set_incident(None)
        self.assertEqual(self.widget.diagnostic_cards, [])
        self.assertTrue(self.widget.ai_content.isHidden())

    def test_long_diagnostic_text_wraps_and_commands_scroll_without_expanding_the_page(self):
        command = "Write-Output '<literal> & text'\n" + "x" * 20000
        incident = make_incident()
        incident["llm_analysis"]["diagnostic_steps"][0].update(
            description="A long <literal> diagnostic description. " * 120,
            command=command,
        )
        self.widget.setStyleSheet((Path(__file__).parents[1] / "ui" / "style.qss").read_text(encoding="utf-8"))
        self.widget.set_incident(incident)
        self.widget.tabs.setCurrentIndex(1)
        self.widget.show()
        for width, height in ((752, 490), (1232, 750)):
            self.widget.resize(width, height)
            self.app.processEvents()
            card = self.widget.diagnostic_cards[0]
            editor = card.command_box
            self.widget.ai_scroll.ensureWidgetVisible(editor)
            self.app.processEvents()
            self.assertEqual(card.description.textFormat(), Qt.TextFormat.PlainText)
            self.assertEqual(editor.toPlainText(), command)
            self.assertTrue(editor.isReadOnly())
            self.assertGreater(editor.verticalScrollBar().maximum(), 0)
            self.assertGreater(self.widget.ai_scroll.verticalScrollBar().maximum(), 0)
            self.assertEqual(editor.horizontalScrollBar().maximum(), 0)
            self.assertEqual(self.widget.ai_scroll.horizontalScrollBar().maximum(), 0)
            right = editor.mapTo(self.widget.ai_scroll.widget(), QPoint(editor.width(), 0)).x()
            self.assertLessEqual(right, self.widget.ai_scroll.viewport().width())

    def test_command_shell_labels_handle_supported_and_legacy_steps(self):
        for shell, label in (("powershell", "PowerShell"), ("cmd", "Command Prompt"),
                             ("bash", "Bash"), (None, "Shell not specified"),
                             ("unknown", "Shell not specified")):
            with self.subTest(shell=shell):
                incident = make_incident()
                step = incident["llm_analysis"]["diagnostic_steps"][0]
                if shell is None:
                    del step["shell"]
                else:
                    step["shell"] = shell
                original = deepcopy(incident)
                self.widget.set_incident(incident)
                card = self.widget.diagnostic_cards[0]
                self.assertEqual(card.shell_label.text(), f"Command · {label}")
                self.assertEqual(card.shell_label.textFormat(), Qt.TextFormat.PlainText)
                card.copy_button.click()
                self.assertEqual(self.app.clipboard().text(), step["command"])
                self.assertEqual(incident, original)


if __name__ == "__main__":
    unittest.main()
