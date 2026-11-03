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
    scope = {
        "log_types": sorted({str(name).casefold() for name in logs}),
        "hours": float(options["hours"]) if options.get("hours") is not None else None,
        "max_records": int(options.get("max_records", 100_000)),
        "rules_json": options.get("rules_json", "[]"),
        "detection_version": options.get("detection_version", 1),
    }
    # Keep focused-scan scope keys identical to existing databases. Broad event
    # collection has its own baseline and cannot hide a change in coverage.
    if options.get("collect_all_events") is True:
        scope["collect_all_events"] = True
    return scope


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
                CREATE TABLE IF NOT EXISTS issue_preferences (
                    issue_key TEXT PRIMARY KEY,
                    note TEXT NOT NULL DEFAULT '',
                    snoozed_until TEXT
                );
                CREATE TABLE IF NOT EXISTS issue_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS activity_issue ON issue_activity(issue_key, created_at);
                CREATE TABLE IF NOT EXISTS troubleshooting (
                    issue_key TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    PRIMARY KEY(issue_key, step_id)
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

    def clear_all(self):
        """Delete local history, notes, review states and checklists in one transaction.

        Caller must stop scans/monitoring and obtain explicit user confirmation.
        Does not delete exported files, settings, credentials or Windows event logs.
        """
        with self._connection() as connection:
            connection.execute("PRAGMA secure_delete = ON")
            for table in ("scans", "review_states", "issue_preferences", "issue_activity", "troubleshooting"):
                connection.execute(f"DELETE FROM {table}")

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
        """Overlay current review decisions, local notes, and unexpired temporary hiding."""
        with self._connection() as connection:
            states = dict(connection.execute("SELECT issue_key, state FROM review_states").fetchall())
            preferences = {row["issue_key"]: row for row in connection.execute("SELECT * FROM issue_preferences")}
        now = datetime.now(timezone.utc)
        for incident in incidents:
            key = incident_key(incident)
            preference = preferences.get(key)
            incident["review_state"] = states.get(key, "active")
            imported_note = incident.get("local_note", "")
            incident["local_note"] = preference["note"] if preference is not None else (
                imported_note if isinstance(imported_note, str) else "")
            until = preference["snoozed_until"] if preference is not None else None
            incident["snoozed_until"] = until
            if until and datetime.fromisoformat(until) > now:
                incident["review_state"] = "snoozed"
        return incidents

    def set_state(self, incident, state):
        """Persist review state for this issue family and update the passed dictionary."""
        if state not in {"active", "acknowledged", "ignored"}:
            raise ValueError("Review state must be active, acknowledged, or ignored.")
        with self._connection() as connection:
            connection.execute("INSERT INTO review_states(issue_key, state, updated_at) VALUES (?, ?, ?) ON CONFLICT(issue_key) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at", (incident_key(incident), state, datetime.now(timezone.utc).isoformat()))
            connection.execute("UPDATE issue_preferences SET snoozed_until=NULL WHERE issue_key=?", (incident_key(incident),))
            self._activity(connection, incident_key(incident), "Review decision", state)
        incident["review_state"] = state
        incident["snoozed_until"] = None

    def issue_preferences(self, incident):
        """Read a restorable issue-family snapshot for Undo without copying evidence."""
        key = incident_key(incident)
        with self._connection() as connection:
            state = connection.execute("SELECT state FROM review_states WHERE issue_key=?", (key,)).fetchone()
            preference = connection.execute("SELECT note, snoozed_until FROM issue_preferences WHERE issue_key=?", (key,)).fetchone()
        return {"issue_key": key, "state": state["state"] if state else "active",
                "note": preference["note"] if preference else "",
                "snoozed_until": preference["snoozed_until"] if preference else None}

    def restore_preferences(self, snapshots):
        """Restore review and snooze decisions together; leave independently edited notes intact."""
        with self._connection() as connection:
            for snapshot in snapshots:
                key = snapshot["issue_key"]
                state = snapshot["state"]
                if state not in {"active", "acknowledged", "ignored"}:
                    raise ValueError("Invalid saved review state.")
                connection.execute("INSERT INTO review_states VALUES (?,?,?) ON CONFLICT(issue_key) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                                   (key, state, datetime.now(timezone.utc).isoformat()))
                connection.execute("INSERT INTO issue_preferences(issue_key,snoozed_until) VALUES (?,?) ON CONFLICT(issue_key) DO UPDATE SET snoozed_until=excluded.snoozed_until",
                                   (key, snapshot.get("snoozed_until")))
                self._activity(connection, key, "Undo review change", state)

    def snooze(self, incident, hours=24):
        """Temporarily hide one family across scans without changing its underlying review state."""
        if type(hours) not in (int, float) or not 0 < hours <= 24 * 365:
            raise ValueError("Choose a temporary hiding period between zero and one year.")
        until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        with self._connection() as connection:
            connection.execute("INSERT INTO issue_preferences(issue_key,snoozed_until) VALUES (?,?) ON CONFLICT(issue_key) DO UPDATE SET snoozed_until=excluded.snoozed_until", (incident_key(incident), until))
            self._activity(connection, incident_key(incident), "Hidden temporarily", f"Until {until}")
        self.apply_states([incident])

    def save_note(self, incident, note):
        """Save local plain-text context for a family; never include it in an AI request."""
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError("Notes must contain at most 4,000 characters.")
        with self._connection() as connection:
            connection.execute("INSERT INTO issue_preferences(issue_key,note) VALUES (?,?) ON CONFLICT(issue_key) DO UPDATE SET note=excluded.note", (incident_key(incident), note))
            self._activity(connection, incident_key(incident), "Note saved", note or "Note cleared")
        incident["local_note"] = note

    @staticmethod
    def _activity(connection, key, kind, detail):
        connection.execute("INSERT INTO issue_activity(issue_key,created_at,kind,detail) VALUES (?,?,?,?)",
                           (key, datetime.now(timezone.utc).isoformat(), kind, detail))

    def record_activity(self, incident, kind, detail):
        """Record a local troubleshooting outcome without altering source evidence."""
        with self._connection() as connection:
            self._activity(connection, incident_key(incident), kind, detail)

    def checklist(self, incident):
        with self._connection() as connection:
            rows = connection.execute("SELECT content_json FROM troubleshooting WHERE issue_key=? ORDER BY rowid",
                                      (incident_key(incident),)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_checklist(self, incident, steps):
        """Save progress atomically; stable step IDs preserve work across later scans."""
        if not isinstance(steps, list) or len(steps) > 50:
            raise ValueError("A checklist can contain at most 50 checks.")
        seen = set()
        for step in steps:
            if (not isinstance(step, dict) or not isinstance(step.get("id"), str)
                    or not 1 <= len(step["id"]) <= 100 or step["id"] in seen
                    or not isinstance(step.get("text"), str) or not 1 <= len(step["text"]) <= 4000
                    or type(step.get("done")) is not bool or not isinstance(step.get("result"), str)
                    or len(step["result"]) > 4000):
                raise ValueError("Each check needs a unique ID, text, completion status and result (up to 4,000 characters).")
            seen.add(step["id"])
        key = incident_key(incident)
        with self._connection() as connection:
            old = {row[0]: json.loads(row[1]) for row in connection.execute(
                "SELECT step_id,content_json FROM troubleshooting WHERE issue_key=?", (key,))}
            for step in steps:
                if old.get(step["id"]) == step:
                    continue
                connection.execute("INSERT INTO troubleshooting VALUES (?,?,?) ON CONFLICT(issue_key,step_id) DO UPDATE SET content_json=excluded.content_json",
                                   (key, step["id"], _canonical(step)))
                status = "Completed" if step["done"] else "Not completed"
                self._activity(connection, key, "Troubleshooting check", f"{status}: {step['text']}\nResult: {step['result'] or 'Not recorded'}")

    def issue_timeline(self, incident, limit=500):
        """Combine local actions with observations in the most recent saved scans.

        Observations are explicitly scan sightings, not a count of distinct failures.
        Repeated evidence in overlapping scan windows remains identifiable by its time.
        """
        key = incident_key(incident)
        with self._connection() as connection:
            scans = connection.execute("SELECT id,created_at,report_json,complete FROM scans ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            actions = connection.execute("SELECT created_at,kind,detail FROM issue_activity WHERE issue_key=? ORDER BY created_at DESC LIMIT ?", (key, limit)).fetchall()
        entries = [dict(row) for row in actions]
        for scan in scans:
            report = json.loads(scan["report_json"])
            matches = [item for item in report.get("incidents", []) if incident_key(item) == key]
            if matches:
                times = [str(item.get("time_generated_start", "Unknown")) for item in matches]
                entries.append(dict(created_at=scan["created_at"], kind="Observed in scan",
                                    detail=f"{len(matches)} occurrence(s) in this snapshot; {'complete' if scan['complete'] else 'incomplete'} scan. Event start times: " + ", ".join(times),
                                    scan_id=scan["id"]))
        return sorted(entries, key=lambda entry: _timestamp(entry["created_at"]), reverse=True)

    def compare_scan(self, result, options=None, *, previous_scan_id=None):
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
            if previous_scan_id is not None:
                row = connection.execute("SELECT * FROM scans WHERE id=? AND id != ?", (previous_scan_id, current_id)).fetchone()
                if row is None:
                    raise HistoryStoreError("Choose a different saved scan as the baseline.")
            elif complete:
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
        # Compare issue families, not duplicate occurrences within a scan window.
        current_families = {incident_key(item): item for item in result}
        previous_families = {incident_key(item): item for item in previous}
        new = [item for key, item in current_families.items() if key not in old_keys]
        persistent = [item for key, item in current_families.items() if key in old_keys]
        disappeared = [item for key, item in previous_families.items() if key not in current_keys] if comparable else []
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
