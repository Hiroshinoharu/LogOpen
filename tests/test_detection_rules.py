"""Verify source-specific rules, custom validation, workflow inclusion, and comparison scope."""

import json
from datetime import datetime
from unittest.mock import Mock
import pytest
import main
from detection_rules import RuleSet, parse_custom_rules
from detection_rules import DEFENDER
from history_store import HistoryStore
from incident_detection import build_incident
from models.scan_options import ScanOptions
from models.scan_result import ScanResult
from tests.helpers import make_event


def custom(**overrides):
    """Return a small declarative exact-match rule suitable for tests and examples."""
    rule = dict(id="example", channel="Application", provider="Example", event_ids=[9], label="Example Alert", severity="Critical")
    rule.update(overrides)
    return json.dumps([rule])


@pytest.mark.parametrize("changes", [{"provider": "*"}, {"event_ids": [True]}, {"event_ids": [-1]},
                         {"severity": "high"}, {"enabled": "true"}, {"command": "anything"}, {"id": " "}])
def test_custom_rule_validation(changes):
    """Reject ambiguous/broad rules and executable or misspelled schema fields."""
    with pytest.raises(ValueError):
        parse_custom_rules(custom(**changes))


def test_rule_requires_provider_and_channel_and_preserves_level():
    """An ID alone never matches another source; assessment cannot rewrite native evidence."""
    rules = RuleSet(custom())
    event = make_event(provider="Example", event_id=9, log_type="Application", level="Information")
    assert rules.apply(event)
    assert event["level"] == "Information" and event["detection_severity"] == "Critical"
    assert not rules.matches({**event, "provider": "Unrelated"})
    assert not rules.matches({**event, "log_type": "System"})
    summary = build_incident([event])
    assert summary["highest_severity"] == "Critical" and summary["incident_score"] == 60
    assert summary["incident_classification"] == "Example Alert"


def test_audit_clear_is_retained_at_success_audit_level():
    """Audit-log clearance is noteworthy even though Windows labels it a successful audit."""
    event = make_event(provider="Microsoft-Windows-Eventlog", event_id=1102, log_type="Security", level="Success Audit")
    assert RuleSet().apply(event)
    assert event["detection_classification"] == "Security Audit Log Cleared"


def test_informational_followups_do_not_hide_stronger_detection():
    """Multiple protection actions cannot rename a threat-detection incident as an informational action."""
    events = [make_event(provider="Microsoft-Windows-Windows Defender", event_id=identifier,
                         log_type=DEFENDER, level="Information") for identifier in (1116, 1117, 1117)]
    rules = RuleSet()
    for event in events:
        rules.apply(event)
    summary = build_incident(events)
    assert summary["incident_classification"] == "Defender Threat Detected"
    assert summary["incident_priority"] == "High"


def test_workflow_keeps_rule_information_and_disables_ai(monkeypatch, tmp_path):
    """Carry informational rule evidence through grouping, scoring, persistence, and AI exclusion."""
    event = make_event(provider="Example", event_id=9, log_type="Application", level="Information", time_generated=datetime.now())
    monkeypatch.setattr(main, "get_recent_events", Mock(return_value=[event]))
    monkeypatch.setattr(main, "export_incidents_to_json", Mock())
    ai = Mock()
    monkeypatch.setattr(main, "add_llm_analyses", ai)
    options = ScanOptions(("Application",), rules_json=custom(), automatic_ai=False)
    history = HistoryStore(tmp_path / "history.sqlite3")
    result = main.scan_system(options, history_store=history)
    assert result[0]["incident_classification"] == "Example Alert"
    ai.assert_not_called()
    restored = history.load_scan(result.scan_id)
    assert restored[0]["events"][0]["level"] == "Information"
    assert restored.scan_options["rules_json"] == options.rules_json


def test_different_detection_rules_prevent_disappearance_claims(tmp_path):
    """History must not infer resolution when a user changes the detection rules."""
    history = HistoryStore(tmp_path / "history.sqlite3")
    old = ScanResult([build_incident([make_event()])])
    history.save_scan(old, ScanOptions())
    changed = history.compare_scan(ScanResult(), ScanOptions(rules_json=custom()))
    assert not changed["comparable"] and changed["no_longer_observed_count"] == 0
    assert history.compare_scan(ScanResult(), ScanOptions(automatic_ai=False))["comparable"]
