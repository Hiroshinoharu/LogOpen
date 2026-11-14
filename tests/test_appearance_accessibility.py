"""Check rendered color roles, focus visibility, and readable text in both themes."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QApplication, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget, QVBoxLayout, QWidget,
)

from ui.appearance import themed_stylesheet


def contrast(first, second):
    """Return relative-luminance contrast from the actual Qt palette colors."""
    def luminance(color):
        channels = [color.redF(), color.greenF(), color.blueF()]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                  for value in channels]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.fixture(params=["dark", "light"])
def themed_surface(request):
    app = QApplication.instance() or QApplication([])
    surface = QWidget(objectName="appSurface")
    base = (Path(__file__).resolve().parents[1] / "ui" / "style.qss").read_text()
    surface.setStyleSheet(themed_stylesheet(base, request.param))
    surface.ensurePolished()
    yield surface, app, base, request.param
    surface.close()
    surface.deleteLater()
    app.processEvents()


def assert_readable(widget, foreground=QPalette.ColorRole.WindowText,
                    background=QPalette.ColorRole.Window):
    widget.ensurePolished()
    palette = widget.palette()
    ratio = contrast(palette.color(foreground), palette.color(background))
    assert ratio >= 4.5, (widget.objectName(), ratio)


def test_action_and_status_text_contrast_in_each_theme(themed_surface):
    surface, _app, _base, _theme = themed_surface
    assert_readable(surface)
    for name in ("scanButton", "secondaryButton", "comparisonButton", "filterChip", "metricCard"):
        button = QPushButton("Readable action", surface, objectName=name)
        assert_readable(button, QPalette.ColorRole.ButtonText, QPalette.ColorRole.Button)

    for state in ("complete", "scanning", "warning", "error"):
        badge = QLabel(state, surface, objectName="stateBadge")
        badge.setProperty("state", state)
        assert_readable(badge)

    for risk in ("safe", "caution", "system_change"):
        label = QLabel(risk, surface, objectName="diagnosticRisk")
        label.setProperty("risk", risk)
        assert_readable(label)

    for name in ("mutedLabel", "scopeLabel", "journeyCaption", "metricTitle"):
        label = QLabel("Helpful supporting text", surface, objectName=name)
        label.ensurePolished()
        assert contrast(label.palette().color(QPalette.ColorRole.WindowText),
                        surface.palette().color(QPalette.ColorRole.Window)) >= 4.5
        assert label.font().pixelSize() >= 14


def test_text_selection_and_field_text_stay_readable(themed_surface):
    surface, _app, _base, _theme = themed_surface
    for widget in (QLineEdit(surface), QTableWidget(surface)):
        assert_readable(widget, QPalette.ColorRole.Text, QPalette.ColorRole.Base)
        assert_readable(widget, QPalette.ColorRole.HighlightedText, QPalette.ColorRole.Highlight)
    for widget in (QLineEdit(surface), QPlainTextEdit(surface)):
        assert_readable(widget, QPalette.ColorRole.PlaceholderText, QPalette.ColorRole.Base)


def test_keyboard_focus_is_visible_without_a_color_change_to_text(themed_surface):
    surface, app, _base, _theme = themed_surface
    layout = QVBoxLayout(surface)
    field = QLineEdit("Keyboard focus", surface)
    other = QPushButton("Next control", surface)
    layout.addWidget(field)
    layout.addWidget(other)
    surface.show()
    surface.activateWindow()
    other.setFocus()
    app.processEvents()
    ordinary = field.grab().toImage().pixelColor(0, field.height() // 2)
    field.setFocus()
    app.processEvents()
    focused = field.grab().toImage().pixelColor(0, field.height() // 2)
    assert field.hasFocus()
    assert focused != ordinary
    assert contrast(focused, field.palette().color(QPalette.ColorRole.Base)) >= 3
    assert contrast(ordinary, field.palette().color(QPalette.ColorRole.Base)) >= 3


def test_two_hundred_percent_text_size_is_available(themed_surface):
    surface, _app, base, theme = themed_surface
    surface.setStyleSheet(themed_stylesheet(base, theme, "largest"))
    label = QLabel("Readable at 200 percent", surface, objectName="mutedLabel")
    field = QLineEdit(surface)
    label.ensurePolished()
    field.ensurePolished()
    assert label.font().pixelSize() == 28
    assert field.font().pixelSize() == 30
