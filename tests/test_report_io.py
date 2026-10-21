"""Saved-report validation and faithful, atomic selected-incident exports."""

import csv
from datetime import datetime, timedelta, timezone
import json

import pytest

from errors import ReportExportError
from incident_detection import build_incident
from json_reporting import export_incidents_to_json
from models.scan_result import ScanResult
from report_io import ReportImportError, export_selected, load_report, parse_report
from tests.helpers import make_event


def sample_incident(**overrides):
    """Create source-backed incident data for report tests without OS access."""
    incident = build_incident([make_event(time_generated=datetime(2026, 10, 18, 12, tzinfo=timezone.utc))])
    incident.update(overrides)
    return incident


def test_load_report_accepts_envelope_and_legacy_array(tmp_path):
    """Both supported formats restore event timestamps and incident durations."""
    original = sample_incident(incident_duration=timedelta(days=2, seconds=15, microseconds=123456))
    for source in ([original], ScanResult([original])):
        path = tmp_path / "report.json"
        export_incidents_to_json(source, path)
        restored = load_report(path)
        assert restored[0]["events"][0]["time_generated"] == original["events"][0]["time_generated"]
        assert restored[0]["incident_duration"] == original["incident_duration"]
        assert restored.complete is True


@pytest.mark.parametrize("payload", [
    None,
    {"incidents": {}},
    ["not an incident"],
    {"incidents": [], "complete": "true"},
    {"incidents": [], "coverage": []},
    {"incidents": [], "warnings": "warning"},
    {"incidents": [], "collected_events": {}},
    {"incidents": [], "collected_events": [None]},
    {"incidents": [], "collected_events": [{"time_generated": "yesterday"}]},
    {"incidents": [], "collected_events": [{"level": 4}]},
    [{"events": [None]}],
    [{"events": [{"time_generated": "yesterday"}]}],
    [{"incident_duration": "forever"}],
    [{"incident_duration": "0:60:00"}],
    [{"incident_score": float("nan")}],
    [{"event_count": "twelve"}],
    [{"llm_analysis": {"diagnostic_steps": "run me"}}],
])
def test_invalid_report_shapes_raise_import_error(payload):
    """Malformed known fields cannot reach UI code as unexpected container types."""
    with pytest.raises(ReportImportError):
        parse_report(payload)


def test_read_failures_and_invalid_json_use_import_error(tmp_path):
    """A missing or malformed file produces a user-facing import exception."""
    path = tmp_path / "report.json"
    with pytest.raises(ReportImportError, match="Could not open"):
        load_report(path)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ReportImportError, match="Could not open"):
        load_report(path)


def test_selected_json_exports_only_selected_evidence(tmp_path):
    """A selected export contains exactly the chosen incident and its AI output."""
    chosen = sample_incident(llm_analysis={"explanation": "Stored advice", "diagnostic_steps": []})
    path = tmp_path / "selected.json"
    export_selected([chosen], path, "json")
    restored = load_report(path)
    assert len(restored) == 1
    assert restored[0]["llm_analysis"] == chosen["llm_analysis"]
    assert restored[0]["events"] == chosen["events"]


@pytest.mark.parametrize("value", ["=HYPERLINK(\"bad\")", "+cmd", "-2+3", "@SUM(1)", "  =SUM(1)", "\tformula", "\nformula"])
def test_csv_neutralizes_spreadsheet_formula_cells(tmp_path, value):
    """Untrusted source labels are exported as literal spreadsheet text."""
    path = tmp_path / "selected.csv"
    export_selected([sample_incident(incident_classification=value)], path, "csv")
    with path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 1
    assert rows[0]["incident_classification"] == "'" + value
    assert rows[0]["incident_priority"] == "Low"
    assert rows[0]["event_count"] == "1"


def test_markdown_includes_human_summary_evidence_and_saved_advice(tmp_path):
    """Readable exports retain diagnostic text without interpreting imported markup."""
    incident = sample_incident(incident_classification="<script>bad</script>")
    incident["llm_analysis"] = {"explanation": "Explanation", "recommended_actions": ["Check logs"], "diagnostic_steps": [{"description": "Inspect", "command": "Get-Service\nGet-Process", "risk_level": "safe"}]}
    path = tmp_path / "report.md"
    export_selected([incident], path, "markdown")
    text = path.read_text(encoding="utf-8")
    assert "Selected incidents: 1" in text
    assert "<script>" not in text
    assert "**Priority:** Low" in text
    assert "Check logs" in text
    assert "    Get-Service\n    Get-Process" in text
    assert "Test message" in text


@pytest.mark.parametrize("format", ["csv", "markdown"])
def test_failed_atomic_export_preserves_previous_file(tmp_path, monkeypatch, format):
    """An interrupted replacement leaves the existing destination and no temp files."""
    path = tmp_path / "selected.report"
    path.write_text("old report", encoding="utf-8")

    def reject_replace(*_args):
        """Simulate a destination locked by another application."""
        raise PermissionError("File is in use")

    monkeypatch.setattr("report_io.os.replace", reject_replace)
    with pytest.raises(ReportExportError):
        export_selected([sample_incident()], path, format)
    assert path.read_text(encoding="utf-8") == "old report"
    assert list(tmp_path.glob("*.tmp")) == []


def test_parse_report_does_not_mutate_input():
    """Date restoration copies source dictionaries before replacing fields."""
    payload = [{"events": [{"time_generated": "2026-10-18T12:00:00Z"}], "time_generated_start": "2026-10-18T12:00:00Z"}]
    original = json.dumps(payload)
    parsed = parse_report(payload)
    assert isinstance(parsed[0]["time_generated_start"], datetime)
    assert json.dumps(payload) == original
