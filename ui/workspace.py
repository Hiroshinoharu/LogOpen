"""Desktop history, filters, report exchange, review state, and manual AI actions."""

from copy import deepcopy
from datetime import datetime

import config

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QPushButton,
    QApplication, QSystemTrayIcon, QStyle,
)

from history_store import HistoryStore
from llm_analysis import AIAnalysisResult, analyse_incident_with_llm_result
from models.scan_options import ScanOptions
from models.scan_result import ScanResult
from report_io import load_report, export_selected
from settings import app_settings
from ui.animations import MotionController
from ui.history_dialog import ComparisonDialog, HistoryDialog
from ui.scan_controls import FilterDialog, ScanOptionsDialog
from ui.timeline import TimelineDialog
from ui.monitoring import MonitorController, MonitorOptionsDialog


class AnalysisWorker(QObject):
    """Analyze an isolated incident snapshot without blocking the GUI thread."""

    result = pyqtSignal(object)
    finished = pyqtSignal()

    def __init__(self, incident, preferences):
        """Snapshot evidence and settings before moving the worker to its thread."""
        super().__init__()
        self.incident = deepcopy(incident)
        self.preferences = preferences

    @pyqtSlot()
    def run(self):
        """Deliver a validated outcome, hiding unexpected exceptions from the UI."""
        try:
            outcome = analyse_incident_with_llm_result(self.incident, settings=self.preferences)
            self.result.emit(outcome)
        except Exception:
            self.result.emit(AIAnalysisResult("failed", "AI analysis failed. Check AI Settings and try again."))
        finally:
            self.finished.emit()


class WorkspaceMixin:
    """Add desktop actions to MainWindow while keeping the scan presentation focused."""

    def _setup_workspace(self, dashboard_layout, results_layout):
        """Build scan controls, filtering, file menus, and motion preferences."""
        self.history_store = HistoryStore()
        self._current_result = None
        self._history_scan_id = None
        self._analysis_thread = None
        self._analysis_worker = None
        self._analysis_target = None
        self._extra_filters = {}
        self._search_text = {}
        self._ui_settings = app_settings._create_qsettings()
        scope_warning = None
        try:
            logs = self._ui_settings.value("scan/log_types", None)
            self.scan_options = ScanOptions(
                log_types=tuple(logs) if isinstance(logs, (list, tuple)) else tuple(config.LOG_TYPES),
                hours=float(self._ui_settings.value("scan/hours", config.SCAN_WINDOW_HOURS)),
                max_records=int(self._ui_settings.value("scan/max_records", config.MAX_RECORDS_PER_LOG)),
                profile=self._ui_settings.value("scan/profile", "general"),
                rules_json=self._ui_settings.value("scan/rules_json", "[]"),
                automatic_ai=str(self._ui_settings.value("scan/automatic_ai", True)).lower() in ("true", "1"),
            )
        except (ValueError, TypeError):
            self.scan_options = ScanOptions()
            scope_warning = "Saved scan scope was invalid. Defaults loaded; review Scan scope before scanning."
        scope_row = QHBoxLayout()
        self.scope_button = QPushButton("Scan scope…", objectName="secondaryButton")
        self.scope_button.clicked.connect(self._edit_scan_scope)
        scope_row.addWidget(self.scope_button)
        self.scope_label = QLabel(objectName="mutedLabel")
        self.scope_label.setTextFormat(Qt.TextFormat.PlainText)
        self.scope_label.setWordWrap(True)
        scope_row.addWidget(self.scope_label, 1)
        self.cancel_button = QPushButton("Cancel scan", objectName="secondaryButton")
        self.cancel_button.clicked.connect(self._cancel_scan)
        self.cancel_button.hide()
        scope_row.addWidget(self.cancel_button)
        dashboard_layout.insertLayout(1, scope_row)
        self.progress_bar = QProgressBar(objectName="scanProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(5)
        self.progress_bar.setAccessibleName("Scan progress")
        self.progress_bar.hide()
        dashboard_layout.insertWidget(2, self.progress_bar)
        self.comparison_button = QPushButton("No comparison yet", objectName="comparisonButton")
        self.comparison_button.setEnabled(False)
        self.comparison_button.clicked.connect(self._show_comparison)
        dashboard_layout.insertWidget(5, self.comparison_button)
        filters = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search incidents, providers, or source messages…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setAccessibleName("Search incidents")
        self.search_input.setMinimumWidth(100)
        filters.addWidget(self.search_input, 1)
        self.priority_filter = QComboBox()
        self.priority_filter.addItems(["All priorities", "Critical", "High", "Medium", "Low"])
        self.priority_filter.setAccessibleName("Filter priority")
        filters.addWidget(self.priority_filter)
        self.review_filter = QComboBox()
        self.review_filter.addItems(["Hide ignored", "All reviews", "Active", "Acknowledged", "Ignored"])
        self.review_filter.setAccessibleName("Filter review state")
        filters.addWidget(self.review_filter)
        self.filter_button = QPushButton("More filters…", objectName="secondaryButton")
        self.filter_button.clicked.connect(self._edit_filters)
        filters.addWidget(self.filter_button)
        results_layout.insertLayout(1, filters)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(180)
        self._filter_timer.timeout.connect(self._apply_filters)
        self.search_input.textChanged.connect(lambda: self._filter_timer.start())
        self.priority_filter.currentIndexChanged.connect(self._apply_filters)
        self.review_filter.currentIndexChanged.connect(self._apply_filters)
        self.incident_table.model().layoutChanged.connect(self._apply_filters)
        self._build_workspace_menus()
        enabled = self._ui_settings.value("ui/animations", True)
        enabled = enabled is True or str(enabled).lower() in ("true", "1")
        self.motion = MotionController(self.page_stack, self.state_badge, enabled)
        self.motion_action.setChecked(enabled)
        self.motion_action.toggled.connect(self._set_motion_enabled)
        self.page_stack.currentChanged.connect(self.motion.reveal)
        self.incident_detail.analysis_requested.connect(self._analyze_selected)
        self.incident_detail.review_requested.connect(self._review_incident)
        self._update_scope_label()
        self._setup_monitoring()
        if scope_warning:
            self.warning_label.setText(scope_warning)
            self.warning_label.show()

    def _build_workspace_menus(self):
        """Expose history/import/export and review actions without crowding the header."""
        menu = self.menuBar().addMenu("File")
        self.open_action = menu.addAction("Open report…", self._open_report)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.history_action = menu.addAction("Scan history…", self._open_history)
        menu.addSeparator()
        self.export_action = menu.addAction("Export selected incidents…", self._export_selection)
        self.export_action.setEnabled(False)
        self.export_visible_action = menu.addAction("Export filtered incidents…", self._export_visible)
        self.export_visible_action.setEnabled(False)
        view = self.menuBar().addMenu("View")
        self.timeline_action = view.addAction("Timeline of filtered incidents…", self._show_timeline)
        self.timeline_action.setEnabled(False)
        view.addAction("Clear filters", self._clear_filters)
        self.motion_action = QAction("Enable animations", self, checkable=True)
        view.addAction(self.motion_action)
        review = self.menuBar().addMenu("Review")
        self.review_actions = []
        for text, state in (("Acknowledge selected", "acknowledged"),
                            ("Ignore selected", "ignored"), ("Restore selected", "active")):
            action = review.addAction(text)
            action.triggered.connect(lambda _checked=False, state=state: self._review_rows(state))
            action.setEnabled(False)
            self.review_actions.append(action)

    def _update_scope_label(self):
        """Describe the pending scope independently of the currently displayed report."""
        self.scope_label.setText(f"{len(self.scan_options.log_types)} channels · "
                                 f"last {self.scan_options.hours:g} hours")
        self.scope_label.setToolTip("\n".join(self.scan_options.log_types))

    def _edit_scan_scope(self):
        """Save valid scope controls for later desktop scans and later launches."""
        dialog = ScanOptionsDialog(self.scan_options, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.scan_options = dialog.options
        for key, value in (("log_types", list(self.scan_options.log_types)),
                           ("hours", self.scan_options.hours), ("max_records", self.scan_options.max_records),
                           ("profile", self.scan_options.profile), ("rules_json", self.scan_options.rules_json),
                           ("automatic_ai", self.scan_options.automatic_ai)):
            self._ui_settings.setValue("scan/" + key, value)
        self._ui_settings.sync()
        self._update_scope_label()
        if self._ui_settings.status() != self._ui_settings.Status.NoError:
            self.status_label.setText("Scope applies to this session, but preferences could not be saved.")

    def _set_motion_enabled(self, enabled):
        """Apply and persist the user's animation/reduced-motion preference."""
        self.motion.set_enabled(enabled)
        self._ui_settings.setValue("ui/animations", enabled)
        self._ui_settings.sync()

    def _workspace_busy(self, busy):
        """Prevent changing datasets while a scan or manual analysis owns them."""
        for control in (self.scope_button, self.open_action, self.history_action):
            control.setEnabled(not busy)
        if hasattr(self, "monitor"):
            self.scope_button.setEnabled(not busy and not self.monitor.active and self.monitor.thread is None)
            self.monitor_start_action.setEnabled(not busy and not self.monitor.active and self.monitor.thread is None)
            self.monitor_latest_action.setEnabled(not busy and self._latest_monitor_result is not None)
        self.incident_detail.analyze_button.setEnabled(not busy and self.incident_detail.incident is not None)
        self.motion.set_active(busy)

    def _show_progress(self, stage, percent):
        """Update stage text and progress only on the GUI thread."""
        if self._cancel_event.is_set():
            self.status_label.setText("Cancelling… waiting for the current operation to finish.")
        else:
            self.status_label.setText(stage)
        self.progress_bar.setValue(percent)
        if percent >= 95:
            self.cancel_button.setEnabled(False)

    def _cancel_scan(self):
        """Request cooperative cancellation, including between any AI requests."""
        if self._scan_thread is not None:
            self._cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Cancelling… an active Windows read or AI request may need to finish.")

    def _remember_result(self, result):
        """Keep the full envelope for later exports/history updates and index search text."""
        self._current_result = result if isinstance(result, ScanResult) else ScanResult(result)
        self._history_scan_id = getattr(result, "scan_id", None) if getattr(result, "history_saved", False) else None
        self._search_text = {
            id(incident): " ".join(str(incident.get(key, "")) for key in (
                "incident_classification", "computer_name", "providers", "event_ids",
                "summary_text", "events", "log_types", "log_type",
            )).casefold() for incident in result
        }
        comparison = getattr(result, "comparison", {}) or {}
        self.comparison_button.setEnabled(bool(comparison))
        if comparison.get("comparable"):
            self.comparison_button.setText(
                f"Changes: {comparison.get('new_count', 0)} new · "
                f"{comparison.get('persistent_count', 0)} persistent · "
                f"{comparison.get('no_longer_observed_count', 0)} no longer observed   →")
        else:
            self.comparison_button.setText("Scan comparison: no matching complete baseline yet")
        self.export_visible_action.setEnabled(bool(result))
        self.timeline_action.setEnabled(bool(result))
        self._apply_filters()

    def _show_timeline(self):
        """Open chronological source evidence for the currently filtered incident rows."""
        TimelineDialog(self._selected_incidents(visible=True), self).exec()

    def _setup_monitoring(self):
        """Create inactive monitoring controls and a session-only tray indicator."""
        self._latest_monitor_result = None
        self._monitor_notifications = False
        self.monitor = MonitorController(self.history_store, self._can_monitor_scan, self)
        self.monitor.completed.connect(self._monitor_completed)
        self.monitor.busy_changed.connect(self._monitor_busy)
        self.monitor.stopped.connect(self._monitor_stopped)
        menu = self.menuBar().addMenu("Monitor")
        self.monitor_start_action = menu.addAction("Start live monitoring…", self._start_monitoring)
        self.monitor_stop_action = menu.addAction("Stop monitoring", self.monitor.stop)
        self.monitor_stop_action.setEnabled(False)
        self.monitor_latest_action = menu.addAction("View latest monitoring scan", self._view_monitor_result)
        self.monitor_latest_action.setEnabled(False)
        self.monitor_label = QLabel("Monitoring off")
        self.monitor_label.setTextFormat(Qt.TextFormat.PlainText)
        self.statusBar().addWidget(self.monitor_label, 1)
        self.statusBar().hide()
        self.monitor.status.connect(self.monitor_label.setText)
        self.monitor_tray = QSystemTrayIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self)
        self.monitor_tray.setToolTip("LogOpen monitoring")
        self.monitor_tray.messageClicked.connect(self._view_monitor_result)

    def _can_monitor_scan(self):
        """Defer background polls while foreground work or modal dialogs own the UI."""
        return (self._scan_thread is None and self._analysis_thread is None
                and self._settings_dialog is None and not self._close_pending
                and QApplication.activeModalWidget() is None)

    def _start_monitoring(self):
        """Start only after the user chooses the polling interval and notifications."""
        dialog = MonitorOptionsDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._monitor_notifications = dialog.notifications.isChecked()
        self.statusBar().show()
        self.monitor_start_action.setEnabled(False)
        self.monitor_stop_action.setEnabled(True)
        self.scope_button.setEnabled(False)
        if self._monitor_notifications and QSystemTrayIcon.isSystemTrayAvailable():
            self.monitor_tray.show()
        self.monitor.start(self.scan_options, dialog.interval.value())

    def _monitor_busy(self, busy):
        """Prevent overlapping foreground scans/AI while a monitoring worker runs."""
        self.scan_button.setEnabled(not busy)
        self.settings_button.setEnabled(not busy)
        self.incident_detail.analyze_button.setEnabled(not busy and self.incident_detail.incident is not None)
        self.monitor_latest_action.setEnabled(not busy and self._latest_monitor_result is not None)
        self.motion.set_active(busy)

    def _monitor_completed(self, result, alerts):
        """Retain the latest scan and show one generic notification without disrupting reading."""
        self._latest_monitor_result = result
        if alerts and self._monitor_notifications and self.monitor_tray.isVisible():
            self.monitor_tray.showMessage("LogOpen: new incidents", f"{len(alerts)} new high-priority incidents. Open LogOpen to review the evidence.",
                                          QSystemTrayIcon.MessageIcon.Warning, 8000)

    def _view_monitor_result(self):
        """Open the most recent monitoring snapshot when foreground work is idle."""
        if self._latest_monitor_result is not None and self._scan_thread is None and self._analysis_thread is None and self.monitor.thread is None:
            self._display_saved_result(self._latest_monitor_result, "Latest monitoring scan")
            self.showNormal()
            self.raise_()

    def _monitor_stopped(self):
        """Restore controls after cancellation cleanup, or complete a deferred close."""
        busy = self._scan_thread is not None or self._analysis_thread is not None
        self.monitor_start_action.setEnabled(not busy)
        self.monitor_stop_action.setEnabled(False)
        self.scope_button.setEnabled(not busy)
        self.monitor_tray.hide()
        if self._close_pending:
            self.close()

    def _edit_filters(self):
        """Apply optional provider, classification, and date constraints locally."""
        dialog = FilterDialog(self._extra_filters, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._extra_filters = dialog.values()
            self._apply_filters()

    def _clear_filters(self):
        """Reset search and optional filters; keep ignored incidents hidden by default."""
        self._extra_filters = {}
        self.search_input.clear()
        self.priority_filter.setCurrentIndex(0)
        self.review_filter.setCurrentIndex(0)
        self._apply_filters()

    def _matches_filters(self, incident):
        """Match literal text, priority, review state, and inclusive incident-start dates."""
        query = self.search_input.text().strip().casefold()
        if query and query not in self._search_text.get(id(incident), ""):
            return False
        priority = self.priority_filter.currentText()
        if priority != "All priorities" and incident.get("incident_priority") != priority:
            return False
        state = incident.get("review_state", "active")
        selected = self.review_filter.currentText()
        if (selected == "Hide ignored" and state == "ignored") or (
            selected not in ("Hide ignored", "All reviews") and state != selected.lower()
        ):
            return False
        for key, field in (("provider", "providers"), ("classification", "incident_classification")):
            if self._extra_filters.get(key, "").casefold() not in str(incident.get(field, "")).casefold():
                return False
        if self._extra_filters.get("dates"):
            value = incident.get("time_generated_start")
            try:
                date = (value if isinstance(value, datetime) else datetime.fromisoformat(value)).date()
            except (TypeError, ValueError):
                return False
            if not self._extra_filters["start"].toPyDate() <= date <= self._extra_filters["end"].toPyDate():
                return False
        return True

    def _apply_filters(self, *_args):
        """Hide unmatched rows without changing sort keys or source-dictionary mapping."""
        visible = 0
        table = self.incident_table
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is None:
                continue
            index = item.data(Qt.ItemDataRole.UserRole)
            matched = self._matches_filters(self._incidents[index])
            table.setRowHidden(row, not matched)
            visible += matched
        if any(table.isRowHidden(index.row()) for index in table.selectionModel().selectedRows()):
            table.clearSelection()
        self.result_count.setText(f"{visible} FOUND" if visible == len(self._incidents)
                                 else f"{visible} / {len(self._incidents)} SHOWN")
        self.filter_button.setText("Filters active…" if any(self._extra_filters.get(key) for key in
                                  ("provider", "classification", "dates")) else "More filters…")
        self.export_visible_action.setEnabled(visible > 0)
        self._update_copy_action()

    def _selected_incidents(self, visible=False):
        """Resolve selected or visible rows to source objects in current display order."""
        table = self.incident_table
        rows = range(table.rowCount()) if visible else sorted(index.row() for index in
                                                             table.selectionModel().selectedRows())
        return [self._incidents[table.item(row, 0).data(Qt.ItemDataRole.UserRole)]
                for row in rows if not table.isRowHidden(row)]

    def _export_selection(self):
        """Export only selected visible rows to a user-chosen file."""
        self._export_rows(self._selected_incidents())

    def _export_visible(self):
        """Export the currently filtered rows, preserving their displayed order."""
        self._export_rows(self._selected_incidents(visible=True))

    def _export_rows(self, incidents):
        """Choose JSON, spreadsheet-friendly CSV, or a readable Markdown report."""
        if not incidents:
            return
        filters = "JSON report (*.json);;CSV spreadsheet (*.csv);;Readable report (*.md)"
        path, selected = QFileDialog.getSaveFileName(self, "Export incidents", "incidents.json", filters)
        if not path:
            return
        format_name = "csv" if selected.startswith("CSV") else "markdown" if selected.startswith("Readable") else "json"
        extension = {"json": ".json", "csv": ".csv", "markdown": ".md"}[format_name]
        if not path.lower().endswith(extension):
            path += extension
        try:
            export_selected(incidents, path, format_name)
        except Exception:
            self.status_label.setText("Could not export the selected incidents. Check the destination and try again.")
        else:
            self.status_label.setText(f"Exported {len(incidents)} incidents to {path}")

    def _open_report(self):
        """Load a validated saved report without collecting events or running AI."""
        path, _ = QFileDialog.getOpenFileName(self, "Open report", "", "JSON reports (*.json)")
        if not path:
            return
        try:
            result = load_report(path)
        except Exception:
            self.status_label.setText("Could not open this report. Choose a valid LogOpen JSON report.")
            return
        self._display_saved_result(result, f"Opened report: {path}")

    def _open_history(self):
        """Browse saved scans and restore the chosen envelope without scanning."""
        try:
            scans = self.history_store.list_scans()
            dialog = HistoryDialog(scans, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            result = self.history_store.load_scan(dialog.scan_id)
        except Exception:
            self.status_label.setText("Could not open scan history. Check access to your local application data.")
            return
        self._display_saved_result(result, "Opened saved scan")

    def _display_saved_result(self, result, message):
        """Apply current review decisions and label historical/imported presentation clearly."""
        try:
            self.history_store.apply_states(result)
        except Exception:
            result.warnings.append("Review states could not be loaded.")
        self._show_scan_results(result)
        self.status_label.setText(f"{message} · {len(result)} incidents")
        self.last_scan.setText(f"Saved: {getattr(result, 'created_at', None) or 'time unavailable'}")
        self._set_state("complete" if result.complete else "warning", "SAVED REPORT")

    def _show_comparison(self):
        """Open the saved comparison attached to the displayed result."""
        if self._current_result is not None:
            ComparisonDialog(getattr(self._current_result, "comparison", {}) or {}, self).exec()

    def _review_rows(self, state):
        """Persist the requested review decision for each selected issue signature."""
        self._set_review_state(self._selected_incidents(), state)

    def _review_incident(self, state):
        """Apply a review decision to the incident currently open in the detail view."""
        incident = self.incident_detail.incident
        if incident is not None:
            self._set_review_state([incident], state)

    def _set_review_state(self, incidents, state):
        """Save issue-level review state and refresh visibility without losing details."""
        try:
            for incident in incidents:
                self.history_store.set_state(incident, state)
            self.history_store.apply_states(self._incidents)
        except Exception:
            self.incident_detail.action_feedback.setText("Could not save the review state.")
            self.incident_detail.review_badge.setText("Review state could not be saved")
            self.status_label.setText("Could not save the review state. Check local application data access.")
            return
        self._apply_filters()
        current = self.incident_detail.incident
        if current is not None:
            self.history_store.apply_states([current])
            self.incident_detail.update_review_state()
            self.incident_detail.action_feedback.setText(f"Marked {state}. Applies to this issue in later scans.")
        self.status_label.setText(f"Marked {len(incidents)} selected incidents {state}.")

    def _analyze_selected(self):
        """Request/retry one incident using saved AI preferences on a worker thread."""
        incident = self.incident_detail.incident
        if incident is None or self._analysis_thread is not None or self._scan_thread is not None or self.monitor.thread is not None:
            return
        preferences = app_settings.AppSettings().load()
        if not preferences.enabled:
            self.incident_detail.action_feedback.setText("Enable AI analysis in Settings before requesting analysis.")
            return
        self._analysis_target = incident
        self._analysis_thread = QThread(self)
        self._analysis_worker = AnalysisWorker(incident, preferences)
        self._analysis_worker.moveToThread(self._analysis_thread)
        self._analysis_thread.started.connect(self._analysis_worker.run)
        self._analysis_worker.result.connect(self._show_manual_analysis)
        self._analysis_worker.finished.connect(self._analysis_thread.quit)
        self._analysis_thread.finished.connect(self._analysis_worker.deleteLater)
        self._analysis_thread.finished.connect(self._analysis_thread.deleteLater)
        self._analysis_thread.finished.connect(self._manual_analysis_finished)
        self.scan_button.setEnabled(False)
        self.settings_button.setEnabled(False)
        self._workspace_busy(True)
        self.incident_detail.analyze_button.setText("Analyzing…")
        self.incident_detail.action_feedback.setText("Requesting AI analysis for this incident…")
        self._analysis_thread.start()

    def _show_manual_analysis(self, outcome):
        """Attach a safe outcome, retain prior valid analysis on retry failure, and save history."""
        incident = self._analysis_target
        if incident is None:
            return
        incident["llm_analysis_status"] = outcome.status
        incident["llm_analysis_message"] = outcome.message
        if outcome.analysis is not None:
            incident["llm_analysis"] = outcome.analysis.model_dump(mode="json")
            incident.pop("llm_analysis_error", None)
        elif outcome.status == "failed":
            incident["llm_analysis_error"] = outcome.message
        message = outcome.message
        if outcome.status == "failed" and incident.get("llm_analysis"):
            message += " The previous analysis has been retained."
        scan_id = self._history_scan_id
        if scan_id:
            try:
                self.history_store.update_scan(scan_id, self._current_result)
            except Exception:
                message += " Updated analysis is visible, but could not be saved to history."
        if self.incident_detail.incident is incident:
            self.incident_detail.set_incident(incident)
            self.incident_detail.tabs.setCurrentIndex(1)
            self.incident_detail.analyze_button.setEnabled(False)
            self.incident_detail.action_feedback.setText(message)
        self.status_label.setText(message)

    def _manual_analysis_finished(self):
        """Restore controls only after the analysis thread exits; honor deferred closing."""
        self._analysis_worker = None
        self._analysis_thread = None
        self._analysis_target = None
        self.scan_button.setEnabled(True)
        self.settings_button.setEnabled(True)
        self.incident_detail.analyze_button.setText("Analyze / retry AI")
        self._workspace_busy(False)
        if self._close_pending:
            self.close()
