"""Typed sort keys for read-only tables, independent of displayed cell text."""

from datetime import datetime
from math import isfinite

from PyQt6.QtWidgets import QTableWidgetItem


class SortableTableItem(QTableWidgetItem):
    """Display literal text while comparing a separate, column-specific key."""

    def __init__(self, text, sort_key):
        """Store the comparison key without occupying source-index data roles."""
        super().__init__(text)
        self.sort_key = sort_key

    def __lt__(self, other):
        """Compare typed keys for ascending order; Qt reverses descending order."""
        if isinstance(other, SortableTableItem):
            return self.sort_key < other.sort_key
        return super().__lt__(other)


def numeric_key(value):
    """Sort finite numbers numerically, with missing/invalid values first ascending."""
    try:
        number = float(value) if not isinstance(value, bool) else float('nan')
        if isfinite(number):
            return (1, number)
    except (TypeError, ValueError, OverflowError):
        pass
    return (0, 0)


def timestamp_key(value):
    """Sort datetimes/ISO timestamps chronologically, with invalid values first.

    Naive timestamps use the local timezone, matching local Windows log times.
    Offset-aware values compare by their actual instant.
    """
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return numeric_key(parsed.timestamp())
    except (TypeError, ValueError, OverflowError, OSError):
        return (0, 0)
