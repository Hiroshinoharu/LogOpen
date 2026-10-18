"""Unit tests for the main LogOpen workflow."""

import importlib
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from models.incident_analysis import IncidentAnalysis
from llm_analysis import AIAnalysisResult
from errors import EventCollectionError, ReportExportError
from tests.helpers import make_event
from settings.app_settings import AISettings
import config


def _load_main_module():
    """Import ``main`` with stubbed win32 modules for test environments."""

    previous_main = sys.modules.pop("main", None)
    previous_event_collection = sys.modules.pop("event_collection", None)
    previous_win32evtlog = sys.modules.get("win32evtlog")
    previous_win32evtlogutil = sys.modules.get("win32evtlogutil")

    fake_win32evtlog = ModuleType("win32evtlog")
    fake_win32evtlog.EVENTLOG_AUDIT_FAILURE = 1
    fake_win32evtlog.EVENTLOG_AUDIT_SUCCESS = 2
    fake_win32evtlog.EVENTLOG_INFORMATION_TYPE = 4
    fake_win32evtlog.EVENTLOG_WARNING_TYPE = 8
    fake_win32evtlog.EVENTLOG_ERROR_TYPE = 16
    fake_win32evtlog.EVENTLOG_SEQUENTIAL_READ = 32
    fake_win32evtlog.EVENTLOG_BACKWARDS_READ = 64
    fake_win32evtlog.OpenEventLog = lambda *_args, **_kwargs: None
    fake_win32evtlog.ReadEventLog = lambda *_args, **_kwargs: []
    fake_win32evtlog.CloseEventLog = lambda *_args, **_kwargs: None

    fake_win32evtlogutil = ModuleType("win32evtlogutil")
    fake_win32evtlogutil.SafeFormatMessage = lambda *_args, **_kwargs: ""

    sys.modules["win32evtlog"] = fake_win32evtlog
    sys.modules["win32evtlogutil"] = fake_win32evtlogutil

    try:
        module = importlib.import_module("main")
        return importlib.reload(module)
    finally:
        sys.modules.pop("main", None)
        sys.modules.pop("event_collection", None)
        if previous_main is not None:
            sys.modules["main"] = previous_main
        if previous_event_collection is not None:
            sys.modules["event_collection"] = previous_event_collection
        if previous_win32evtlog is not None:
            sys.modules["win32evtlog"] = previous_win32evtlog
        else:
            sys.modules.pop("win32evtlog", None)
        if previous_win32evtlogutil is not None:
            sys.modules["win32evtlogutil"] = previous_win32evtlogutil
        else:
            sys.modules.pop("win32evtlogutil", None)


class MainWorkflowTests(unittest.TestCase):
    """Behavioral checks for main workflow, using controlled test data."""
    def setUp(self):
        """Patch saved AI preferences with a predictable enabled/model/limit snapshot."""
        settings_patch = patch("settings.app_settings.AppSettings.load", return_value=AISettings(
            True, config.DEFAULT_ANALYSIS_MODEL, 3,
        ))
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

    def test_scan_system_returns_incident_summaries(self):
        """Check scan system returns incident summaries."""
        main_module = _load_main_module()
        event = make_event()

        with patch.object(main_module.config, "LOG_TYPES", ["System"]), patch.object(
            main_module, "get_recent_events", return_value=[event]
        ), patch.object(
            main_module, "filter_events_by_time", side_effect=lambda events, _hours: events
        ), patch.object(
            main_module, "add_llm_analyses"
        ), patch.object(
            main_module, "export_incidents_to_json"
        ) as export_report, patch("builtins.print"):
            incidents = main_module.scan_system()

        self.assertIsInstance(incidents, list)
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["events"], [event])
        self.assertIn("incident_priority", incidents[0])
        self.assertIn("incident_classification", incidents[0])
        self.assertIn("incident_score", incidents[0])
        self.assertIs(export_report.call_args.args[0], incidents)

    def test_scan_system_returns_empty_list_when_no_events_are_found(self):
        """Check scan system returns empty list when no events are found."""
        main_module = _load_main_module()

        with patch.object(main_module.config, "LOG_TYPES", ["System"]), patch.object(
            main_module, "get_recent_events", return_value=[]
        ), patch.object(
            main_module, "add_llm_analyses"
        ) as analyse, patch.object(
            main_module, "export_incidents_to_json"
        ), patch("builtins.print"):
            incidents = main_module.scan_system()

        self.assertEqual(incidents, [])
        analyse.assert_not_called()

    def test_scan_system_raises_when_no_configured_log_can_be_read(self):
        """Check scan system raises when no configured log can be read."""
        main_module = _load_main_module()

        with patch.object(main_module.config, "LOG_TYPES", ["System"]), patch.object(
            main_module, "get_recent_events", side_effect=EventCollectionError("access denied")
        ), patch.object(main_module, "_write_status"):
            with self.assertRaisesRegex(RuntimeError, "none of the configured event logs"):
                main_module.scan_system()

    def test_scan_system_propagates_interrupts_and_output_failures(self):
        """Check scan system propagates interrupts and output failures."""
        main_module = _load_main_module()

        for error in (KeyboardInterrupt(), BrokenPipeError()):
            with self.subTest(error=type(error).__name__), patch.object(
                main_module, "_run_workflow", side_effect=error
            ):
                with self.assertRaises(type(error)):
                    main_module.scan_system()

    def test_main_returns_exit_codes(self):
        """Check main returns exit codes."""
        main_module = _load_main_module()

        for incidents in ([], [{"incident_priority": "Low"}]):
            with self.subTest(incidents=incidents), patch.object(
                main_module, "scan_system", return_value=incidents
            ):
                self.assertEqual(main_module.main(), 0)

        for error, expected_code in (
            (KeyboardInterrupt(), 130),
            (BrokenPipeError(), 0),
            (RuntimeError("scan failed"), 1),
            (EventCollectionError("read failed"), 1),
            (ReportExportError("write failed"), 1),
        ):
            with self.subTest(error=type(error).__name__), patch.object(
                main_module, "scan_system", side_effect=error
            ), patch.object(main_module, "_write_status"):
                self.assertEqual(main_module.main(), expected_code)

    def test_main_combines_logs_and_preserves_their_sources(self):
        """Check main combines logs and preserves their sources."""
        main_module = _load_main_module()
        system_event = make_event(
            log_type="System",
            provider="SharedProvider",
            event_id=1000,
        )
        application_event = make_event(
            log_type="Application",
            provider="SharedProvider",
            event_id=1000,
        )

        get_recent_calls = []
        bundle_inputs = []
        displayed_incidents = []
        exported_incidents = []
        print_calls = []
        llm_analysis = {
            "explanation": "A test analysis.",
            "likely_causes": ["A test cause."],
            "recommended_actions": ["A test action."],
            "remediation_notes": ["A test note."],
            "diagnostic_steps": [
                {"description": "Inspect the affected service.", "command": "Get-Service", "shell": "powershell", "risk_level": "safe"},
                {"description": "Review related events.", "command": None, "shell": None, "risk_level": "safe"},
            ],
        }

        def fake_get_recent_events(log_type, limit, **kwargs):
            """Record collection arguments and return the prepared events for each requested log."""
            get_recent_calls.append((log_type, limit))
            self.assertEqual(kwargs["hours"], config.SCAN_WINDOW_HOURS)
            self.assertEqual(kwargs["levels"], ("Critical", "Error", "Warning", "Failure Audit"))
            self.assertTrue(callable(kwargs["event_matcher"]))
            if log_type == "System":
                return [system_event]
            if log_type == "Application":
                return [application_event]
            return []

        def fake_bundle_incidents(events):
            """Capture the events passed to grouping and return one synthetic combined group."""
            bundle_inputs.append([event["log_type"] for event in events])
            return [events]

        def fake_display_incident_reports(incidents):
            """Capture summaries passed to terminal reporting without printing them."""
            displayed_incidents.extend(incidents)

        def fake_export_incidents_to_json(incidents, file_path):
            """Capture exported incidents and destination without writing a report."""
            exported_incidents.append((incidents, file_path))

        def fake_print(*args, **kwargs):
            """Capture rendered output for workflow assertions."""
            print_calls.append(" ".join(str(arg) for arg in args))

        with patch.object(main_module.config, "LOG_TYPES", ["System", "Application"]), patch.object(
            main_module,
            "get_recent_events",
            side_effect=fake_get_recent_events,
        ), patch.object(
            main_module,
            "filter_events_by_time",
            side_effect=lambda events, _hours: events,
        ), patch.object(
            main_module,
            "filter_events",
            side_effect=lambda events, _levels: events,
        ), patch.object(
            main_module,
            "bundle_incidents",
            side_effect=fake_bundle_incidents,
        ), patch.object(
            main_module,
            "display_incident_reports",
            side_effect=fake_display_incident_reports,
        ), patch.object(
            main_module,
            "export_incidents_to_json",
            side_effect=fake_export_incidents_to_json,
        ), patch.object(
            main_module,
            "analyse_incident_with_llm_result",
            return_value=AIAnalysisResult("completed", "AI analysis completed.", IncidentAnalysis(**llm_analysis)),
        ), patch.object(
            main_module,
            "select_incidents_for_llm",
            side_effect=lambda incidents, **_kwargs: incidents,
        ), patch("builtins.print", side_effect=fake_print):
            main_module.main()

        self.assertEqual(
            get_recent_calls,
            [("System", config.MAX_RECORDS_PER_LOG), ("Application", config.MAX_RECORDS_PER_LOG)],
        )
        self.assertEqual(bundle_inputs, [["System", "Application"]])
        self.assertEqual(len(displayed_incidents), 1)
        self.assertEqual(displayed_incidents[0]["event_count"], 2)
        self.assertEqual(displayed_incidents[0]["recurrence_count_24h"], 1)
        self.assertEqual(len(exported_incidents), 1)
        exported_payload, exported_path = exported_incidents[0]
        self.assertEqual(exported_path, "reports/incidents.json")
        self.assertEqual(len(exported_payload), 1)
        self.assertEqual(exported_payload[0]["event_count"], 2)
        self.assertEqual(exported_payload[0]["providers"], ["SharedProvider"])
        self.assertEqual(exported_payload[0]["log_types"], ["Application", "System"])
        self.assertEqual(exported_payload[0]["llm_analysis"], llm_analysis)
        self.assertTrue(
            any("the System and Application logs" in call for call in print_calls)
        )

    def test_main_continues_when_one_configured_log_cannot_be_read(self):
        """Check main continues when one configured log cannot be read."""
        main_module = _load_main_module()
        application_event = make_event(log_type="Application")

        def fake_get_recent_events(log_type, limit, **kwargs):
            """Raise a controlled error for one log and return valid events for the other."""
            self.assertEqual(limit, config.MAX_RECORDS_PER_LOG)
            if log_type == "System":
                raise EventCollectionError("access denied")
            return [application_event]

        with patch.object(
            main_module.config,
            "LOG_TYPES",
            ["System", "Application"],
        ), patch.object(
            main_module,
            "get_recent_events",
            side_effect=fake_get_recent_events,
        ), patch.object(
            main_module,
            "filter_events_by_time",
            side_effect=lambda events, _hours: events,
        ), patch.object(
            main_module,
            "filter_events",
            side_effect=lambda events, _levels: events,
        ), patch.object(
            main_module,
            "display_incident_reports",
        ), patch.object(
            main_module,
            "analyse_incident_with_llm_result",
            return_value=None,
        ), patch.object(
            main_module,
            "export_incidents_to_json",
        ) as export_report, patch.object(main_module, "_write_status") as status:
            exit_code = main_module.main()

        self.assertEqual(exit_code, 1)
        exported_incidents = export_report.call_args.args[0]
        self.assertEqual(len(exported_incidents), 1)
        self.assertEqual(exported_incidents[0]["log_type"], "Application")
        self.assertTrue(
            any("System log" in call.args[0] for call in status.call_args_list)
        )

    def test_main_returns_failure_when_no_configured_log_can_be_read(self):
        """Check main returns failure when no configured log can be read."""
        main_module = _load_main_module()

        with patch.object(main_module.config, "LOG_TYPES", ["System"]), patch.object(
            main_module,
            "get_recent_events",
            side_effect=EventCollectionError("access denied"),
        ), patch.object(
            main_module,
            "export_incidents_to_json",
        ) as export_report, patch.object(main_module, "_write_status") as status:
            exit_code = main_module.main()

        self.assertEqual(exit_code, 1)
        export_report.assert_not_called()
        self.assertTrue(
            any(
                call.args[0].startswith("Error: LogOpen could not complete")
                for call in status.call_args_list
            )
        )

    def test_add_llm_analyses_records_empty_exception_messages(self):
        """Check add llm analyses records empty exception messages."""
        main_module = _load_main_module()
        incident = {"incident_priority": "High", "llm_analysis": None}

        with patch.object(
            main_module,
            "analyse_incident_with_llm_result",
            side_effect=RuntimeError(),
        ), patch.object(main_module, "_write_status"):
            main_module.add_llm_analyses([incident])

        self.assertIsNone(incident["llm_analysis"])
        self.assertEqual(incident["llm_analysis_error"], "AI analysis could not be completed. Check AI Settings.")

    def test_add_llm_analyses_handles_model_serialization_failure(self):
        """Check add llm analyses handles model serialization failure."""
        main_module = _load_main_module()
        incident = {"incident_priority": "High", "llm_analysis": None}

        class InvalidAnalysis:
            """Simulate an analysis object whose JSON serialization fails."""
            def model_dump(_analysis, *, mode):
                """Raise a controlled serialization error to test per-incident failure handling."""
                self.assertEqual(mode, "json")
                raise ValueError("invalid structured response")

        with patch.object(
            main_module,
            "analyse_incident_with_llm_result",
            return_value=AIAnalysisResult("completed", "AI analysis completed.", InvalidAnalysis()),
        ), patch.object(main_module, "_write_status"):
            main_module.add_llm_analyses([incident])

        self.assertIsNone(incident["llm_analysis"])
        self.assertEqual(
            incident["llm_analysis_error"],
            "AI analysis could not be completed. Check AI Settings.",
        )
