import json

def make_incident_json_safe(incident):
    """
    Convert an incident dictionary to a JSON-safe format.

    :param incident: Incident dictionary to convert.
    :return: JSON-safe incident dictionary.
    """
    json_safe_incident = {}
    for key, value in incident.items():
        if isinstance(value, (dict, list)):
            json_safe_incident[key] = make_incident_json_safe(value)
        else:
            json_safe_incident[key] = value
    return json_safe_incident

def export_incidents_to_json(incidents, file_path):
    """
    Export a list of incidents to a JSON file.

    :param incidents: List of incident dictionaries to export.
    :param file_path: Path to the output JSON file.
    """
    json_safe_incidents = [make_incident_json_safe(incident) for incident in incidents]
    with open(file_path, 'w') as json_file:
        json.dump(json_safe_incidents, json_file, indent=4)