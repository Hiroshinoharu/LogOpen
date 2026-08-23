# LogOpen

LogOpen reads recent Windows Event Log entries, filters for warnings and errors,
groups related events into incidents, and presents or exports incident summaries.

## Run the project

Run `python main.py` on Windows with the `pywin32` package installed. The program
reads the configured event logs, reports detected incidents in the terminal, and
writes the latest JSON report to `reports/incidents.json`.

## Repository guide

| File or directory | Purpose |
| --- | --- |
| `main.py` | Application entry point. Orchestrates collection, filtering, incident detection, terminal output, and JSON export. |
| `config.py` | Central configuration for log types, bundling thresholds, and provider display names. |
| `event_collection.py` | Reads raw Windows Event Log records and converts them into the project's event dictionary format. |
| `event_filtering.py` | Filters collected events by severity level and time window. |
| `incident_detection.py` | Compares related events, groups them into incidents, and builds incident summary dictionaries. |
| `incident_recurrence.py` | Adds time-window recurrence metadata to completed incident summaries. |
| `incident_classification.py` | Converts event providers and known event patterns into readable classifications. |
| `incident_scoring.py` | Calculates incident scores, records the score reasons, and maps scores to priority levels. |
| `terminal_reporting.py` | Formats incident summaries and event messages for command-line output. |
| `json_reporting.py` | Converts incident data into JSON-safe values and writes report files. |
| `reports/incidents.json` | Generated incident report from the most recent run; this is output data, not source configuration. |
| `tests/` | Unit tests for the workflow and each functional module. |
| `tests/helpers.py` | Shared test factory for creating representative event dictionaries. |
| `tests/test_event_collection.py` | Tests Windows Event Log parsing and collection behavior. |
| `tests/test_event_filtering.py` | Tests severity and time-window filtering. |
| `tests/test_incident_classification.py` | Tests provider, event, and incident classification rules. |
| `tests/test_incident_detection.py` | Tests event similarity, incident bundling, and incident summary generation. |
| `tests/test_terminal_reporting.py` | Tests terminal report formatting. |
| `tests/test_main.py` | Tests the end-to-end orchestration performed by `main.py`. |
| `tests/__init__.py` | Marks the test directory as a Python package. |

## Processing flow

`main.py` collects events -> filters recent warnings and errors -> bundles related
events -> builds incident summaries -> displays them in the terminal and exports
them as JSON.
