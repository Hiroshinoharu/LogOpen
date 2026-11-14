"""User-initiated GitHub update checks and cancellable package downloads."""

from pathlib import Path
import sys
from threading import Event

from PyQt6.QtCore import QObject, QStandardPaths, QThread, QTimer, Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit,
    QProgressBar, QPushButton, QVBoxLayout, QScrollArea, QFrame, QWidget,
)

from ui.icons import decorate_buttons, icon
from update_service import Release, UpdateError, download_release, latest_release, version_tuple, installer_cache_path
from version_info import application_version
from ui.sounds import SoundEffects


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
    """Download verified packages and offer an explicit, graceful installer handoff."""

    install_requested = pyqtSignal(object, object)

    def __init__(self, parent=None, *, auto_check=True):
        super().__init__(parent)
        self.setWindowTitle("LogOpen updates")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(590, 560)
        self.setMinimumSize(440, 400)
        self._thread = self._worker = self._release = self._download_path = None
        self._close_pending = False
        self._close_result = QDialog.DialogCode.Rejected
        self._download_asset = None
        self._install_after_download = False
        self.current_version = application_version()
        self.sounds = SoundEffects()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        self.scroll = QScrollArea(objectName="settingsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget(objectName="settingsContent")
        fields = QVBoxLayout(content)
        fields.setSpacing(14)
        heading = QLabel("Keep LogOpen up to date", objectName="pageTitle")
        heading.setWordWrap(True)
        fields.addWidget(heading)
        version = QLabel(f"Your version: {self.current_version}", objectName="mutedLabel")
        version.setWordWrap(True)
        fields.addWidget(version)
        description = QLabel("Check for new releases from Hiroshinoharu/LogOpen on GitHub. Download & install verifies the installer, stops monitoring, and closes LogOpen before opening setup. Setup can launch LogOpen when it finishes."
                             + (" You are running from source: setup updates the installed app, not this source checkout." if not getattr(sys, "frozen", False) else ""))
        description.setWordWrap(True)
        fields.addWidget(description)
        self.status = QLabel("Choose Check for updates to get started.", objectName="settingsMessage")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleDescription("Update status")
        fields.addWidget(self.status)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlaceholderText("Release notes will appear here.")
        self.notes.setAccessibleName("Release notes")
        self.notes.setMinimumHeight(160)
        fields.addWidget(self.notes, 1)
        self.packages = QComboBox()
        self.packages.setAccessibleName("Update package")
        self.packages.setAccessibleDescription("Choose a Windows installer or portable ZIP to download. Download and install always uses the Windows installer.")
        self.packages.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.packages.setMinimumContentsLength(12)
        package_label = QLabel("&Package to download")
        package_label.setBuddy(self.packages)
        package_label.setWordWrap(True)
        fields.addWidget(package_label)
        fields.addWidget(self.packages)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setAccessibleName("Update download progress")
        self.progress.setFormat("Download: %p%")
        self.progress.setAccessibleDescription("The download is checked before it is saved. The update status reports when verification is complete.")
        self.progress.hide()
        fields.addWidget(self.progress)
        self.download_hint = QLabel("Downloads are checked against the release's SHA256 checksum before being saved.", objectName="mutedLabel")
        self.download_hint.setWordWrap(True)
        fields.addWidget(self.download_hint)
        self.install_button = QPushButton("Download && install…", objectName="scanButton")
        self.install_button.setIcon(icon("download", "#0b3029"))
        self.install_button.setMinimumHeight(38)
        self.install_button.setToolTip("Download the Windows installer to LogOpen's update folder, then close this app and open setup.")
        self.install_button.clicked.connect(self.download_and_install)
        fields.addWidget(self.install_button)
        self.check_button = QPushButton("Check for updates", objectName="secondaryButton")
        self.check_button.setIcon(icon("restart"))
        self.check_button.clicked.connect(self.check_updates)
        fields.addWidget(self.check_button)
        self.download_button = QPushButton("Download update", objectName="scanButton")
        self.download_button.setMinimumHeight(38)
        self.download_button.setIcon(icon("download", "#0b3029"))
        self.download_button.clicked.connect(self.download_update)
        fields.addWidget(self.download_button)
        footer = QHBoxLayout()
        self.folder_button = QPushButton("Open download folder", objectName="secondaryButton")
        self.folder_button.setIcon(icon("folder"))
        self.folder_button.clicked.connect(self._open_download_folder)
        self.folder_button.hide()
        fields.addWidget(self.folder_button)
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        self.cancel_button = QPushButton("Cancel download", objectName="secondaryButton")
        self.cancel_button.setAccessibleDescription("Stop the download and keep the current version running.")
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
        self._download_asset = None
        self._install_after_download = False
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

    def _installer_asset(self):
        return next((asset for asset in self._release.assets if asset.name.endswith("-Setup-x64.exe")), None) if self._release else None

    @pyqtSlot()
    def download_and_install(self):
        """The explicit install action needs no file-picker or manual folder navigation."""
        if self._thread is not None or not self._newer_release() or self._close_pending:
            return
        asset = self._installer_asset()
        if asset is None:
            return
        if self._download_path is not None and self._download_asset == asset:
            self.install_requested.emit(self._download_path, asset)
            return
        try:
            path = installer_cache_path(asset)
        except UpdateError as exc:
            self._show_error(str(exc))
            return
        self._install_after_download = True
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText("Downloading and verifying the installer… Cancel to keep this version running.")
        self._start(asset, path)

    def _start(self, asset=None, destination=None):
        if asset is not None:
            self._download_path = None
            self._download_asset = asset
        self._token = Event()
        self._thread = QThread(self)
        self._worker = UpdateWorker(self._token, asset, destination)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.result.connect(self._show_result)
        self._worker.error.connect(self._show_error)
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
        self.install_button.setEnabled(not busy and self._newer_release() and self._installer_asset() is not None)
        self.install_button.setText("Install downloaded update…" if self._download_path is not None and
                                   self._download_asset == self._installer_asset() else "Download && install…")
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
            self.status.setText("Download verified and saved. Choose Install downloaded update to close LogOpen and open setup. "
                                if self._download_path.suffix.lower() == ".exe" else
                                "Download verified and saved. Extract the ZIP into a new folder, then open LogOpen.exe.")
            self.folder_button.show()
            if not self._close_pending:
                self.sounds.play("success")

    @pyqtSlot(str)
    def _show_error(self, message):
        self._install_after_download = False
        self.status.setText(message)
        if not self._close_pending and not message.startswith("Download cancelled."):
            self.sounds.play("attention")

    @pyqtSlot()
    def _finished(self):
        self._thread = self._worker = None
        self.cancel_button.hide()
        self._set_busy(False)
        if self._close_pending:
            self.done(self._close_result)
        elif self._install_after_download and self._download_path is not None:
            self._install_after_download = False
            self.install_requested.emit(self._download_path, self._download_asset)

    @pyqtSlot()
    def cancel_download(self):
        self._install_after_download = False
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
