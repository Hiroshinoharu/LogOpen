import json

from llm_analysis import analyse_incident_with_llm

with open("reports/incidents.json", encoding="utf-8") as file:
    incidents = json.load(file)

incident = incidents[0]

analysis = analyse_incident_with_llm(incident)

print(analysis.model_dump_json(indent=2))