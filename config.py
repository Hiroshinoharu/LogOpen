"""Central configuration for LogOpen's input logs and incident rules."""

from datetime import timedelta

# Constants for incident detection and bundling
LOG_TYPES = ["System", "Application"]
INCIDENT_BUNDLE_TIMEDELTA = timedelta(minutes=1)
EVENT_SIMILARITY_THRESHOLD = 3

# Classification mapping for known providers
PROVIDER_CLASSIFICATIONS = {
    "DCOM": "Distributed COM",
    "Microsoft-Windows-DNS-Client": "DNS Client",
    "Microsoft-Windows-DNS-Server": "DNS Server",
    "Microsoft-Windows-GroupPolicy": "Group Policy",
    "Microsoft-Windows-GroupPolicy-Client": "Group Policy Client",
    "Microsoft-Windows-GroupPolicy-User": "Group Policy User",
    "Microsoft-Windows-GroupPolicy-Machine": "Group Policy Machine",
    "Microsoft-Windows-WLAN-AutoConfig": "WLAN AutoConfig",
    "Service Control Manager": "Service Control Manager",
    "Tcpip": "TCP/IP",
    "winsrvext": "Windows Server Extension",
}

# LLM analysis configuration
ENABLE_LLM_ANALYSIS = True
LLM_ANALYSIS_MIN_EVENTS = 5

# Constants for LLM analysis
MAX_CONTEXT_EVENTS = 25
MAX_MESSAGE_LENGTH = 1_500
DEFAULT_ANALYSIS_MODEL = "gpt-5.6-luna"

# Instructions for LLM analysis
ANALYSIS_INSTRUCTIONS = """
You are an expert in troubleshooting PC issues, particularly Windows operating systems.
You are also an expert in analyzing and interpreting event logs, system logs, and other
diagnostic information to identify potential issues and recommend resolutions.

Analyze the incident context and return a structured response containing exactly these
four fields:
- explanation: A clear explanation of what the incident indicates and its likely impact.
- likely_causes: A list of the most likely root causes, ordered from most to least plausible
  based on the supplied evidence.
- recommended_actions: A prioritized list of practical diagnostic or corrective actions.
- remediation_notes: Additional cautions, verification steps, and relevant follow-up notes.

Base the response only on the supplied incident evidence.

Clearly distinguish confirmed facts from hypotheses.

Do not invent:
- Event IDs
- providers
- services
- applications
- registry keys
- commands
- configuration values
- causes that are unsupported by the supplied evidence

If the evidence is insufficient to determine a specific cause, explicitly say that the
cause is uncertain rather than guessing.

Prefer safe, non-destructive diagnostic steps before recommending configuration changes
or other potentially disruptive actions.

When suggesting remediation:
- prioritize verification and diagnosis first
- explain why an action may help
- avoid destructive actions unless strongly justified by the supplied evidence
- mention when administrator privileges, backups, or additional investigation may be needed

Keep the explanation concise and technically accurate.
Do not repeat the raw incident data unnecessarily.
"""
