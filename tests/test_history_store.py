"""History persistence, evidence deduplication, comparisons, and review-state regressions."""

from datetime import datetime, timedelta, timezone

import pytest

from history_store import HistoryStore, HistoryStoreError
from incident_detection import build_incident
from models.scan_result import ScanResult
from tests.helpers import make_event


SCOPE = {"log_types": ["System", "Application"], "hours": 24, "max_records": 100_000}
NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def incident_at(hours=0, *, provider="DCOM", computer="Test-PC", offsets=(0,)):
    """Create a deterministic occurrence whose events can overlap another scan."""
    return build_incident([make_event(provider=provider, computer_name=computer, time_generated=NOW + timedelta(hours=hours, seconds=offset)) for offset in offsets])


def scan(*incidents, complete=True, cancelled=False, options=None):
    """Wrap synthetic incidents in a scoped ScanResult without touching real data."""
    result = ScanResult(incidents)
    result.complete = complete
    result.cancelled = cancelled
    result.scan_options = dict(SCOPE if options is None else options)
    return result


def test_history_round_trip_restores_dates_coverage_and_ai(tmp_path):
    """Saving/loading preserves report metadata, source evidence, and optional AI data."""
    store = HistoryStore(tmp_path / "history.sqlite3")
    result = scan(incident_at())
    result[0]["llm_analysis"] = {"explanation": "Saved explanation", "diagnostic_steps": []}
    result.coverage = {"System": {"oldest_time": NOW, "records_read": 200}}
    result.warnings = ["Application log unavailable"]
    identity = store.save_scan(result)
    restored = store.load_scan(identity)
    assert restored.scan_id == identity
    assert restored.scan_options == SCOPE
    assert restored.created_at == result.created_at
    assert restored[0]["time_generated_start"] == NOW
    assert restored[0]["incident_duration"] == timedelta(0)
    assert restored[0]["events"][0]["time_generated"] == NOW
    assert restored.coverage == result.coverage
    assert restored.warnings == result.warnings
    assert restored[0]["llm_analysis"]["explanation"] == "Saved explanation"
    assert restored[0]["review_state"] == "active"
    assert store.list_scans()[0]["incident_count"] == 1
    assert store.list_scans()[0]["log_types"] == SCOPE["log_types"]


def test_routine_activity_survives_history_and_report_without_incidents(tmp_path):
    """An empty problem list can still contain useful all-level monitoring evidence."""
    from json_reporting import export_incidents_to_json
    from report_io import load_report
    store = HistoryStore(tmp_path / "history.sqlite3")
    result = scan(options={**SCOPE, "collect_all_events": True})
    result.collected_events = [make_event(time_generated=NOW, level="Information", event_id=98765)]
    scan_id = store.save_scan(result)
    restored = store.load_scan(scan_id)
    assert not restored and restored.collected_events == result.collected_events
    path = tmp_path / "activity.json"
    export_incidents_to_json(restored, path)
    assert load_report(path).collected_events == result.collected_events


def test_broad_collection_uses_separate_baseline_but_focused_scope_is_compatible(tmp_path):
    """Existing scans remain comparable; changing event coverage starts another baseline."""
    store = HistoryStore(tmp_path / "history.sqlite3")
    store.save_scan(scan(incident_at()))
    focused = scan(options={**SCOPE, "collect_all_events": False})
    assert store.compare_scan(focused)["comparable"]
    broad = scan(options={**SCOPE, "collect_all_events": True})
    assert not store.compare_scan(broad)["comparable"]
    store.save_scan(broad)
    assert store.compare_scan(scan(options=broad.scan_options))["comparable"]


def test_default_store_obeys_isolated_data_directory(tmp_path, monkeypatch):
    """The test/development override prevents writes into the actual user profile."""
    monkeypatch.setenv("LOGOPEN_DATA_DIR", str(tmp_path / "isolated"))
    store = HistoryStore()
    assert not store.path.exists()
    store.save_scan(scan())
    assert store.path == tmp_path / "isolated" / "history.sqlite3"
    assert store.path.exists()


def test_update_preserves_id_scope_and_timestamp(tmp_path):
    """Manual AI results update the existing scan rather than creating another scan."""
    store = HistoryStore(tmp_path / "history.sqlite3")
    original = scan(incident_at())
    identity = store.save_scan(original)
    edited = scan(incident_at())
    edited[0]["llm_analysis"] = {"explanation": "New analysis"}
    store.update_scan(identity, edited)
    assert len(store.list_scans()) == 1
    restored = store.load_scan(identity)
    assert restored.created_at == original.created_at
    assert restored.scan_options == original.scan_options
    assert restored[0]["llm_analysis"]["explanation"] == "New analysis"


def test_review_state_follows_issue_across_scans_but_not_computers(tmp_path):
    """Acknowledgements and ignored state persist across occurrences of one issue family."""
    store = HistoryStore(tmp_path / "history.sqlite3")
    first = incident_at(-24)
    store.set_state(first, "ignored")
    same_issue = incident_at()
    other_pc = incident_at(computer="Another-PC")
    other_provider = incident_at(provider="OtherProvider")
    store.apply_states([same_issue, other_pc, other_provider])
    assert same_issue["review_state"] == "ignored"
    assert other_pc["review_state"] == other_provider["review_state"] == "active"
    store.set_state(same_issue, "acknowledged")
    store.apply_states([first])
    assert first["review_state"] == "acknowledged"
    store.set_state(first, "active")
    store.apply_states([same_issue])
    assert same_issue["review_state"] == "active"
    with pytest.raises(ValueError):
        store.set_state(first, "fixed")


def test_recurrence_merges_overlapping_windows_and_does_not_count_scans(tmp_path):
    """Shared source events connect partial/full observations into one occurrence."""
    store = HistoryStore(tmp_path / "history.sqlite3")
    store.save_scan(scan(incident_at(-4, offsets=(0, 20)), incident_at(-2)))
    store.save_scan(scan(incident_at(-4, offsets=(20, 40)), incident_at(-2)))
    current = [incident_at(-4, offsets=(40,)), incident_at(-2), incident_at()]
    store.enrich_incidents(current)
    assert [item["recurrence_count_24h"] for item in current] == [1, 2, 3]
    assert [item["recurrence_count_7d"] for item in current] == [1, 2, 3]
    assert current[-1]["is_recurring"] is True
    assert len(current[0]["events"]) == 1


def test_recurrence_includes_seven_day_history_and_inclusive_boundaries(tmp_path):
    """Old observed occurrences extend recurrence beyond the current 24-hour scan."""
    store = HistoryStore(tmp_path / "history.sqlite3")
    store.save_scan(scan(*(incident_at(hours) for hours in (-169, -168, -72, -48, -24, -2))))
    current = [incident_at(), incident_at(computer="Other-PC")]
    store.enrich_incidents(current)
    assert current[0]["recurrence_count_24h"] == 3
    assert current[0]["recurrence_count_7d"] == 6
    assert current[0]["is_recurring"] is True
    assert current[1]["recurrence_count_7d"] == 1


def test_comparison_tracks_issue_families_and_disappearance(tmp_path):
    """Only completed same-scope scans label previously seen families no longer observed."""
    store = HistoryStore(tmp_path / "history.sqlite3")
    previous = scan(incident_at(-24), incident_at(-24, provider="OldProvider"))
    identity = store.save_scan(previous)
    result = scan(incident_at(), incident_at(provider="NewProvider"))
    comparison = store.compare_scan(result)
    assert comparison["comparable"] is True
    assert comparison["previous_scan_id"] == identity
    assert comparison["previous_scope"] == SCOPE
    assert comparison["new_count"] == comparison["persistent_count"] == comparison["no_longer_observed_count"] == 1
    assert comparison["new"][0]["providers"] == ["NewProvider"]
    assert comparison["no_longer_observed"][0]["providers"] == ["OldProvider"]
    store.save_scan(result)
    restored = store.load_scan(result.scan_id)
    assert restored.comparison["persistent"][0]["time_generated_start"] == NOW


@pytest.mark.parametrize("current_options,complete,cancelled", [
    ({**SCOPE, "hours": 48}, True, False),
    ({**SCOPE, "log_types": ["System"]}, True, False),
    ({**SCOPE, "max_records": 500}, True, False),
    (SCOPE, False, False),
    (SCOPE, True, True),
    ({}, True, False),
])
def test_incomparable_scans_never_claim_disappearance(tmp_path, current_options, complete, cancelled):
    """Scope changes, partial scans, cancellation, and unknown scopes do not imply absence."""
    store = HistoryStore(tmp_path / "history.sqlite3")
    store.save_scan(scan(incident_at(-24)))
    result = scan(complete=complete, cancelled=cancelled, options=current_options)
    comparison = store.compare_scan(result)
    assert comparison["comparable"] is False
    assert comparison["no_longer_observed"] == []


def test_completed_comparison_skips_partial_intervening_scan(tmp_path):
    """An incomplete scan cannot erase a valid completed baseline."""
    store = HistoryStore(tmp_path / "history.sqlite3")
    identity = store.save_scan(scan(incident_at(-48)))
    store.save_scan(scan(complete=False))
    comparison = store.compare_scan(scan(options={**SCOPE, "log_types": ["application", "SYSTEM"]}))
    assert comparison["comparable"] is True
    assert comparison["previous_scan_id"] == identity
    assert comparison["no_longer_observed_count"] == 1


def test_missing_scan_and_invalid_database_have_clear_failures(tmp_path):
    """Unavailable entries and database corruption are surfaced as history errors."""
    store = HistoryStore(tmp_path / "history.sqlite3")
    with pytest.raises(HistoryStoreError, match="no longer exists"):
        store.load_scan("missing")
    store.path.write_bytes(b"not a sqlite database")
    with pytest.raises(HistoryStoreError, match="local scan history"):
        store.list_scans()
