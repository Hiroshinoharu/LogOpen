"""Validated notification choices and local-clock quiet hours."""

from dataclasses import asdict, dataclass
from datetime import datetime
import json

PRIORITIES = ("Critical", "High", "Medium", "Low", "Unknown")


@dataclass(frozen=True)
class NotificationPreferences:
    priorities: tuple = PRIORITIES
    quiet: bool = False
    quiet_start: int = 22 * 60
    quiet_end: int = 8 * 60
    batch_seconds: int = 0

    def is_quiet(self, now=None):
        now = now or datetime.now().astimezone()
        minute = now.hour * 60 + now.minute
        if not self.quiet:
            return False
        if self.quiet_start == self.quiet_end:
            return True
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= minute < self.quiet_end
        return minute >= self.quiet_start or minute < self.quiet_end

    def allows(self, incident, now=None):
        priority = incident.get("incident_priority", "Unknown")
        if priority not in PRIORITIES:
            priority = "Unknown"
        return priority in self.priorities and not self.is_quiet(now)

    def to_json(self):
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, value):
        try:
            data = json.loads(value)
            if not isinstance(data, dict):
                raise ValueError()
            priorities = data.get("priorities", list(PRIORITIES))
            if not isinstance(priorities, list) or any(item not in PRIORITIES for item in priorities):
                raise ValueError()
            for field, maximum in (("quiet_start", 1439), ("quiet_end", 1439), ("batch_seconds", 600)):
                if field in data and (type(data[field]) is not int or not 0 <= data[field] <= maximum):
                    raise ValueError()
            if "quiet" in data and type(data["quiet"]) is not bool:
                raise ValueError()
            return cls(tuple(priorities), data.get("quiet", False), data.get("quiet_start", 1320),
                       data.get("quiet_end", 480), data.get("batch_seconds", 0))
        except (TypeError, ValueError):
            return cls()
