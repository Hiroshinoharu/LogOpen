"""Discover local event channels and check access without enabling or editing logs."""

from detection_rules import CHANNEL_DESCRIPTIONS
import windows_events as events


def list_channels(cancel_event=None):
    """Return name, kind, enabled state, and description for registered channels.

    Access remains 'Not checked' until an explicit probe. Per-channel metadata
    failures remain visible instead of hiding channels. Cancellation stops
    between calls; all enumeration/configuration handles are closed.
    """
    events._require_native()
    native = events.native
    enumeration = native.EvtOpenChannelEnum()
    channels = []
    try:
        while cancel_event is None or not cancel_event.is_set():
            name = native.EvtNextChannelPath(enumeration)
            if name is None:
                break
            item = dict(name=name, kind="Unknown", enabled=None, access="Not checked",
                        description=CHANNEL_DESCRIPTIONS.get(name, "Windows event channel registered on this computer."))
            handle = None
            try:
                handle = native.EvtOpenChannelConfig(name)
                # pywin32 returns (value, EVT_VARIANT_TYPE), not the value alone.
                item["enabled"] = bool(native.EvtGetChannelConfigProperty(handle, native.EvtChannelConfigEnabled)[0])
                kind = native.EvtGetChannelConfigProperty(handle, native.EvtChannelConfigType)[0]
                item["kind"] = {0: "Administrative", 1: "Operational", 2: "Analytic", 3: "Debug"}.get(kind, "Unknown")
                if kind in (2, 3):
                    item["access"] = "Not supported (trace channel)"
            except Exception:
                item["access"] = "Metadata unavailable; check access"
            finally:
                events.close_handle(handle)
            channels.append(item)
    finally:
        events.close_handle(enumeration)
    return sorted(channels, key=lambda item: item["name"].casefold())


def check_channels(names, cancel_event=None):
    """Probe reverse-query/read access to selected channels, retaining no event data.

    Returns per-name status, including access denied and missing/unsupported
    channels. A disabled channel may still contain readable historical records.
    This never enables channels, changes audit policy, or elevates privileges.
    """
    events._require_native()
    native = events.native
    results = {}
    for name in names:
        if cancel_event is not None and cancel_event.is_set():
            break
        handle = None
        batch = ()
        try:
            handle = native.EvtQuery(name, native.EvtQueryChannelPath | native.EvtQueryReverseDirection, "*")
            batch = native.EvtNext(handle, 1, 1000)
            results[name] = "Readable" if batch else "Readable (empty)"
        except Exception as exc:
            code = getattr(exc, "winerror", None)
            results[name] = {5: "Access denied", 15007: "Channel not found", 15000: "Unsupported query"}.get(
                code, "Unavailable: " + events.describe_error(exc))
        finally:
            for event in batch:
                events.close_handle(event)
            events.close_handle(handle)
    return results
