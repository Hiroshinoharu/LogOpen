import json

from llm_analysis import analyse_incident_with_llm

def main():
    """Explicit manual smoke test; importing this file never calls OpenAI."""
    with open("reports/incidents.json", encoding="utf-8") as file:
        incidents = json.load(file)

    incident = incidents[0]
    analysis = analyse_incident_with_llm(incident)
    print(analysis.model_dump_json(indent=2) if analysis else "AI analysis was skipped.")


if __name__ == "__main__":
    main()
