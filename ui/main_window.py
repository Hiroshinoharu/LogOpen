"""Desktop dashboard, incident navigation, and background scan coordination.

ScanWorker performs the blocking workflow on a QThread. MainWindow slots
update widgets on the GUI thread and retain ScanResult coverage metadata.
Closing is deferred until active scan/settings workers finish safely.
"""

import sys
from admin_access import access_label, elevation_status, relaunch_as_administrator
from datetime import datetime
from html import escape
from pathlib import Path
from threading import Event

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

import config
from errors import describe_error
from main import scan_system
from ui.app_restart import relaunch_app
from ui.guidance import QuickStartDialog, ScanJourney
from ui.icons import decorate_buttons, icon
from ui.incident_detail import IncidentDetailWidget
from ui.settings_dialog import SettingsDialog
from ui.sounds import SoundEffects
from ui.table_layout import RoomyTable, expand_window
from ui.table_sorting import SortableTableItem, numeric_key
from ui.update_dialog import UpdateDialog
from update_service import launch_installer, UpdateError
from ui.workspace import WorkspaceMixin
from ui.productivity import ProductivityMixin
from ui.assistance import AssistanceMixin
from ui.insights import MetricCard
from ui.signal_visual import SignalVisual
from ui.monitoring_status import MonitoringStatus


def _label(text, name):
    """Create a named QLabel that treats its text literally, not as HTML."""
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _metric_card(title, caption, tone="default"):
    """Return a styled dashboard card and its value label for subsequent updates."""
    card = MetricCard()
    card.setObjectName("metricCard")
    card.setProperty("tone", tone)
    layout = QHBoxLayout(card)
    layout.setContentsMargins(18, 10, 18, 10)
    layout.setSpacing(16)
    value = _label("—", "metricValue")
    value.setProperty("tone", tone)
    layout.addWidget(value)
    text = QVBoxLayout()
    text.setSpacing(3)
    text.addWidget(_label(title, "metricTitle"))
    caption_label = _label(caption, "mutedLabel")
    caption_label.setWordWrap(True)
    text.addWidget(caption_label)
    layout.addLayout(text, 1)
    for label in card.findChildren(QLabel):
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
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


class MainWindow(AssistanceMixin, ProductivityMixin, WorkspaceMixin, QMainWindow):
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
        self.journey.set_step(0)
        self.results_hint.setText("Reading recent activity. Your results will appear here when the scan finishes.")
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
        self._update_metric_descriptions()
        self._show_empty_state(
            "···", "Making sense of your system events",
            "Collecting recent events and analyzing incidents.\n"
            "You can keep using this window while the scan runs.",
        )

        # Keep both objects alive until QThread reports that it has stopped.
        self._scan_thread = QThread(self)
        options = self._focused_scan_options or self.scan_options
        self._focused_scan_options = None
        self._scan_worker = ScanWorker(options, self._cancel_event, self.history_store)
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
        self.sounds.play("start")

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
        self._update_metric_descriptions()
        self.result_count.setText(f"{len(incidents)} FOUND")
        self.journey.set_step(1 if incidents else 0)
        self.results_hint.setText(
            "Start with High or Critical. Select a row to understand it."
            if incidents else "A scan groups related Windows warnings and errors into problems you can review."
        )
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
                "No matching problems were found in the Windows logs checked.\n"
                "If something still feels wrong, try a longer time range in Scan options." if complete else
                "This scan is incomplete. Review the warnings above\n"
                "before drawing conclusions about your system.",
            )
        self.warning_label.setText("\n".join(warnings) if len(warnings) <= 1 else
                                   f"{len(warnings)} scan warnings · Open What was checked? / Coverage for details.")
        self.warning_label.setToolTip("<qt>" + escape("\n".join(warnings)).replace("\n", "<br>") + "</qt>")
        self.warning_label.setVisible(bool(warnings))
        status = "incomplete" if not complete else "complete with warnings" if warnings else "complete"
        self.status_label.setText(
            f"Scan {status} · {len(incidents)} {'problem' if len(incidents) == 1 else 'problems'} found."
        )
        if getattr(incidents, "cancelled", False):
            self._set_state("warning", "CANCELLED")
            self.status_label.setText(f"Scan cancelled - {len(incidents)} partial incidents available")
        self._remember_result(incidents)
        self._productivity_result_shown(incidents)
        self.motion.reveal()
        if self._scan_thread is not None and not self._close_pending and not getattr(incidents, "cancelled", False):
            self.sounds.play("success" if complete and not warnings else "attention")

    @pyqtSlot(str)
    def _show_scan_error(self, message):
        """Clear stale results and display a failed-scan state on the GUI thread."""
        self._clear_incidents()
        self._check_again_context = None
        self.warning_label.clear()
        self.journey.set_step(0)
        self.warning_label.hide()
        self.status_label.setText(f"Scan failed: {message}")
        self._set_state("error", "SCAN FAILED")
        self.result_count.setText("UNAVAILABLE")
        self._show_empty_state(
            "!", "This scan couldn't be completed",
            "Check the status message above, then try scanning again.",
        )
        if self._scan_thread is not None and not self._close_pending:
            self.sounds.play("attention")

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
        self.scan_button.setText("Scan my PC")
        if self._close_pending:
            self.close()
        else:
            self._flush_monitor_updates()

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
        if self._updates_dialog is not None:
            self._close_pending = True
            self._updates_dialog.reject()
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
        if self._admin_restart_pending:
            self._admin_restart_pending = False
            success, message = relaunch_as_administrator(int(self.winId()))
            if not success:
                self._close_pending = False
                self.status_label.setText(message)
                self.statusBar().showMessage(message, 20000)
                event.ignore()
                return
        if self._pending_installer is not None:
            path, asset = self._pending_installer
            self._pending_installer = None
            try:
                launch_installer(path, asset)
            except UpdateError as exc:
                self._close_pending = False
                self.status_label.setText(str(exc))
                self.statusBar().showMessage(str(exc), 15000)
                event.ignore()
                return
        if self._restart_pending:
            self._restart_pending = False
            if not relaunch_app():
                self._close_pending = False
                self.status_label.setText("Could not restart LogOpen. Please close and reopen the app manually.")
                event.ignore()
                return
        super().closeEvent(event)

    @pyqtSlot()
    def _restart_app(self):
        """Relaunch only after the normal close path has finished background work."""
        self._restart_pending = True
        self.close()

    def _restart_as_administrator(self):
        """Save the open note draft and wait for workers before requesting Windows consent."""
        if self._close_pending or self._admin_restart_pending or self._elevated is True:
            return
        incident = self.incident_detail.incident
        if incident is not None:
            draft = self.incident_detail.note_editor.toPlainText()
            if draft != incident.get("local_note", ""):
                try:
                    self.history_store.save_note(incident, draft)
                except (ValueError, RuntimeError):
                    self.statusBar().showMessage("Could not save your note. Save or shorten it before restarting as administrator.", 15000)
                    return
        self._restart_pending = False
        self._admin_restart_pending = True
        self._close_pending = True
        self.close()

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
        self._settings_dialog.restart_requested.connect(self._restart_app)
        self._settings_dialog.admin_restart_requested.connect(self._restart_as_administrator)
        self._settings_dialog.sounds_changed.connect(self._sync_sound_action)
        self._settings_dialog.privacy_requested.connect(self._open_privacy)
        self._settings_dialog.open()

    @pyqtSlot(int)
    def _settings_closed(self, _result):
        """Drop the dialog reference and schedule a pending parent-window close."""
        self._settings_dialog = None
        self.incident_detail.refresh_ai_model_hint()
        if self._close_pending:
            QTimer.singleShot(0, self.close)

    @pyqtSlot()
    def _open_terminal_settings(self):
        self._open_settings()
        if self._settings_dialog is not None:
            self._settings_dialog.tabs.setCurrentIndex(1)

    @pyqtSlot()
    def _open_updates(self):
        if self._scan_thread is not None or self._analysis_thread is not None:
            return
        if self._updates_dialog is not None:
            self._updates_dialog.raise_()
            self._updates_dialog.activateWindow()
            return
        self._updates_dialog = UpdateDialog(self)
        self._updates_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._updates_dialog.finished.connect(self._updates_closed)
        self._updates_dialog.install_requested.connect(self._install_update)
        self._updates_dialog.open()

    def _install_update(self, path, asset):
        """Drain monitoring and other workers before handing off to interactive setup."""
        self._pending_installer = (path, asset)
        self._close_pending = True
        if self._updates_dialog is not None:
            self._updates_dialog.accept()
        else:
            QTimer.singleShot(0, self.close)

    @pyqtSlot(int)
    def _updates_closed(self, _result):
        self._updates_dialog = None
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
        self.empty_symbol.setVisible(symbol != "1" and self.height() >= 800)
        self.empty_eyebrow.setText({
            "1": "LESS GUESSWORK. MORE CLARITY.",
            "···": "READING THE SIGNALS",
            "✓": "YOUR SCAN, EXPLAINED",
            "!": "LET'S TAKE ANOTHER LOOK",
        }.get(symbol, "YOUR SCAN, EXPLAINED"))
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
        self.filters_widget.setVisible(bool(self._incidents))
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
                    glyph, color = {
                        "Critical": ("critical", "#ffadb7"), "High": ("warning", "#f4bc72"),
                        "Medium": ("info", "#adcaff"), "Low": ("info", "#80e5d3"),
                    }.get(value, ("info", "#b8c9df"))
                    item.setIcon(icon(glyph, color, 16))
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
        for row in range(self.incident_table.rowCount()):
            self.incident_table.item(row, 2).setToolTip(
                "A higher score means review this sooner. This ranks log activity; it is not a PC health score."
            )

    def _clear_incidents(self):
        """Clear stored incidents, table, and detail page and return to the dashboard."""
        self._incidents = []
        if hasattr(self, "filters_widget"):
            self.filters_widget.hide()
            self.filter_feedback.hide()
            self.start_here_card.hide()
            self._start_here_incident = None
        if hasattr(self, "preview"):
            self.preview.set_incident(None)
            self.filter_chips.hide()
        if hasattr(self, "support_action"):
            self.support_action.setEnabled(False)
            self.support_filtered_action.setEnabled(False)
            self.compare_scans_action.setEnabled(False)
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
        self.incident_detail.check_again_button.setEnabled(self._analysis_thread is None and self._scan_thread is None and self.monitor.thread is None)
        self.page_stack.setCurrentWidget(self.incident_detail)
        self.journey.set_step(2)
        self.incident_detail.back_button.setFocus()

    @pyqtSlot()
    def _show_incident_list(self):
        """Return to the dashboard and table focus, preserving the current selection."""
        if self._pending_monitor_refresh and self._latest_monitor_result is not None:
            self._refresh_monitor_result()
        self.page_stack.setCurrentWidget(self.dashboard)
        self.journey.set_step(1 if self._incidents else 0)
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
            if hasattr(self, "support_action"):
                self.support_action.setEnabled(selected)
            for action in self.review_actions:
                action.setEnabled(selected)
        self._update_preview()

    def __init__(self):
        """Build the dashboard/detail widgets, connect actions, and load the stylesheet.

        Initializes empty results and worker references; opening the window does not
        start a scan. The user starts work through the Scan button.
        """
        super().__init__()

        self.sounds = SoundEffects()
        self._scan_thread = None
        self._scan_worker = None
        self._settings_dialog = None
        self._updates_dialog = None
        self._close_pending = False
        self._restart_pending = False
        self._admin_restart_pending = False
        self._pending_installer = None
        self._elevated = elevation_status()
        self._incidents = []
        self._cancel_event = Event()

        self.setWindowTitle("LogOpen — " + access_label(self._elevated))
        self._base_window_title = self.windowTitle()
        self.setWindowIcon(icon("logo", size=32))
        expand_window(self, 1500, 950)
        self.setMinimumSize(800, 620)
        self._base_stylesheet = Path(__file__).with_name("style.qss").read_text(encoding="utf-8")
        self.setStyleSheet(self._base_stylesheet)

        self.central_widget = QWidget()
        self.central_widget.setObjectName("appSurface")
        # When text is very large, keep every control reachable by scrolling.
        self.workspace_scroll = QScrollArea()
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.workspace_scroll.setParent(self)
        self.workspace_scroll.hide()
        self.setCentralWidget(self.central_widget)
        layout = QVBoxLayout(self.central_widget)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(18)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(12)
        mark = _label("L/", "brandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(42, 42)
        self.brand_mark = mark
        brand_row.addWidget(mark)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(3)
        self.title = _label("LogOpen", "brandTitle")
        brand_text.addWidget(self.title)
        self.subtitle = _label("Windows, explained.", "brandEyebrow")
        self.subtitle.setWordWrap(True)
        self.subtitle.setMinimumWidth(0)
        brand_text.addWidget(self.subtitle)
        brand_row.addLayout(brand_text)
        brand_row.addStretch()
        self.help_button = QPushButton("Quick guide", objectName="headerButton")
        self.help_button.setIcon(icon("help"))
        self.help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.help_button.clicked.connect(lambda: QuickStartDialog(self).exec())
        brand_row.addWidget(self.help_button)
        self.updates_button = QPushButton("Updates", objectName="headerButton")
        self.updates_button.setIcon(icon("download"))
        self.updates_button.setToolTip("Check GitHub and download the latest LogOpen release.")
        self.updates_button.clicked.connect(self._open_updates)
        brand_row.addWidget(self.updates_button)
        self.live_button = QPushButton("Live monitoring", objectName="headerButton")
        self.live_button.setIcon(icon("eye"))
        self.live_button.setToolTip("Start or stop automatic local scans (Ctrl+M)")
        self.live_button.setShortcut(QKeySequence("Ctrl+M"))
        self.live_button.clicked.connect(self._toggle_monitoring)
        brand_row.addWidget(self.live_button)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setIcon(icon("settings"))
        self.settings_button.setObjectName("headerButton")
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.setToolTip("Choose AI help, your API key, and your preferred terminal")
        self.settings_button.clicked.connect(self._open_settings)
        brand_row.addWidget(self.settings_button)
        self.state_badge = _label("READY TO SCAN", "stateBadge")
        self.state_badge.setProperty("state", "ready")
        brand_row.addWidget(self.state_badge)
        layout.addLayout(brand_row)
        self.brand_row = brand_row

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        self.monitoring_status = MonitoringStatus()
        self.monitoring_status.hide()
        layout.addWidget(self.monitoring_status)
        self.journey = ScanJourney()
        layout.addWidget(self.journey)

        self.page_stack = QStackedWidget()
        self.dashboard = QWidget()
        dashboard_layout = QVBoxLayout(self.dashboard)
        dashboard_layout.setContentsMargins(0, 0, 0, 0)
        dashboard_layout.setSpacing(12)
        self.page_stack.addWidget(self.dashboard)
        layout.addWidget(self.page_stack, 1)

        self.scan_hero = QFrame(objectName="scanHero")
        self.hero_layout = QVBoxLayout(self.scan_hero)
        self.hero_layout.setContentsMargins(20, 16, 20, 16)
        self.hero_layout.setSpacing(12)
        heading_row = QHBoxLayout()
        heading_row.setSpacing(24)
        heading_text = QVBoxLayout()
        heading_text.setSpacing(6)
        self.hero_eyebrow = _label("YOUR WINDOWS WORKSPACE", "heroEyebrow")
        heading_text.addWidget(self.hero_eyebrow)
        self.dashboard_title = _label("Find the signal in the noise.", "pageTitle")
        self.dashboard_title.setWordWrap(True)
        heading_text.addWidget(self.dashboard_title)
        self.status_label = _label("Start a scan to find recent problems recorded by Windows.", "statusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setAccessibleDescription("Current scan status")
        status_row = QHBoxLayout()
        self.scan_status_row = status_row
        status_row.addWidget(self.status_label, 1)
        self.coverage_button = QToolButton()
        self.coverage_button.setText("What was checked?")
        self.coverage_button.setToolTip("Inspect this report's source logs, time window, limits, and skipped coverage.")
        self.coverage_button.setAccessibleName("What was checked? Scan coverage")
        status_row.addWidget(self.coverage_button)
        heading_text.addLayout(status_row)
        heading_row.addLayout(heading_text, 1)
        self.scan_button = QPushButton("Scan my PC")
        self.scan_button.setIcon(icon("scan", "#0b3029"))
        self.scan_button.setObjectName("scanButton")
        self.scan_button.setMinimumSize(196, 46)
        self.scan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_button.setToolTip("Scan recent Windows warnings and errors")
        self.scan_button.setAccessibleName("Scan my PC for recent Windows problems")
        self.scan_button.clicked.connect(self.run_scan)
        action_column = QVBoxLayout()
        action_column.setSpacing(6)
        action_column.addWidget(self.scan_button)
        self.scan_trust = _label("No API key needed to scan", "trustNote")
        self.scan_trust.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scan_trust.setWordWrap(True)
        action_column.addWidget(self.scan_trust)
        heading_row.addLayout(action_column)
        self.hero_layout.addLayout(heading_row)
        dashboard_layout.addWidget(self.scan_hero)
        self.scan_heading_row = heading_row

        self.warning_label = _label("", "scanWarning")
        self.warning_label.setWordWrap(True)
        self.warning_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.warning_label.setAccessibleDescription("Scan warnings")
        self.warning_label.hide()
        dashboard_layout.addWidget(self.warning_label)

        metrics = QHBoxLayout()
        metrics.setSpacing(14)
        card, self.incident_total = _metric_card("Problems found", "Related records, one clear view")
        self.problems_card = card
        metrics.addWidget(card, 1)
        card, self.attention_total = _metric_card(
            "Review first", "High and Critical priority", "warm"
        )
        self.attention_card = card
        metrics.addWidget(card, 1)
        card, self.event_total = _metric_card(
            "Log entries", "The evidence behind each problem", "teal"
        )
        self.events_card = card
        metrics.addWidget(card, 1)
        self.metrics_panel = QWidget()
        self.metrics_panel.setLayout(metrics)
        metrics.setContentsMargins(0, 0, 0, 0)
        dashboard_layout.addWidget(self.metrics_panel)

        results_panel = QFrame()
        self.results_panel = results_panel
        results_panel.setObjectName("resultsPanel")
        results_layout = QVBoxLayout(results_panel)
        results_layout.setContentsMargins(16, 16, 16, 14)
        results_layout.setSpacing(14)
        results_header = QHBoxLayout()
        self.results_header = results_header
        results_heading = QVBoxLayout()
        results_heading.setSpacing(4)
        self.results_title = _label("Problems to review", "sectionTitle")
        self.results_hint = _label(
            "A scan groups related Windows warnings and errors into problems you can review.", "mutedLabel"
        )
        self.results_hint.setWordWrap(True)
        results_heading.addWidget(self.results_title)
        results_heading.addWidget(self.results_hint)
        results_header.addLayout(results_heading, 1)
        self.detail_button = QPushButton("Understand problem")
        self.detail_button.setIcon(icon("arrow"))
        self.detail_button.setToolTip("Select a problem in the list, then open its summary and next steps.")
        self.detail_button.setObjectName("secondaryButton")
        self.detail_button.setEnabled(False)
        self.detail_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.detail_button.clicked.connect(self._open_incident_detail)
        results_header.addWidget(self.detail_button)
        self.result_count = _label("AWAITING SCAN", "countBadge")
        results_header.addWidget(self.result_count)
        results_layout.addLayout(results_header)

        self.results_stack = QStackedWidget()
        self.results_stack.setMinimumHeight(144)
        self.incident_table = QTableWidget(0, 5)
        # Keep scan order until the user chooses a column; retain their choice.
        self.incident_table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.incident_table.setSortingEnabled(True)
        self.incident_table.setObjectName("incidentTable")
        self.incident_table.setHorizontalHeaderLabels(
            ["Priority", "What happened", "Priority score", "Seen before", "Log entries"]
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
        self.roomy_incidents = RoomyTable(self.incident_table, [130, 400, 130, 130, 110], flexible=1)
        self.copy_action = QAction("Copy selected rows", self.incident_table)
        self.copy_action.setIcon(icon("copy"))
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
        self.incident_detail.settings_requested.connect(self._open_settings)
        self.page_stack.addWidget(self.incident_detail)

        self.empty_state = QWidget()
        self.empty_layout = QHBoxLayout(self.empty_state)
        self.empty_layout.setContentsMargins(24, 14, 24, 14)
        self.empty_layout.setSpacing(32)
        empty_text = QVBoxLayout()
        empty_text.setSpacing(10)
        empty_text.addStretch()
        self.empty_eyebrow = _label("LESS GUESSWORK. MORE CLARITY.", "emptyEyebrow")
        empty_text.addWidget(self.empty_eyebrow)
        self.empty_symbol = _label("24h", "emptySymbol")
        self.empty_symbol.setFixedSize(48, 48)
        self.empty_symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_text.addWidget(self.empty_symbol, 0, Qt.AlignmentFlag.AlignLeft)
        self.empty_title = _label("", "emptyTitle")
        self.empty_description = _label("", "emptyDescription")
        for label in (self.empty_title, self.empty_description):
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            empty_text.addWidget(label)
        empty_text.addStretch()
        self.empty_layout.addLayout(empty_text, 3)
        self.signal_visual = SignalVisual()
        self.empty_layout.addWidget(self.signal_visual, 2)
        self.results_stack.addWidget(self.empty_state)
        self._show_empty_state(
            "1", "From Windows logs to a next step.",
            "Windows records the details. LogOpen brings related problems together "
            "so you can understand what happened and decide what to try.\n"
            "Choose Scan my PC above to get started.",
        )
        results_layout.addWidget(self.results_stack, 1)
        dashboard_layout.addWidget(results_panel, 1)

        footer = QHBoxLayout()
        self.footer_row = footer
        footer.setSpacing(16)
        scope = _label(
            "Scans read Windows logs. Suggested steps are never run automatically.",
            "scopeLabel",
        )
        scope.setWordWrap(True)
        self.footer_scope = scope
        footer.addWidget(scope, 1)
        self.last_scan = _label("No scans yet", "mutedLabel")
        footer.addWidget(self.last_scan)
        dashboard_layout.addLayout(footer)
        self._setup_workspace(dashboard_layout, results_layout)
        self._setup_keyboard_navigation()
        decorate_buttons(self)
        self.page_stack.currentChanged.connect(self._update_layout_density)
        self._update_layout_density()

    def _setup_keyboard_navigation(self):
        """Follow the visual workflow without skipping scan or filter controls."""
        controls = (
            self.help_button, self.updates_button, self.live_button, self.settings_button,
            self.monitoring_status.test_button, self.monitoring_status.preferences_button,
            self.coverage_button, self.scan_button, self.scan_goal,
            self.scope_button, self.history_button, self.open_report_button,
            self.cancel_button, self.comparison_button, self.problems_card,
            self.attention_card, self.events_card, self.search_input,
            self.priority_filter, self.review_filter, self.filter_button,
            self.start_here_button, self.clear_filters_button,
            self.incident_table, self.detail_button,
        )
        for before, after in zip(controls, controls[1:]):
            self.setTabOrder(before, after)
        self.find_action = QAction("Search problems", self)
        self.find_action.setShortcut(QKeySequence.StandardKey.Find)
        self.find_action.triggered.connect(self._focus_problem_search)
        self.addAction(self.find_action)
        self._focus_scroll_timer = QTimer(self)
        self._focus_scroll_timer.setSingleShot(True)
        self._focus_scroll_timer.timeout.connect(self._reveal_workspace_focus)
        QApplication.instance().focusChanged.connect(self._queue_workspace_focus)

    def _queue_workspace_focus(self, *_args):
        if self.centralWidget() is self.workspace_scroll:
            self._focus_scroll_timer.start(0)

    def _reveal_workspace_focus(self):
        focused = QApplication.focusWidget()
        if self.centralWidget() is self.workspace_scroll and focused is not None and self.central_widget.isAncestorOf(focused):
            self.workspace_scroll.ensureWidgetVisible(focused, 16, 16)

    def _focus_problem_search(self):
        self._show_incident_list()
        if self._incidents:
            self.search_input.setFocus()
            self.search_input.selectAll()

    def resizeEvent(self, event):
        """Prioritize readable result rows over summary cards in short windows."""
        super().resizeEvent(event)
        if hasattr(self, "motion"):
            self.motion.finish_reveal()
        self._update_layout_density()

    def _update_layout_density(self):
        """Keep the results and log reader usable in a short or narrow window."""
        compact_limit = {"normal": 1000, "large": 1150, "larger": 1300}.get(getattr(self, "_text_size", "normal"), 1000)
        largest = getattr(self, "_text_size", "normal") == "largest"
        short = largest or self.height() < compact_limit
        if largest and self.centralWidget() is self.central_widget:
            self.takeCentralWidget()
            self.workspace_scroll.setWidget(self.central_widget)
            self.setCentralWidget(self.workspace_scroll)
            self.workspace_scroll.show()
            self.central_widget.show()
        elif not largest and self.centralWidget() is self.workspace_scroll:
            self.workspace_scroll.takeWidget()
            self.takeCentralWidget()
            self.workspace_scroll.setParent(self)
            self.workspace_scroll.hide()
            self.setCentralWidget(self.central_widget)
            self.central_widget.show()
        scale = {"normal": 1, "large": 1.15, "larger": 1.3, "largest": 2}.get(getattr(self, "_text_size", "normal"), 1)
        # The enlarged workspace may exceed a small screen; the outer scroll
        # area follows keyboard focus instead of allowing controls to be cut off.
        if hasattr(self, "brand_row"):
            self.brand_row.setDirection(QBoxLayout.Direction.TopToBottom if largest else QBoxLayout.Direction.LeftToRight)
            self.scan_heading_row.setDirection(QBoxLayout.Direction.TopToBottom if largest else QBoxLayout.Direction.LeftToRight)
            self.scan_status_row.setDirection(QBoxLayout.Direction.TopToBottom if largest else QBoxLayout.Direction.LeftToRight)
            self.results_header.setDirection(QBoxLayout.Direction.TopToBottom if largest else QBoxLayout.Direction.LeftToRight)
            self.footer_row.setDirection(QBoxLayout.Direction.TopToBottom if largest else QBoxLayout.Direction.LeftToRight)
            mark_size = 84 if largest else 42
            self.brand_mark.setFixedSize(mark_size, mark_size)
            self.subtitle.setVisible(not largest and self.width() >= 1000 * scale and self.height() >= 760)
            self.updates_button.setVisible(largest or self.width() >= 1100 * scale)
        very_tight = not largest and (self.height() < 700 or (self.height() < 760 and getattr(self, "_text_size", "normal") != "normal"))
        has_result = getattr(self, "_current_result", None) is not None
        monitoring_visible = hasattr(self, "monitoring_status") and not self.monitoring_status.isHidden()
        if hasattr(self, "monitoring_status"):
            self.monitoring_status.set_compact(not largest and (self.height() < 950 * scale or self.width() < 1100 * scale))
        if hasattr(self, "results_panel"):
            self.scan_hero.setVisible(not (monitoring_visible and not largest and self.height() < 850 * scale))
            margins = (16, 12, 16, 10) if short else (24, 22, 24, 18)
            self.central_widget.layout().setContentsMargins(*margins)
            self.central_widget.layout().setSpacing(8 if short else 18)
            self.dashboard.layout().setSpacing(6 if short else 12)
            hero_margins = (12, 6, 12, 6) if very_tight else (12, 10, 12, 10) if short else (20, 16, 20, 16)
            self.hero_layout.setContentsMargins(*hero_margins)
            self.hero_layout.setSpacing(6 if short else 12)
            self.hero_eyebrow.setVisible(not has_result and not largest and self.height() >= 850 * scale)
            self.scan_trust.setVisible(not has_result and not largest and self.height() >= 850 * scale)
            margins = (12, 8, 12, 8) if very_tight else (12, 12, 12, 10) if short else (16, 16, 16, 14)
            self.results_panel.layout().setContentsMargins(*margins)
            self.results_panel.layout().setSpacing(4 if very_tight else 8 if short else 14)
            header_height = self.incident_table.horizontalHeader().sizeHint().height()
            scrollbar_height = self.incident_table.horizontalScrollBar().sizeHint().height()
            self.results_stack.setMinimumHeight(max(144, header_height + scrollbar_height + 80))
            self.results_title.setVisible(has_result)
            self.detail_button.setVisible(has_result and bool(self._incidents))
            self.result_count.setVisible(has_result)
            self.results_hint.setVisible(has_result and not short)
            self.coverage_button.setVisible(has_result)
        if hasattr(self, "metrics_panel"):
            self.metrics_panel.setVisible(
                bool(self._incidents) and not largest
                and self.height() >= 900 * scale and self.width() >= 1000 * scale
            )
        if hasattr(self, "signal_visual"):
            self.signal_visual.setVisible(not largest and self.width() >= 1000 * scale and self.height() >= 750 * scale)
            self.empty_eyebrow.setVisible(not largest and self.height() >= 800 * scale)
            self.empty_symbol.setVisible(self.empty_symbol.text() != "1" and not largest and self.height() >= 800 * scale)
            empty_margins = (16, 8, 16, 8) if short else (40, 20, 40, 20)
            self.empty_layout.setContentsMargins(*empty_margins)
        if hasattr(self, "state_badge"):
            self.state_badge.setVisible(self.width() >= 1000)
        if hasattr(self, "page_stack"):
            self.journey.setVisible(not short and self.height() >= 1200 * scale)
            self.journey.set_compact(short)
        if hasattr(self, "scan_goal"):
            self.scan_goal_widget.setVisible(not very_tight)
            self.dashboard_title.setVisible(not has_result and not very_tight)
            self.footer_scope.setVisible(not very_tight)
            self.last_scan.setVisible(not very_tight)
            self.scope_label.setVisible(self.width() >= 1000)
            compact = self.width() < 1000 or short
            self.scan_goal_label.setText("Check for" if self.width() < 1100 * scale else "What would you like to check?")
            self.scan_goal_hint.setVisible(not compact)
            self.scan_goal_widget.layout().setDirection(QBoxLayout.Direction.TopToBottom if largest else QBoxLayout.Direction.LeftToRight)
            self.scope_row.setDirection(QBoxLayout.Direction.TopToBottom if largest else QBoxLayout.Direction.LeftToRight)
            self.filters_layout.setDirection(QBoxLayout.Direction.TopToBottom if largest else QBoxLayout.Direction.LeftToRight)
            self.start_here_explanation.setVisible(not short)
            # Warnings and comparisons appear during polling. Reserve the rows
            # before showing optional guidance, including on scaled laptop screens.
            tight = not self._has_room_for_guidance()
            self.start_here_card.setVisible(any(self._matches_filters(item) for item in self._incidents) and not tight)
            self.comparison_button.setVisible(not tight and bool(getattr(self._current_result, "comparison", {}).get("comparable")))
        if hasattr(self, "preview"):
            self.preview.setVisible(self.preview_action.isChecked() and self.width() >= 1400 * scale and bool(self._incidents))
            self.coverage_button.setText("Coverage" if self.width() < 1000 else "What was checked?")
            self._refresh_filter_chips()
        if largest and hasattr(self, "_focus_scroll_timer"):
            self._queue_workspace_focus()
        if hasattr(self, "monitor"):
            self._update_statusbar_labels()

    def _has_room_for_guidance(self):
        return self.height() >= (1100 if getattr(self, "_text_size", "normal") == "normal" else 1250)

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
