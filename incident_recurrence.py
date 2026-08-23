"""Annotate incident summaries with deterministic classification recurrence data."""

from datetime import timedelta


RECURRENCE_24H_WINDOW = timedelta(hours=24)
RECURRENCE_7D_WINDOW = timedelta(days=7)
RECURRENCE_24H_THRESHOLD = 3
RECURRENCE_7D_THRESHOLD = 5


def apply_recurrence_metadata(incident_summaries):
    """Add recurrence counts to summaries without merging any incidents."""

    summaries_by_classification = {}
    for summary in incident_summaries:
        summaries_by_classification.setdefault(
            summary["incident_classification"], []
        ).append(summary)

    for summaries in summaries_by_classification.values():
        summaries.sort(key=lambda summary: summary["time_generated_start"])

        for summary in summaries:
            incident_start = summary["time_generated_start"]
            count_24h = sum(
                incident_start - RECURRENCE_24H_WINDOW
                <= candidate["time_generated_start"]
                <= incident_start
                for candidate in summaries
            )
            count_7d = sum(
                incident_start - RECURRENCE_7D_WINDOW
                <= candidate["time_generated_start"]
                <= incident_start
                for candidate in summaries
            )

            summary["recurrence_count_24h"] = count_24h
            summary["recurrence_count_7d"] = count_7d
            summary["is_recurring"] = (
                count_24h >= RECURRENCE_24H_THRESHOLD
                or count_7d >= RECURRENCE_7D_THRESHOLD
            )

    return incident_summaries
