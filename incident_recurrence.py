"""Annotate incident summaries with deterministic classification recurrence data."""

from datetime import timedelta
from bisect import bisect_left, bisect_right


RECURRENCE_24H_WINDOW = timedelta(hours=24)
RECURRENCE_7D_WINDOW = timedelta(days=7)
RECURRENCE_24H_THRESHOLD = 3
RECURRENCE_7D_THRESHOLD = 5


def apply_recurrence_metadata(incident_summaries):
    """Add classification recurrence counts and is_recurring to each summary.

    Args:
        incident_summaries: Dictionaries with incident_classification and
            comparable time_generated_start values.

    Returns:
        list: The same outer list, in its original order, with mutated entries.

    Counts cover inclusive 24-hour and seven-day windows ending at each
    incident's start, including itself and all equal-start incidents. Matching
    uses the classification label only, without a computer/provider partition.
    Three occurrences in 24 hours or five in seven days mark an incident as
    recurring. Sorting and binary searches avoid pairwise history scans.

    Only supplied summaries count; no saved history is loaded. A 24-hour scan
    cannot establish a full seven-day history. Call refresh_incident_summary
    afterward to include recurrence in scores; no groups are merged here.
    """

    summaries_by_classification = {}
    for summary in incident_summaries:
        summaries_by_classification.setdefault(
            summary["incident_classification"], []
        ).append(summary)

    for summaries in summaries_by_classification.values():
        summaries.sort(key=lambda summary: summary["time_generated_start"])
        starts = [summary["time_generated_start"] for summary in summaries]

        for summary in summaries:
            incident_start = summary["time_generated_start"]
            # Inclusive windows, including all incidents with the same start.
            # Binary searches keep larger scans from doing quadratic comparisons.
            end = bisect_right(starts, incident_start)
            count_24h = end - bisect_left(starts, incident_start - RECURRENCE_24H_WINDOW)
            count_7d = end - bisect_left(starts, incident_start - RECURRENCE_7D_WINDOW)

            summary["recurrence_count_24h"] = count_24h
            summary["recurrence_count_7d"] = count_7d
            summary["is_recurring"] = (
                count_24h >= RECURRENCE_24H_THRESHOLD
                or count_7d >= RECURRENCE_7D_THRESHOLD
            )

    return incident_summaries
