"""Opt-in polling while LogOpen is open, with cooperative thread-safe shutdown."""

from dataclasses import replace
from threading import Event
from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                            QLabel, QSpinBox, QVBoxLayout)
from main import scan_system
from monitoring import AlertTracker


class MonitorOptionsDialog(QDialog):
    """Choose polling frequency and OS notifications before starting monitoring."""

    def __init__(self, parent=None):
        """Explain baseline behavior and automatic-AI exclusion next to the controls."""
        super().__init__(parent)
        self.setWindowTitle("Start live monitoring")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        note = QLabel("Run periodic local scans of the current scope while this window is open. "
                      "The first scan establishes a silent baseline. Later new High/Critical active incidents can notify you. "
                      "Automatic AI is off for monitoring. Results are saved to local history and the latest report.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.interval = QSpinBox()
        self.interval.setRange(30, 3600)
        self.interval.setValue(60)
        self.interval.setSuffix(" seconds after each scan")
        form.addRow("Scan interval", self.interval)
        self.notifications = QCheckBox("Show desktop notifications for new high-priority incidents")
        self.notifications.setChecked(True)
        form.addRow(self.notifications)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


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

    def __init__(self, history, can_scan, parent=None):
        """Keep the timer and scheduling state on the GUI thread; do not auto-start."""
        super().__init__(parent)
        self.history, self.can_scan = history, can_scan
        self.active = False
        self.thread = self.worker = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._tick)

    def start(self, options, interval):
        """Begin a fresh session with an immutable scope and a silent baseline scan."""
        if self.active or self.thread is not None:
            return
        if type(interval) is not int or not 30 <= interval <= 3600:
            raise ValueError("Monitoring interval must be between 30 and 3600 seconds.")
        self.options = replace(options, automatic_ai=False)
        self.interval = interval * 1000
        self.tracker = AlertTracker()
        self.active = True
        self.timer.start(0)
        self.status.emit("Monitoring starting; automatic AI is off.")

    def stop(self):
        """Stop scheduling and request cancellation of any active scan."""
        self.active = False
        self.timer.stop()
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
            self.status.emit("Monitoring waiting for the current task…")
            self.timer.start(1000)
            return
        self.token = Event()
        self.thread = QThread(self)
        self.worker = MonitorWorker(self.options, self.history, self.token)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.result.connect(self._result)
        self.worker.error.connect(self.status.emit)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._finished)
        self.busy_changed.emit(True)
        self.status.emit("Monitoring: scanning local events…")
        self.thread.start()

    @pyqtSlot(object)
    def _result(self, result):
        """Deliver completed evidence and deduplicated alerts without replacing views."""
        if not self.active or getattr(result, "cancelled", False):
            return
        alerts = self.tracker.observe(result)
        self.completed.emit(result, alerts)
        coverage = ("incomplete coverage" if not getattr(result, "complete", True) else
                    "complete with warnings" if getattr(result, "warnings", ()) else "complete")
        self.status.emit(f"Monitoring: {len(result)} incidents, {len(alerts)} new alerts; {coverage}.")

    @pyqtSlot()
    def _finished(self):
        """Release native work before rescheduling or allowing the window to close."""
        self.thread = self.worker = None
        self.busy_changed.emit(False)
        if self.active:
            self.timer.start(self.interval)
        else:
            self.status.emit("Monitoring stopped.")
            self.stopped.emit()
