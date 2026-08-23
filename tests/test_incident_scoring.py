"""Unit tests for incident scoring, priorities, and score breakdowns."""

from datetime import timedelta
import unittest

from incident_scoring import calculate_incident_score, get_incident_priority


def make_incident(**overrides):
    """Build the minimum incident summary required by the scoring rules."""

    incident = {
        "highest_severity": "Information",
        "event_count": 1,
        "incident_duration": timedelta(),
        "incident_classification": "Unknown Issue",
    }
    incident.update(overrides)
    return incident


class IncidentScoringTests(unittest.TestCase):
    def test_returns_zero_score_and_empty_breakdown_without_matching_rules(self):
        score, reasons, breakdown = calculate_incident_score(make_incident())

        self.assertEqual(score, 0)
        self.assertEqual(reasons, [])
        self.assertEqual(
            breakdown,
            {
                "severity": 0,
                "event_count": 0,
                "classification_impact": 0,
                "recurrence": 0,
                "duration": 0,
            },
        )

    def test_duration_under_one_minute_adds_no_score(self):
        score, reasons, breakdown = calculate_incident_score(
            make_incident(incident_duration=timedelta(seconds=59))
        )

        self.assertEqual(score, 0)
        self.assertEqual(breakdown["duration"], 0)
        self.assertEqual(reasons, [])

    def test_duration_between_one_and_five_minutes_adds_five_points(self):
        score, reasons, breakdown = calculate_incident_score(
            make_incident(incident_duration=timedelta(minutes=5))
        )

        self.assertEqual(score, 5)
        self.assertEqual(breakdown["duration"], 5)
        self.assertEqual(
            reasons, ["Incident lasted between 1 and 5 minutes"]
        )

    def test_duration_between_five_and_fifteen_minutes_adds_ten_points(self):
        score, reasons, breakdown = calculate_incident_score(
            make_incident(incident_duration=timedelta(minutes=5, seconds=1))
        )

        self.assertEqual(score, 10)
        self.assertEqual(breakdown["duration"], 10)
        self.assertEqual(
            reasons, ["Incident lasted between 5 and 15 minutes"]
        )

    def test_duration_over_fifteen_minutes_adds_fifteen_points(self):
        score, reasons, breakdown = calculate_incident_score(
            make_incident(incident_duration=timedelta(minutes=15, seconds=1))
        )

        self.assertEqual(score, 15)
        self.assertEqual(breakdown["duration"], 15)
        self.assertEqual(reasons, ["Incident lasted over 15 minutes"])

    def test_breakdown_combines_all_scoring_categories_and_caps_score(self):
        incident = make_incident(
            highest_severity="Error",
            event_count=10,
            incident_duration=timedelta(minutes=16),
            incident_classification="Defender Service Crash",
            recurrence_count_24h=3,
            recurrence_count_7d=5,
        )

        score, reasons, breakdown = calculate_incident_score(incident)

        self.assertEqual(score, 100)
        self.assertEqual(
            breakdown,
            {
                "severity": 40,
                "event_count": 30,
                "classification_impact": 20,
                "recurrence": 30,
                "duration": 15,
            },
        )
        self.assertEqual(sum(breakdown.values()), 135)
        self.assertEqual(score, min(sum(breakdown.values()), 100))
        self.assertEqual(get_incident_priority(score), "Critical")
        self.assertEqual(
            reasons,
            [
                "Error-level incident detected",
                "High event count (>10)",
                "Incident lasted over 15 minutes",
                "Classification occurred 3 times in the last 24 hours",
                "Classification occurred 5 times in the last 7 days",
                "Incident classification 'Defender Service Crash' has an impact score of 20",
            ],
        )

    def test_breakdown_uses_moderate_event_and_warning_scores(self):
        score, reasons, breakdown = calculate_incident_score(
            make_incident(highest_severity="Warning", event_count=5)
        )

        self.assertEqual(score, 35)
        self.assertEqual(
            breakdown,
            {
                "severity": 20,
                "event_count": 15,
                "classification_impact": 0,
                "recurrence": 0,
                "duration": 0,
            },
        )
        self.assertEqual(
            reasons,
            [
                "Warning-level incident detected",
                "Moderate event count (5-9)",
            ],
        )

    def test_get_incident_priority_uses_all_threshold_boundaries(self):
        self.assertEqual(get_incident_priority(0), "Low")
        self.assertEqual(get_incident_priority(39), "Low")
        self.assertEqual(get_incident_priority(40), "Medium")
        self.assertEqual(get_incident_priority(60), "High")
        self.assertEqual(get_incident_priority(80), "Critical")


if __name__ == "__main__":
    unittest.main()
