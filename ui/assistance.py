"""Connect case tools and notification preferences to the desktop workspace."""

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog, QPushButton, QSystemTrayIcon

from history_store import incident_key
from notification_preferences import NotificationPreferences
from ui.case_tools import (ChecklistDialog, CompareScansDialog, IssueHistoryDialog,
                           SimpleExplanationDialog, SupportReportDialog)
from ui.notification_options import MonitorHealthDialog, NotificationOptionsDialog, health_text


class AssistanceMixin:
    def _setup_assistance(self):
        self.incident_detail.simple_requested.connect(lambda: self._case_dialog(SimpleExplanationDialog))
        self.incident_detail.history_requested.connect(lambda: self._case_dialog(IssueHistoryDialog, stored=True))
        self.incident_detail.checklist_requested.connect(lambda: self._case_dialog(ChecklistDialog, stored=True))
        self.compare_scans_action = self.view_menu.addAction("Compare scans…", self._choose_comparison)
        self.compare_scans_action.setEnabled(False)
        self.support_action = self.file_menu.addAction("Prepare support report for selected incidents…", self._prepare_support_report)
        self.support_filtered_action = self.file_menu.addAction("Prepare support report for filtered incidents…", lambda: self._prepare_support_report(filtered=True))
        for action in (self.support_action, self.support_filtered_action):
            action.setEnabled(False)
        self.notification_preferences = NotificationPreferences.from_json(self._ui_settings.value("monitor/notification_preferences", "{}"))
        self._monitor_notifications = str(self._ui_settings.value("monitor/desktop_notifications", False)).lower() in ("true", "1")
        self._notification_batch = {}
        self._notification_timer = QTimer(self)
        self._notification_timer.setSingleShot(True)
        self._notification_timer.timeout.connect(self._flush_notification_batch)
        self.monitor.stopped.connect(self._clear_notification_batch)
        self.monitor.health_changed.connect(self._refresh_monitor_health)
        self.monitor_menu.addAction("Notification preferences…", self._edit_notifications)
        self.monitor_menu.addAction("Monitoring health…", self._show_monitor_health)
        self.monitor_health_button = QPushButton("Health: Off", objectName="secondaryButton")
        self.monitor_health_button.clicked.connect(self._show_monitor_health)
        self.statusBar().addPermanentWidget(self.monitor_health_button)
        self._refresh_monitor_health()

    def _case_dialog(self, dialog_type, *, stored=False):
        incident = self.incident_detail.incident
        if incident is None:
            return
        try:
            dialog = dialog_type(incident, self.history_store, self) if stored else dialog_type(incident, self)
            dialog.exec()
        except RuntimeError:
            self.incident_detail.note_feedback.setText("Could not read local troubleshooting history. Check access to your application data.")

    def _choose_comparison(self):
        if self._current_result is None:
            return
        try:
            CompareScansDialog(self._current_result, self.history_store, self).exec()
        except RuntimeError:
            self.status_label.setText("Could not load comparison baselines from local history.")

    def _prepare_support_report(self, *, filtered=False):
        incidents = self._selected_incidents(visible=filtered)
        if not incidents:
            return
        try:
            SupportReportDialog(incidents, self._current_result, self.history_store, self).exec()
        except RuntimeError:
            self.status_label.setText("Could not load local notes and checks for the support report.")

    def _edit_notifications(self):
        dialog = NotificationOptionsDialog(self.notification_preferences, self._monitor_notifications, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.notification_preferences = dialog.preferences()
        self._monitor_notifications = dialog.desktop.isChecked()
        self._ui_settings.setValue("monitor/notification_preferences", self.notification_preferences.to_json())
        self._ui_settings.setValue("monitor/desktop_notifications", self._monitor_notifications)
        self._ui_settings.sync()
        self._clear_notification_batch()
        if self.monitor.active and self._monitor_notifications and QSystemTrayIcon.isSystemTrayAvailable():
            self.monitor_tray.show()
        else:
            self.monitor_tray.hide()

    def _queue_notifications(self, alerts):
        for incident in alerts:
            if self.notification_preferences.allows(incident):
                self._notification_batch[incident_key(incident)] = incident
        if not self._notification_batch:
            return
        delay = self.notification_preferences.batch_seconds
        if delay == 0:
            self._flush_notification_batch()
        elif not self._notification_timer.isActive():
            self._notification_timer.start(delay * 1000)

    def _clear_notification_batch(self):
        self._notification_timer.stop()
        self._notification_batch.clear()

    def _flush_notification_batch(self):
        incidents = list(self._notification_batch.values())
        self._clear_notification_batch()
        if self._close_pending or (self.notification_preferences.batch_seconds and not self.monitor.active):
            return
        try:
            self.history_store.apply_states(incidents)
        except RuntimeError:
            # A delayed alert must not disregard a review decision made meanwhile.
            return
        incidents = [item for item in incidents if item.get("review_state", "active") == "active"
                     and self.notification_preferences.allows(item)]
        if not incidents:
            return
        self.sounds.play("attention")
        if self._monitor_notifications and self.monitor_tray.isVisible():
            self.monitor_tray.showMessage("LogOpen: new incidents",
                f"New activity in {len(incidents)} {'problem' if len(incidents) == 1 else 'problems'}. Open LogOpen to review the evidence.",
                QSystemTrayIcon.MessageIcon.Information, 8000)

    def _refresh_monitor_health(self):
        state, text = health_text(self.monitor)
        short = {"Waiting for the current task": "Waiting", "Last scan failed": "Error", "Coverage gaps": "Gaps"}.get(state, state)
        self.monitor_health_button.setText("Health: " + short)
        from html import escape
        self.monitor_health_button.setToolTip(escape(text).replace("\n", "<br>"))
        self.monitor_health_button.setAccessibleDescription(text)

    def _show_monitor_health(self):
        MonitorHealthDialog(self.monitor, self).exec()
