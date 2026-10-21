"""User-initiated GitHub update checks and cancellable package downloads."""

from pathlib import Path
from threading import Event

from PyQt6.QtCore import QObject, QStandardPaths, QThread, QTimer, Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit,
    QProgressBar, QPushButton, QVBoxLayout,
)

from ui.icons import decorate_buttons, icon
from update_service import Release, UpdateError, download_release, latest_release, version_tuple
from version_info import application_version


class UpdateWorker(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished = pyqtSignal()

    def __init__(self, token, asset=None, destination=None):
        super().__init__()
        self.token, self.asset, self.destination = token, asset, destination

    @pyqtSlot()
    def run(self):
        try:
            if self.asset is None:
                result = latest_release()
                if not self.token.is_set():
                    self.result.emit(result)
            else:
                self.result.emit(download_release(
                    self.asset, self.destination, cancel_event=self.token,
                    progress=lambda received, total: self.progress.emit(int(received * 100 / total)),
                ))
        except UpdateError as error:
            self.error.emit(str(error))
        except Exception:
            self.error.emit("The update could not be completed. Please try again.")
        finally:
            self.finished.emit()


class UpdateDialog(QDialog):
    """Download an update without executing it or interrupting the installed app."""

    def __init__(self, parent=None, *, auto_check=True):
        super().__init__(parent)
        self.setWindowTitle("LogOpen updates")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(590, 560)
        self.setMinimumSize(440, 400)
        self._thread = self._worker = self._release = self._download_path = None
        self._close_pending = False
        self._close_result = QDialog.DialogCode.Rejected
        self.current_version = application_version()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        heading = QLabel("Keep LogOpen up to date", objectName="pageTitle")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(QLabel(f"Your version: {self.current_version}", objectName="mutedLabel"))
        description = QLabel("Check for new releases from Hiroshinoharu/LogOpen on GitHub. Download the update here, then run the installer when you are ready.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.status = QLabel("Choose Check for updates to get started.", objectName="settingsMessage")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Update status")
        layout.addWidget(self.status)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlaceholderText("Release notes will appear here.")
        self.notes.setAccessibleName("Release notes")
        layout.addWidget(self.notes, 1)
        self.packages = QComboBox()
        self.packages.setAccessibleName("Update package")
        layout.addWidget(self.packages)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setAccessibleName("Update download progress")
        self.progress.hide()
        layout.addWidget(self.progress)
        self.download_hint = QLabel("Downloads are checked against the release's SHA256 checksum before being saved.", objectName="mutedLabel")
        self.download_hint.setWordWrap(True)
        layout.addWidget(self.download_hint)
        row = QHBoxLayout()
        self.check_button = QPushButton("Check for updates", objectName="secondaryButton")
        self.check_button.setIcon(icon("restart"))
        self.check_button.clicked.connect(self.check_updates)
        row.addWidget(self.check_button)
        self.download_button = QPushButton("Download update", objectName="scanButton")
        self.download_button.setMinimumHeight(38)
        self.download_button.setIcon(icon("download", "#0b3029"))
        self.download_button.clicked.connect(self.download_update)
        row.addWidget(self.download_button)
        layout.addLayout(row)
        footer = QHBoxLayout()
        self.folder_button = QPushButton("Open download folder", objectName="secondaryButton")
        self.folder_button.setIcon(icon("folder"))
        self.folder_button.clicked.connect(self._open_download_folder)
        self.folder_button.hide()
        footer.addWidget(self.folder_button)
        self.cancel_button = QPushButton("Cancel download", objectName="secondaryButton")
        self.cancel_button.clicked.connect(self.cancel_download)
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        footer.addStretch()
        self.close_button = QPushButton("Close", objectName="secondaryButton")
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        decorate_buttons(self)
        self._set_busy(False)
        if auto_check:
            QTimer.singleShot(0, self.check_updates)

    @pyqtSlot()
    def check_updates(self):
        if self._thread is not None:
            return
        self._release = self._download_path = None
        self.folder_button.hide()
        self.packages.clear()
        self.notes.clear()
        self.progress.hide()
        self.status.setText("Checking GitHub for updates…")
        self._start()

    @pyqtSlot()
    def download_update(self):
        if self._thread is not None or self._release is None or not self._newer_release():
            return
        asset = self.packages.currentData()
        if asset is None:
            return
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home())
        file_filter = "Installer (*.exe)" if asset.name.endswith(".exe") else "Portable ZIP (*.zip)"
        path, _ = QFileDialog.getSaveFileName(self, "Save LogOpen update", str(Path(folder) / asset.name), file_filter)
        if not path:
            return
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText("Downloading update… You can cancel or close this window to stop.")
        self._start(asset, path)

    def _start(self, asset=None, destination=None):
        self._token = Event()
        self._thread = QThread(self)
        self._worker = UpdateWorker(self._token, asset, destination)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.result.connect(self._show_result)
        self._worker.error.connect(self.status.setText)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._finished)
        self._set_busy(True)
        self.cancel_button.setVisible(asset is not None)
        self._thread.start()

    def _newer_release(self):
        return self._release is not None and version_tuple(self._release.version) > version_tuple(self.current_version)

    def _set_busy(self, busy):
        self.check_button.setEnabled(not busy)
        self.packages.setEnabled(not busy and self._release is not None)
        self.download_button.setEnabled(not busy and self._newer_release())
        self.cancel_button.setEnabled(busy)

    @pyqtSlot(object)
    def _show_result(self, result):
        if isinstance(result, Release):
            self._release = result
            self.notes.setPlainText(result.notes or "No release notes were provided.")
            self.packages.clear()
            for asset in result.assets:
                label = "Windows installer" if asset.name.endswith(".exe") else "Portable ZIP"
                self.packages.addItem(icon("download"), f"{label} · {asset.size / (1024 * 1024):.1f} MB", asset)
            self.status.setText(f"Version {result.version} is ready to download." if self._newer_release()
                                else f"You are up to date. Latest published version: {result.version}.")
        else:
            self._download_path = Path(result)
            self.progress.setValue(100)
            self.status.setText("Download verified and saved. Close LogOpen, then run the installer to update. "
                                if self._download_path.suffix.lower() == ".exe" else
                                "Download verified and saved. Extract the ZIP into a new folder, then open LogOpen.exe.")
            self.folder_button.show()

    @pyqtSlot()
    def _finished(self):
        self._thread = self._worker = None
        self.cancel_button.hide()
        self._set_busy(False)
        if self._close_pending:
            self.done(self._close_result)

    @pyqtSlot()
    def cancel_download(self):
        if self._thread is not None:
            self._token.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("Stopping the download…")

    def _open_download_folder(self):
        if self._download_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._download_path.parent)))

    def done(self, result):
        if self._thread is not None:
            self._close_pending = True
            self._close_result = result
            self._token.set()
            self.status.setText("Waiting for the update check or download to stop…")
            return
        super().done(result)

    def closeEvent(self, event):
        if self._thread is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
