"""Scan results with coverage and reporting status, including empty scans."""

from datetime import datetime, timezone
from uuid import uuid4


class ScanResult(list):
    """Incident list with collection and reporting metadata, even when empty.

    Attributes:
        coverage: Mapping from source log name to collection statistics.
        warnings: Human-readable collection, processing, or export issues.
        complete: Whether collection/incident processing covered the scan scope.
        export_error: Report write failure message, or None if export succeeded
            or has not yet been attempted.
        cancelled: Whether the user stopped work before the workflow finished.
        scan_id: Stable UUID used to associate a scan with its history entry.
        created_at: UTC timestamp at which collection was started.
        scan_options: JSON-compatible collection scope for history comparisons.
        comparison: New/persistent/no-longer-observed results against a prior scan.
        history_saved: Runtime-only flag identifying a locally persisted scan.

    AI and export failures do not themselves set complete to False. Callers must
    inspect metadata rather than infer scan success from the number of incidents.
    """

    def __init__(self, incidents=()):
        """Shallow-copy incidents into the list and initialize empty scan metadata.

        Individual incident dictionaries remain shared with the supplied iterable.
        """
        super().__init__(incidents)
        self.coverage = {}
        self.warnings = []
        self.complete = True
        self.export_error = None
        self.cancelled = False
        self.scan_id = str(uuid4())
        self.created_at = datetime.now(timezone.utc)
        self.scan_options = {}
        self.comparison = {}
        self.history_saved = False

    def as_report(self):
        """Return the incidents, coverage, identity, scope, and comparison envelope.

        The incidents and warnings lists are copied; nested data and coverage are
        shared. Datetimes still need JSON conversion. export_error is caller-only
        metadata; the workflow also includes its message in warnings when needed.
        """
        return {
            "incidents": list(self),
            "complete": self.complete,
            "coverage": self.coverage,
            "warnings": list(self.warnings),
            "cancelled": self.cancelled,
            "scan_id": self.scan_id,
            "created_at": self.created_at,
            "scan_options": dict(self.scan_options),
            "comparison": self.comparison,
        }
