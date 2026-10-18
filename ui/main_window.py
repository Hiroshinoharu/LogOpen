"""Desktop dashboard, incident navigation, and background scan coordination.

ScanWorker performs the blocking workflow on a QThread. MainWindow slots
update widgets on the GUI thread and retain ScanResult coverage metadata.
Closing is deferred until active scan/settings workers finish safely.
"""

import sys
from threading import Event
from datetime import datetime
from html import escape
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

import config
from errors import describe_error
from main import scan_system
from ui.incident_detail import IncidentDetailWidget
from ui.settings_dialog import SettingsDialog
from ui.table_sorting import SortableTableItem, numeric_key
from ui.workspace import WorkspaceMixin
from ui.table_layout import RoomyTable, expand_window


def _label(text, name):
    """Create a named QLabel that treats its text literally, not as HTML."""
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _metric_card(title, caption, tone="default"):
    """Return a styled dashboard card and its value label for subsequent updates."""
    card = QFrame()
    card.setObjectName("metricCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 12, 18, 12)
    layout.setSpacing(4)
    layout.addWidget(_label(title, "metricTitle"))
    value = _label("—", "metricValue")
    value.setProperty("tone", tone)
    layout.addWidget(value)
    caption_label = _label(caption, "mutedLabel")
    caption_label.setWordWrap(True)
    layout.addWidget(caption_label)
    return card, value


class ScanWorker(QObject):
    """Run a blocking scan off the GUI thread without accessing widgets.

    success(object) preserves the ScanResult subclass and its metadata; error(str)
    carries a display message. finished is emitted after either outcome so the
    owning thread can quit and its objects can be deleted.
    """

    # A QVariantList would discard the ScanResult's coverage/warning metadata.
    success = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()
    progress = pyqtSignal(str, int)

    def __init__(self, options=None, cancel_event=None, history_store=None):
        """Retain scope and cooperative cancellation controls for the scan thread."""
        super().__init__()
        self.options = options
        self.cancel_event = cancel_event
        self.history_store = history_store

    @pyqtSlot()
    def run(self):
        """Execute scan_system and emit success/error, followed by finished in finally.

        Connected to QThread.started; all widget updates belong to receiver slots.
        """
        try:
            incidents = scan_system(self.options, progress=self.progress.emit,
                                    cancel_event=self.cancel_event, history_store=self.history_store)
        except KeyboardInterrupt:
            self.error.emit("Scan interrupted.")
        except Exception as exc:
            self.error.emit(describe_error(exc))
        else:
            self.success.emit(incidents)
        finally:
            self.finished.emit()


class MainWindow(WorkspaceMixin, QMainWindow):
    """Own the dashboard, incident-detail page, settings dialog, and scan worker.

    Retains incident dictionaries for navigation without rescanning. Worker and
    thread references prevent premature destruction; _close_pending coordinates
    a deferred close while background work completes.
    """
    @pyqtSlot()
    def run_scan(self):
        """Clear old results and launch one ScanWorker on a fresh QThread.

        GUI-thread slot. Ignores overlapping requests, disables scan/settings
        controls, connects result/error/cleanup signals, then starts the worker.
        """
        if self._scan_thread is not None or self._analysis_thread is not None or self.monitor.thread is not None:
            return

        self.scan_button.setEnabled(False)
        self.settings_button.setEnabled(False)
        self.scan_button.setText("Scanning system…")
        self.status_label.setText("Scanning system...")
        self.warning_label.clear()
        self.warning_label.hide()
        self._clear_incidents()
        self._current_result = None
        self._cancel_event = Event()
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.comparison_button.setEnabled(False)
        self.comparison_button.setText("Scanning for changes…")
        self.export_visible_action.setEnabled(False)
        self._workspace_busy(True)
        self._set_state("scanning", "SCANNING")
        self.result_count.setText("IN PROGRESS")
        for value in (self.incident_total, self.attention_total, self.event_total):
            value.setText("—")
        self._show_empty_state(
            "···", "Making sense of your system events",
            "Collecting recent events and analyzing incidents.\n"
            "You can keep using this window while the scan runs.",
        )

        # Keep both objects alive until QThread reports that it has stopped.
        self._scan_thread = QThread(self)
        self._scan_worker = ScanWorker(self.scan_options, self._cancel_event, self.history_store)
        self._scan_worker.moveToThread(self._scan_thread)

        self._scan_thread.started.connect(self._scan_worker.run)
        self._scan_worker.success.connect(self._show_scan_results)
        self._scan_worker.error.connect(self._show_scan_error)
        self._scan_worker.progress.connect(self._show_progress)
        self._scan_worker.finished.connect(self._scan_thread.quit)
        self._scan_thread.finished.connect(self._scan_worker.deleteLater)
        self._scan_thread.finished.connect(self._scan_thread.deleteLater)
        self._scan_thread.finished.connect(self._scan_finished)
        self._scan_thread.start()

    @pyqtSlot(object)
    def _show_scan_results(self, incidents):
        """Render results and coverage warnings on the GUI thread after worker success.

        incidents may be a ScanResult or a legacy plain list. Updates counts, table,
        status, and empty-state text while distinguishing an empty incomplete scan
        from a complete scan with no incidents. Does not rerun analysis.
        """
        complete = getattr(incidents, "complete", True)
        warnings = getattr(incidents, "warnings", [])
        self.incident_total.setText(str(len(incidents)))
        self.attention_total.setText(str(sum(
            incident.get("incident_priority") in ("High", "Critical")
            for incident in incidents
        )))
        self.event_total.setText(str(sum(
            incident.get("event_count", 0) for incident in incidents
        )))
        self.result_count.setText(f"{len(incidents)} FOUND")
        self.last_scan.setText(f"Last scan at {datetime.now():%H:%M}")
        if not complete:
            self._set_state("warning", "INCOMPLETE")
        elif warnings:
            self._set_state("warning", "COMPLETE WITH WARNINGS")
        else:
            self._set_state("complete", "COMPLETE")
        if incidents:
            self._populate_incident_table(incidents)
            self.incident_table.scrollToTop()
            self.results_stack.setCurrentWidget(self.incident_table)
        else:
            self._clear_incidents()
            self._show_empty_state(
                "✓" if complete else "!",
                "No incidents found" if complete else "No incidents found in collected events",
                "No recent events matched the selected severity and detection rules\n"
                "in this scan of your configured logs." if complete else
                "This scan is incomplete. Review the warnings above\n"
                "before drawing conclusions about your system.",
            )
        self.warning_label.setText("\n".join(warnings))
        self.warning_label.setVisible(bool(warnings))
        status = "incomplete" if not complete else "complete with warnings" if warnings else "complete"
        self.status_label.setText(
            f"Scan {status} - {len(incidents)} incidents found"
        )
        if getattr(incidents, "cancelled", False):
            self._set_state("warning", "CANCELLED")
            self.status_label.setText(f"Scan cancelled - {len(incidents)} partial incidents available")
        self._remember_result(incidents)
        self.motion.reveal()

    @pyqtSlot(str)
    def _show_scan_error(self, message):
        """Clear stale results and display a failed-scan state on the GUI thread."""
        self._clear_incidents()
        self.warning_label.clear()
        self.warning_label.hide()
        self.status_label.setText(f"Scan failed: {message}")
        self._set_state("error", "SCAN FAILED")
        self.result_count.setText("UNAVAILABLE")
        self._show_empty_state(
            "!", "This scan couldn't be completed",
            "Check the status message above, then try scanning again.",
        )

    @pyqtSlot()
    def _scan_finished(self):
        """Release stopped-thread/worker references and re-enable controls.

        Connected to thread completion, not merely worker result delivery. Performs
        a pending window close only after the running thread has stopped.
        """
        self._scan_worker = None
        self._scan_thread = None
        self.scan_button.setEnabled(True)
        self.cancel_button.hide()
        self.progress_bar.hide()
        self._workspace_busy(False)
        self.settings_button.setEnabled(True)
        self.scan_button.setText("Scan for Incidents")
        if self._close_pending:
            self.close()

    def closeEvent(self, event):
        """Defer closing while settings or scanning owns active asynchronous work.

        Requests the settings dialog to close or waits for the scan, ignoring this
        close event until cleanup can safely retry it. Never terminates a worker.
        """
        if self.monitor.active or self.monitor.thread is not None:
            self.monitor.stop()
            if self.monitor.thread is not None:
                self._close_pending = True
                event.ignore()
                return
        if self._settings_dialog is not None:
            self._close_pending = True
            self._settings_dialog.reject()
            event.ignore()
            return
        if self._scan_thread is not None:
            # Let the scan finish while the UI keeps processing Qt events.
            self._close_pending = True
            self.status_label.setText("Waiting for the scan to finish before closing...")
            event.ignore()
            return
        if self._analysis_thread is not None:
            self._close_pending = True
            self.incident_detail.action_feedback.setText("Waiting for the AI request to finish before closing…")
            event.ignore()
            return
        super().closeEvent(event)

    @pyqtSlot()
    def _open_settings(self):
        """Open a single modal SettingsDialog without blocking Qt's event loop.

        Ignores requests during a scan or while a settings dialog already exists.
        """
        if self._scan_thread is not None or self._analysis_thread is not None or self._settings_dialog is not None:
            return
        self._settings_dialog = SettingsDialog(self)
        self._settings_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._settings_dialog.finished.connect(self._settings_closed)
        self._settings_dialog.open()

    @pyqtSlot(int)
    def _settings_closed(self, _result):
        """Drop the dialog reference and schedule a pending parent-window close."""
        self._settings_dialog = None
        if self._close_pending:
            QTimer.singleShot(0, self.close)

    def _set_state(self, state, text):
        """Set the state badge text/property and repolish its stylesheet immediately."""
        self.state_badge.setText(text)
        self.state_badge.setProperty("state", state)
        self.state_badge.style().unpolish(self.state_badge)
        self.state_badge.style().polish(self.state_badge)

    def _show_empty_state(self, symbol, title, description):
        """Set the placeholder symbol, title, and description and show its page."""
        self.empty_symbol.setText(symbol)
        self.empty_title.setText(title)
        self.empty_description.setText(description)
        self.results_stack.setCurrentWidget(self.empty_state)

    def _populate_incident_table(self, incidents):
        """Populate read-only rows while retaining each source dictionary's identity.

        Sorting is disabled during insertion. The first cell stores the original
        incident index in UserRole, so selecting a sorted row opens the right data.
        Copies the outer incident list only; input dictionaries remain unchanged.
        Typed keys sort scores/counts numerically and priorities by severity rank.
        """
        self._clear_incidents()
        self._incidents = list(incidents)
        sorting_enabled = self.incident_table.isSortingEnabled()
        self.incident_table.setSortingEnabled(False)
        self.incident_table.setRowCount(len(incidents))
        for row, incident in enumerate(incidents):
            recurring = incident.get("is_recurring")
            values = (
                incident.get("incident_priority", "Unknown"),
                incident.get("incident_classification", "Unknown"),
                f"{incident.get('incident_score', '—')} / 100",
                "Yes" if recurring is True else "No" if recurring is False else "—",
                incident.get("event_count", "—"),
            )
            sort_keys = (
                {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}.get(values[0], 0),
                str(values[1]).casefold(),
                numeric_key(incident.get("incident_score")),
                2 if recurring is True else 1 if recurring is False else 0,
                numeric_key(incident.get("event_count")),
            )
            for column, value in enumerate(values):
                item = SortableTableItem(str(value), sort_keys[column])
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0:
                    # Store a source index so row sorting cannot change the mapping.
                    item.setData(Qt.ItemDataRole.UserRole, row)
                if column in (2, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                # Qt tooltips can interpret HTML; source text must stay literal.
                preview = str(value)
                if len(preview) > 160:
                    preview = preview[:157] + "…"
                item.setToolTip("<qt>" + escape(preview) + "</qt>")
                self.incident_table.setItem(row, column, item)
        self.incident_table.setSortingEnabled(sorting_enabled)
        self.roomy_incidents.fit_contents()

    def _clear_incidents(self):
        """Clear stored incidents, table, and detail page and return to the dashboard."""
        self._incidents = []
        if hasattr(self, "export_action"):
            self.export_action.setEnabled(False)
            for action in self.review_actions:
                action.setEnabled(False)
        self.incident_table.setRowCount(0)
        self.incident_detail.set_incident(None)
        self.detail_button.setEnabled(False)
        self.page_stack.setCurrentWidget(self.dashboard)

    def _selected_incident(self):
        """Return the selected row's source dictionary, or None for an invalid selection.

        Uses the stored UserRole index rather than the visible row number so table
        sorting does not change which incident opens.
        """
        selected_rows = self.incident_table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        row = self.incident_table.currentRow()
        if row not in [index.row() for index in selected_rows]:
            row = selected_rows[0].row()
        item = self.incident_table.item(row, 0)
        source_index = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(source_index, int) and 0 <= source_index < len(self._incidents):
            return self._incidents[source_index]
        return None

    @pyqtSlot()
    def _open_incident_detail(self):
        """Show the selected incident in the detail page and focus its Back button."""
        incident = self._selected_incident()
        if incident is None:
            return
        self.incident_detail.set_incident(incident)
        self.incident_detail.analyze_button.setEnabled(self._analysis_thread is None and self._scan_thread is None and self.monitor.thread is None)
        self.page_stack.setCurrentWidget(self.incident_detail)
        self.incident_detail.back_button.setFocus()

    @pyqtSlot()
    def _show_incident_list(self):
        """Return to the dashboard and table focus, preserving the current selection."""
        self.page_stack.setCurrentWidget(self.dashboard)
        if self._incidents:
            self.results_stack.setCurrentWidget(self.incident_table)
            self.incident_table.setFocus()
        else:
            self.results_stack.setCurrentWidget(self.empty_state)

    @pyqtSlot()
    def _copy_selected_rows(self):
        """Copy selected table rows as tab-separated text to the system clipboard."""
        rows = sorted(index.row() for index in self.incident_table.selectionModel().selectedRows())
        if not rows:
            return
        text = "\n".join(
            "\t".join(self.incident_table.item(row, column).text()
                      for column in range(self.incident_table.columnCount()))
            for row in rows
        )
        QApplication.clipboard().setText(text)

    @pyqtSlot()
    def _update_copy_action(self):
        """Enable copy/detail actions only when the table has a selection."""
        self.copy_action.setEnabled(bool(self.incident_table.selectedItems()))
        self.detail_button.setEnabled(self._selected_incident() is not None)
        if hasattr(self, "export_action"):
            selected = bool(self._selected_incidents())
            self.export_action.setEnabled(selected)
            for action in self.review_actions:
                action.setEnabled(selected)

    def __init__(self):
        """Build the dashboard/detail widgets, connect actions, and load the stylesheet.

        Initializes empty results and worker references; opening the window does not
        start a scan. The user starts work through the Scan button.
        """
        super().__init__()

        self._scan_thread = None
        self._scan_worker = None
        self._settings_dialog = None
        self._close_pending = False
        self._incidents = []
        self._cancel_event = Event()

        self.setWindowTitle("LogOpen")
        expand_window(self, 1500, 950)
        self.setMinimumSize(800, 620)
        self.setStyleSheet(Path(__file__).with_name("style.qss").read_text(encoding="utf-8"))

        self.central_widget = QWidget()
        self.central_widget.setObjectName("appSurface")
        self.setCentralWidget(self.central_widget)
        layout = QVBoxLayout(self.central_widget)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(18)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(12)
        mark = _label("L/", "brandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(42, 42)
        brand_row.addWidget(mark)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(3)
        self.title = _label("LogOpen", "brandTitle")
        brand_text.addWidget(self.title)
        self.subtitle = _label("Windows Incident Analysis", "subtitle")
        brand_text.addWidget(self.subtitle)
        brand_row.addLayout(brand_text)
        brand_row.addStretch()
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("secondaryButton")
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.setToolTip("Configure AI analysis and your OpenAI API key")
        self.settings_button.clicked.connect(self._open_settings)
        brand_row.addWidget(self.settings_button)
        self.state_badge = _label("READY TO SCAN", "stateBadge")
        self.state_badge.setProperty("state", "ready")
        brand_row.addWidget(self.state_badge)
        layout.addLayout(brand_row)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        self.page_stack = QStackedWidget()
        self.dashboard = QWidget()
        dashboard_layout = QVBoxLayout(self.dashboard)
        dashboard_layout.setContentsMargins(0, 0, 0, 0)
        dashboard_layout.setSpacing(12)
        self.page_stack.addWidget(self.dashboard)
        layout.addWidget(self.page_stack, 1)

        heading_row = QHBoxLayout()
        heading_row.setSpacing(24)
        heading_text = QVBoxLayout()
        heading_text.setSpacing(6)
        heading_text.addWidget(_label("Incident overview", "pageTitle"))
        self.status_label = _label("Ready to scan for incidents.", "statusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setAccessibleName("Current scan status")
        heading_text.addWidget(self.status_label)
        heading_row.addLayout(heading_text, 1)
        self.scan_button = QPushButton("Scan for Incidents")
        self.scan_button.setObjectName("scanButton")
        self.scan_button.setMinimumSize(196, 46)
        self.scan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_button.setToolTip("Scan recent Windows warnings and errors")
        self.scan_button.setAccessibleName("Scan for Incidents")
        self.scan_button.clicked.connect(self.run_scan)
        heading_row.addWidget(self.scan_button)
        dashboard_layout.addLayout(heading_row)

        self.warning_label = _label("", "scanWarning")
        self.warning_label.setWordWrap(True)
        self.warning_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.warning_label.setAccessibleName("Scan warnings")
        self.warning_label.hide()
        dashboard_layout.addWidget(self.warning_label)

        metrics = QHBoxLayout()
        metrics.setSpacing(14)
        card, self.incident_total = _metric_card("INCIDENTS FOUND", "In the latest scan")
        metrics.addWidget(card, 1)
        card, self.attention_total = _metric_card(
            "NEEDS ATTENTION", "High & critical priority", "warm"
        )
        metrics.addWidget(card, 1)
        card, self.event_total = _metric_card(
            "EVENTS GROUPED", "Warnings & errors in incidents", "teal"
        )
        metrics.addWidget(card, 1)
        self.metrics_panel = QWidget()
        self.metrics_panel.setLayout(metrics)
        metrics.setContentsMargins(0, 0, 0, 0)
        dashboard_layout.addWidget(self.metrics_panel)

        results_panel = QFrame()
        results_panel.setObjectName("resultsPanel")
        results_layout = QVBoxLayout(results_panel)
        results_layout.setContentsMargins(16, 16, 16, 14)
        results_layout.setSpacing(14)
        results_header = QHBoxLayout()
        results_heading = QVBoxLayout()
        results_heading.setSpacing(4)
        self.results_title = _label("Incidents", "sectionTitle")
        self.results_hint = _label(
            "Double-click a row or press Enter to view incident details.", "mutedLabel"
        )
        self.results_hint.setWordWrap(True)
        results_heading.addWidget(self.results_title)
        results_heading.addWidget(self.results_hint)
        results_header.addLayout(results_heading, 1)
        self.detail_button = QPushButton("View details")
        self.detail_button.setObjectName("secondaryButton")
        self.detail_button.setEnabled(False)
        self.detail_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.detail_button.clicked.connect(self._open_incident_detail)
        results_header.addWidget(self.detail_button)
        self.result_count = _label("AWAITING SCAN", "countBadge")
        results_header.addWidget(self.result_count)
        results_layout.addLayout(results_header)

        self.results_stack = QStackedWidget()
        self.results_stack.setMinimumHeight(180)
        self.incident_table = QTableWidget(0, 5)
        # Keep scan order until the user chooses a column; retain their choice.
        self.incident_table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.incident_table.setSortingEnabled(True)
        self.incident_table.setObjectName("incidentTable")
        self.incident_table.setHorizontalHeaderLabels(
            ["Priority", "Classification", "Score", "Recurring", "Events"]
        )
        for column in (2, 4):
            self.incident_table.horizontalHeaderItem(column).setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        self.incident_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.incident_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.incident_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.incident_table.setAlternatingRowColors(True)
        self.incident_table.setShowGrid(False)
        self.incident_table.setWordWrap(False)
        self.incident_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.incident_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.incident_table.setAccessibleName("Incident scan results")
        self.incident_table.setAccessibleDescription(
            "Read-only incident table. Use arrow keys to select rows and Ctrl+C to copy. "
            "Double-click a row or press Enter to open its incident details."
        )
        header = self.incident_table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(70)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 108), (2, 100), (3, 100), (4, 76)):
            self.incident_table.setColumnWidth(column, width)
        rows = self.incident_table.verticalHeader()
        rows.hide()
        rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        rows.setDefaultSectionSize(46)
        self.roomy_incidents = RoomyTable(self.incident_table, [130, 500, 130, 130, 110], flexible=1)
        self.copy_action = QAction("Copy selected rows", self.incident_table)
        self.copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        self.copy_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.copy_action.setEnabled(False)
        self.copy_action.triggered.connect(self._copy_selected_rows)
        self.incident_table.addAction(self.copy_action)
        self.incident_table.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self.incident_table.itemSelectionChanged.connect(self._update_copy_action)
        self.incident_table.itemActivated.connect(self._open_incident_detail)
        self.results_stack.addWidget(self.incident_table)

        self.incident_detail = IncidentDetailWidget()
        self.incident_detail.back_requested.connect(self._show_incident_list)
        self.page_stack.addWidget(self.incident_detail)

        self.empty_state = QWidget()
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(12, 10, 12, 10)
        empty_layout.setSpacing(12)
        empty_layout.addStretch()
        self.empty_symbol = _label("24h", "emptySymbol")
        self.empty_symbol.setFixedSize(48, 48)
        self.empty_symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_symbol, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_title = _label("", "emptyTitle")
        self.empty_description = _label("", "emptyDescription")
        for label in (self.empty_title, self.empty_description):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            empty_layout.addWidget(label)
        empty_layout.addStretch()
        self.results_stack.addWidget(self.empty_state)
        self._show_empty_state(
            "24h", "Start with a system scan",
            "Review recent warnings and errors from your Windows logs,\n"
            "grouped into incidents that are easier to understand.",
        )
        results_layout.addWidget(self.results_stack, 1)
        dashboard_layout.addWidget(results_panel, 1)

        footer = QHBoxLayout()
        footer.setSpacing(16)
        scope = _label(
            "Local history · Commands are suggestions only",
            "scopeLabel",
        )
        scope.setWordWrap(True)
        footer.addWidget(scope, 1)
        self.last_scan = _label("No scans yet", "mutedLabel")
        footer.addWidget(self.last_scan)
        dashboard_layout.addLayout(footer)
        self.setTabOrder(self.scan_button, self.incident_table)
        self.setTabOrder(self.incident_table, self.detail_button)
        self._setup_workspace(dashboard_layout, results_layout)

    def resizeEvent(self, event):
        """Prioritize readable result rows over summary cards in short windows."""
        super().resizeEvent(event)
        if hasattr(self, "metrics_panel"):
            self.metrics_panel.setVisible(self.height() >= 760)

def main():
    """Create the QApplication, show MainWindow, and enter the Qt event loop.

    Selects the Fusion widget style and exits with the event loop's result.
    Intended for python -m ui.main_window, not an existing Qt application.
    """
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
