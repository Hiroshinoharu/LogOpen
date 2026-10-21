"""Searchable channel selection and asynchronous, read-only Windows access checks."""

from threading import Event

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                            QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QHeaderView)

from channel_catalog import check_channels, list_channels
from ui.table_layout import RoomyTable, expand_window
from ui.icons import decorate_buttons


class ChannelWorker(QObject):
    """Run channel discovery or access checks on the owning native worker thread."""

    result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, names, token):
        """Use None for inventory; a name tuple requests access checks only."""
        super().__init__()
        self.names, self.token = names, token

    @pyqtSlot()
    def run(self):
        """Emit a result or readable error, always allowing thread cleanup."""
        try:
            self.result.emit(list_channels(self.token) if self.names is None else check_channels(self.names, self.token))
        except Exception:
            self.error.emit("Windows channels could not be read. Check Windows Event Log availability.")
        finally:
            self.finished.emit()


class ChannelBrowser(QDialog):
    """Choose administrative/operational channels without changing OS configuration."""

    def __init__(self, names, parent=None):
        """Build a searchable checkbox table and asynchronously load local channels."""
        super().__init__(parent)
        self.setWindowTitle("Windows event channels")
        expand_window(self, 1700, 1000)
        self.names = tuple(names)
        self._thread = None
        self._closing = False
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search channel name, description, type, or access status…")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Channel (check to include)", "Type", "Logging", "Access", "Description"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 350)
        self.table.setColumnWidth(3, 170)
        self.roomy_table = RoomyTable(self.table, [560, 145, 110, 280, 450], flexible=0)
        layout.addWidget(self.table, 1)
        self.status = QLabel("Loading registered channels…")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        note = QLabel("Checking access reads at most one event per selected channel. Disabled logs may have older records. "
                      "Analytic/debug trace channels are not supported. This window does not enable logs or change permissions.")
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.check_button = QPushButton("Check selected access")
        self.check_button.clicked.connect(self._check_selected)
        row.addWidget(self.check_button)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        row.addWidget(self.buttons)
        layout.addLayout(row)
        decorate_buttons(self)
        self._start(None)

    def selected_names(self):
        """Return checked channel names, including selections hidden by search."""
        return tuple(self.table.item(row, 0).text() for row in range(self.table.rowCount())
                     if self.table.item(row, 0).checkState() == Qt.CheckState.Checked)

    def _start(self, names):
        """Start one cancellable native operation and keep its QThread alive."""
        if self._thread is not None:
            return
        self._token = Event()
        self._thread = QThread(self)
        self._worker = ChannelWorker(names, self._token)
        self._worker.moveToThread(self._thread)
        self._worker.result.connect(self._loaded)
        self._worker.error.connect(self.status.setText)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._finished)
        self.check_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._thread.start()

    @pyqtSlot(object)
    def _loaded(self, result):
        """Apply inventory or access results on the GUI thread, preserving checks."""
        if self._closing:
            return
        self.table.setSortingEnabled(False)
        if isinstance(result, dict):
            for row in range(self.table.rowCount()):
                name = self.table.item(row, 0).text()
                if name in result:
                    self.table.item(row, 3).setText(result[name])
            self.status.setText(f"Checked {len(result)} selected channels.")
        else:
            known = {item["name"] for item in result}
            result.extend(dict(name=name, kind="Unknown", enabled=None, access="Not installed", description="Selected channel was not found.")
                          for name in self.names if name not in known)
            self.table.setRowCount(len(result))
            for row, channel in enumerate(result):
                values = (channel["name"], channel["kind"],
                          "Enabled" if channel["enabled"] else "Disabled" if channel["enabled"] is False else "Unknown",
                          channel["access"], channel["description"])
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if col == 0:
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(Qt.CheckState.Checked if value in self.names else Qt.CheckState.Unchecked)
                        if channel["kind"] in ("Analytic", "Debug"):
                            item.setCheckState(Qt.CheckState.Unchecked)
                            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                    item.setToolTip(value)
                    self.table.setItem(row, col, item)
            self.status.setText(f"{len(result)} channels. Check the channels to include, then test their access.")
        self.table.setSortingEnabled(True)
        self.roomy_table.fit_contents()
        self._filter()

    @pyqtSlot()
    def _finished(self):
        """Release worker references before accepting a pending close."""
        self._thread = self._worker = None
        self.check_button.setEnabled(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        if self._closing:
            super().reject()

    def _filter(self):
        """Hide nonmatching rows without clearing the user's checked channels."""
        query = self.search.text().casefold()
        for row in range(self.table.rowCount()):
            text = " ".join(self.table.item(row, col).text() for col in range(5))
            self.table.setRowHidden(row, query not in text.casefold())

    def _check_selected(self):
        """Probe only checked channels to avoid querying every registered source."""
        names = self.selected_names()
        if not names:
            self.status.setText("Check at least one channel first.")
            return
        self.status.setText("Checking read access…")
        self._start(names)

    def accept(self):
        """Return the checked names only after native worker cleanup."""
        if self._thread is None:
            self.names = self.selected_names()
            super().accept()

    def reject(self):
        """Cancel ongoing discovery cooperatively; never destroy a running thread."""
        if self._thread is not None:
            self._closing = True
            self._token.set()
            self.status.setText("Closing after the current Windows operation finishes…")
            return
        super().reject()

    def closeEvent(self, event):
        """Route the window close button through cooperative worker cleanup."""
        self.reject()
        event.ignore()
