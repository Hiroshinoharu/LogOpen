"""
JSON reporting utilities for LogOpen.

This module converts analysed Windows Event Log incidents into
JSON-serializable data and exports the resulting incident reports
to JSON files.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from errors import ReportExportError, describe_error


def make_json_safe(value):
    """
    Recursively convert Python objects into JSON-serializable values.

    Dictionaries and lists are recursively processed so that nested
    objects are also converted. Datetime objects are converted to
    ISO 8601 formatted strings. Values that are already JSON-safe
    are returned unchanged.

    Args:
        value: The Python object to convert.

    Returns:
        A JSON-serializable representation of the supplied value.
    """
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
    """
    Export analysed incident data to a JSON file.

    Converts the supplied incident data into a JSON-safe format before
    writing it to the specified file. The resulting JSON is indented
    to improve readability.

    Args:
        incidents (list[dict]): Incident summaries to export.
        file_path (str): Path of the JSON file to create.

    Returns:
        None
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
