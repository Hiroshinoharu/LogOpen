"""Pydantic schemas for structured AI explanations and diagnostic suggestions."""

from typing import Literal
from pydantic import BaseModel, model_validator


DiagnosticShell = Literal["powershell", "cmd", "bash"]

class DiagnosticStep(BaseModel):
    """One diagnostic suggestion with an optional command and an explicit risk.

    Attributes:
        description: Human-readable purpose of the suggested step.
        command: Literal command text, or None for a prose-only step.
        shell: powershell, cmd, or bash; optional for older saved reports.
        risk_level: safe, caution, or system_change, as supplied by the analysis.

    Commands are displayed/copied, never executed by this model or the UI.
    Risk labels are model output rather than verification that a command is safe.
    """
    description: str
    command: str | None = None
    # Optional for compatibility with reports generated before shell metadata.
    shell: DiagnosticShell | None = None
    risk_level: Literal["safe", "caution", "system_change"]

    @model_validator(mode="after")
    def validate_command_shell(self):
        """Return this step, rejecting a shell when command text is absent or blank.

        Raises ValueError during Pydantic validation for that inconsistent pair.
        A command without a shell remains valid for legacy reports; new AI results
        undergo the stricter configured-shell check in llm_analysis.
        """
        if not (self.command and self.command.strip()) and self.shell is not None:
            raise ValueError("shell must be null when no command is provided")
        return self

class IncidentAnalysis(BaseModel):
    """Structured explanation accepted from the OpenAI parsing response.

    explanation is prose; likely_causes, recommended_actions, and
    remediation_notes are lists of strings. diagnostic_steps contains validated
    DiagnosticStep objects. Every field is required, but lists may be empty.
    model_dump(mode="json") produces the dictionary stored on an incident.
    """
    explanation: str
    likely_causes: list[str]
    recommended_actions: list[str]
    remediation_notes: list[str]
    diagnostic_steps: list[DiagnosticStep]
