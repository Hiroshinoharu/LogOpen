"""Regression coverage for scan accuracy, partial results, and AI outcomes."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import config
import event_collection
import main
from errors import EventCollectionError, ReportExportError
from incident_detection import build_incident, bundle_incidents
from incident_recurrence import apply_recurrence_metadata
from json_reporting import export_incidents_to_json
from llm_analysis import build_incident_context
from models.scan_result import ScanResult
from settings.app_settings import AISettings, AppSettings
from tests.helpers import make_event


@pytest.fixture
def log_backend(monkeypatch):
    """Replace native event-log reads and message formatting with a configurable mock backend."""
    backend = SimpleNamespace(
        EVENTLOG_AUDIT_FAILURE=1, EVENTLOG_AUDIT_SUCCESS=2,
        EVENTLOG_INFORMATION_TYPE=4, EVENTLOG_WARNING_TYPE=8,
        EVENTLOG_ERROR_TYPE=16, EVENTLOG_SEQUENTIAL_READ=32,
        EVENTLOG_BACKWARDS_READ=64,
        OpenEventLog=Mock(return_value="handle"),
        ReadEventLog=Mock(), CloseEventLog=Mock(),
    )
    formatter = Mock(return_value="Synthetic event")
    monkeypatch.setattr(event_collection, "win32evtlog", backend)
    monkeypatch.setattr(event_collection, "win32evtlogutil", SimpleNamespace(SafeFormatMessage=formatter))
    monkeypatch.setattr(event_collection, "_WIN32_IMPORT_ERROR", None)
    backend.formatter = formatter
    return backend


def raw_event(*, hours=0, level=4):
    """Build a fake native record at the requested age and severity for collection tests."""
    return SimpleNamespace(
        ComputerName="Test-PC", SourceName="ExampleProvider", EventID=100,
        TimeGenerated=datetime.now() - timedelta(hours=hours), EventType=level,
    )


def test_collection_finds_error_after_500_informational_records(log_backend):
    """Check collection finds error after 500 informational records."""
    information = [raw_event(hours=1) for _ in range(500)]
    log_backend.ReadEventLog.side_effect = [
        information[:250], information[250:],
        [raw_event(hours=2, level=16), raw_event(hours=25, level=16)],
    ]
    events = event_collection.get_recent_events("System", 10_000, hours=24, levels=("Error", "Warning"))
    assert len(events) == 1 and events[0]["level"] == "Error"
    assert events.complete and events.stop_reason == "time_boundary"
    assert events.records_read == 502
    log_backend.formatter.assert_called_once()
    log_backend.CloseEventLog.assert_called_once_with("handle")


def test_collection_marks_empty_capped_results_incomplete(log_backend):
    """Check collection marks empty capped results incomplete."""
    log_backend.ReadEventLog.return_value = [raw_event(), raw_event(), raw_event(level=16)]
    events = event_collection.get_recent_events("System", 2, hours=24, levels=("Error",))
    assert events == []
    assert not events.complete and events.stop_reason == "record_limit"
    assert events.records_read == 2 and events.warnings
    log_backend.CloseEventLog.assert_called_once()


def test_collection_eof_is_complete_even_without_matches(log_backend):
    """Check collection eof is complete even without matches."""
    log_backend.ReadEventLog.side_effect = [[raw_event()], []]
    events = event_collection.get_recent_events("System", 10, hours=24, levels=("Error",))
    assert events == [] and events.complete
    assert events.stop_reason == "end_of_log" and not events.warnings


def test_read_failure_after_nonmatching_records_retains_partial_coverage(log_backend):
    """Check read failure after nonmatching records retains partial coverage."""
    log_backend.ReadEventLog.side_effect = [[raw_event()], OSError("read failed")]
    events = event_collection.get_recent_events("System", 10, hours=24, levels=("Error",))
    assert events == [] and not events.complete
    assert events.stop_reason == "read_error" and events.records_read == 1
    assert "read failed" in events.warnings[0]
    log_backend.CloseEventLog.assert_called_once()


def test_initial_read_failure_is_wrapped_and_handle_is_closed(log_backend):
    """Check initial read failure is wrapped and handle is closed."""
    log_backend.ReadEventLog.side_effect = PermissionError("denied")
    with pytest.raises(EventCollectionError, match="denied"):
        event_collection.get_recent_events("System", 10, hours=24)
    log_backend.CloseEventLog.assert_called_once_with("handle")


def test_invalid_records_mark_coverage_incomplete_without_losing_valid_events(log_backend):
    """Check invalid records mark coverage incomplete without losing valid events."""
    log_backend.ReadEventLog.side_effect = [[SimpleNamespace(), raw_event(level=16)], []]
    events = event_collection.get_recent_events("System", 10, hours=24, levels=("Error",))
    assert len(events) == 1 and not events.complete
    assert "invalid event" in events.warnings[0]


def event_at(seconds, *, provider="ProviderA", event_id=100, **kwargs):
    """Build a parsed event at a fixed reference time plus the requested seconds and overrides."""
    return make_event(provider=provider, event_id=event_id,
                      time_generated=datetime(2026, 10, 18, 12) + timedelta(seconds=seconds), **kwargs)


def test_numeric_id_alone_does_not_merge_different_providers():
    """Check numeric id alone does not merge different providers."""
    events = [event_at(0), event_at(10, provider="ProviderB")]
    assert bundle_incidents(events) == [[events[0]], [events[1]]]


def test_interleaved_related_sequences_remain_in_their_groups():
    """Check interleaved related sequences remain in their groups."""
    events = [event_at(0), event_at(10, provider="ProviderB", event_id=200),
              event_at(20), event_at(30, provider="ProviderB", event_id=200)]
    assert bundle_incidents(list(reversed(events))) == [events[::2], events[1::2]]


def test_active_group_expires_after_its_last_event():
    """Check active group expires after its last event."""
    events = [event_at(0), event_at(10, provider="ProviderB", event_id=200), event_at(61)]
    assert bundle_incidents(events) == [[event] for event in events]


def test_groups_never_join_different_computers():
    """Check groups never join different computers."""
    events = [event_at(0), event_at(10, computer_name="Other-PC")]
    assert bundle_incidents(events) == [[event] for event in events]


def test_recurrence_retains_inclusive_boundaries_and_duplicate_starts():
    """Check recurrence retains inclusive boundaries and duplicate starts."""
    now = datetime(2026, 10, 18, 12)
    starts = [now - timedelta(days=8), now - timedelta(days=7),
              now - timedelta(hours=24), now, now, now + timedelta(seconds=1)]
    summaries = [{"incident_classification": "Example", "time_generated_start": start} for start in starts]
    apply_recurrence_metadata(summaries)
    for summary in summaries:
        for window, key in ((timedelta(hours=24), "recurrence_count_24h"),
                            (timedelta(days=7), "recurrence_count_7d")):
            assert summary[key] == sum(summary["time_generated_start"] - window <= start
                                       <= summary["time_generated_start"] for start in starts)


def test_shared_component_preserves_cross_log_sources_through_context_and_export(tmp_path):
    """Check shared component preserves cross log sources through context and export."""
    events = [
        event_at(0, log_type="Application", provider="Application Error", event_id=1000,
                 message="Faulting application: MsMpEng.exe"),
        event_at(5, log_type="System", provider="Service Control Manager", event_id=7031,
                 message="The Microsoft Defender Antivirus Service terminated unexpectedly."),
    ]
    groups = bundle_incidents(events)
    assert len(groups) == 1
    summary = build_incident(groups[0])
    assert summary["log_types"] == ["Application", "System"]
    assert summary["log_type"] == "Application, System"
    assert "Application and System logs" in summary["summary_text"]
    context = build_incident_context(summary)
    first, second = context.split("Event 1:\n")[1].split("Event 2:\n")
    assert "Log Type: Application" in first and "Event ID: 1000" in first
    assert "Log Type: System" in second and "Event ID: 7031" in second
    path = tmp_path / "report.json"
    export_incidents_to_json(ScanResult([summary]), path)
    saved = json.loads(path.read_text())["incidents"][0]
    assert saved["log_types"] == ["Application", "System"]
    assert [event["log_type"] for event in saved["events"]] == ["Application", "System"]


@pytest.fixture
def local_scan(monkeypatch):
    """Stub collection, presentation, AI, and export so workflow failures can be tested
    without external effects.
    """
    monkeypatch.setattr(config, "LOG_TYPES", ["System"])
    monkeypatch.setattr(main, "get_recent_events", Mock(return_value=[make_event(time_generated=datetime.now())]))
    monkeypatch.setattr(main, "display_incident_reports", Mock())
    monkeypatch.setattr(main, "add_llm_analyses", Mock())
    monkeypatch.setattr(main, "export_incidents_to_json", Mock())


def test_export_failure_keeps_incidents_and_cli_reports_failure(local_scan, monkeypatch):
    """Check export failure keeps incidents and cli reports failure."""
    main.export_incidents_to_json.side_effect = ReportExportError("disk full")
    result = main.scan_system()
    assert len(result) == 1 and result.complete
    assert "disk full" in result.export_error
    assert result.export_error in result.warnings
    monkeypatch.setattr(main, "scan_system", lambda: result)
    assert main.main() == 1


def test_collection_failure_keeps_other_log_and_records_missing_coverage(local_scan, monkeypatch):
    """Check collection failure keeps other log and records missing coverage."""
    monkeypatch.setattr(config, "LOG_TYPES", ["System", "Application"])
    main.get_recent_events.side_effect = [EventCollectionError("denied"), [
        make_event(log_type="Application", time_generated=datetime.now()),
    ]]
    result = main.scan_system()
    assert len(result) == 1 and result[0]["log_type"] == "Application"
    assert main.get_recent_events.call_count == 2
    assert not result.complete and "denied" in result.warnings[0]
    assert not result.coverage["System"]["complete"]
    assert result.coverage["Application"]["complete"]


def test_empty_partial_scan_retains_coverage_in_json(local_scan, tmp_path):
    """Check empty partial scan retains coverage in json."""
    events = event_collection.CollectedEvents()
    events.records_read = 100
    events.stop_reason = "record_limit"
    events.warn("System: record limit reached; scan incomplete.")
    main.get_recent_events.return_value = events
    result = main.scan_system()
    assert result == [] and not result.complete
    assert result.coverage["System"]["records_read"] == 100
    path = tmp_path / "report.json"
    export_incidents_to_json(result, path)
    saved = json.loads(path.read_text())
    assert saved["incidents"] == [] and not saved["complete"]
    assert saved["coverage"]["System"]["stop_reason"] == "record_limit"
    assert saved["warnings"] == events.warnings


def test_disabled_and_unselected_ai_have_distinct_reasons(monkeypatch):
    """Check disabled and unselected ai have distinct reasons."""
    AppSettings().save(AISettings(False, "test-model", 1))
    incident = {"incident_priority": "Critical", "incident_score": 90}
    request = Mock()
    monkeypatch.setattr(main, "analyse_incident_with_llm_result", request)
    main.add_llm_analyses([incident])
    assert incident["llm_analysis_status"] == "disabled"
    request.assert_not_called()

    AppSettings().save(AISettings(True, "test-model", 1))
    lower = {"incident_priority": "Low", "event_count": 1}
    main.add_llm_analyses([lower])
    assert lower["llm_analysis_status"] == "not_selected"
    assert "criteria" in lower["llm_analysis_message"]
    request.assert_not_called()


def test_missing_credentials_is_explained_in_saved_incident(monkeypatch, tmp_path):
    """Check missing credentials is explained in saved incident."""
    from settings.ai_service import MissingCredentialsError
    import llm_analysis
    monkeypatch.setattr(llm_analysis, "create_openai_client", Mock(side_effect=MissingCredentialsError()))
    AppSettings().save(AISettings(True, "test-model", 1))
    incident = {"incident_priority": "Critical", "incident_score": 90}
    main.add_llm_analyses([incident])
    assert incident["llm_analysis_status"] == "failed" and incident["llm_analysis"] is None
    assert "credentials are not configured" in incident["llm_analysis_message"]
    path = tmp_path / "report.json"
    export_incidents_to_json(ScanResult([incident]), path)
    assert json.loads(path.read_text())["incidents"][0]["llm_analysis_status"] == "failed"
