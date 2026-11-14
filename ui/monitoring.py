"""Opt-in polling while LogOpen is open, with cooperative thread-safe shutdown."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event
from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                            QFrame, QLabel, QScrollArea, QSpinBox, QVBoxLayout, QWidget)
from main import scan_system
from monitoring import AlertTracker
from ui.icons import decorate_buttons


class MonitorOptionsDialog(QDialog):
    """Choose polling frequency and OS notifications before starting monitoring."""

    def __init__(self, parent=None):
        """Explain baseline behavior and automatic-AI exclusion next to the controls."""
        super().__init__(parent)
        self.setWindowTitle("Start live monitoring")
        self.setMinimumWidth(430)
        self.resize(570, 500)
        layout = QVBoxLayout(self)
        scroll = QScrollArea(objectName="settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(objectName="settingsContent")
        fields = QVBoxLayout(content)
        note = QLabel("Run periodic local scans of the current scope while this window is open. "
                      "The first scan establishes a silent baseline. Later new active incidents at any priority can notify you. "
                      "The incident list refreshes after every scan. An open problem stays on screen until you return to results. "
                      "Automatic AI is off for monitoring. Results are saved to local history and the latest report.")
        note.setWordWrap(True)
        fields.addWidget(note)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.interval = QSpinBox()
        self.interval.setRange(30, 3600)
        self.interval.setValue(60)
        self.interval.setSuffix(" seconds")
        self.interval.setAccessibleName("Seconds to wait after each monitoring scan")
        form.addRow("&Wait after each scan", self.interval)
        self.notifications = QCheckBox("Show desktop notifications")
        self.notifications.setAccessibleDescription("Notify for new incidents, using your notification preferences.")
        self.notifications.setChecked(True)
        form.addRow(self.notifications)
        self.all_events = QCheckBox("Include routine activity")
        self.all_events.setChecked(True)
        self.all_events.setAccessibleName("Collect all event levels during monitoring")
        form.addRow(self.all_events)
        fields.addLayout(form)
        scope_note = QLabel("All levels are saved for the selected Windows logs within your scan's time window "
                            "and entry limit. Review them with Monitor → View collected events. "
                            "Routine activity does not become a problem or trigger an alert. "
                            "Choose priorities, quiet hours and combined alerts in Monitor → Notification preferences.")
        scope_note.setWordWrap(True)
        fields.addWidget(scope_note)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        decorate_buttons(self)


class MonitorWorker(QObject):
    """Run a local-only scan snapshot on a worker thread."""

    result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, options, history, token):
        """Force automatic AI off even when ordinary manual scans allow it."""
        super().__init__()
        self.options = replace(options, automatic_ai=False)
        self.history, self.token = history, token

    @pyqtSlot()
    def run(self):
        """Emit saved local results or a safe error, always finishing the thread."""
        try:
            self.result.emit(scan_system(self.options, history_store=self.history, cancel_event=self.token))
        except Exception:
            self.error.emit("Monitoring scan failed. Check the selected channels and permissions.")
        finally:
            self.finished.emit()


class MonitorController(QObject):
    """Schedule nonoverlapping scans; defer polling while another UI task is busy."""

    completed = pyqtSignal(object, object)
    status = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    stopped = pyqtSignal()
    health_changed = pyqtSignal()

    def __init__(self, history, can_scan, parent=None):
        """Keep the timer and scheduling state on the GUI thread; do not auto-start."""
        super().__init__(parent)
        self.history, self.can_scan = history, can_scan
        self.active = False
        self.last_success = self.last_finished = self.last_result = self.next_due = None
        self.last_error = ""
        self.waiting = False
        self.thread = self.worker = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._tick)

    def start(self, options, interval, *, collect_all_events=True):
        """Begin a fresh session with an immutable scope and a silent baseline scan."""
        if self.active or self.thread is not None:
            return
        if type(interval) is not int or not 30 <= interval <= 3600:
            raise ValueError("Monitoring interval must be between 30 and 3600 seconds.")
        self.options = replace(options, automatic_ai=False, collect_all_events=collect_all_events)
        self.interval = interval * 1000
        self.tracker = AlertTracker()
        self.active = True
        self.last_success = self.last_finished = self.last_result = None
        self.last_error = ""
        self.waiting = False
        self.next_due = datetime.now(timezone.utc)
        self.timer.start(0)
        self.health_changed.emit()
        scope = "all event levels" if collect_all_events else "warnings and detection rules"
        self.status.emit(f"Monitoring starting: {scope}; automatic AI is off.")

    def stop(self):
        """Stop scheduling and request cancellation of any active scan."""
        self.active = False
        self.next_due = None
        self.waiting = False
        self.timer.stop()
        self.health_changed.emit()
        if self.thread is not None:
            self.token.set()
            self.status.emit("Stopping monitoring after the current operation…")
        else:
            self.status.emit("Monitoring stopped.")
            self.stopped.emit()

    @pyqtSlot()
    def _tick(self):
        """Launch one scan or defer until foreground operations become idle."""
        if not self.active or self.thread is not None:
            return
        if not self.can_scan():
            self.waiting = True
            self.status.emit("Monitoring waiting for the current task…")
            self.timer.start(1000)
            self.health_changed.emit()
            return
        self.waiting = False
        self.next_due = None
        self.token = Event()
        self.thread = QThread(self)
        self.worker = MonitorWorker(self.options, self.history, self.token)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.result.connect(self._result)
        self.worker.error.connect(self._error)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._finished)
        self.busy_changed.emit(True)
        self.status.emit("Monitoring: scanning local events…")
        self.thread.start()
        self.health_changed.emit()

    @pyqtSlot(str)
    def _error(self, message):
        self.last_error = message
        self.status.emit(message)
        self.health_changed.emit()

    @pyqtSlot(object)
    def _result(self, result):
        """Deliver completed evidence and deduplicated alerts for the live results view."""
        if not self.active or getattr(result, "cancelled", False):
            return
        self.last_result = result
        self.last_finished = datetime.now(timezone.utc)
        self.last_error = ""
        if result.complete:
            self.last_success = self.last_finished
        self.health_changed.emit()
        alerts = self.tracker.observe(result)
        self.completed.emit(result, alerts)
        coverage = ("incomplete coverage" if not getattr(result, "complete", True) else
                    "complete with warnings" if getattr(result, "warnings", ()) else "complete")
        events = getattr(result, "collected_events", None)
        count = f"{len(events):,} events collected, " if events is not None else ""
        self.status.emit(f"Monitoring: {count}{len(result)} incidents, {len(alerts)} new alerts; {coverage}.")

    @pyqtSlot()
    def _finished(self):
        """Release native work before rescheduling or allowing the window to close."""
        self.thread = self.worker = None
        self.busy_changed.emit(False)
        if self.active:
            self.next_due = datetime.now(timezone.utc) + timedelta(milliseconds=self.interval)
            self.timer.start(self.interval)
        else:
            self.status.emit("Monitoring stopped.")
            self.stopped.emit()
        self.health_changed.emit()
