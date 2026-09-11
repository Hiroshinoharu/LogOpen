from typing import Literal
from pydantic import BaseModel, model_validator


DiagnosticShell = Literal["powershell", "cmd", "bash"]

class DiagnosticStep(BaseModel):
    """
    This class represents a diagnostic step in the incident analysis process, including its description, command to execute (if applicable), and risk level.
    
    Args:
        BaseModel (class): The base class for the Pydantic model.
    """
    description: str
    command: str | None = None
    # Optional for compatibility with reports generated before shell metadata.
    shell: DiagnosticShell | None = None
    risk_level: Literal["safe", "caution", "system_change"]

    @model_validator(mode="after")
    def validate_command_shell(self):
        if not (self.command and self.command.strip()) and self.shell is not None:
            raise ValueError("shell must be null when no command is provided")
        return self

class IncidentAnalysis(BaseModel):
    """
    This class represents the analysis of an incident, including its explanation, likely causes, recommended actions, remediation notes, and diagnostic steps.

    Args:
        BaseModel (class): The base class for the Pydantic model.
    """
    explanation: str
    likely_causes: list[str]
    recommended_actions: list[str]
    remediation_notes: list[str]
    diagnostic_steps: list[DiagnosticStep]
