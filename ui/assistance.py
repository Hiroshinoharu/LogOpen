"""Connect case tools and notification preferences to the desktop workspace."""

from datetime import datetime, timezone
from time import monotonic

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QPushButton, QSystemTrayIcon

from history_store import incident_key
from notification_preferences import NotificationPreferences
from notification_delivery import notification_environment
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
        self._notification_feedback = ""
        self._notification_environment = notification_environment()
        self._notification_environment_checked = monotonic()
        self._notification_batch = {}
        self._notification_timer = QTimer(self)
        self._notification_timer.setSingleShot(True)
        self._notification_timer.timeout.connect(self._flush_notification_batch)
        self.monitor.stopped.connect(self._clear_notification_batch)
        self.monitor.health_changed.connect(self._refresh_monitor_health)
        self.monitor_menu.addAction("Notification preferences…", self._edit_notifications)
        self.test_notification_action = self.monitor_menu.addAction("Test desktop notification", self._test_notification)
        self.monitor_menu.addAction("Windows notification settings…", self._open_notification_settings)
        self.monitor_menu.addAction("Monitoring health…", self._show_monitor_health)
        self.monitor_health_button = QPushButton("Health: Off", objectName="secondaryButton")
        self.monitor_health_button.clicked.connect(self._show_monitor_health)
        self.statusBar().addPermanentWidget(self.monitor_health_button)
        self.monitoring_status.test_requested.connect(self._test_notification)
        self.monitoring_status.preferences_requested.connect(self._edit_notifications)
        self._monitor_presentation_timer = QTimer(self)
        self._monitor_presentation_timer.setInterval(1000)
        self._monitor_presentation_timer.timeout.connect(self._refresh_monitor_presentation)
        self._monitor_presentation_timer.start()
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
        if self.monitor.active and QSystemTrayIcon.isSystemTrayAvailable():
            self.monitor_tray.show()
        else:
            self.monitor_tray.hide()
        self._notification_feedback = ""
        self._refresh_monitor_presentation()

    def _notification_status_text(self):
        """Explain the actual alert policy separately from whether scans are running."""
        if not self._monitor_notifications:
            return "Desktop alerts are off in LogOpen."
        preferences = self.notification_preferences
        if preferences.is_quiet():
            if preferences.quiet_start == preferences.quiet_end:
                return "Notifications paused all day · quiet hours."
            hour, minute = divmod(preferences.quiet_end, 60)
            return f"Notifications paused until {hour:02}:{minute:02} · quiet hours."
        if not preferences.priorities:
            return "Notifications paused · no priorities selected."
        environment = self._notification_environment
        if environment.state == "suppressed":
            return "Desktop alerts paused by Windows. " + environment.message
        if not self.monitor_tray.isSystemTrayAvailable():
            return "Desktop notifications unavailable · no system tray."
        if not self.monitor_tray.supportsMessages():
            return "Desktop notifications are not supported by this desktop."
        grouping = f" Grouped for {preferences.batch_seconds} seconds." if preferences.batch_seconds else ""
        return "Desktop alerts on · new problems after the first scan." + grouping

    def _refresh_monitor_presentation(self):
        if not hasattr(self, "notification_preferences"):
            return
        if monotonic() - self._notification_environment_checked >= 15:
            self._notification_environment = notification_environment()
            self._notification_environment_checked = monotonic()
        monitor = self.monitor
        running = monitor.active or monitor.thread is not None
        baseline = bool(getattr(getattr(monitor, "tracker", None), "initialized", False))
        tone = "active" if monitor.active else "off"
        if monitor.thread is not None and not monitor.active:
            title, detail, tone = "Stopping live monitoring", "Waiting for the current Windows log read to finish.", "warning"
        elif monitor.active and monitor.thread is not None:
            title = "Live monitoring · scanning" if baseline else "Live monitoring · first scan (silent)"
            detail = "Checking for new problems." if baseline else "Establishing a silent baseline. Existing problems do not trigger alerts."
        elif monitor.active and monitor.waiting:
            title, detail, tone = "Live monitoring · waiting", "The next scan starts after the current task or dialog finishes.", "warning"
        elif monitor.active and monitor.last_error:
            title, detail, tone = "Live monitoring · scan failed", monitor.last_error + " Monitoring will retry.", "error"
        elif monitor.active and monitor.last_result is not None and not monitor.last_result.complete:
            title, detail, tone = "Live monitoring · partial coverage", "Some logs could not be fully checked. Open Health for the gaps.", "warning"
        elif monitor.active and monitor.last_result is None:
            title, detail = "Live monitoring · starting", "The first scan establishes a silent baseline; alerts begin with new problems afterward."
        elif monitor.active:
            title = "Live monitoring is on"
            due = monitor.next_due
            if due is not None:
                remaining = max(0, round((due - datetime.now(timezone.utc)).total_seconds()))
                detail = f"Baseline complete · next scan in {remaining} seconds. Keep LogOpen open."
            else:
                detail = "Baseline complete · waiting for the next scan. Keep LogOpen open."
        else:
            title, detail = "Live monitoring is off", "Automatic scans run only while monitoring is on and LogOpen stays open."
        notification_text = self._notification_status_text()
        if self._notification_feedback:
            notification_text += " " + self._notification_feedback
        if self._monitor_notifications and (self.notification_preferences.is_quiet() or not self.notification_preferences.priorities
                                           or self._notification_environment.state == "suppressed") and tone == "active":
            tone = "warning"
        was_visible = not self.monitoring_status.isHidden()
        show_status = running or bool(self._notification_feedback)
        self.monitoring_status.update_status(title, detail, notification_text, tone=tone, active=monitor.active)
        self.monitoring_status.setVisible(show_status)
        self.live_button.setText("Monitoring on" if monitor.active else "Stopping…" if running else "Live monitoring")
        if self.live_button.property("active") != monitor.active:
            self.live_button.setProperty("active", monitor.active)
            self.live_button.style().unpolish(self.live_button)
            self.live_button.style().polish(self.live_button)
        self.live_button.setAccessibleDescription(title + ". " + notification_text)
        self.setWindowTitle(self._base_window_title + (" · Live monitoring" if running else ""))
        tray_hint = title + "\n" + notification_text
        if self.monitor_tray.toolTip() != tray_hint:
            self.monitor_tray.setToolTip(tray_hint)
        # A countdown tick must not rebuild filter buttons or steal their focus.
        if was_visible != show_status:
            self._update_layout_density()

    def _send_desktop_notification(self, title, message, *, test=False):
        """Request a native banner; Qt provides no acknowledgment that Windows displayed it."""
        self._notification_environment = notification_environment()
        self._notification_environment_checked = monotonic()
        environment = self._notification_environment
        if environment.state == "suppressed":
            self._notification_feedback = "Windows is currently suppressing banners. " + environment.message
            return False
        if not self.monitor_tray.isVisible():
            if not self.monitor_tray.isSystemTrayAvailable():
                self._notification_feedback = "No system tray is available for desktop notifications."
                return False
            self.monitor_tray.show()
        if not self.monitor_tray.supportsMessages():
            self._notification_feedback = "This desktop does not support notification banners."
            return False
        try:
            self.monitor_tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 10000)
        except Exception:
            self._notification_feedback = "The desktop notification request failed. Try Windows notification settings."
            return False
        if test:
            self._notification_feedback = "Test requested · Windows display is not confirmed."
        else:
            self._notification_feedback = "Alert requested · Windows display is not confirmed."
        return True

    def _test_notification(self):
        """Send one user-requested sample without changing quiet hours or alert preferences."""
        sent = self._send_desktop_notification(
            "LogOpen: notification test",
            "This is a test from LogOpen. Real alerts report new problems after the first monitoring scan. "
            "This test bypasses LogOpen quiet hours; your saved preferences are unchanged.", test=True)
        self._refresh_monitor_presentation()
        return sent

    def _open_notification_settings(self):
        opened = QDesktopServices.openUrl(QUrl("ms-settings:notifications"))
        self._notification_feedback = ("Windows notification settings opened." if opened else
                                       "Could not open Windows settings. Open Settings → System → Notifications.")
        self._refresh_monitor_presentation()

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
        if self._monitor_notifications:
            self._send_desktop_notification("LogOpen: new incidents",
                f"New activity in {len(incidents)} {'problem' if len(incidents) == 1 else 'problems'}. Open LogOpen to review the evidence.",
            )
        self._refresh_monitor_presentation()

    def _refresh_monitor_health(self):
        state, text = health_text(self.monitor)
        short = {"Waiting for the current task": "Waiting", "Last scan failed": "Error", "Coverage gaps": "Gaps"}.get(state, state)
        self.monitor_health_button.setText("Health: " + short)
        self.monitor_health_button.setProperty("fullText", "Health: " + short)
        from html import escape
        self.monitor_health_button.setToolTip(escape(text).replace("\n", "<br>"))
        self.monitor_health_button.setAccessibleDescription(text)
        self._update_statusbar_labels()
        self._refresh_monitor_presentation()

    def _show_monitor_health(self):
        MonitorHealthDialog(self.monitor, self).exec()
