"""Shared test helpers."""

from datetime import datetime


def make_event(
    *,
    log_type="System",
    computer_name="Test-PC",
    provider="DCOM",
    event_id=10016,
    time_generated=None,
    level="Warning",
    message="Test message",
):
    """Build a synthetic event dictionary."""

    if time_generated is None:
        time_generated = datetime(2026, 8, 19, 12, 0, 0)

    return {
        "log_type": log_type,
        "computer_name": computer_name,
        "provider": provider,
        "event_id": event_id,
        "time_generated": time_generated,
        "level": level,
        "message": message,
    }
