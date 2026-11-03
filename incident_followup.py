"""Local evidence checks for focused rescans; absence is never treated as a repair."""

from datetime import datetime, timezone

from history_store import incident_key
from models.scan_options import ScanOptions
from monitoring import evidence_id


def focused_options(incident, result, defaults):
    """Restrict a local-only recheck to the incident's evidenced channels, preserving known limits."""
    names = incident.get("log_types") or [event.get("log_type") for event in incident.get("events", [])]
    if isinstance(names, str):
        names = [names]
    names = tuple(dict.fromkeys(name for name in names if isinstance(name, str) and name.strip()))
    if not names:
        # A comma-joined display label is not a registered Windows channel.
        label = incident.get("log_type")
        names = (label,) if isinstance(label, str) and label and "," not in label else ()
    if not names:
        raise ValueError("This report has no source log names. Choose the relevant Windows logs in Scan options and scan again.")
    scope = getattr(result, "scan_options", {}) or {}
    return ScanOptions(log_types=names, hours=scope.get("hours", defaults.hours),
                       max_records=scope.get("max_records", defaults.max_records),
                       rules_json=scope.get("rules_json", defaults.rules_json),
                       profile="custom", automatic_ai=False)


def followup_summary(incident, baseline, result):
    """Describe fresh family evidence, requiring complete known coverage before reporting absence."""
    key = incident_key(incident)
    old = {evidence_id(event) for item in baseline if incident_key(item) == key
           for event in item.get("events", [])}
    old.update(evidence_id(event) for event in incident.get("events", []))
    matches = [item for item in result if incident_key(item) == key]
    boundary = getattr(baseline, "created_at", None)
    try:
        boundary = datetime.fromisoformat(str(boundary).replace("Z", "+00:00"))
        boundary = boundary.replace(tzinfo=timezone.utc) if boundary.tzinfo is None else boundary
    except (TypeError, ValueError):
        boundary = None
    fresh = []
    unknown = False
    for item in matches:
        for event in item.get("events", []):
            if evidence_id(event) in old:
                continue
            try:
                stamp = event.get("time_generated")
                stamp = stamp if isinstance(stamp, datetime) else datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
                if boundary is None:
                    unknown = True
                elif stamp >= boundary:
                    fresh.append(event)
            except (ValueError, TypeError):
                unknown = True
    if fresh:
        return f"Check again: {len(fresh)} new matching log {'entry' if len(fresh) == 1 else 'entries'} since the previous scan. Review the latest evidence."
    channels = getattr(result, "scan_options", {}).get("log_types", ())
    scope = getattr(result, "scan_options", {})
    old_scope = getattr(baseline, "scan_options", {})
    coverage = getattr(result, "coverage", {})
    complete = (getattr(result, "complete", False) and not getattr(result, "cancelled", False)
                and bool(channels) and all(coverage.get(name, {}).get("complete") is True for name in channels))
    try:
        scanned_at = result.created_at
        scanned_at = scanned_at if isinstance(scanned_at, datetime) else datetime.fromisoformat(str(scanned_at).replace("Z", "+00:00"))
        scanned_at = scanned_at.replace(tzinfo=timezone.utc) if scanned_at.tzinfo is None else scanned_at
        elapsed = (scanned_at - boundary).total_seconds() if boundary is not None else -1
        complete = (complete and 0 <= elapsed <= float(scope["hours"]) * 3600
                    and "rules_json" in old_scope and scope.get("rules_json") == old_scope["rules_json"]
                    and scope.get("detection_version") == old_scope.get("detection_version")
                    and set(channels).issubset(old_scope.get("log_types", ())))
    except (ValueError, TypeError, KeyError, AttributeError):
        complete = False
    if not complete or unknown or boundary is None:
        return "Check again: coverage or timing is incomplete. The available evidence cannot establish whether new matching activity occurred."
    if matches:
        return "Check again: only previously observed matching activity was found. No new matching entries were observed since the previous scan; this does not prove a fix."
    return "Check again: no matching activity was observed in this scan. This does not prove a fix; unscanned periods remain unknown."
