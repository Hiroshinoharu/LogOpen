"""Convert incident data to JSON and atomically replace report files.

ScanResult exports include coverage/warnings alongside incidents; plain
lists retain the legacy array format. This module performs no analysis.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from errors import ReportExportError, describe_error
from models.scan_result import ScanResult


def make_json_safe(value):
    """Copy supported report containers and convert their date/time values.

    ScanResult becomes its report envelope before normal list handling.
    Dictionary values and list items are converted recursively; datetimes use
    ISO 8601 text and timedeltas use str(). Other values pass through unchanged,
    so unsupported objects can still cause json.dump to fail. Inputs are not
    mutated, and dictionary keys are left as supplied.
    """
    if isinstance(value, ScanResult):
        return make_json_safe(value.as_report())

    if isinstance(value, dict):
        return {
            key: make_json_safe(item)
            for key, item in value.items()
        }

    elif isinstance(value, list):
        return [
            make_json_safe(item)
            for item in value
        ]

    elif isinstance(value, datetime):
        return value.isoformat()

    elif isinstance(value, timedelta):
        return str(value)

    else:
        return value


def export_incidents_to_json(incidents, file_path):
    """Write a readable UTF-8 report, replacing the destination after serialization.

    Args:
        incidents: ScanResult for a metadata envelope, or a list of incident
            dictionaries for the legacy top-level array format.
        file_path: String or path-like destination, relative to the working
            directory unless absolute. Missing parent directories are created.

    Raises:
        ReportExportError: Conversion, serialization, or filesystem work fails.

    Writes and fsyncs a sibling temporary file, then uses os.replace. A failed
    write leaves the previous report intact and attempts temporary-file cleanup.
    Returns None; the caller decides how export failures affect scan status.
    """
    temporary_path = None
    try:
        output_path = Path(file_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        json_safe_incidents = make_json_safe(incidents)

        # Write beside the destination and replace it only after JSON serialization
        # succeeds, preserving the previous report if anything goes wrong.
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as json_file:
            temporary_path = Path(json_file.name)
            json.dump(json_safe_incidents, json_file, indent=4)
            json_file.write("\n")
            json_file.flush()
            os.fsync(json_file.fileno())

        os.replace(temporary_path, output_path)
        temporary_path = None
    except Exception as exc:
        raise ReportExportError(
            f"Could not write the JSON report to {file_path!s}: "
            f"{describe_error(exc)}"
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
