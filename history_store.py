"""Persist local scans, compare matching scopes, and remember incident review state.

SQLite lives under LOCALAPPDATA/LogOpen, or LOGOPEN_DATA_DIR when supplied. Each
operation opens its own connection so GUI and scan workers never share SQLite
connections. Stored reports contain source evidence and existing AI output, but
never API credentials. No network or diagnostic commands are used here.
"""

from bisect import bisect_left, bisect_right
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from json_reporting import make_json_safe
from report_io import parse_report


class HistoryStoreError(RuntimeError):
    """The local history database could not be read or written."""


def _canonical(value):
    """Serialize supported report values deterministically for keys and database rows."""
    return json.dumps(make_json_safe(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _timestamp(value):
    """Normalize aware/local-naive timestamps to comparable UTC seconds."""
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return value.timestamp()


def _scope(options):
    """Normalize scan scope so equivalent log orderings compare equally."""
    if options is None:
        options = {}
    if hasattr(options, "as_dict"):
        options = options.as_dict()
    if not isinstance(options, dict):
        options = {name: getattr(options, name) for name in ("log_types", "hours", "max_records") if hasattr(options, name)}
    logs = options.get("log_types", [])
    if isinstance(logs, str):
        logs = [logs]
    return {
        "log_types": sorted({str(name).casefold() for name in logs}),
        "hours": float(options["hours"]) if options.get("hours") is not None else None,
        "max_records": int(options.get("max_records", 100_000)),
        "rules_json": options.get("rules_json", "[]"),
        "detection_version": options.get("detection_version", 1),
    }


def _options(options):
    """Copy user-facing scope metadata from a dictionary or ScanOptions object."""
    if hasattr(options, "as_dict"):
        return dict(options.as_dict())
    if isinstance(options, dict):
        return dict(options)
    return {name: getattr(options, name) for name in ("log_types", "hours", "max_records") if hasattr(options, name)}


def incident_key(incident):
    """Return a stable issue-family key across occurrences, scans, and event counts.

    Review state and comparison match computer, classification, provider names,
    and event identifiers. Different occurrences of that issue share a key;
    unrelated computers and providers keep independent states.
    """
    identity = [
        str(incident.get("computer_name", "")).casefold(),
        str(incident.get("incident_classification", "")).casefold(),
        sorted({str(value).casefold() for value in incident.get("providers", [])}),
        sorted({str(value) for value in incident.get("event_ids", [])}),
    ]
    return hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()


def _evidence_keys(incident):
    """Identify individual event evidence so overlapping scans count it only once."""
    keys = []
    for event in incident.get("events", []):
        if not isinstance(event, dict) or event.get("time_generated") is None:
            continue
        evidence = [str(event.get(name, "")) for name in ("computer_name", "log_type", "provider", "event_id", "message")]
        evidence.append(_timestamp(event["time_generated"]))
        if "record_number" in event:
            evidence.append(event["record_number"])
        keys.append(hashlib.sha256(_canonical(evidence).encode("utf-8")).hexdigest())
    if not keys:
        keys.append(_canonical([incident_key(incident), incident.get("time_generated_start"), incident.get("time_generated_end")]))
    return keys


class HistoryStore:
    """Access local scan snapshots and issue states with short-lived connections."""

    def __init__(self, path=None):
        """Choose a database file without opening it or creating directories.

        An explicit path is useful for tests. Otherwise LOGOPEN_DATA_DIR takes
        precedence over LOCALAPPDATA/LogOpen; non-Windows hosts use ~/.local/share.
        """
        if path is None:
            directory = os.environ.get("LOGOPEN_DATA_DIR")
            if not directory:
                directory = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "LogOpen"
            path = Path(directory) / "history.sqlite3"
        self.path = Path(path)

    @contextmanager
    def _connection(self):
        """Open/initialize SQLite, commit successful work, and always release the handle."""
        connection = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS scans (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    incident_count INTEGER NOT NULL,
                    complete INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS scans_created ON scans(created_at DESC);
                CREATE INDEX IF NOT EXISTS scans_scope ON scans(scope_json, created_at DESC);
                CREATE TABLE IF NOT EXISTS review_states (
                    issue_key TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError) as exc:
            if connection is not None:
                connection.rollback()
            raise HistoryStoreError(f"Could not access local scan history: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    def save_scan(self, result, options=None):
        """Save a full snapshot and attach scan_id, created_at, and scan_options.

        Existing IDs are updated, making a retry idempotent. The return value is
        the string scan ID. Cancelled/partial scans are retained but cannot serve
        as completed comparison baselines.
        """
        scan_id = getattr(result, "scan_id", None) or str(uuid4())
        created_at = getattr(result, "created_at", None) or datetime.now(timezone.utc)
        if not isinstance(created_at, datetime):
            created_at = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        scope_options = _options(options if options is not None else getattr(result, "scan_options", {}))
        result.scan_id = scan_id
        result.created_at = created_at
        result.scan_options = scope_options
        payload = result.as_report()
        payload.update(scan_id=scan_id, created_at=created_at, scan_options=scope_options, cancelled=getattr(result, "cancelled", False), comparison=getattr(result, "comparison", {}))
        with self._connection() as connection:
            connection.execute("""
                INSERT INTO scans(id, created_at, scope_json, options_json, report_json, incident_count, complete)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET created_at=excluded.created_at,
                    scope_json=excluded.scope_json, options_json=excluded.options_json,
                    report_json=excluded.report_json, incident_count=excluded.incident_count,
                    complete=excluded.complete
            """, (scan_id, created_at.astimezone(timezone.utc).isoformat(), _canonical(_scope(scope_options)), _canonical(scope_options), _canonical(payload), len(result), int(bool(result.complete) and not getattr(result, "cancelled", False))))
        return scan_id

    def update_scan(self, scan_id, result):
        """Replace an existing snapshot after manual analysis, preserving its identity/scope."""
        previous = self.load_scan(scan_id)
        result.scan_id = previous.scan_id
        result.created_at = previous.created_at
        result.scan_options = previous.scan_options
        return self.save_scan(result, previous.scan_options)

    def list_scans(self, limit=100):
        """Return newest-first lightweight scan metadata without deserializing evidence."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("History limit must be a positive integer.")
        with self._connection() as connection:
            rows = connection.execute("SELECT id, created_at, options_json, incident_count, complete FROM scans ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)).fetchall()
        records = []
        for row in rows:
            options = json.loads(row["options_json"])
            records.append({"id": row["id"], "created_at": datetime.fromisoformat(row["created_at"]), "incident_count": row["incident_count"], "complete": bool(row["complete"]), "log_types": options.get("log_types", []), "hours": options.get("hours"), "scan_options": options})
        return records

    def load_scan(self, scan_id):
        """Restore one saved ScanResult and overlay the current issue review states."""
        with self._connection() as connection:
            row = connection.execute("SELECT report_json FROM scans WHERE id = ?", (str(scan_id),)).fetchone()
        if row is None:
            raise HistoryStoreError("The selected scan no longer exists in local history.")
        result = parse_report(json.loads(row["report_json"]))
        result.history_saved = True
        self.apply_states(result)
        return result

    def apply_states(self, incidents):
        """Attach active/acknowledged/ignored state to each supplied incident in place."""
        with self._connection() as connection:
            states = dict(connection.execute("SELECT issue_key, state FROM review_states").fetchall())
        for incident in incidents:
            incident["review_state"] = states.get(incident_key(incident), "active")
        return incidents

    def set_state(self, incident, state):
        """Persist review state for this issue family and update the passed dictionary."""
        if state not in {"active", "acknowledged", "ignored"}:
            raise ValueError("Review state must be active, acknowledged, or ignored.")
        with self._connection() as connection:
            connection.execute("INSERT INTO review_states(issue_key, state, updated_at) VALUES (?, ?, ?) ON CONFLICT(issue_key) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at", (incident_key(incident), state, datetime.now(timezone.utc).isoformat()))
        incident["review_state"] = state

    def compare_scan(self, result, options=None):
        """Compare issue families against the latest suitable saved scope.

        Complete current scans prefer the latest completed same-scope baseline.
        Other comparisons still label new/persistent families, but disappearance
        is reported only for two completed, uncancelled scans with known equal
        scope. "No longer observed" describes a scan window, not a proven fix.
        """
        scope_options = _options(options if options is not None else getattr(result, "scan_options", {}))
        scope = _scope(scope_options)
        complete = bool(result.complete) and not getattr(result, "cancelled", False)
        current_id = getattr(result, "scan_id", None) or ""
        with self._connection() as connection:
            row = None
            if complete:
                row = connection.execute("SELECT * FROM scans WHERE scope_json = ? AND complete = 1 AND id != ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (_canonical(scope), current_id)).fetchone()
            if row is None:
                row = connection.execute("SELECT * FROM scans WHERE scope_json = ? AND id != ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (_canonical(scope), current_id)).fetchone()
            if row is None:
                row = connection.execute("SELECT * FROM scans WHERE id != ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (current_id,)).fetchone()
        previous = parse_report(json.loads(row["report_json"])) if row is not None else []
        previous_options = json.loads(row["options_json"]) if row is not None else {}
        known_scope = bool(scope["log_types"]) and scope["hours"] is not None
        comparable = bool(row is not None and complete and row["complete"] and known_scope and _scope(previous_options) == scope)
        old_keys = {incident_key(item) for item in previous}
        current_keys = {incident_key(item) for item in result}
        new = [item for item in result if incident_key(item) not in old_keys]
        persistent = [item for item in result if incident_key(item) in old_keys]
        disappeared = [item for item in previous if incident_key(item) not in current_keys] if comparable else []
        reason = "Compared completed scans with the same logs, time window, and record limit." if comparable else "Disappearance is unavailable until two completed scans cover the same known scope."
        if row is None:
            reason = "This is the first saved scan; there is no previous baseline."
        comparison = {"new": new, "persistent": persistent, "no_longer_observed": disappeared, "new_count": len(new), "persistent_count": len(persistent), "no_longer_observed_count": len(disappeared), "comparable": comparable, "previous_scan_id": row["id"] if row is not None else None, "previous_created_at": row["created_at"] if row is not None else None, "previous_scope": previous_options, "reason": reason}
        result.comparison = comparison
        return comparison

    def enrich_incidents(self, incidents):
        """Add seven-day recurrence using distinct observed incident occurrences.

        Saved/current observations sharing source events are merged transitively,
        so rescanning overlapping windows never inflates recurrence. Counts group
        by computer and classification, include the current occurrence, and use
        inclusive windows ending at each incident start. Partial scans contribute
        evidence but do not establish absence. Caller must refresh incident scores
        afterward; no claim is made about periods that were never scanned.
        """
        if not incidents:
            return incidents
        current = list(incidents)
        earliest = min(_timestamp(item["time_generated_start"]) for item in current) - timedelta(days=7).total_seconds()
        latest = max(_timestamp(item["time_generated_start"]) for item in current)
        with self._connection() as connection:
            rows = connection.execute("SELECT report_json FROM scans").fetchall()
        observations = []
        for row in rows:
            for incident in json.loads(row["report_json"]).get("incidents", []):
                start = _timestamp(incident["time_generated_start"])
                end = _timestamp(incident.get("time_generated_end", incident["time_generated_start"]))
                if end >= earliest and start <= latest:
                    observations.append(incident)
        current_offset = len(observations)
        observations.extend(current)
        parents = list(range(len(observations)))

        def find(index):
            """Find a merged occurrence representative with path compression."""
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        evidence_owners = {}
        for index, incident in enumerate(observations):
            group = (str(incident.get("computer_name", "")).casefold(), incident.get("incident_classification", ""))
            for evidence in _evidence_keys(incident):
                key = (group, evidence)
                if key in evidence_owners:
                    parents[find(index)] = find(evidence_owners[key])
                else:
                    evidence_owners[key] = index
        occurrences = {}
        for index, incident in enumerate(observations):
            root = find(index)
            start = _timestamp(incident["time_generated_start"])
            group = (str(incident.get("computer_name", "")).casefold(), incident.get("incident_classification", ""))
            if root not in occurrences or start < occurrences[root][1]:
                occurrences[root] = (group, start)
        starts_by_group = {}
        for group, start in occurrences.values():
            starts_by_group.setdefault(group, []).append(start)
        for starts in starts_by_group.values():
            starts.sort()
        for index, incident in enumerate(current, current_offset):
            group, own_start = occurrences[find(index)]
            starts = starts_by_group[group]
            end = _timestamp(incident["time_generated_start"])
            stop = bisect_right(starts, end)
            for field, seconds in (("recurrence_count_24h", 86400), ("recurrence_count_7d", 604800)):
                count = stop - bisect_left(starts, end - seconds)
                # A truncated window can show the tail of a longer occurrence;
                # include that current occurrence once even if it began earlier.
                incident[field] = count + int(own_start < end - seconds)
            incident["is_recurring"] = incident["recurrence_count_24h"] >= 3 or incident["recurrence_count_7d"] >= 5
        return incidents
