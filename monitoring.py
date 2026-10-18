"""Session-scoped notification deduplication for opt-in periodic local scans."""

from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json


def evidence_id(event):
    """Hash stable source identity, independent of rendering and incident regrouping."""
    fields = [str(event.get(key, "")) for key in
              ("computer_name", "log_type", "provider", "event_id", "record_number", "time_generated")]
    if "record_number" not in event:
        fields.append(str(event.get("message", "")))
    return hashlib.sha256(json.dumps(fields).encode("utf-8")).hexdigest()


class AlertTracker:
    """Identify new high-priority active evidence after a silent first-scan baseline.

    Notifications only concern events timestamped since monitoring started.
    Overlapping scan windows cannot alert again for retained evidence. At most
    100,000 event identities are retained per session; older entries expire.
    """

    def __init__(self, started_at=None):
        """Start an empty session; never import old alerts or write notification state."""
        self.started_at = started_at or datetime.now(timezone.utc)
        self.seen = OrderedDict()
        self.initialized = False

    def observe(self, result):
        """Return active High/Critical incidents containing unseen, new-timestamp evidence.

        Cancelled results cannot establish a baseline. Ignored/acknowledged
        evidence is remembered silently, so restoring it alone cannot notify.
        Partial scans may alert on retained evidence but never claim disappearance.
        """
        if getattr(result, "cancelled", False):
            return []
        alerts = []
        for incident in result:
            unseen = False
            for event in incident.get("events", []):
                key = evidence_id(event)
                stamp = event.get("time_generated")
                recent = hasattr(stamp, "timestamp") and stamp.timestamp() >= self.started_at.timestamp()
                unseen = unseen or (key not in self.seen and recent)
                self.seen[key] = None
                self.seen.move_to_end(key)
            if unseen and self.initialized and incident.get("incident_priority") in ("High", "Critical") and incident.get("review_state", "active") == "active":
                alerts.append(incident)
        while len(self.seen) > 100_000:
            self.seen.popitem(last=False)
        self.initialized = True
        return alerts
