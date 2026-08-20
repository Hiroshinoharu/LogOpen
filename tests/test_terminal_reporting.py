"""Unit tests for terminal reporting helpers."""

from io import StringIO
import unittest

from terminal_reporting import clean_message, display_incident_reports
from tests.helpers import make_event


class TerminalReportingTests(unittest.TestCase):
    def test_clean_message_removes_empty_lines(self):
        message = "\nFirst line\n\nSecond line\n"

        self.assertEqual(clean_message(message), "First line\nSecond line")

    def test_display_incident_reports_writes_summary(self):
        buffer = StringIO()
        incidents = [[
            make_event(
                provider="Microsoft-Windows-DNS-Client",
                event_id=1014,
                message="Name resolution timed out.\n\nRetry later.",
            )
        ]]

        display_incident_reports(incidents, out=buffer)
        output = buffer.getvalue()

        self.assertIn("Incident 1:", output)
        self.assertIn("Summary: Warning DNS Resolution Timeout", output)
        self.assertIn("Incident Classification: DNS Resolution Timeout", output)
        self.assertIn("Provider Classifications:", output)
        self.assertIn("Name resolution timed out.", output)
        self.assertIn("Retry later.", output)

    def test_display_incident_reports_handles_empty_input(self):
        buffer = StringIO()

        display_incident_reports([], out=buffer)

        self.assertEqual(buffer.getvalue(), "No incidents to display.\n")


if __name__ == "__main__":
    unittest.main()
