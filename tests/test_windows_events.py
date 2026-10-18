"""Check modern XML collection, partial coverage, channel access, and handle lifetime."""

from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import windows_events as events
from channel_catalog import check_channels, list_channels
from detection_rules import RuleSet
from errors import EventCollectionError


def xml(*, level=2, keywords="0x0", event_id=20, channel="System", provider="Microsoft-Windows-WindowsUpdateClient"):
    """Create namespace-correct evidence with repeated payload names and correlation IDs."""
    return f'''<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System>
    <Provider Name="{provider}"/><EventID>{event_id}</EventID><Level>{level}</Level><Keywords>{keywords}</Keywords>
    <TimeCreated SystemTime="2026-10-18T12:30:00.1234567Z"/><EventRecordID>42</EventRecordID>
    <Channel>{channel}</Channel><Computer>Test-PC</Computer><Correlation ActivityID="activity"/>
    <Execution ProcessID="12" ThreadID="34"/></System><EventData>
    <Data Name="Name">A &amp; B</Data><Data Name="Name">second</Data></EventData></Event>'''


@pytest.fixture
def native(monkeypatch):
    """Provide isolated native handles whose closure and API parameters can be asserted."""
    backend = SimpleNamespace(
        EvtQueryChannelPath=1, EvtQueryReverseDirection=512, EvtRenderEventXml=1, EvtFormatMessageEvent=1,
        EvtChannelConfigEnabled=0, EvtChannelConfigType=2,
        EvtQuery=Mock(return_value=Mock()), EvtNext=Mock(return_value=()),
        EvtOpenChannelConfig=Mock(return_value=Mock()), EvtGetChannelConfigProperty=Mock(return_value=(True, 13)),
        EvtOpenPublisherMetadata=Mock(return_value=Mock()), EvtFormatMessage=Mock(return_value="Rendered message"),
        EvtRender=Mock(return_value=xml()), EvtOpenChannelEnum=Mock(return_value=Mock()),
        EvtNextChannelPath=Mock(side_effect=["System", None]))
    monkeypatch.setattr(events, "native", backend)
    return backend


def test_xml_retains_utc_payload_and_correlation():
    """Structured evidence survives rendering independently of installed message DLLs."""
    event = events.parse_event_xml(xml(), "Other")
    assert event["log_type"] == "System" and event["record_number"] == 42
    assert event["time_generated"] == datetime(2026, 10, 18, 12, 30, 0, 123456, tzinfo=timezone.utc)
    assert event["event_data"] == [{"name": "Name", "value": "A & B"}, {"name": "Name", "value": "second"}]
    assert event["ActivityID"] == "activity" and event["ProcessID"] == "12"


@pytest.mark.parametrize("level,keywords,expected", [(1, "0", "Critical"), (5, "0", "Verbose"),
                         (0, "0x8010000000000000", "Failure Audit"), (0, "0x8020000000000000", "Success Audit")])
def test_native_levels_and_audit_keywords(level, keywords, expected):
    """Audit outcomes come from keyword bits rather than being mistaken for Information."""
    assert events.parse_event_xml(xml(level=level, keywords=keywords), "System")["level"] == expected


def test_modern_query_filters_time_and_retains_rule_information(native):
    """Read an operational channel exactly; retain matching Information events and close every handle."""
    handle = Mock()
    native.EvtNext.side_effect = [(handle,), ()]
    native.EvtRender.return_value = xml(level=4)
    result = events.collect_events("System", 500, hours=24, levels=("Error",), event_matcher=RuleSet().matches)
    assert len(result) == 1 and result.complete and result[0]["level"] == "Information"
    assert native.EvtQuery.call_args.args[:2] == ("System", 513)
    assert "TimeCreated[@SystemTime > '" in native.EvtQuery.call_args.args[2]
    assert "Z']]]" in native.EvtQuery.call_args.args[2] and "+00:00" not in native.EvtQuery.call_args.args[2]
    handle.Close.assert_called_once()
    native.EvtQuery.return_value.Close.assert_called_once()
    native.EvtOpenPublisherMetadata.return_value.Close.assert_called_once()


def test_nonmatches_count_toward_cap_without_message_formatting(native):
    """A high-volume channel cannot conceal truncation behind zero matching events."""
    handles = [Mock(), Mock()]
    native.EvtNext.return_value = handles
    native.EvtRender.return_value = xml(level=4)
    result = events.collect_events("System", 2, levels=("Error",))
    assert not result and not result.complete and result.records_read == 2
    assert result.stop_reason == "record_limit"
    native.EvtFormatMessage.assert_not_called()
    assert all(handle.Close.call_count == 1 for handle in handles)


def test_render_failure_and_cancellation_close_unprocessed_batch(native):
    """Cancellation between records releases the entire native batch, including unread handles."""
    token = Event()
    handles = [Mock(), Mock()]
    native.EvtNext.return_value = handles

    def render(*_args):
        """Request cancellation after the first native record is rendered."""
        token.set()
        return xml()

    native.EvtRender.side_effect = render
    result = events.collect_events("System", 10, cancel_event=token)
    assert len(result) == 1 and result.stop_reason == "cancelled"
    assert all(handle.Close.call_count == 1 for handle in handles)


def test_missing_channel_never_falls_back(native):
    """Access errors must not silently return Application events for another requested channel."""
    native.EvtQuery.side_effect = PermissionError("denied")
    with pytest.raises(EventCollectionError, match="NoSuchChannel"):
        events.collect_events("NoSuchChannel", 10)
    assert native.EvtQuery.call_count == 1
    native.EvtNext.assert_not_called()


def test_late_read_failure_and_invalid_xml_preserve_evidence(native):
    """Bad individual records and later native errors make retained evidence explicitly partial."""
    handles = [Mock(), Mock()]
    native.EvtNext.side_effect = [handles, OSError("read failed")]
    native.EvtRender.side_effect = ["<bad", xml()]
    result = events.collect_events("System", 10)
    assert len(result) == 1 and not result.complete and result.stop_reason == "read_error"
    assert len(result.warnings) == 2


def test_missing_message_resources_keep_payload(native):
    """Missing publisher resources do not discard usable structured event data."""
    native.EvtNext.side_effect = [(Mock(),), ()]
    native.EvtOpenPublisherMetadata.side_effect = OSError("no resources")
    result = events.collect_events("System", 10)
    assert result.complete and "Name: A & B" in result[0]["message"]


def test_disabled_channel_is_not_complete_empty_coverage(native):
    """An empty disabled log cannot imply that logging covered the requested period."""
    native.EvtGetChannelConfigProperty.return_value = (False, 13)
    result = events.collect_events("System", 10)
    assert not result.complete and "disabled" in result.warnings[0]


def test_channel_inventory_and_access_close_handles(native):
    """Unpack variant metadata, leave access unknown until probed, and close all handles."""
    native.EvtGetChannelConfigProperty.side_effect = [(True, 13), (0, 8)]
    channels = list_channels()
    assert channels[0]["kind"] == "Administrative" and channels[0]["access"] == "Not checked"
    native.EvtOpenChannelEnum.return_value.Close.assert_called_once()
    native.EvtOpenChannelConfig.return_value.Close.assert_called_once()
    native.EvtNext.return_value = (Mock(),)
    assert check_channels(["System"]) == {"System": "Readable"}
    native.EvtNext.return_value[0].Close.assert_called_once()


def test_cancelled_before_open_never_queries(native):
    """An already-cancelled scan performs no native reads."""
    token = Event()
    token.set()
    result = events.collect_events("System", 10, cancel_event=token)
    assert result.stop_reason == "cancelled"
    native.EvtQuery.assert_not_called()
