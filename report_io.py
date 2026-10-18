"""Read saved scans and export selected incidents without running scans or commands."""

import csv
import io
import json
import math
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from errors import ReportExportError
from json_reporting import export_incidents_to_json
from models.scan_result import ScanResult


class ReportImportError(ValueError):
    """A saved report cannot be read or has an unsupported data structure."""


def _datetime(value, field):
    """Restore an ISO timestamp, retaining its timezone; reject invalid values."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ReportImportError(f"{field} must contain an ISO timestamp.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReportImportError(f"{field} contains an invalid timestamp.") from exc


def _duration(value):
    """Restore timedelta text emitted by json_reporting, including day prefixes."""
    if isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(value) and value >= 0:
            try:
                return timedelta(seconds=value)
            except OverflowError:
                pass
        raise ReportImportError("incident_duration must be a finite, nonnegative duration.")
    match = re.fullmatch(r"(?:(\d+) days?, )?(\d+):(\d{2}):(\d{2}(?:\.\d{1,6})?)", str(value))
    if not match:
        raise ReportImportError("incident_duration contains an invalid duration.")
    days, hours, minutes, seconds = match.groups()
    if int(minutes) >= 60 or float(seconds) >= 60:
        raise ReportImportError("incident_duration contains an invalid duration.")
    try:
        return timedelta(days=int(days or 0), hours=int(hours), minutes=int(minutes), seconds=float(seconds))
    except OverflowError as exc:
        raise ReportImportError("incident_duration is too large.") from exc


def _restore_incident(value, index):
    """Validate one incident and copy nested event/date data into useful Python types."""
    prefix = f"Incident {index + 1}"
    if not isinstance(value, dict):
        raise ReportImportError(f"{prefix} must be an object.")
    incident = dict(value)
    for field in ("incident_classification", "computer_name", "incident_priority", "highest_severity", "summary_text", "log_type", "review_state", "llm_analysis_status", "llm_analysis_message"):
        if field in incident and not isinstance(incident[field], str):
            raise ReportImportError(f"{prefix}: {field} must be text.")
    if "is_recurring" in incident and not isinstance(incident["is_recurring"], bool):
        raise ReportImportError(f"{prefix}: is_recurring must be true or false.")
    for field in ("event_count", "incident_score", "recurrence_count_24h", "recurrence_count_7d"):
        if field in incident:
            number = incident[field]
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < 0:
                raise ReportImportError(f"{prefix}: {field} must be a finite, nonnegative number.")
    for field in ("log_types", "providers", "event_ids", "levels", "incident_score_reasons"):
        if field in incident and (not isinstance(incident[field], list) or any(not isinstance(item, (str, int)) for item in incident[field])):
            raise ReportImportError(f"{prefix}: {field} must be a list of labels or identifiers.")
    for field in ("providers_counts", "event_ids_counts", "event_classification_counts", "provider_classifications", "incident_score_breakdown"):
        if field in incident and not isinstance(incident[field], dict):
            raise ReportImportError(f"{prefix}: {field} must be an object.")
    for field in ("time_generated_start", "time_generated_end"):
        if field in incident:
            incident[field] = _datetime(incident[field], f"{prefix}: {field}")
    if "incident_duration" in incident:
        incident["incident_duration"] = _duration(incident["incident_duration"])
    events = incident.get("events", [])
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        raise ReportImportError(f"{prefix}: events must be a list of objects.")
    incident["events"] = []
    for event in events:
        restored = dict(event)
        if "time_generated" in restored:
            restored["time_generated"] = _datetime(restored["time_generated"], "Event time_generated")
        for field in ("log_type", "computer_name", "provider", "level", "message"):
            if field in restored and not isinstance(restored[field], str):
                raise ReportImportError(f"{prefix}: event {field} must be text.")
        incident["events"].append(restored)
    analysis = incident.get("llm_analysis")
    if analysis is not None:
        if not isinstance(analysis, dict):
            raise ReportImportError(f"{prefix}: llm_analysis must be an object or null.")
        steps = analysis.get("diagnostic_steps", [])
        if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
            raise ReportImportError(f"{prefix}: diagnostic_steps must be a list of objects.")
    return incident


def parse_report(data):
    """Build a ScanResult from a report envelope or legacy incident array.

    Known metadata, incident containers, and timestamps are validated. Optional
    fields missing from older reports stay optional. Input dictionaries are not
    mutated; commands and AI output remain inert report content.

    Raises:
        ReportImportError: The report structure or a supported field is invalid.
    """
    if isinstance(data, list):
        envelope = {"incidents": data}
    elif isinstance(data, dict) and isinstance(data.get("incidents"), list):
        envelope = data
    else:
        raise ReportImportError("Expected an incident array or an object containing an incidents array.")
    result = ScanResult(_restore_incident(value, index) for index, value in enumerate(envelope["incidents"]))
    for field in ("complete", "cancelled"):
        value = envelope.get(field, field == "complete")
        if not isinstance(value, bool):
            raise ReportImportError(f"{field} must be true or false.")
        setattr(result, field, value)
    warnings = envelope.get("warnings", [])
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise ReportImportError("warnings must be a list of messages.")
    result.warnings = list(warnings)
    coverage = envelope.get("coverage", {})
    if not isinstance(coverage, dict) or any(not isinstance(item, dict) for item in coverage.values()):
        raise ReportImportError("coverage must map log names to coverage objects.")
    result.coverage = {name: dict(value) for name, value in coverage.items()}
    for metadata in result.coverage.values():
        if metadata.get("oldest_time") is not None:
            metadata["oldest_time"] = _datetime(metadata["oldest_time"], "Coverage oldest_time")
    options = envelope.get("scan_options", envelope.get("options", {}))
    if not isinstance(options, dict):
        raise ReportImportError("scan_options must be an object.")
    result.scan_options = dict(options)
    result.scan_id = envelope.get("scan_id")
    if result.scan_id is not None and not isinstance(result.scan_id, str):
        raise ReportImportError("scan_id must be text or null.")
    created = envelope.get("created_at")
    result.created_at = _datetime(created, "created_at") if created is not None else None
    comparison = envelope.get("comparison", {})
    if not isinstance(comparison, dict):
        raise ReportImportError("comparison must be an object.")
    result.comparison = dict(comparison)
    for field in ("new", "persistent", "no_longer_observed"):
        if field in comparison:
            if not isinstance(comparison[field], list):
                raise ReportImportError(f"comparison.{field} must be an incident array.")
            result.comparison[field] = [_restore_incident(value, index) for index, value in enumerate(comparison[field])]
    return result


def load_report(path):
    """Read UTF-8 JSON from path and restore a validated ScanResult.

    Supports both current envelopes and historical top-level arrays. No scan,
    API request, history write, or command execution occurs while opening a file.
    """
    try:
        with Path(path).open(encoding="utf-8-sig") as source:
            data = json.load(source)
        return parse_report(data)
    except ReportImportError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ReportImportError(f"Could not open the report: {exc}") from exc


def _csv_cell(value):
    """Convert a cell to text and neutralize spreadsheet formula prefixes."""
    text = str(value if value is not None else "")
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def _markdown_text(value):
    """Escape text used in Markdown paragraphs so imported markup stays literal."""
    text = str(value if value is not None else "")
    return re.sub(r"([\\`*_{}\[\]<>#+.!|~-])", r"\\\1", text)


def _markdown(incidents):
    """Create a readable selected-incident report including evidence and AI advice."""
    lines = ["# LogOpen incident report", "", f"Selected incidents: {len(incidents)}", ""]
    for index, incident in enumerate(incidents, 1):
        lines.extend([f"## {index}. {_markdown_text(incident.get('incident_classification', 'Unclassified incident'))}", ""])
        fields = (("Priority", "incident_priority"), ("Score", "incident_score"), ("Review state", "review_state"), ("Computer", "computer_name"), ("Logs", "log_type"), ("Start", "time_generated_start"), ("End", "time_generated_end"), ("Events", "event_count"))
        for label, field in fields:
            lines.append(f"- **{label}:** {_markdown_text(incident.get(field, 'Not available'))}")
        lines.extend(["", _markdown_text(incident.get("summary_text", "")), ""])
        analysis = incident.get("llm_analysis")
        if isinstance(analysis, dict):
            lines.extend(["### AI analysis", ""])
            for field, title in (("explanation", "Explanation"), ("likely_causes", "Likely causes"), ("recommended_actions", "Recommended actions"), ("remediation_notes", "Remediation notes")):
                value = analysis.get(field, [])
                values = value if isinstance(value, list) else [value]
                lines.extend([f"**{title}**", ""])
                lines.extend(f"- {_markdown_text(item)}" for item in values)
                lines.append("")
            for step in analysis.get("diagnostic_steps", []):
                lines.append(f"- {_markdown_text(step.get('description', ''))} (risk: {_markdown_text(step.get('risk_level', 'unspecified'))})")
                command = step.get("command")
                if command:
                    lines.extend(["", "    " + str(command).replace("\n", "\n    "), ""])
        lines.extend(["### Source events", ""])
        for event in incident.get("events", []):
            lines.append(f"- {_markdown_text(event.get('time_generated', ''))} · {_markdown_text(event.get('provider', ''))} · Event {_markdown_text(event.get('event_id', ''))}")
            lines.extend("> " + _markdown_text(line) for line in str(event.get("message", "")).splitlines())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _atomic_write(path, text):
    """Write UTF-8 through a sibling temporary file, preserving existing files on failure."""
    temporary = None
    try:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", newline="", dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        temporary = None
    except (OSError, ValueError, TypeError) as exc:
        raise ReportExportError(f"Could not write the selected incident report: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def export_selected(incidents, path, format="json"):
    """Atomically export exactly the supplied incidents as JSON, CSV, or Markdown.

    JSON retains all evidence and AI details as a reloadable legacy array. CSV
    contains one summary row per incident and protects text against spreadsheet
    formula execution. Markdown includes evidence and saved AI advice. This
    function does not reanalyse data or execute diagnostic commands.

    Raises:
        ValueError: The format or incident container is unsupported.
        ReportExportError: Serialization or filesystem work fails.
    """
    selected = list(incidents)
    if any(not isinstance(item, dict) for item in selected):
        raise ValueError("Selected incidents must be incident dictionaries.")
    chosen = str(format).lower().lstrip(".")
    if chosen == "json":
        export_incidents_to_json(selected, path)
    elif chosen == "csv":
        columns = ("incident_classification", "incident_priority", "incident_score", "review_state", "computer_name", "log_type", "time_generated_start", "time_generated_end", "event_count", "summary_text")
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows([_csv_cell(incident.get(field, "")) for field in columns] for incident in selected)
        _atomic_write(path, output.getvalue())
    elif chosen in {"markdown", "md"}:
        _atomic_write(path, _markdown(selected))
    else:
        raise ValueError("Choose json, csv, or markdown for the export format.")
