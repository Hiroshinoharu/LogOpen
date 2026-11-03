"""Notification controls and a live monitoring-health view."""

from PyQt6.QtCore import QTime, QTimer
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                            QHBoxLayout, QPlainTextEdit, QSpinBox, QTimeEdit, QVBoxLayout)
from notification_preferences import NotificationPreferences, PRIORITIES
from ui.case_tools import body, finish
from ui.icons import decorate_buttons


class NotificationOptionsDialog(QDialog):
    def __init__(self, preferences, desktop=True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Notification preferences")
        self.resize(570, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(body("Choose which new incidents can make a sound or show a desktop alert. All incidents still appear in the app and scans continue during quiet hours. Sound also follows View → Sound effects."))
        self.desktop = QCheckBox("Show desktop notifications")
        self.desktop.setChecked(desktop)
        layout.addWidget(self.desktop)
        priorities = QHBoxLayout()
        self.priorities = {}
        for name in PRIORITIES:
            box = QCheckBox(name)
            box.setChecked(name in preferences.priorities)
            self.priorities[name] = box
            priorities.addWidget(box)
        layout.addLayout(priorities)
        self.quiet = QCheckBox("Use quiet hours (this computer's local time)")
        self.quiet.setChecked(preferences.quiet)
        layout.addWidget(self.quiet)
        form = QFormLayout()
        self.start = QTimeEdit(QTime(preferences.quiet_start // 60, preferences.quiet_start % 60))
        self.end = QTimeEdit(QTime(preferences.quiet_end // 60, preferences.quiet_end % 60))
        for control in (self.start, self.end):
            control.setDisplayFormat("HH:mm")
        form.addRow("Quiet from", self.start)
        form.addRow("Quiet until", self.end)
        self.batch = QSpinBox()
        self.batch.setRange(0, 600)
        self.batch.setSuffix(" seconds")
        self.batch.setSpecialValueText("One summary per scan")
        self.batch.setValue(preferences.batch_seconds)
        form.addRow("Combine alerts over", self.batch)
        layout.addLayout(form)
        layout.addWidget(body("Quiet hours can cross midnight. Identical start and end times silence alerts all day. Suppressed alerts are not replayed later."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        decorate_buttons(self)

    def preferences(self):
        return NotificationPreferences(tuple(name for name, box in self.priorities.items() if box.isChecked()),
                                       self.quiet.isChecked(), self.start.time().hour() * 60 + self.start.time().minute(),
                                       self.end.time().hour() * 60 + self.end.time().minute(), self.batch.value())


def health_text(monitor):
    if monitor.thread is not None:
        state = "Scanning" if monitor.active else "Stopping"
    elif not monitor.active:
        state = "Off"
    elif monitor.waiting:
        state = "Waiting for the current task"
    elif monitor.last_error:
        state = "Last scan failed"
    elif monitor.last_result is not None and not monitor.last_result.complete:
        state = "Coverage gaps"
    elif monitor.last_result is not None and monitor.last_result.warnings:
        state = "Warnings"
    elif monitor.last_result is not None:
        state = "Ready"
    else:
        state = "Starting"
    def stamp(value):
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z") if value else "None this session"
    due = stamp(monitor.next_due) if monitor.active and monitor.next_due else "Not scheduled"
    if monitor.waiting:
        due = "Waiting until the current task finishes"
    text = [f"Monitoring: {state}", f"Last complete scan: {stamp(monitor.last_success)}",
            f"Last result received: {stamp(monitor.last_finished)}", f"Next scan due: {due}"]
    if monitor.last_error:
        text.append("Last error: " + monitor.last_error)
    result = monitor.last_result
    if result is not None:
        text.append("Latest coverage: " + ("Complete" if result.complete else "Incomplete"))
        for name in result.scan_options.get("log_types", []):
            coverage = result.coverage.get(name, {})
            text.append(f"{name}: {'Complete' if coverage.get('complete') is True else 'Partial / unknown'}; "
                        f"{coverage.get('stop_reason', 'reason unavailable')}; {coverage.get('records_read', '?')} records read")
        text.extend(str(warning) for warning in result.warnings)
    text.append("Monitoring runs only while LogOpen is open. Timing may be delayed while another task runs or the PC sleeps.")
    return state, "\n".join(text)


class MonitorHealthDialog(QDialog):
    def __init__(self, monitor, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Monitoring health")
        self.resize(700, 450)
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(lambda: self.text.setPlainText(health_text(monitor)[1]))
        self.timer.start()
        self.text.setPlainText(health_text(monitor)[1])
        finish(self, layout)
