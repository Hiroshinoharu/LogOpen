"""
JSON reporting utilities for LogOpen.

This module converts analysed Windows Event Log incidents into
JSON-serializable data and exports the resulting incident reports
to JSON files.
"""

import json
from datetime import datetime


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
    json_safe_incidents = make_json_safe(incidents)

    with open(file_path, "w", encoding="utf-8") as json_file:
        json.dump(
            json_safe_incidents,
            json_file,
            indent=4
        )