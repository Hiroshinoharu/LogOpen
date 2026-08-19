"""Unit tests for the main LogOpen workflow."""

import importlib
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from tests.helpers import make_event


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
    def test_main_collects_and_bundles_each_log_type_separately(self):
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

        def fake_get_recent_events(log_type, limit):
            get_recent_calls.append((log_type, limit))
            if log_type == "System":
                return [system_event]
            if log_type == "Application":
                return [application_event]
            return []

        def fake_bundle_incidents(events):
            bundle_inputs.append([event["log_type"] for event in events])
            return [events]

        def fake_display_incident_reports(incidents):
            displayed_incidents.extend(incidents)

        def fake_export_incidents_to_json(incidents, file_path):
            exported_incidents.append((incidents, file_path))

        def fake_print(*args, **kwargs):
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
        ), patch("builtins.print", side_effect=fake_print):
            main_module.main()

        self.assertEqual(
            get_recent_calls,
            [("System", 500), ("Application", 500)],
        )
        self.assertEqual(bundle_inputs, [["System", "Application"]])
        self.assertEqual(displayed_incidents, [[system_event, application_event]])
        self.assertEqual(
            exported_incidents,
            [([[system_event, application_event]], "reports/incidents.json")],
        )
        self.assertTrue(
            any("the System and Application logs" in call for call in print_calls)
        )
