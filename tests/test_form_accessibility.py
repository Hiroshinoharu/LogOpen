"""Keyboard correction, persistent form labels, and readable enlarged dialogs."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QDate, QPoint, QRect
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QScrollArea

from models.scan_options import ScanOptions
from notification_preferences import NotificationPreferences
from settings.app_settings import AISettings
from ui.ai_preview import AIPrivacyPreview
from ui.appearance import themed_stylesheet
from ui.feedback_dialog import FeedbackDialog
from ui.monitoring import MonitorOptionsDialog
from ui.notification_options import NotificationOptionsDialog
from ui.scan_controls import FilterDialog, ScanOptionsDialog
from ui.settings_dialog import SettingsDialog
from ui.update_dialog import UpdateDialog
from update_service import Release, ReleaseAsset


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    # Windows' offscreen plugin has no system fonts until one is registered.
    # Real glyph metrics are necessary to test readable text reflow.
    font_path = Path("C:/Windows/Fonts/segoeui.ttf")
    font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.exists() else -1
    yield application
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)


def close_dialog(dialog, app):
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_scan_and_filter_inputs_have_visible_associated_labels(app):
    for dialog, names in (
        (ScanOptionsDialog(ScanOptions()), ("profile", "hours", "logs", "limit")),
        (FilterDialog({}), ("provider", "classification", "start", "end")),
    ):
        try:
            labels = dialog.findChildren(QLabel)
            for name in names:
                control = getattr(dialog, name)
                assert control.accessibleName(), name
                assert any(label.buddy() is control and label.text() for label in labels), name
        finally:
            close_dialog(dialog, app)


def test_invalid_scan_reveals_and_focuses_the_field_to_correct(app):
    dialog = ScanOptionsDialog(ScanOptions())
    try:
        dialog.show()
        dialog.logs.clear()
        dialog.accept()
        app.processEvents()
        assert dialog.isVisible()
        assert dialog.advanced.toggle_button.isChecked()
        assert dialog.error.text()
        assert QApplication.focusWidget() is dialog.logs
        assert dialog.logs.accessibleDescription() == dialog.error.text()
    finally:
        close_dialog(dialog, app)


def test_invalid_date_filter_focuses_start_date_and_reset_clears_feedback(app):
    dialog = FilterDialog({"dates": True, "start": QDate(2026, 2, 2), "end": QDate(2026, 2, 1)})
    try:
        dialog.show()
        dialog.accept()
        app.processEvents()
        assert dialog.isVisible()
        assert QApplication.focusWidget() is dialog.start
        assert "start date" in dialog.error.text()
        dialog.reset()
        assert not dialog.error.text()
    finally:
        close_dialog(dialog, app)


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("kind", ["scan", "filters", "settings", "notifications", "monitor", "feedback", "ai", "updates"])
def test_dialogs_reflow_at_two_hundred_percent_and_scroll_to_controls(app, theme, kind):
    if not QFontDatabase.families():
        pytest.skip("No fonts available for a meaningful text-reflow check")
    if kind == "scan":
        dialog = ScanOptionsDialog(ScanOptions())
        dialog.advanced.set_expanded(True)
        target = dialog.limit
    elif kind == "filters":
        dialog = FilterDialog({"dates": True})
        target = dialog.end
    elif kind == "settings":
        dialog = SettingsDialog()
        dialog.tabs.setCurrentIndex(1)
        target = dialog.open_terminal_button
    elif kind == "notifications":
        dialog = NotificationOptionsDialog(NotificationPreferences())
        target = dialog.batch
    elif kind == "monitor":
        dialog = MonitorOptionsDialog()
        target = dialog.all_events
    elif kind == "feedback":
        dialog = FeedbackDialog()
        target = dialog.preview
    elif kind == "updates":
        dialog = UpdateDialog(auto_check=False)
        asset = ReleaseAsset("LogOpen-99.0.0-Setup-x64.exe", "https://example.test/update", 25, "a" * 64)
        dialog._show_result(Release("99.0.0", "Local test release notes", (asset,)))
        dialog._download_asset = asset
        dialog._show_result(Path(asset.name))
        dialog._set_busy(False)
        dialog.progress.show()
        dialog.progress.setValue(42)
        dialog.cancel_button.show()
        target = dialog.packages
    else:
        dialog = AIPrivacyPreview({}, AISettings(False, "gpt-4.1-mini", 3))
        target = dialog.preview
    try:
        stylesheet = (Path(__file__).parents[1] / "ui" / "style.qss").read_text(encoding="utf-8")
        dialog.setStyleSheet(themed_stylesheet(stylesheet, theme, "largest"))
        dialog.resize(580, 460)
        dialog.show()
        app.processEvents()
        scroll = next(area for area in dialog.findChildren(QScrollArea) if area.isVisible())
        assert scroll.widget().width() <= scroll.viewport().width() + 1
        assert scroll.verticalScrollBar().maximum() > 0
        scroll.ensureWidgetVisible(target)
        app.processEvents()
        target_rect = QRect(target.mapTo(scroll.viewport(), QPoint()), target.size())
        # Editors can be taller than the viewport and scroll internally. Their
        # input area must still be reachable through the outer reading scroll.
        visible = scroll.viewport().rect().intersected(target_rect)
        assert visible.width() >= min(80, target.width())
        assert visible.height() >= min(80, target.height())
        if kind == "updates":
            assert dialog._thread is None  # Opening reading controls never checks online.
            assert dialog.packages.accessibleName()
            assert dialog.progress.accessibleName()
            fixed_actions = (dialog.cancel_button, dialog.close_button)
        elif kind == "filters":
            fixed_actions = (dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel),)
        else:
            fixed_actions = ()
        for action in fixed_actions:
            rect = QRect(action.mapTo(dialog, QPoint()), action.size())
            assert dialog.rect().contains(rect)
            assert action.isVisible()
            assert not scroll.isAncestorOf(action)
    finally:
        close_dialog(dialog, app)
