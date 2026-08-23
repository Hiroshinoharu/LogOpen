"""Unit tests for historical incident recurrence analysis."""

from datetime import datetime, timedelta
import unittest

from incident_detection import refresh_incident_summary
from incident_recurrence import apply_recurrence_metadata


def make_summary(classification, start):
    """Build the minimum incident summary needed for recurrence tests."""

    return {
        "incident_classification": classification,
        "time_generated_start": start,
        "time_generated_end": start,
        "highest_severity": "Information",
        "event_count": 1,
        "incident_duration": timedelta(),
        "provider_classifications": {"ExampleProvider": "Example Provider"},
        "log_type": "System",
        "computer_name": "Test-PC",
    }


class IncidentRecurrenceTests(unittest.TestCase):
    def test_single_occurrence_is_not_recurring(self):
        summary = make_summary("Example Issue", datetime(2026, 8, 19, 12, 0))

        apply_recurrence_metadata([summary])

        self.assertEqual(summary["recurrence_count_24h"], 1)
        self.assertEqual(summary["recurrence_count_7d"], 1)
        self.assertFalse(summary["is_recurring"])

    def test_two_matching_occurrences_within_24_hours_are_counted(self):
        base_time = datetime(2026, 8, 19, 12, 0)
        first = make_summary("Example Issue", base_time)
        second = make_summary("Example Issue", base_time + timedelta(hours=2))

        apply_recurrence_metadata([first, second])

        self.assertEqual(second["recurrence_count_24h"], 2)
        self.assertEqual(second["recurrence_count_7d"], 2)
        self.assertFalse(second["is_recurring"])

    def test_three_occurrences_within_24_hours_add_recurrence_score(self):
        base_time = datetime(2026, 8, 19, 12, 0)
        summaries = [
            make_summary("Example Issue", base_time + timedelta(hours=offset))
            for offset in (0, 2, 4)
        ]

        apply_recurrence_metadata(summaries)
        refresh_incident_summary(summaries[-1])

        self.assertEqual(summaries[-1]["incident_score"], 10)
        self.assertIn(
            "Classification occurred 3 times in the last 24 hours",
            summaries[-1]["incident_score_reasons"],
        )

    def test_five_matching_occurrences_within_seven_days_add_recurrence_score(self):
        base_time = datetime(2026, 8, 19, 12, 0)
        summaries = [
            make_summary("Example Issue", base_time + timedelta(days=offset))
            for offset in (0, 1, 2, 4, 6)
        ]

        apply_recurrence_metadata(summaries)
        refresh_incident_summary(summaries[-1])

        self.assertEqual(summaries[-1]["recurrence_count_24h"], 1)
        self.assertEqual(summaries[-1]["recurrence_count_7d"], 5)
        self.assertTrue(summaries[-1]["is_recurring"])
        self.assertEqual(summaries[-1]["incident_score"], 20)
        self.assertIn(
            "Classification occurred 5 times in the last 7 days",
            summaries[-1]["incident_score_reasons"],
        )

    def test_unrelated_classifications_do_not_affect_each_other(self):
        base_time = datetime(2026, 8, 19, 12, 0)
        first = make_summary("Example Issue", base_time)
        unrelated = make_summary("Different Issue", base_time + timedelta(hours=1))
        second = make_summary("Example Issue", base_time + timedelta(hours=2))

        apply_recurrence_metadata([first, unrelated, second])

        self.assertEqual(unrelated["recurrence_count_24h"], 1)
        self.assertEqual(second["recurrence_count_24h"], 2)

    def test_occurrences_outside_recurrence_windows_are_not_counted(self):
        latest_time = datetime(2026, 8, 19, 12, 0)
        old_summary = make_summary("Example Issue", latest_time - timedelta(days=8))
        latest_summary = make_summary("Example Issue", latest_time)

        apply_recurrence_metadata([old_summary, latest_summary])

        self.assertEqual(latest_summary["recurrence_count_24h"], 1)
        self.assertEqual(latest_summary["recurrence_count_7d"], 1)
        self.assertFalse(latest_summary["is_recurring"])


if __name__ == "__main__":
    unittest.main()
