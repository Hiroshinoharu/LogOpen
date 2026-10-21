"""Validated collection scope shared by the desktop, CLI, and saved reports."""

from dataclasses import dataclass, field
import math
import json

import config
from detection_rules import PROFILES, parse_custom_rules


@dataclass(frozen=True)
class ScanOptions:
    """An immutable snapshot of the log names, lookback hours, and per-log cap.

    Defaults are read from config when constructed. Log names are trimmed and
    retained in order; this does not check whether those logs exist on Windows.
    Invalid values raise ValueError before collection or file writes begin.
    """

    log_types: tuple[str, ...] = field(default_factory=lambda: tuple(config.LOG_TYPES)
                                    if isinstance(config.LOG_TYPES, (list, tuple)) else config.LOG_TYPES)
    hours: float = field(default_factory=lambda: config.SCAN_WINDOW_HOURS)
    max_records: int = field(default_factory=lambda: config.MAX_RECORDS_PER_LOG)
    profile: str = "general"
    rules_json: str = "[]"
    automatic_ai: bool = True
    collect_all_events: bool = False

    def __post_init__(self):
        """Validate fields and normalize log names without changing global config."""
        if not isinstance(self.log_types, (list, tuple)) or not self.log_types:
            raise ValueError("Select at least one Windows event log.")
        if any(not isinstance(name, str) or not name.strip() for name in self.log_types):
            raise ValueError("Each Windows log name must be a non-empty string.")
        names = tuple(dict.fromkeys(name.strip() for name in self.log_types))
        if isinstance(self.hours, bool) or not isinstance(self.hours, (int, float)):
            raise ValueError("The scan lookback must be a positive number of hours.")
        if not math.isfinite(self.hours) or not 0 < self.hours <= 24 * 365:
            raise ValueError("The scan lookback must be greater than zero and at most one year.")
        if isinstance(self.max_records, bool) or not isinstance(self.max_records, int) or self.max_records < 1:
            raise ValueError("The record limit must be a positive integer.")
        object.__setattr__(self, "log_types", names)
        if self.profile not in PROFILES:
            raise ValueError("Select a known scan profile.")
        if type(self.automatic_ai) is not bool:
            raise ValueError("automatic_ai must be true or false.")
        if type(self.collect_all_events) is not bool:
            raise ValueError("collect_all_events must be true or false.")
        rules = parse_custom_rules(self.rules_json)
        object.__setattr__(self, "rules_json", json.dumps(rules, sort_keys=True, separators=(",", ":")))

    def as_dict(self):
        """Return JSON-compatible scope metadata, copying the list of log names."""
        return {"log_types": list(self.log_types), "hours": self.hours,
                "max_records": self.max_records, "profile": self.profile,
                "rules_json": self.rules_json, "automatic_ai": self.automatic_ai,
                "collect_all_events": self.collect_all_events,
                "detection_version": 2}
