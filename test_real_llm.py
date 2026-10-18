"""Manual AI smoke test using the first incident in reports/incidents.json.

Run explicitly from the project directory. It uses saved AI preferences and
resolved credentials, so execution can send report details in a billable
request. Importing this module or collecting it with pytest makes no request.
"""

import json

from llm_analysis import analyse_incident_with_llm

def main():
    """Load a report envelope/legacy array and print analysis of its first incident.

    Uses the direct AI helper, bypassing scan selection/limits while respecting
    the enabled preference. Empty reports and unavailable analysis are reported
    to stdout; file-read/JSON errors are allowed to propagate for manual diagnosis.
    """
    with open("reports/incidents.json", encoding="utf-8") as file:
        report = json.load(file)
    incidents = report.get("incidents", []) if isinstance(report, dict) else report
    if not incidents:
        print("No incidents in the report to analyze.")
        return

    incident = incidents[0]
    analysis = analyse_incident_with_llm(incident)
    print(analysis.model_dump_json(indent=2) if analysis else "AI analysis was skipped.")


if __name__ == "__main__":
    main()
