"""Release-facing privacy, data deletion and review boundaries; no paid requests."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from PyQt6.QtWidgets import QDialog, QMessageBox

import main
from feedback_report import problem_report
from history_store import HistoryStore
from llm_analysis import prepare_ai_request, analyse_incident_with_llm_result
from models.scan_result import ScanResult
from settings.app_settings import AISettings, AppSettings, _create_qsettings
from settings.privacy import PrivacyOptions, PrivacyPreferences
from settings.model_compatibility import compatibility_label
from tests.test_settings_dialog import app
from tests.test_llm_analysis import install_openai_mock
from tests.test_model_cost_prompts import output
from ui.ai_preview import AIPrivacyPreview
from ui.privacy_dialog import PrivacyDialog
from ui.feedback_dialog import FeedbackDialog
from ui.main_window import MainWindow
from scripts.release_readiness import REQUIRED_CHECKS, evidence_failures


def incident():
    return {"incident_priority": "High", "incident_score": 60,
            "incident_classification": "Example", "computer_name": "PRIVATE-PC",
            "summary_text": "Alice encountered an error on PRIVATE-PC.",
            "local_note": "PRIVATE NOTE",
            "events": [{"user_name": "Alice", "computer_name": "PRIVATE-PC",
                        "message": "Alice opened C:\\Users\\Alice\\private.txt\nRemove secret-marker too"}]}


def test_privacy_is_masked_and_automatic_sharing_is_off_by_default():
    assert PrivacyPreferences().load() == PrivacyOptions(True, False)
    storage = _create_qsettings()
    storage.setValue("privacy/mask_identifiers", "broken")
    storage.setValue("privacy/automatic_sharing", "broken")
    storage.sync()
    assert PrivacyPreferences().load() == PrivacyOptions(True, False)


def test_automatic_requests_require_explicit_consent_and_are_masked(monkeypatch):
    AppSettings().save(AISettings(True, "gpt-5.6-luna", 1))
    data = ScanResult([incident()])
    client, _ = install_openai_mock(monkeypatch, output_parsed=output())
    main.add_llm_analyses(data)
    client.responses.parse.assert_not_called()
    assert "Review" in data[0]["llm_analysis_message"]
    PrivacyPreferences().save(PrivacyOptions(True, True))
    main.add_llm_analyses(data)
    request = client.responses.parse.call_args.kwargs
    assert "Alice" not in request["input"] and "PRIVATE-PC" not in request["input"]
    assert "PRIVATE NOTE" not in request["input"]
    assert request["store"] is False


def test_preview_matches_sent_request_and_never_mutates_evidence(app, monkeypatch):
    data = incident()
    original = deepcopy(data)
    preferences = AISettings(True, "gpt-5.6-luna", 1)
    widget = AIPrivacyPreview(data, preferences)
    try:
        widget.extra.setText("secret-marker")
        reviewed = deepcopy(widget.request)
        assert widget.preview.toPlainText() == reviewed["input"]
        assert widget.instructions.toPlainText() == reviewed["instructions"]
        assert "secret-marker" not in reviewed["input"]
        assert "Alice" not in reviewed["input"]
        assert data == original
        data["summary_text"] = "NEW PRIVATE DETAIL"
        client, _ = install_openai_mock(monkeypatch, output_parsed=output())
        outcome = analyse_incident_with_llm_result(data, settings=preferences, prepared_request=reviewed)
        assert outcome.status == "completed"
        sent = client.responses.parse.call_args.kwargs
        assert sent["input"] == reviewed["input"]
        assert sent["instructions"] == reviewed["instructions"]
        assert "NEW PRIVATE DETAIL" not in sent["input"]
    finally:
        widget.close()


def test_cancelling_manual_preview_never_starts_worker(app, monkeypatch):
    AppSettings().save(AISettings(True, "gpt-5.6-luna", 1))
    widget = MainWindow()
    try:
        widget.incident_detail.set_incident(incident())
        request = Mock()
        monkeypatch.setattr("ui.workspace.analyse_incident_with_llm_result", request)
        monkeypatch.setattr(AIPrivacyPreview, "exec", lambda self: QDialog.DialogCode.Rejected)
        widget._analyze_selected()
        assert widget._analysis_thread is None
        request.assert_not_called()
    finally:
        widget.close()


def test_masking_can_be_explicitly_disabled_without_changing_default():
    request = prepare_ai_request(incident(), mask_identifiers=False)
    assert "Alice" in request["input"]
    assert PrivacyPreferences().load().mask_identifiers


def test_history_clear_is_atomic_and_does_not_remove_exports(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    data = incident()
    store.save_scan(ScanResult([data]))
    store.set_state(data, "ignored")
    export = tmp_path / "report.json"
    export.write_text("keep this export")
    with store._connection() as connection:
        connection.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON review_states BEGIN SELECT RAISE(ABORT, 'test'); END")
    with pytest.raises(RuntimeError):
        store.clear_all()
    assert len(store.list_scans()) == 1
    with store._connection() as connection:
        connection.execute("DROP TRIGGER fail_delete")
    store.clear_all()
    with store._connection() as connection:
        for table in ("scans", "review_states", "issue_preferences", "issue_activity", "troubleshooting"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert export.read_text() == "keep this export"


def test_delete_requires_idle_and_confirmation(app, monkeypatch, tmp_path):
    store = Mock(path=tmp_path / "history.sqlite3")
    busy = Mock(return_value=True)
    widget = PrivacyDialog(store, busy)
    try:
        widget.clear_history.setChecked(True)
        question = Mock(return_value=QMessageBox.StandardButton.Cancel)
        monkeypatch.setattr(QMessageBox, "question", question)
        widget.delete_selected()
        question.assert_not_called()
        store.clear_all.assert_not_called()
        busy.return_value = False
        widget.delete_selected()
        store.clear_all.assert_not_called()
        question.return_value = QMessageBox.StandardButton.Yes
        busy.side_effect = [False, True]
        widget.delete_selected()
        store.clear_all.assert_not_called()  # Re-check after the confirmation dialog.
    finally:
        widget.close()


def test_key_removal_disables_ai_and_automatic_sharing(app, monkeypatch, tmp_path):
    AppSettings().save(AISettings(True, "gpt-5.6-luna", 1))
    PrivacyPreferences().save(PrivacyOptions(True, True))
    remove = Mock()
    monkeypatch.setattr("ui.privacy_dialog.delete_openai_api_key", remove)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    widget = PrivacyDialog(HistoryStore(tmp_path / "history.sqlite3"), lambda: False)
    try:
        widget.clear_key.setChecked(True)
        widget.delete_selected()
        remove.assert_called_once_with()
        assert not AppSettings().load().enabled
        assert not PrivacyPreferences().load().automatic_sharing
    finally:
        widget.close()


def test_history_clear_resets_display_and_cannot_restore_deleted_notes(app):
    widget = MainWindow()
    try:
        data = ScanResult([incident()])
        data.collected_events = [{"message": "private old event"}]
        widget._show_scan_results(data)
        widget.monitor.last_result = data
        widget._latest_monitor_result = data
        widget._history_deleted()
        assert widget._current_result is None
        assert widget.monitor.last_result is None
        assert widget._latest_monitor_result is None
        assert widget.incident_table.rowCount() == 0
        assert widget.incident_detail.incident is None
        assert widget._undo_review == []
        assert not widget.collected_events_action.isEnabled()
    finally:
        widget.close()


def test_feedback_default_excludes_evidence_and_masks_secrets():
    text = problem_report("Alice hit an error", "Authorization: Bearer private-token\nsk-fake-private-key", [incident()])
    assert "private-token" not in text and "sk-fake" not in text
    assert "Alice" not in text and "secret-marker" not in text and "PRIVATE NOTE" not in text
    included = problem_report("", incidents=[incident()], include_incidents=True)
    assert "secret-marker" in included and "PRIVATE-PC" not in included


def test_feedback_saves_reviewed_edits_exactly(app, monkeypatch, tmp_path):
    widget = FeedbackDialog()
    destination = tmp_path / "report.txt"
    try:
        widget.preview.setPlainText("My reviewed report")
        monkeypatch.setattr("ui.feedback_dialog.QFileDialog.getSaveFileName", lambda *args: (str(destination), ""))
        widget.save()
        assert destination.read_text(encoding="utf-8") == "My reviewed report"
    finally:
        widget.close()


def test_compatibility_does_not_guess_snapshots_or_claim_live_tests():
    assert compatibility_label("gpt-5.6-luna") == "Documented support"
    assert compatibility_label("gpt-5.6-luna-2099-01-01") == "Compatibility unverified"
    assert compatibility_label("ft:gpt-4.1:org:custom:id") == "Compatibility unverified"


def test_release_evidence_requires_exact_artifact_and_true_results():
    evidence = {"installer_sha256": "abc", "windows_build": "Windows test build", "tester": "tester",
                "checks": {name: True for name in REQUIRED_CHECKS}}
    assert evidence_failures(evidence, "abc") == []
    assert evidence_failures(evidence, "other")
    evidence["checks"]["clean_install_without_python"] = "true"
    assert "Not passed: clean_install_without_python" in evidence_failures(evidence, "abc")
