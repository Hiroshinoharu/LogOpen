"""Scan results with coverage and reporting status, including empty scans."""


class ScanResult(list):
    """Incident list with collection and reporting metadata, even when empty.

    Attributes:
        coverage: Mapping from source log name to collection statistics.
        warnings: Human-readable collection, processing, or export issues.
        complete: Whether collection/incident processing covered the scan scope.
        export_error: Report write failure message, or None if export succeeded
            or has not yet been attempted.

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

    def as_report(self):
        """Return the incidents/complete/coverage/warnings envelope used for export.

        The incidents and warnings lists are copied; nested data and coverage are
        shared. Datetimes still need JSON conversion. export_error is caller-only
        metadata; the workflow also includes its message in warnings when needed.
        """
        return {
            "incidents": list(self),
            "complete": self.complete,
            "coverage": self.coverage,
            "warnings": list(self.warnings),
        }
