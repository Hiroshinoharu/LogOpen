"""Small declarative detection rules, matched by channel, provider, and event ID.

Rules describe recorded activity, not proof of an attack or a root cause.
Custom rules contain data only: no Python, shell commands, or regular expressions.
"""

import json

DEFAULT_LEVELS = ("Critical", "Error", "Warning", "Failure Audit")
DEFENDER = "Microsoft-Windows-Windows Defender/Operational"
UPDATE = "Microsoft-Windows-WindowsUpdateClient/Operational"
WLAN = "Microsoft-Windows-WLAN-AutoConfig/Operational"
POWERSHELL = "Microsoft-Windows-PowerShell/Operational"
PERFORMANCE = "Microsoft-Windows-Diagnostics-Performance/Operational"

PROFILES = {
    "general": ("General troubleshooting", ("System", "Application")),
    "security": ("Security", ("System", "Application", "Security", DEFENDER, POWERSHELL)),
    "network": ("Networking", ("System", WLAN, "Microsoft-Windows-DNS-Client/Operational")),
    "performance": ("Performance and updates", ("System", "Application", PERFORMANCE, UPDATE)),
    "custom": ("Custom channels", ()),
}

CHANNEL_DESCRIPTIONS = {
    "System": "Windows services, drivers, hardware, and system failures.",
    "Application": "Application errors, crashes, and application-specific events.",
    "Security": "Windows audit events; access and audit policy determine what is available.",
    DEFENDER: "Microsoft Defender detections, actions, and protection status.",
    UPDATE: "Windows Update client activity and installation failures.",
    WLAN: "Wi-Fi connections, authentication, and disconnections.",
    POWERSHELL: "PowerShell engine and enabled logging; script records may contain sensitive data.",
    PERFORMANCE: "Windows startup, shutdown, and performance diagnostics.",
    "Microsoft-Windows-DNS-Client/Operational": "DNS client activity; this channel may be disabled.",
}


def _rule(identifier, channel, provider, event_ids, label, severity):
    """Build a built-in rule with the same inspectable schema as custom rules."""
    return dict(id=identifier, channel=channel, provider=provider, event_ids=event_ids,
                label=label, severity=severity, enabled=True)


BUILTIN_RULES = (
    _rule("failed-logon", "Security", "Microsoft-Windows-Security-Auditing", [4625], "Failed Logon", "Warning"),
    _rule("account-lockout", "Security", "Microsoft-Windows-Security-Auditing", [4740], "Account Locked Out", "Warning"),
    _rule("audit-cleared", "Security", "Microsoft-Windows-Eventlog", [1102], "Security Audit Log Cleared", "Error"),
    _rule("defender-detection", DEFENDER, "Microsoft-Windows-Windows Defender", [1116], "Defender Threat Detected", "Error"),
    _rule("defender-action", DEFENDER, "Microsoft-Windows-Windows Defender", [1117], "Defender Protection Action", "Information"),
    _rule("defender-disabled", DEFENDER, "Microsoft-Windows-Windows Defender", [5001], "Defender Real-Time Protection Disabled", "Error"),
    _rule("update-failed", "*", "Microsoft-Windows-WindowsUpdateClient", [20], "Windows Update Installation Failed", "Error"),
    _rule("wifi-failed", WLAN, "Microsoft-Windows-WLAN-AutoConfig", [8002], "Wi-Fi Connection Failed", "Error"),
    _rule("wifi-disconnected", WLAN, "Microsoft-Windows-WLAN-AutoConfig", [8003], "Wi-Fi Disconnected", "Information"),
    _rule("boot-performance", PERFORMANCE, "Microsoft-Windows-Diagnostics-Performance", [100], "Boot Performance Report", "Information"),
    _rule("shutdown-performance", PERFORMANCE, "Microsoft-Windows-Diagnostics-Performance", [200], "Shutdown Performance Report", "Information"),
)


def parse_custom_rules(text):
    """Validate a JSON array of at most 100 exact-match rules and return copies.

    Each rule requires id, channel (or '*'), provider, event_ids, label, and
    severity. Optional enabled defaults to true. Unknown fields, duplicate IDs,
    invalid types, and overly broad/malformed identifiers raise ValueError.
    """
    if not isinstance(text, str) or len(text) > 100_000:
        raise ValueError("Custom rules must be JSON text smaller than 100 KB.")
    try:
        rules = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise ValueError("Custom rules must be a valid JSON array.") from exc
    if not isinstance(rules, list) or len(rules) > 100:
        raise ValueError("Provide an array of at most 100 custom rules.")
    seen = set()
    validated = []
    required = {"id", "channel", "provider", "event_ids", "label", "severity"}
    for index, rule in enumerate(rules, 1):
        prefix = f"Rule {index}: "
        if not isinstance(rule, dict) or not required <= rule.keys() or rule.keys() - required - {"enabled"}:
            raise ValueError(prefix + "use id, channel, provider, event_ids, label, severity, and optional enabled.")
        for field in ("id", "channel", "provider", "label", "severity"):
            if not isinstance(rule[field], str) or not rule[field].strip() or len(rule[field]) > 512:
                raise ValueError(prefix + f"{field} must be nonempty text, at most 512 characters.")
        item = {key: value.strip() if isinstance(value, str) else value for key, value in rule.items()}
        if item["id"] in seen:
            raise ValueError(prefix + "duplicate rule id.")
        seen.add(item["id"])
        if item["provider"] == "*":
            raise ValueError(prefix + "specify an exact provider so event IDs are not confused across sources.")
        ids = item["event_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(type(n) is not int or not 0 <= n <= 65535 for n in ids):
            raise ValueError(prefix + "event_ids must contain 1–100 integers between 0 and 65535.")
        if item["severity"] not in ("Information", "Warning", "Error", "Critical"):
            raise ValueError(prefix + "severity must be Information, Warning, Error, or Critical.")
        if type(item.get("enabled", True)) is not bool:
            raise ValueError(prefix + "enabled must be true or false.")
        item["enabled"] = item.get("enabled", True)
        item["event_ids"] = sorted(set(ids))
        validated.append(item)
    return validated


class RuleSet:
    """Index one immutable scan's rules by provider and ID for efficient matching."""

    def __init__(self, custom_json="[]"):
        """Validate custom rules and give them precedence over built-in labels."""
        self.index = {}
        for rule in [*parse_custom_rules(custom_json), *BUILTIN_RULES]:
            if rule.get("enabled", True):
                for event_id in rule["event_ids"]:
                    self.index.setdefault((rule["provider"].casefold(), event_id), []).append(rule)

    def matches(self, event):
        """Return matching rules without modifying Windows evidence or event severity."""
        candidates = self.index.get((event.get("provider", "").casefold(), event.get("event_id")), ())
        channel = event.get("log_type", "").casefold()
        return [rule for rule in candidates if rule["channel"] == "*" or rule["channel"].casefold() == channel]

    def apply(self, event):
        """Annotate matching rules and return whether the event deserves inclusion.

        Custom rules take precedence for the label. Original level stays intact;
        detection_severity is separately available for incident prioritization.
        """
        matches = self.matches(event)
        if matches:
            event["detection_rules"] = [{key: rule[key] for key in ("id", "label", "severity")} for rule in matches]
            event["detection_classification"] = matches[0]["label"]
            event["detection_severity"] = matches[0]["severity"]
        return bool(matches) or event.get("level") in DEFAULT_LEVELS
