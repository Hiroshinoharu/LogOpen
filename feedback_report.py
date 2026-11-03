"""Minimal diagnostic reports, generated locally and never automatically uploaded."""
import os
import platform
import re
import struct
import sys
from importlib.util import find_spec
from support_report import redact_support, support_text
from version_info import application_version


def problem_report(description, status="", incidents=(), *, include_incidents=False):
    text = (f"LOGOPEN PROBLEM REPORT\nApp version: {application_version()}\n"
            f"OS: {platform.system()} {platform.release()} ({platform.version()})\n"
            f"Architecture: {struct.calcsize('P') * 8}-bit\n"
            f"Distribution: {'packaged' if getattr(sys, 'frozen', False) else 'source'}\n"
            f"Windows event bindings available: {find_spec('win32evtlog') is not None}\n\n"
            f"What happened / steps to reproduce:\n{description}\n\nVisible app status:\n{status}\n")
    if include_incidents:
        text += "\n" + support_text(incidents)
    # Also remove identifiers from free-text errors and descriptions.
    local = {"user_name": os.environ.get("USERNAME", ""), "computer_name": os.environ.get("COMPUTERNAME", "")}
    text = redact_support(text, [*incidents, local])
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[API key]", text)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+", r"\1[removed]", text)
    return text
