"""Desktop history, filters, report exchange, review state, and manual AI actions."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime

import config
from detection_rules import PROFILES, SCAN_GOALS

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, QItemSelectionModel, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QWidget,
    QProgressBar, QPushButton, QVBoxLayout, QSizePolicy,
    QApplication, QSystemTrayIcon, QStyle, QMenu,
)

from history_store import HistoryStore, incident_key
from llm_analysis import AIAnalysisResult, analyse_incident_with_llm_result, build_scan_context
from models.scan_options import ScanOptions
from models.scan_result import ScanResult
from report_io import load_report, export_selected
from settings import app_settings
from ui.animations import MotionController
from ui.history_dialog import ComparisonDialog, HistoryDialog
from ui.scan_controls import FilterDialog, ScanOptionsDialog
from ui.timeline import TimelineDialog
from ui.monitoring import MonitorController, MonitorOptionsDialog
from ui.icons import action_icon_name, icon
from ui.guidance import problem_explanation
from ui.table_sorting import numeric_key


class AnalysisWorker(QObject):
    """Analyze an isolated incident snapshot without blocking the GUI thread."""

    result = pyqtSignal(object)
    finished = pyqtSignal()

    def __init__(self, incident, preferences, scan_context=None, prepared_request=None):
        """Snapshot evidence and settings before moving the worker to its thread."""
        super().__init__()
        self.incident = deepcopy(incident)
        self.preferences = preferences
        self.scan_context = deepcopy(scan_context)
        self.prepared_request = deepcopy(prepared_request)

    @pyqtSlot()
    def run(self):
        """Deliver a validated outcome, hiding unexpected exceptions from the UI."""
        try:
            reviewed = {"prepared_request": self.prepared_request} if self.prepared_request is not None else {}
            outcome = analyse_incident_with_llm_result(self.incident, settings=self.preferences,
                                                      scan_context=self.scan_context, **reviewed)
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
            scope_warning = "Saved scan options were invalid. Defaults loaded; review Scan options before scanning."
        scope_row = QHBoxLayout()
        self.scope_button = QPushButton("Scan options", objectName="secondaryButton")
        self.scope_button.setIcon(icon("settings"))
        self.scope_button.setToolTip("Choose how far back to look. Default options are a good place to start.")
        self.scope_button.clicked.connect(self._edit_scan_scope)
        scope_row.addWidget(self.scope_button)
        self.scope_label = QLabel(objectName="mutedLabel")
        self.scope_label.setTextFormat(Qt.TextFormat.PlainText)
        self.scope_label.setWordWrap(True)
        scope_row.addWidget(self.scope_label, 1)
        self.history_button = QPushButton("Scan history", objectName="secondaryButton")
        self.history_button.setIcon(icon("history"))
        self.history_button.setToolTip("Reopen a previous scan without scanning again.")
        self.history_button.clicked.connect(self._open_history)
        scope_row.addWidget(self.history_button)
        self.open_report_button = QPushButton("Open report", objectName="secondaryButton")
        self.open_report_button.setIcon(icon("folder"))
        self.open_report_button.clicked.connect(self._open_report)
        scope_row.addWidget(self.open_report_button)
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
        self.scan_goal_widget = QWidget()
        goal_row = QHBoxLayout(self.scan_goal_widget)
        goal_row.setContentsMargins(0, 0, 0, 0)
        self.scan_goal_label = QLabel("What would you like to check?")
        goal_row.addWidget(self.scan_goal_label)
        self.scan_goal = QComboBox()
        self.scan_goal.setAccessibleName("Choose a symptom to scan")
        for key, (label, _description) in SCAN_GOALS.items():
            self.scan_goal.addItem(label, key)
        self.scan_goal_label.setBuddy(self.scan_goal)
        self.scan_goal.setMinimumContentsLength(18)
        goal_row.addWidget(self.scan_goal)
        self.scan_goal_hint = QLabel(objectName="mutedLabel")
        self.scan_goal_hint.setWordWrap(True)
        goal_row.addWidget(self.scan_goal_hint, 1)
        self.scan_goal.currentIndexChanged.connect(self._choose_scan_goal)
        dashboard_layout.insertWidget(1, self.scan_goal_widget)
        self.comparison_button = QPushButton("No comparison yet", objectName="comparisonButton")
        self.comparison_button.setEnabled(False)
        self.comparison_button.setIcon(icon("history", "#80e5d3"))
        self.comparison_button.hide()
        self.comparison_button.clicked.connect(self._show_comparison)
        dashboard_layout.insertWidget(5, self.comparison_button)
        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search these problems…")
        self.search_input.setAccessibleName("Search problems")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(100)
        filters.addWidget(self.search_input, 1)
        self.priority_filter = QComboBox()
        self.priority_filter.addItems(["All priorities", "Critical", "High", "Medium", "Low", "High and Critical"])
        self.priority_filter.setAccessibleName("Filter priority")
        self.priority_filter.setToolTip("High and Critical problems are a useful place to start.")
        filters.addWidget(self.priority_filter)
        self.review_filter = QComboBox()
        for label, value in (("Visible problems", "visible"), ("All problems", "all"),
                             ("To review", "active"), ("Reviewed", "acknowledged"), ("Hidden", "ignored"),
                             ("Hidden temporarily", "snoozed")):
            self.review_filter.addItem(label, value)
        self.review_filter.setAccessibleName("Filter review state")
        self.review_filter.setToolTip("Choose whether to include problems you have already reviewed or hidden.")
        filters.addWidget(self.review_filter)
        self.filter_button = QPushButton("More filters…", objectName="secondaryButton")
        self.filter_button.setIcon(icon("settings"))
        self.filter_button.clicked.connect(self._edit_filters)
        filters.addWidget(self.filter_button)
        self.filters_widget = QWidget()
        self.filters_widget.setLayout(filters)
        self.filters_widget.hide()
        results_layout.insertWidget(1, self.filters_widget)
        self.start_here_card = QFrame(objectName="startHereCard")
        start_layout = QHBoxLayout(self.start_here_card)
        start_layout.setContentsMargins(12, 10, 12, 10)
        start_text = QVBoxLayout()
        start_text.setSpacing(4)
        self.start_here_title = QLabel(objectName="detailFieldTitle")
        self.start_here_title.setTextFormat(Qt.TextFormat.PlainText)
        self.start_here_title.setWordWrap(True)
        self.start_here_explanation = QLabel(objectName="mutedLabel")
        self.start_here_explanation.setTextFormat(Qt.TextFormat.PlainText)
        self.start_here_explanation.setWordWrap(True)
        start_text.addWidget(self.start_here_title)
        start_text.addWidget(self.start_here_explanation)
        start_layout.addLayout(start_text, 1)
        self.start_here_button = QPushButton("Understand this problem", objectName="secondaryButton")
        self.start_here_button.setIcon(icon("arrow"))
        self.start_here_button.clicked.connect(self._open_start_here)
        start_layout.addWidget(self.start_here_button)
        self._start_here_incident = None
        self.start_here_card.hide()
        results_layout.insertWidget(2, self.start_here_card)
        self.filter_feedback = QFrame(objectName="filterFeedback")
        no_matches = QHBoxLayout(self.filter_feedback)
        no_matches.addWidget(QLabel("No problems match these filters. Try a different search."), 1)
        self.clear_filters_button = QPushButton("Show all problems", objectName="secondaryButton")
        self.clear_filters_button.clicked.connect(self._show_all_problems)
        no_matches.addWidget(self.clear_filters_button)
        self.filter_feedback.hide()
        results_layout.insertWidget(3, self.filter_feedback)
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
        self._setup_productivity(results_layout)
        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction(icon("help"), "Quick guide", self.help_button.click)
        help_menu.addAction(icon("download"), "Check for updates…", self._open_updates)
        self.feedback_action = help_menu.addAction("Report a problem…", self._report_problem)
        self.privacy_action = help_menu.addAction("Privacy and local data…", self._open_privacy)
        for menu in self.menuBar().findChildren(QMenu):
            for action in menu.actions():
                if not action.isSeparator() and action.icon().isNull():
                    action.setIcon(icon(action_icon_name(action.text())))
        for action in self.menuBar().actions():
            action.setIcon(QIcon())
        if scope_warning:
            self.warning_label.setText(scope_warning)
            self.warning_label.show()

    def _report_problem(self):
        from ui.feedback_dialog import FeedbackDialog
        FeedbackDialog(self.status_label.text(), self._selected_incidents(), self).exec()

    def _privacy_busy(self):
        return (self._scan_thread is not None or self._analysis_thread is not None or self.monitor.active
                or self.monitor.thread is not None
                or (self._settings_dialog is not None and self._settings_dialog._test_thread is not None))

    def _open_privacy(self):
        from ui.privacy_dialog import PrivacyDialog
        dialog = PrivacyDialog(self.history_store, self._privacy_busy, self)
        dialog.history_cleared.connect(self._history_deleted)
        dialog.ai_disabled.connect(self._privacy_disabled_ai)
        dialog.exec()
        self.incident_detail.refresh_ai_model_hint()
        if self._settings_dialog is not None:
            self._settings_dialog._refresh_key_status()

    def _privacy_disabled_ai(self):
        if self._settings_dialog is not None:
            self._settings_dialog.enabled_checkbox.setChecked(False)
            self._settings_dialog.key_input.clear()

    def _history_deleted(self):
        self._clear_notification_batch()
        self._pending_monitor_refresh = self._pending_monitor_open = False
        self._latest_monitor_result = None
        self._unread_monitor_incidents = 0
        self._search_text.clear()
        for action in (self.monitor_latest_action, self.monitor_events_action, self.collected_events_action,
                       self.timeline_action, self.export_visible_action):
            action.setEnabled(False)
        self._clear_incidents()
        self._current_result = None
        self._history_scan_id = None
        self._check_again_context = None
        self._focused_scan_options = None
        self._remember_review_undo([])
        self._group_members.clear()
        self._expanded_groups.clear()
        self.comparison_button.hide()
        self._displayed_stamp = None
        self.monitor.last_result = None
        self.monitor.last_success = self.monitor.last_finished = None
        self.monitor.last_error = ""
        self.monitor.health_changed.emit()
        for label in (self.incident_total, self.attention_total, self.event_total):
            label.setText("0")
        self.result_count.setText("NO RESULTS")
        self.last_scan.setText("No scan loaded")
        self.warning_label.clear()
        self.warning_label.setToolTip("")
        self.warning_label.hide()
        self.journey.set_step(0)
        self._set_state("ready", "READY")
        self.status_label.setText("Local history deleted. Exported reports remain on disk.")
        self._show_empty_state("✓", "History cleared", "Run a new scan when you are ready.")

    def _build_workspace_menus(self):
        """Expose history/import/export and review actions without crowding the header."""
        menu = self.menuBar().addMenu("File")
        self.file_menu = menu
        self.admin_action = menu.addAction(icon("key"), "Restart as administrator…", self._restart_as_administrator)
        import sys
        self.admin_action.setEnabled(sys.platform == "win32" and self._elevated is not True)
        self.admin_action.setToolTip("Windows asks for permission. Monitoring stops; run a new scan after restart. Administrator access does not override every log's security policy.")
        menu.addSeparator()
        self.open_action = menu.addAction(icon("folder"), "Open report…", self._open_report)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.history_action = menu.addAction(icon("history"), "Scan history…", self._open_history)
        menu.addSeparator()
        self.export_action = menu.addAction("Export selected incidents…", self._export_selection)
        self.export_action.setEnabled(False)
        self.export_visible_action = menu.addAction("Export filtered incidents…", self._export_visible)
        self.export_visible_action.setEnabled(False)
        view = self.menuBar().addMenu("View")
        self.view_menu = view
        self.timeline_action = view.addAction("Timeline of filtered incidents…", self._show_timeline)
        self.timeline_action.setEnabled(False)
        self.collected_events_action = view.addAction(icon("clock"), "All collected events…", self._show_collected_events)
        self.collected_events_action.setEnabled(False)
        view.addAction("Clear filters", self._clear_filters)
        self.motion_action = QAction("Enable animations", self, checkable=True)
        view.addAction(self.motion_action)
        self.sound_action = QAction("Sound effects", self, checkable=True)
        self._sync_sound_action(self.sounds.preferences.enabled())
        self.sound_action.toggled.connect(self._set_sound_enabled)
        view.addAction(self.sound_action)
        view.addAction(icon("terminal"), "Terminal settings…", self._open_terminal_settings)
        review = self.menuBar().addMenu("Review")
        self.review_menu = review
        self.review_actions = []
        for text, state in (("Acknowledge selected", "acknowledged"),
                            ("Ignore selected", "ignored"), ("Restore selected", "active")):
            action = review.addAction(text)
            action.triggered.connect(lambda _checked=False, state=state: self._review_rows(state))
            action.setEnabled(False)
            self.review_actions.append(action)

    def _update_scope_label(self):
        """Describe the pending scope independently of the currently displayed report."""
        self.scope_label.setText(f"Last {self.scan_options.hours:g} hours · "
                                 f"{len(self.scan_options.log_types)} Windows logs")
        self.scope_label.setToolTip("\n".join(self.scan_options.log_types))
        self.scan_goal.blockSignals(True)
        self.scan_goal.setCurrentIndex(self.scan_goal.findData(self.scan_options.profile))
        self.scan_goal.blockSignals(False)
        self.scan_goal_hint.setText(SCAN_GOALS[self.scan_options.profile][1])
        self.scan_goal.setToolTip(self.scan_goal_hint.text())

    def _choose_scan_goal(self):
        """Stage a symptom's source logs without starting a scan or changing evidence."""
        if not self.scan_goal.isEnabled():
            return
        profile = self.scan_goal.currentData()
        if profile == "custom":
            self._update_scope_label()
            self._edit_scan_scope()
            return
        self.scan_options = replace(self.scan_options, profile=profile, log_types=PROFILES[profile][1])
        self._save_scan_scope()

    def _edit_scan_scope(self):
        """Save valid scope controls for later desktop scans and later launches."""
        dialog = ScanOptionsDialog(self.scan_options, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.scan_options = dialog.options
        self._save_scan_scope()

    def _save_scan_scope(self):
        """Persist a validated scope shared by symptom choices and advanced options."""
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
        for control in (self.scope_button, self.open_action, self.history_action,
                        self.history_button, self.open_report_button, self.updates_button):
            control.setEnabled(not busy)
        if hasattr(self, "monitor"):
            self.scope_button.setEnabled(not busy and not self.monitor.active and self.monitor.thread is None)
            self.monitor_start_action.setEnabled(not busy and not self.monitor.active and self.monitor.thread is None)
            self.monitor_latest_action.setEnabled(not busy and self._latest_monitor_result is not None)
            self.monitor_events_action.setEnabled(not busy and self._latest_monitor_result is not None)
            self.monitor_button.setEnabled(self.monitor.active or not busy)
        self.scan_goal.setEnabled(self.scope_button.isEnabled())
        self.incident_detail.analyze_button.setEnabled(not busy and self.incident_detail.incident is not None)
        self.incident_detail.check_again_button.setEnabled(not busy and self.incident_detail.incident is not None)
        self.motion.set_active(busy)

    def _sync_sound_action(self, enabled):
        self.sound_action.blockSignals(True)
        self.sound_action.setChecked(enabled)
        self.sound_action.setIcon(icon("sound" if enabled else "muted"))
        self.sound_action.blockSignals(False)

    def _set_sound_enabled(self, enabled):
        try:
            self.sounds.preferences.save(enabled)
        except Exception:
            self.status_label.setText("Could not save sound preferences. Check your user profile permissions.")
        self._sync_sound_action(self.sounds.preferences.enabled())

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
        self.comparison_button.setVisible(bool(comparison.get("comparable")))
        if comparison.get("comparable"):
            self.comparison_button.setText(
                f"Changes: {comparison.get('new_count', 0)} new · "
                f"{comparison.get('persistent_count', 0)} persistent · "
                f"{comparison.get('no_longer_observed_count', 0)} no longer observed   →")
        else:
            self.comparison_button.setText("Scan comparison: no matching complete baseline yet")
        self.export_visible_action.setEnabled(bool(result))
        self.timeline_action.setEnabled(bool(result))
        self.collected_events_action.setEnabled(getattr(result, "collected_events", None) is not None)
        self._apply_filters()

    def _show_timeline(self):
        """Open chronological source evidence for the currently filtered incident rows."""
        TimelineDialog(self._selected_incidents(visible=True), self).exec()

    def _show_collected_events(self):
        """Review all levels in the opened scan, including scans without problems."""
        result = self._current_result
        events = getattr(result, "collected_events", None)
        if events is not None:
            TimelineDialog(result, self, collected_events=events).exec()

    def _setup_monitoring(self):
        """Create inactive monitoring controls and a session-only tray indicator."""
        self._latest_monitor_result = None
        self._monitor_notifications = False
        self._pending_monitor_refresh = False
        self._pending_monitor_open = False
        self._unread_monitor_incidents = 0
        self.monitor = MonitorController(self.history_store, self._can_monitor_scan, self)
        self.monitor.completed.connect(self._monitor_completed)
        self.monitor.busy_changed.connect(self._monitor_busy)
        self.monitor.stopped.connect(self._monitor_stopped)
        menu = self.menuBar().addMenu("Monitor")
        self.monitor_menu = menu
        self.monitor_start_action = menu.addAction("Start live monitoring…", self._start_monitoring)
        self.monitor_stop_action = menu.addAction("Stop monitoring", self.monitor.stop)
        self.monitor_stop_action.setEnabled(False)
        self.monitor_latest_action = menu.addAction("View latest monitoring scan", self._view_monitor_result)
        self.monitor_latest_action.setEnabled(False)
        self.monitor_events_action = menu.addAction(icon("clock"), "View collected events…", self._view_monitor_events)
        self.monitor_events_action.setEnabled(False)
        self.monitor_label = QLabel("Live updates off")
        self.monitor_label.setTextFormat(Qt.TextFormat.PlainText)
        self.monitor_label.setMinimumWidth(0)
        self.monitor_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.statusBar().addWidget(self.monitor_label, 1)
        self.monitor_notice = QPushButton(objectName="secondaryButton")
        self.monitor_notice.setToolTip("Open the latest monitoring results. Your current filters still apply.")
        self.monitor_notice.clicked.connect(self._view_monitor_result)
        self.monitor_notice.hide()
        self.statusBar().addPermanentWidget(self.monitor_notice)
        self.monitor_button = QPushButton("Watch for new incidents", objectName="secondaryButton")
        self.monitor_button.setIcon(icon("eye"))
        self.monitor_button.setToolTip("Start periodic local scans, automatic result refresh, and optional desktop notifications.")
        self.monitor_button.clicked.connect(self._toggle_monitoring)
        self.statusBar().addPermanentWidget(self.monitor_button)
        self.monitor.status.connect(self.monitor_label.setText)
        self.monitor.status.connect(self.monitor_label.setToolTip)
        self.monitor_tray = QSystemTrayIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self)
        self.monitor_tray.setToolTip("LogOpen monitoring")
        self.monitor_tray.messageClicked.connect(self._view_monitor_result)

    def _toggle_monitoring(self):
        """Expose monitoring beside its live status, including cancellation while polling."""
        if self.monitor.active:
            self.monitor.stop()
        elif self.monitor.thread is None:
            self._start_monitoring()

    def _can_monitor_scan(self):
        """Defer background polls while foreground work or modal dialogs own the UI."""
        return (self._scan_thread is None and self._analysis_thread is None
                and self._settings_dialog is None and not self._close_pending
                and QApplication.activeModalWidget() is None)

    def _start_monitoring(self):
        """Start only after the user chooses the polling interval and notifications."""
        if self.monitor.active or self.monitor.thread is not None:
            return
        dialog = MonitorOptionsDialog(self)
        dialog.notifications.setChecked(str(self._ui_settings.value("monitor/desktop_notifications", True)).lower() in ("true", "1"))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._monitor_notifications = dialog.notifications.isChecked()
        self._ui_settings.setValue("monitor/desktop_notifications", self._monitor_notifications)
        self._ui_settings.sync()
        self._clear_notification_batch()
        self._latest_monitor_result = None
        self._unread_monitor_incidents = 0
        self.monitor_notice.hide()
        self._pending_monitor_refresh = False
        self._pending_monitor_open = False
        self.statusBar().show()
        self.monitor_start_action.setEnabled(False)
        self.monitor_stop_action.setEnabled(True)
        self.scope_button.setEnabled(False)
        self.scan_goal.setEnabled(False)
        self.monitor_button.setText("Stop watching")
        self.monitor_button.setIcon(icon("stop"))
        if self._monitor_notifications and QSystemTrayIcon.isSystemTrayAvailable():
            self.monitor_tray.show()
        self.monitor.start(self.scan_options, dialog.interval.value(), collect_all_events=dialog.all_events.isChecked())

    def _monitor_busy(self, busy):
        """Prevent overlapping foreground scans/AI while a monitoring worker runs."""
        self.scan_button.setEnabled(not busy)
        self.settings_button.setEnabled(not busy)
        self._workspace_busy(busy)
        self.incident_detail.analyze_button.setEnabled(not busy and self.incident_detail.incident is not None)
        self.monitor_latest_action.setEnabled(not busy and self._latest_monitor_result is not None)
        self.monitor_events_action.setEnabled(not busy and self._latest_monitor_result is not None)
        self.motion.set_active(busy)
        if self.monitor.active:
            self.monitor_button.setText("Stop watching")
        elif busy:
            self.monitor_button.setText("Stopping…")
            self.monitor_button.setEnabled(False)
        if not busy:
            self._flush_monitor_updates()

    def _monitor_completed(self, result, alerts):
        """Refresh the dashboard and announce new evidence without interrupting a detail page."""
        if self._close_pending or getattr(result, "cancelled", False):
            return
        self._latest_monitor_result = result
        try:
            self.history_store.apply_states(result)
        except Exception:
            if "Review states could not be loaded." not in result.warnings:
                result.warnings.append("Review states could not be loaded.")
        alerts = [incident for incident in alerts if incident.get("review_state", "active") == "active"]
        self._pending_monitor_refresh = True
        if self.page_stack.currentWidget() is self.dashboard:
            self._refresh_monitor_result()
        if alerts:
            self._unread_monitor_incidents += len(alerts)
            count = self._unread_monitor_incidents
            self.monitor_notice.setText(f"{count} new {'incident' if count == 1 else 'incidents'} · View")
            self.monitor_notice.setAccessibleName(self.monitor_notice.text())
            self.monitor_notice.show()
        self._queue_notifications(alerts)

    def _refresh_monitor_result(self):
        """Replace the results snapshot while retaining filters, sort, selection, and scroll."""
        result = self._latest_monitor_result
        if result is None:
            return
        selected = {(incident_key(item), str(item.get("time_generated_start", "")))
                    for item in self._selected_incidents()}
        scroll = self.incident_table.verticalScrollBar().value()
        horizontal = self.incident_table.horizontalScrollBar().value()
        try:
            self.history_store.apply_states(result)
        except Exception:
            if "Review states could not be loaded." not in result.warnings:
                result.warnings.append("Review states could not be loaded.")
        self._show_scan_results(result)
        for row in range(self.incident_table.rowCount()):
            item = self.incident_table.item(row, 0)
            incident = self._incidents[item.data(Qt.ItemDataRole.UserRole)]
            if not self.incident_table.isRowHidden(row) and (
                incident_key(incident), str(incident.get("time_generated_start", ""))
            ) in selected:
                self.incident_table.setCurrentCell(row, 0, QItemSelectionModel.SelectionFlag.Select |
                                                   QItemSelectionModel.SelectionFlag.Rows)
        self.incident_table.verticalScrollBar().setValue(scroll)
        self.incident_table.horizontalScrollBar().setValue(horizontal)
        self._pending_monitor_refresh = False

    def _flush_monitor_updates(self):
        """Honor a notification click once foreground work and the polling thread are idle."""
        if (self._close_pending or self._scan_thread is not None or self._analysis_thread is not None
                or self.monitor.thread is not None):
            return
        if self._pending_monitor_open:
            self._view_monitor_result()

    def _view_monitor_events(self):
        """Open the latest monitoring evidence without replacing the current report."""
        result = self._latest_monitor_result
        if (result is not None and self._scan_thread is None and self._analysis_thread is None
                and self.monitor.thread is None):
            events = getattr(result, "collected_events", None)
            TimelineDialog(result, self, collected_events=events).exec()

    def _view_monitor_result(self):
        """Open live results, deferring a click delivered before worker cleanup."""
        if self._latest_monitor_result is None:
            return
        self._pending_monitor_open = True
        if self._scan_thread is None and self._analysis_thread is None and self.monitor.thread is None and not self._close_pending:
            self._pending_monitor_open = False
            self._refresh_monitor_result()
            self._unread_monitor_incidents = 0
            self.monitor_notice.hide()
            if self.isMinimized():
                self.showNormal()
            else:
                self.show()
            self.raise_()
            self.activateWindow()

    def _monitor_stopped(self):
        """Restore controls after cancellation cleanup, or complete a deferred close."""
        busy = self._scan_thread is not None or self._analysis_thread is not None
        self.monitor_start_action.setEnabled(not busy)
        self.monitor_stop_action.setEnabled(False)
        self.scope_button.setEnabled(not busy)
        self.scan_goal.setEnabled(not busy)
        self.monitor_button.setEnabled(not busy)
        self.monitor_button.setText("Watch for new incidents")
        self.monitor_button.setIcon(icon("eye"))
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

    def _show_all_problems(self):
        """Recover an empty filtered view, including problems hidden by review state."""
        self._clear_filters()
        self.review_filter.setCurrentIndex(1)

    def _matches_filters(self, incident):
        """Match literal text, priority, review state, and inclusive incident-start dates."""
        query = self.search_input.text().strip().casefold()
        if query and query not in self._search_text.get(id(incident), ""):
            return False
        priority = self.priority_filter.currentText()
        if priority == "High and Critical":
            if incident.get("incident_priority") not in ("High", "Critical"):
                return False
        elif priority != "All priorities" and incident.get("incident_priority") != priority:
            return False
        state = incident.get("review_state", "active")
        selected = self.review_filter.currentData()
        if (selected == "visible" and state in ("ignored", "snoozed")) or (
            selected not in ("visible", "all") and state != selected
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
        if hasattr(self, "group_action"):
            self._refresh_groups()
        if any(table.isRowHidden(index.row()) for index in table.selectionModel().selectedRows()):
            table.clearSelection()
        self.result_count.setText(f"{visible} FOUND" if visible == len(self._incidents)
                                 else f"{visible} / {len(self._incidents)} SHOWN")
        if hasattr(self, "group_action") and self.group_action.isChecked() and visible:
            self.result_count.setText(f"{len(self._group_members)} GROUPS · {visible} INCIDENTS")
        self.filter_button.setText("Filters active…" if any(self._extra_filters.get(key) for key in
                                  ("provider", "classification", "dates")) else "More filters…")
        self.export_visible_action.setEnabled(visible > 0)
        if hasattr(self, "support_filtered_action"):
            self.support_filtered_action.setEnabled(visible > 0)
        self.filter_feedback.setVisible(bool(self._incidents) and visible == 0)
        self._update_copy_action()
        self._update_start_here()
        if hasattr(self, "filter_chips"):
            self._refresh_filter_chips()

    def _update_start_here(self):
        """Recommend the highest-priority active incident within the visible results."""
        visible = self._selected_incidents(visible=True)
        candidates = [incident for incident in visible
                      if incident.get("review_state", "active") == "active"]
        ranks = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
        self._start_here_incident = max(candidates, key=lambda item: (
            ranks.get(item.get("incident_priority"), 0), numeric_key(item.get("incident_score")),
        ), default=None)
        tight = self.height() < (760 if getattr(self, "_text_size", "normal") == "normal" else 950)
        self.start_here_card.setVisible(bool(visible) and not tight)
        self.start_here_button.setVisible(self._start_here_incident is not None)
        if self._start_here_incident is None:
            self.start_here_title.setText("No unreviewed problems in this view")
            self.start_here_explanation.setText("Change the filters to see other problems, or start another scan.")
            return
        incident = self._start_here_incident
        classification = str(incident.get("incident_classification") or "Unclassified problem")
        priority = str(incident.get("incident_priority") or "Unknown")
        self.start_here_title.setText(f"Start here · {priority} priority · {classification}")
        self.start_here_explanation.setText(problem_explanation(classification) or
                                          "Windows recorded this activity. Open the summary for checks you can try.")
        self.start_here_title.setToolTip(self.start_here_explanation.text())

    def _open_start_here(self):
        """Select the recommended source object even after sorting, then open its details."""
        self._update_start_here()
        if self._start_here_incident is not None and self.group_action.isChecked():
            self._expanded_groups.add(incident_key(self._start_here_incident))
            self._apply_filters()
        for row in range(self.incident_table.rowCount()):
            item = self.incident_table.item(row, 0)
            if item is not None and self._incidents[item.data(Qt.ItemDataRole.UserRole)] is self._start_here_incident:
                self.incident_table.clearSelection()
                self.incident_table.setCurrentCell(row, 0)
                self._open_incident_detail()
                return

    def _selected_incidents(self, visible=False):
        """Resolve selected or visible rows to source objects in current display order."""
        table = self.incident_table
        rows = range(table.rowCount()) if visible else sorted(index.row() for index in
                                                             table.selectionModel().selectedRows())
        indices = []
        for row in rows:
            item = table.item(row, 0)
            if item is None:
                continue
            index = item.data(Qt.ItemDataRole.UserRole)
            incident = self._incidents[index]
            if visible:
                if self._matches_filters(incident):
                    indices.append(index)
            elif not table.isRowHidden(row):
                indices.append(index)
                if hasattr(self, "group_action") and self.group_action.isChecked():
                    key = incident_key(incident)
                    if key not in self._expanded_groups:
                        indices.extend(self._group_members.get(key, []))
        return [self._incidents[index] for index in dict.fromkeys(indices)]

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
        self._check_again_context = None
        self.status_label.setText(f"{message} · {len(result)} incidents")
        self._update_relative_scan_time()
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
            snapshots = [self.history_store.issue_preferences(incident) for incident in incidents]
            for incident in incidents:
                self.history_store.set_state(incident, state)
            self.history_store.apply_states(self._incidents)
        except Exception:
            self.incident_detail.action_feedback.setText("Could not save the review state.")
            self.incident_detail.review_badge.setText("Review state could not be saved")
            self.status_label.setText("Could not save the review state. Check local application data access.")
            return
        self._remember_review_undo(snapshots)
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
            self.incident_detail.action_feedback.setText("Enable AI in Settings to get an explanation.")
            return
        from ui.ai_preview import AIPrivacyPreview
        preview = AIPrivacyPreview(incident, preferences, build_scan_context(self._current_result), self)
        if preview.exec() != QDialog.DialogCode.Accepted or preview.request is None:
            return
        if self._scan_thread is not None or self._analysis_thread is not None or self.monitor.thread is not None or self._close_pending:
            self.incident_detail.action_feedback.setText("A task started while reviewing. Wait for it to finish and try again.")
            return
        self._analysis_target = incident
        self._analysis_thread = QThread(self)
        self._analysis_worker = AnalysisWorker(preview.incident, preferences, preview.scan_context, preview.request)
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
            incident["llm_prompt_version"] = config.ANALYSIS_PROMPT_VERSION
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

        if not self._close_pending:
            self.sounds.play("success" if outcome.status == "completed" else "attention")

    def _manual_analysis_finished(self):
        """Restore controls only after the analysis thread exits; honor deferred closing."""
        self._analysis_worker = None
        self._analysis_thread = None
        self._analysis_target = None
        self.scan_button.setEnabled(True)
        self.settings_button.setEnabled(True)
        self.incident_detail.analyze_button.setText("Explain with AI / retry")
        self._workspace_busy(False)
        if self._close_pending:
            self.close()
        else:
            self._flush_monitor_updates()
