"""Unit tests for Windows event collection helpers."""

from datetime import datetime
import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest


def _load_event_collection(win32evtlog_module, win32evtlogutil_module):
    """Import ``event_collection`` with stubbed win32 modules."""

    previous_event_collection = sys.modules.pop("event_collection", None)
    previous_win32evtlog = sys.modules.get("win32evtlog")
    previous_win32evtlogutil = sys.modules.get("win32evtlogutil")
    sys.modules["win32evtlog"] = win32evtlog_module
    sys.modules["win32evtlogutil"] = win32evtlogutil_module

    try:
        module = importlib.import_module("event_collection")
        return importlib.reload(module)
    finally:
        sys.modules.pop("event_collection", None)
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


class EventCollectionTests(unittest.TestCase):
    def test_get_recent_events_reads_requested_log_type(self):
        open_calls = []
        close_calls = []
        read_batches = []

        fake_win32evtlog = ModuleType("win32evtlog")
        fake_win32evtlog.EVENTLOG_AUDIT_FAILURE = 1
        fake_win32evtlog.EVENTLOG_AUDIT_SUCCESS = 2
        fake_win32evtlog.EVENTLOG_INFORMATION_TYPE = 4
        fake_win32evtlog.EVENTLOG_WARNING_TYPE = 8
        fake_win32evtlog.EVENTLOG_ERROR_TYPE = 16
        fake_win32evtlog.EVENTLOG_SEQUENTIAL_READ = 32
        fake_win32evtlog.EVENTLOG_BACKWARDS_READ = 64

        raw_event = SimpleNamespace(
            ComputerName="Test-PC",
            SourceName="ExampleProvider",
            EventID=0x8000002A,
            TimeGenerated=datetime(2026, 8, 19, 11, 59, 0),
            EventType=fake_win32evtlog.EVENTLOG_ERROR_TYPE,
        )
        read_batches.extend([[raw_event], []])

        def open_event_log(server, log_type):
            open_calls.append((server, log_type))
            return "handle"

        def read_event_log(handle, flags, offset):
            self.assertEqual(handle, "handle")
            self.assertEqual(
                flags,
                fake_win32evtlog.EVENTLOG_SEQUENTIAL_READ
                | fake_win32evtlog.EVENTLOG_BACKWARDS_READ,
            )
            self.assertEqual(offset, 0)
            return read_batches.pop(0)

        def close_event_log(handle):
            close_calls.append(handle)

        fake_win32evtlog.OpenEventLog = open_event_log
        fake_win32evtlog.ReadEventLog = read_event_log
        fake_win32evtlog.CloseEventLog = close_event_log

        fake_win32evtlogutil = ModuleType("win32evtlogutil")
        fake_win32evtlogutil.SafeFormatMessage = (
            lambda event, log_type: f"{log_type}:{event.SourceName}"
        )

        event_collection = _load_event_collection(
            fake_win32evtlog,
            fake_win32evtlogutil,
        )

        events = event_collection.get_recent_events("Application", limit=5)

        self.assertEqual(open_calls, [("", "Application")])
        self.assertEqual(close_calls, ["handle"])
        self.assertEqual(
            events,
            [{
                "log_type": "Application",
                "computer_name": "Test-PC",
                "provider": "ExampleProvider",
                "event_id": 42,
                "time_generated": datetime(2026, 8, 19, 11, 59, 0),
                "level": "Error",
                "message": "Application:ExampleProvider",
            }],
        )
