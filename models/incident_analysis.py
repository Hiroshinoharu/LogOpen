from pydantic import BaseModel

class IncidentAnalysis(BaseModel):
    explanation: str
    likely_causes: list[str]
    recommended_actions: list[str]
    remediation_notes: list[str]