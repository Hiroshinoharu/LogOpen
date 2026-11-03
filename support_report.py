"""Local, editable support reports. Redaction never changes stored evidence."""

import re


def support_text(incidents, result=None, checklists=None, *, include_notes=False):
    checklists = checklists or {}
    from history_store import incident_key
    lines = ["LOGOPEN SUPPORT REPORT", "", f"Selected occurrences: {len(incidents)}",
             f"Scan time: {getattr(result, 'created_at', None) or 'Unknown'}",
             "This report describes recorded events. Absence from a later scan does not prove a fix."]
    if result is not None:
        coverage = "Complete" if result.complete and not result.cancelled and result.coverage else "Incomplete / unknown / cancelled"
        lines += [f"Scan coverage: {coverage}",
                  f"Scope: {result.scan_options}", f"Coverage details: {result.coverage}",
                  "Warnings: " + "; ".join(result.warnings)]
    for index, item in enumerate(incidents, 1):
        lines += ["", f"{index}. {item.get('incident_classification', 'Unclassified incident')}"]
        for label, field in (("Priority", "incident_priority"), ("Review", "review_state"),
                             ("Computer", "computer_name"), ("Start", "time_generated_start"),
                             ("End", "time_generated_end"), ("Summary", "summary_text")):
            lines.append(f"{label}: {item.get(field, 'Not recorded')}")
        if include_notes:
            lines.append(f"Local note: {item.get('local_note', '')}")
        steps = checklists.get(incident_key(item), [])
        lines.append("Troubleshooting results:")
        lines.extend(f"[{'Done' if step['done'] else 'Pending'}] {step['text']}\nResult: {step['result'] or 'Not recorded'}" for step in steps)
        if not steps:
            lines.append("No saved checks.")
        lines.append("Source evidence:")
        for event in item.get("events", []):
            lines += [f"{event.get('time_generated', 'Unknown time')} | {event.get('log_type', '')} | "
                      f"{event.get('provider', '')} | Event {event.get('event_id', '')}",
                      str(event.get("message", "No message"))]
    return "\n".join(lines) + "\n"


def redact_support(text, incidents, *, usernames=True, computers=True, paths=True, extra=()):
    """Best-effort masking of common fields/paths plus user-specified literal values."""
    names, machines = set(), set()
    for item in incidents:
        for record in [item, *item.get("events", [])]:
            if record.get("computer_name"):
                machines.add(str(record["computer_name"]))
            for field in ("user_name", "username", "account_name", "target_user_name", "subject_user_name"):
                if record.get(field):
                    names.add(str(record[field]))
    if usernames:
        names.update(re.findall(r"(?i)[A-Z]:[\\/]Users[\\/]([^\\/\s\"'<>]+)", text))
        text = re.sub(r"(?im)((?:user\s*name|account\s*name|TargetUserName|SubjectUserName)\s*[:=]\s*)([^\r\n]+)",
                      r"\1[username]", text)
        text = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[email]", text)
    if paths:
        text = re.sub(r'''(?i)"(?:[A-Z]:[\\/]|\\\\)[^"\r\n]*"|'(?:[A-Z]:[\\/]|\\\\)[^'\r\n]*'|(?:[A-Z]:[\\/]|\\\\)[^\r\n<>"|]*''', "[path]", text)
        text = re.sub(r"(?<!\w)/(?:home|Users|tmp|var|etc|opt)/[^\s\"'<>]*", "[path]", text)
    replacements = [(value, "[username]") for value in names if usernames]
    replacements += [(value, "[computer]") for value in machines if computers]
    replacements += [(value.strip(), "[removed]") for value in extra if value.strip()]
    for value, replacement in sorted(replacements, key=lambda pair: len(pair[0]), reverse=True):
        text = re.sub(re.escape(value), lambda _match, token=replacement: token, text, flags=re.IGNORECASE)
    return text
