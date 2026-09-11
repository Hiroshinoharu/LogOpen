"""Unit tests for safe JSON incident report exports."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from errors import ReportExportError
from json_reporting import export_incidents_to_json
from models.incident_analysis import IncidentAnalysis


class JsonReportingTests(unittest.TestCase):
    def test_export_preserves_nested_diagnostic_steps(self):
        steps = [
            {"description": "Inspect the affected service.", "command": "Get-Service", "shell": "powershell", "risk_level": "safe"},
            {"description": "Review related events.", "command": None, "shell": None, "risk_level": "safe"},
        ]
        analysis = IncidentAnalysis(
            explanation="An incident needs investigation.",
            likely_causes=[], recommended_actions=[], remediation_notes=[],
            diagnostic_steps=steps,
        )
        with TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "incidents.json"
            export_incidents_to_json([{"llm_analysis": analysis.model_dump(mode="json")}], output_path)
            exported = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(exported[0]["llm_analysis"]["diagnostic_steps"], steps)

    def test_export_creates_parent_directories_and_writes_valid_json(self):
        with TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "nested" / "incidents.json"

            export_incidents_to_json([{"event_count": 2}], output_path)

            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                [{"event_count": 2}],
            )

    def test_failed_serialization_preserves_the_previous_report(self):
        with TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "incidents.json"
            output_path.write_text('[{"previous": true}]\n', encoding="utf-8")

            with self.assertRaisesRegex(ReportExportError, "Could not write"):
                export_incidents_to_json([{"invalid": object()}], output_path)

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                '[{"previous": true}]\n',
            )
            self.assertEqual(
                list(output_path.parent.glob(".incidents.json.*.tmp")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
