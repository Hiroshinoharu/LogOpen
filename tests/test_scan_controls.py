"""Verify configurable collection, progress, cooperative cancellation, and history."""

from datetime import datetime
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import event_collection
import main
from incident_detection import bundle_incidents
from llm_analysis import AIAnalysisResult
from models.scan_options import ScanOptions
from settings.app_settings import AISettings, AppSettings
from tests.helpers import make_event


@pytest.fixture
def workflow(monkeypatch):
    """Use one fresh synthetic event and isolate persistence and optional AI."""
    event = make_event(time_generated=datetime.now())
    monkeypatch.setattr(main, "get_recent_events", Mock(return_value=[event]))
    monkeypatch.setattr(main, "display_incident_reports", Mock())
    monkeypatch.setattr(main, "export_incidents_to_json", Mock())
    monkeypatch.setattr(main, "add_llm_analyses", Mock())
    history = Mock()
    history.compare_scan.return_value = {"new_count": 1, "persistent_count": 0}
    return history


@pytest.mark.parametrize("changes", [
    {"log_types": ()}, {"log_types": "System"}, {"log_types": ("",)},
    {"hours": 0}, {"hours": True}, {"hours": float("nan")},
    {"hours": float("inf")}, {"hours": 24 * 366},
    {"max_records": 0}, {"max_records": True}, {"max_records": 1.2},
    {"collect_all_events": "true"}, {"collect_all_events": 1},
])
def test_invalid_scan_options_fail_before_collection(changes):
    """Reject ambiguous or unbounded scope values at the input boundary."""
    with pytest.raises(ValueError):
        ScanOptions(**changes)


def test_scope_is_normalized_without_mutating_input():
    """Trim/deduplicate log names and return fresh JSON-compatible metadata."""
    names = [" System ", "Application", "System"]
    options = ScanOptions(names, 2.5, 500)
    assert options.log_types == ("System", "Application")
    assert names[0] == " System "
    snapshot = options.as_dict()
    snapshot["log_types"].append("Security")
    assert len(options.log_types) == 2


def test_custom_scope_progress_and_history_workflow(workflow):
    """Apply the selected scope, enrich before AI, persist, and finish progress at 100."""
    options = ScanOptions(("Application",), 48, 2500)
    progress = []

    def enrich(incidents):
        """Stand in for history by adding recurrence that must influence scoring."""
        incidents[0]["recurrence_count_7d"] = 8
        incidents[0]["is_recurring"] = True

    workflow.enrich_incidents.side_effect = enrich
    result = main.scan_system(options, progress=lambda label, percent: progress.append((label, percent)),
                              history_store=workflow)
    call = main.get_recent_events.call_args
    assert call.args == ("Application",)
    assert call.kwargs["limit"] == 2500 and call.kwargs["hours"] == 48
    assert callable(call.kwargs["progress"])
    assert result.scan_options == options.as_dict()
    assert result[0]["incident_score_breakdown"]["recurrence"] == 20
    assert result.comparison["new_count"] == 1
    assert [item[1] for item in progress] == sorted(item[1] for item in progress)
    assert progress[0][1] == 0 and progress[-1][1] == 100
    workflow.save_scan.assert_called_once_with(result, options)
    main.export_incidents_to_json.assert_called_once_with(result, "reports/incidents.json")


@pytest.mark.parametrize("all_events", [False, True])
def test_all_level_collection_keeps_routine_activity_out_of_problems(workflow, all_events):
    """Retain all requested evidence without scoring routine events or sending them to AI."""
    events = [make_event(time_generated=datetime.now(), provider="RoutineProvider", event_id=98765, level=level)
              for level in ("Information", "Success Audit", "Verbose", "Unknown", "Warning")]

    def collect(_name, **kwargs):
        levels = kwargs["levels"]
        return [event for event in events if levels is None or event["level"] in levels]

    main.get_recent_events.side_effect = collect
    options = ScanOptions(("System",), max_records=100, automatic_ai=False, collect_all_events=all_events)
    result = main.scan_system(options, history_store=workflow)
    assert [event for incident in result for event in incident["events"]] == [events[-1]]
    assert result.collected_events == (events if all_events else None)
    assert main.get_recent_events.call_args.kwargs["limit"] == 100
    main.add_llm_analyses.assert_not_called()


def test_all_level_partial_coverage_keeps_events_and_warnings(workflow):
    """Broadening levels must not turn a capped collection into a complete scan."""
    events = event_collection.CollectedEvents()
    events.append(make_event(time_generated=datetime.now(), level="Information", event_id=98765))
    events.records_read = 100
    events.stop_reason = "record_limit"
    events.warn("The record cap was reached.")
    main.get_recent_events.return_value = events
    result = main.scan_system(ScanOptions(("System",), automatic_ai=False, collect_all_events=True), history_store=workflow)
    assert not result and result.collected_events == events
    assert not result.complete and result.coverage["System"]["stop_reason"] == "record_limit"
    assert "The record cap was reached." in result.warnings


def test_cancellation_before_collection_does_not_touch_storage(workflow):
    """A pre-set stop request returns empty incomplete metadata without side effects."""
    token = Event()
    token.set()
    result = main.scan_system(ScanOptions(("System",)), cancel_event=token, history_store=workflow)
    assert result.cancelled and not result.complete
    assert result.coverage["System"]["stop_reason"] == "cancelled"
    main.get_recent_events.assert_not_called()
    main.export_incidents_to_json.assert_not_called()
    workflow.save_scan.assert_not_called()


def test_cancellation_during_ai_preserves_deterministic_results(workflow):
    """Stopping AI retains built summaries and does not overwrite saved scans."""
    token = Event()
    main.add_llm_analyses.side_effect = lambda *_args, **_kwargs: token.set()
    result = main.scan_system(ScanOptions(("System",)), cancel_event=token, history_store=workflow)
    assert result.cancelled and not result.complete and len(result) == 1
    assert "incident_score" in result[0]
    main.export_incidents_to_json.assert_not_called()
    workflow.save_scan.assert_not_called()


def test_history_failure_keeps_results_and_latest_export(workflow):
    """Unavailable history is visible but does not discard the deterministic scan."""
    workflow.enrich_incidents.side_effect = OSError("sensitive storage detail")
    workflow.save_scan.side_effect = OSError("sensitive storage detail")
    result = main.scan_system(ScanOptions(("System",)), history_store=workflow)
    assert len(result) == 1 and result.complete
    assert len(result.warnings) == 2
    assert all("sensitive" not in warning for warning in result.warnings)
    main.export_incidents_to_json.assert_called_once()


def test_ai_stops_before_next_request(monkeypatch):
    """Cancellation after one response prevents spending on the next selected incident."""
    AppSettings().save(AISettings(True, "test-model", 3))
    token = Event()
    incidents = [{"incident_priority": "Critical", "incident_score": 90} for _ in range(3)]

    def request(*_args, **_kwargs):
        """Simulate cancellation while an AI request is already in flight."""
        token.set()
        return AIAnalysisResult("failed", "Synthetic failure")

    client = Mock(side_effect=request)
    monkeypatch.setattr(main, "analyse_incident_with_llm_result", client)
    main.add_llm_analyses(incidents, cancel_event=token)
    assert client.call_count == 1
    assert incidents[0]["llm_analysis_status"] == "failed"
    assert all(item["llm_analysis_status"] == "cancelled" for item in incidents[1:])


@pytest.mark.parametrize("review_state", ["active", "acknowledged"])
def test_ignored_incidents_do_not_use_automatic_ai_budget(monkeypatch, review_state):
    """An ignored high-score issue does not displace active or acknowledged work."""
    AppSettings().save(AISettings(True, "test-model", 1))
    ignored = {"incident_priority": "Critical", "incident_score": 99, "review_state": "ignored"}
    available = {"incident_priority": "High", "incident_score": 70, "review_state": review_state}
    request = Mock(return_value=AIAnalysisResult("failed", "Synthetic failure"))
    monkeypatch.setattr(main, "analyse_incident_with_llm_result", request)
    main.add_llm_analyses([ignored, available])
    assert request.call_count == 1 and request.call_args.args[0] is available
    assert ignored["llm_analysis_status"] == "not_selected"
    assert "ignored" in ignored["llm_analysis_message"]


def test_native_collection_stops_between_records_and_closes_handle(monkeypatch):
    """Return already parsed evidence and release the native handle on cancellation."""
    token = Event()
    raw = SimpleNamespace(ComputerName="Test-PC", SourceName="Example", EventID=1, RecordNumber=123,
                          TimeGenerated=datetime.now(), EventType=16)
    backend = SimpleNamespace(EVENTLOG_AUDIT_FAILURE=1, EVENTLOG_AUDIT_SUCCESS=2,
                              EVENTLOG_INFORMATION_TYPE=4, EVENTLOG_WARNING_TYPE=8,
                              EVENTLOG_ERROR_TYPE=16, EVENTLOG_SEQUENTIAL_READ=32,
                              EVENTLOG_BACKWARDS_READ=64, OpenEventLog=Mock(return_value="handle"),
                              ReadEventLog=Mock(return_value=[raw, raw]), CloseEventLog=Mock())

    def format_message(*_args):
        """Set the stop token while the first record is being formatted."""
        token.set()
        return "Synthetic event"

    monkeypatch.setattr(event_collection, "win32evtlog", backend)
    monkeypatch.setattr(event_collection, "_WIN32_IMPORT_ERROR", None)
    monkeypatch.setattr(event_collection, "win32evtlogutil", SimpleNamespace(SafeFormatMessage=format_message))
    result = event_collection.get_classic_events("System", 100, cancel_event=token)
    assert len(result) == 1 and result.records_read == 1
    assert result[0]["record_number"] == 123
    assert not result.complete and result.stop_reason == "cancelled"
    backend.CloseEventLog.assert_called_once_with("handle")


def test_grouping_honors_cancellation():
    """No incidents are built when grouping receives an already-stopped token."""
    token = Event()
    token.set()
    assert bundle_incidents([make_event()], cancel_event=token) == []
