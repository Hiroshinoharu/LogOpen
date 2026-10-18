"""Roomy table columns and screen-aware window sizes shared across the desktop."""

from html import escape
from PyQt6.QtCore import QObject, QEvent, Qt
from PyQt6.QtWidgets import QHeaderView


def expand_window(window, width=1500, height=900):
    """Use most of the available display while retaining taskbar space and resizing."""
    screen = window.screen()
    if screen is not None:
        area = screen.availableGeometry()
        width = min(width, int(area.width() * 0.94))
        height = min(height, int(area.height() * 0.90))
    window.resize(width, height)
    window.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)


class RoomyTable(QObject):
    """Keep readable column widths, distribute spare space, and allow horizontal scrolling."""

    def __init__(self, table, widths, flexible=0):
        """Replace squeezing/stretch modes with user-resizable columns and sensible minimums."""
        super().__init__(table)
        self.table = table
        self.widths = list(widths)
        self.flexible = flexible
        self._adjusting = False
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setMinimumSectionSize(80)
        table.setWordWrap(False)
        table.verticalHeader().hide()
        table.setHorizontalScrollMode(table.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.viewport().installEventFilter(self)
        header.sectionResized.connect(self._user_resize)
        self._apply()

    def fit_contents(self):
        """Fit up to 1,000 rows with padding and full-text tooltips, capped at 900 px per column."""
        metrics = self.table.fontMetrics()
        for column, baseline in enumerate(self.widths):
            heading = self.table.horizontalHeaderItem(column)
            width = metrics.horizontalAdvance(heading.text()) + 48 if heading else baseline
            for row in range(min(self.table.rowCount(), 1000)):
                item = self.table.item(row, column)
                if item is not None:
                    item.setToolTip("<qt>" + escape(item.text()) + "</qt>")
                    width = max(width, metrics.horizontalAdvance(item.text()) + 40)
            self.widths[column] = max(baseline, min(900, width))
        self._apply()

    def _apply(self):
        """Expand the primary text column into unused space without shrinking other columns."""
        self._adjusting = True
        try:
            spare = max(0, self.table.viewport().width() - sum(self.widths))
            for column, width in enumerate(self.widths):
                self.table.setColumnWidth(column, width + (spare if column == self.flexible else 0))
        finally:
            self._adjusting = False

    def _user_resize(self, column, _old, width):
        """Remember manual column adjustments instead of replacing them on window resize."""
        if not self._adjusting:
            self.widths[column] = width

    def eventFilter(self, watched, event):
        """Redistribute extra viewport width when the window grows or becomes visible."""
        if event.type() == QEvent.Type.Resize:
            self._apply()
        return super().eventFilter(watched, event)
