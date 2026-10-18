"""Read Windows Event Log channels through the modern, read-only Evt API.

Native handles stay on the calling worker thread and are closed explicitly.
XML provides stable event identity and payload even without message resources.
"""

from datetime import datetime, timedelta, timezone
import logging
import math
from xml.etree import ElementTree as ET

from errors import EventCollectionError, describe_error

try:
    import win32evtlog as native
except ImportError:
    native = None

LOGGER = logging.getLogger(__name__)
NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
LEVELS = {0: "Information", 1: "Critical", 2: "Error", 3: "Warning",
          4: "Information", 5: "Verbose"}


def _require_native():
    """Fail clearly on unsupported systems before attempting any OS operation."""
    if native is None or not hasattr(native, "EvtQuery"):
        raise EventCollectionError("Modern Windows Event Log support requires Windows and pywin32.")


def close_handle(handle):
    """Close a PyEVT_HANDLE without hiding an earlier read or rendering error."""
    if handle is not None:
        try:
            handle.Close()
        except Exception:
            LOGGER.warning("Could not close a Windows event handle.")


def parse_event_xml(xml, channel):
    """Normalize rendered event XML into evidence without executing payloads.

    Returns UTC timestamps, native level/audit outcome, record ID, named data,
    correlation IDs, and process/thread IDs. Repeated data names remain a list.
    Invalid/missing required system fields raise ValueError or XML ParseError.
    """
    root = ET.fromstring(xml)
    system = root.find("e:System", NS)
    if system is None:
        raise ValueError("Event has no System metadata.")
    provider = system.find("e:Provider", NS)
    created = system.find("e:TimeCreated", NS)
    if provider is None or created is None:
        raise ValueError("Event has no provider or creation timestamp.")
    stamp = datetime.fromisoformat(created.attrib["SystemTime"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Event timestamp has no timezone.")
    keywords = int(system.findtext("e:Keywords", "0", NS), 0)
    level = LEVELS.get(int(system.findtext("e:Level", "0", NS)), "Unknown")
    if keywords & 0x0010000000000000:
        level = "Failure Audit"
    elif keywords & 0x0020000000000000:
        level = "Success Audit"
    data = [{"name": node.get("Name", str(index)), "value": "".join(node.itertext())}
            for index, node in enumerate(root.findall("e:EventData/e:Data", NS))]
    user_data = root.find("e:UserData", NS)
    if user_data is not None:
        data.extend({"name": node.tag.rsplit("}", 1)[-1], "value": node.text or ""}
                    for node in user_data.iter() if len(node) == 0)
    event = {"log_type": system.findtext("e:Channel", channel, NS),
             "computer_name": system.findtext("e:Computer", "", NS),
             "provider": provider.attrib["Name"],
             "event_id": int(system.findtext("e:EventID", "", NS)),
             "time_generated": stamp.astimezone(timezone.utc), "level": level,
             "record_number": int(system.findtext("e:EventRecordID", "0", NS)),
             "event_data": data, "keywords": keywords,
             "message": root.findtext("e:RenderingInfo/e:Message", "", NS)}
    for tag, attributes in (("Correlation", ("ActivityID", "RelatedActivityID")),
                            ("Execution", ("ProcessID", "ThreadID"))):
        element = system.find("e:" + tag, NS)
        if element is not None:
            for attribute in attributes:
                if attribute in element.attrib:
                    event[attribute] = element.attrib[attribute]
    return event


def _format_message(handle, event, publishers):
    """Reuse publisher metadata; retain structured payload when resources are missing."""
    if event["message"]:
        return
    provider = event["provider"]
    try:
        if provider not in publishers:
            publishers[provider] = None
            publishers[provider] = native.EvtOpenPublisherMetadata(provider)
        if publishers[provider] is not None:
            event["message"] = native.EvtFormatMessage(
                publishers[provider], handle, native.EvtFormatMessageEvent) or ""
    except Exception:
        pass
    if not event["message"]:
        payload = "\n".join(f"{item['name']}: {item['value']}" for item in event["event_data"])
        event["message"] = "Event message resources unavailable." + ("\n" + payload if payload else "")


def collect_events(channel, limit, *, hours=None, levels=None, cancel_event=None,
                   progress=None, event_matcher=None):
    """Collect one channel with bounded reads, cancellation, and honest coverage.

    Windows applies an absolute UTC time predicate before enumeration, so clock
    changes do not cause premature time-boundary stops. The cap counts all
    returned records, including those subsequently rejected by severity/rules.
    Errors before any record raise EventCollectionError; later errors preserve
    usable evidence and mark the result incomplete. No OS settings are changed.
    """
    from event_collection import CollectedEvents
    _require_native()
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError("log_type must be a non-empty string")
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    if hours is not None and (isinstance(hours, bool) or not isinstance(hours, (int, float))
                              or not math.isfinite(hours) or not 0 < hours <= 8760):
        raise ValueError("hours must be positive and at most one year")
    result = CollectedEvents()
    query_handle = None
    publishers = {}

    def cancelled():
        """Record incomplete cancellation once and leave the caller's token set."""
        if cancel_event is not None and cancel_event.is_set():
            result.stop_reason = "cancelled"
            result.warn(f"{channel}: collection cancelled; retained records may be incomplete.")
            return True
        return False

    if cancelled():
        return result
    if limit == 0:
        result.stop_reason = "record_limit"
        result.warn(f"{channel}: no records read because the record limit is zero.")
        return result
    query = "*"
    if hours is not None:
        # Windows' XPath timestamp parser requires the UTC Z suffix; +00:00
        # can silently match zero events instead of reporting an invalid query.
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="microseconds").replace("+00:00", "Z")
        query = f"*[System[TimeCreated[@SystemTime > '{cutoff}']]]"
    try:
        config_handle = None
        try:
            config_handle = native.EvtOpenChannelConfig(channel)
            enabled = native.EvtGetChannelConfigProperty(config_handle, native.EvtChannelConfigEnabled)[0]
            if not enabled:
                result.warn(f"{channel}: channel logging is disabled; only retained historical records can be read.")
        except Exception:
            result.warn(f"{channel}: logging status could not be checked; current coverage is uncertain.")
        finally:
            close_handle(config_handle)
        query_handle = native.EvtQuery(channel, native.EvtQueryChannelPath | native.EvtQueryReverseDirection, query)
        while not cancelled():
            batch = native.EvtNext(query_handle, min(64, limit - result.records_read), 1000)
            if not batch:
                result.stop_reason = "time_boundary" if hours is not None else "end_of_log"
                break
            try:
                for handle in batch:
                    if cancelled():
                        break
                    result.records_read += 1
                    try:
                        event = parse_event_xml(native.EvtRender(handle, native.EvtRenderEventXml), channel)
                        stamp = event["time_generated"]
                        result.oldest_time = min(result.oldest_time, stamp) if result.oldest_time else stamp
                        matches = event_matcher(event) if event_matcher is not None else False
                        if levels is None or event["level"] in levels or matches:
                            _format_message(handle, event, publishers)
                            result.append(event)
                    except (ValueError, TypeError, KeyError, ET.ParseError):
                        result.warn(f"{channel}: scan incomplete; an invalid event was skipped.")
            finally:
                for handle in batch:
                    close_handle(handle)
            if progress is not None:
                progress(result.records_read)
            if cancelled():
                break
            if result.records_read >= limit:
                result.stop_reason = "record_limit"
                result.warn(f"{channel}: scan incomplete; the {limit:,}-record limit was reached.")
                break
    except Exception as exc:
        if result.records_read:
            result.stop_reason = "read_error"
            result.warn(f"{channel}: scan incomplete; reading stopped: {describe_error(exc)}")
        else:
            raise EventCollectionError(f"Could not read {channel}: {describe_error(exc)}") from exc
    finally:
        close_handle(query_handle)
        for handle in publishers.values():
            close_handle(handle)
    return result
