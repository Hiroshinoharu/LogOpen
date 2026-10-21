"""Pydantic schemas for structured AI explanations and diagnostic suggestions."""

from typing import Literal
from pydantic import BaseModel, Field, model_validator


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
    description: str = Field(description="A plain-language check, why it helps, and what result to look for; state elevation if required.")
    command: str | None = Field(default=None, description="One valid command for the declared shell, without Markdown or a prompt prefix; null for a UI or prose-only step.")
    # Optional for compatibility with reports generated before shell metadata.
    shell: DiagnosticShell | None = Field(default=None, description="The command's shell; null when command is null. Follow the configured allowed shells.")
    risk_level: Literal["safe", "caution", "system_change"] = Field(description="safe for ordinary read-only checks, caution for temporary changes or elevated diagnostics, system_change for persistent changes.")

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
    explanation: str = Field(description="Two to four plain-language sentences explaining the recorded evidence, possible impact, and uncertainty.")
    likely_causes: list[str] = Field(description="Up to three evidence-supported hypotheses with the clue for each; empty when a cause is unsupported.")
    recommended_actions: list[str] = Field(description="One to four useful next actions, lowest disruption first, with where to act and why; avoid duplicating diagnostic commands.")
    remediation_notes: list[str] = Field(description="Up to three relevant verification, escalation, or missing-evidence notes; may be empty.")
    diagnostic_steps: list[DiagnosticStep] = Field(description="Up to four focused checks; include commands only when useful and reliable. May be empty.")
