"""Exercise saved reading preferences and keyboard access through the real UI."""

from PyQt6.QtCore import QAbstractAnimation, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel

from tests.test_guided_ui import desktop, problem
from ui.accessibility import ReadingOptionsDialog
from ui.main_window import MainWindow


def pump(app):
    """Let nested layouts and scroll ranges settle after changing font sizes."""
    for _ in range(4):
        app.processEvents()


def test_primary_scan_action_keeps_a_large_pointer_target(desktop):
    window, app = desktop
    window.resize(800, 620)
    pump(app)
    assert window.scan_button.height() >= 44
    assert window.scan_button.width() >= 44


def test_reading_choices_apply_save_and_match_the_appearance_menu(desktop):
    window, app = desktop
    dialog = ReadingOptionsDialog(window)
    try:
        dialog.theme.setCurrentIndex(dialog.theme.findData("light"))
        dialog.text_size.setCurrentIndex(dialog.text_size.findData("largest"))
        pump(app)
        assert window._theme == "light"
        assert window._text_size == "largest"
        assert window._theme_actions["light"].isChecked()
        assert window._text_size_actions["largest"].isChecked()
        assert sum(action.isChecked() for action in window._theme_actions.values()) == 1
        assert sum(action.isChecked() for action in window._text_size_actions.values()) == 1
        assert window._ui_settings.value("ui/theme") == "light"
        assert window._ui_settings.value("ui/text_size") == "largest"
        assert window.search_input.font().pixelSize() == 30
    finally:
        dialog.close()

    reopened = MainWindow()
    try:
        assert reopened._theme == "light" and reopened._text_size == "largest"
        assert reopened._theme_actions["light"].isChecked()
        assert reopened._text_size_actions["largest"].isChecked()
        assert reopened.centralWidget() is reopened.workspace_scroll
        reading = ReadingOptionsDialog(reopened)
        assert reading.theme.currentData() == "light"
        assert reading.text_size.currentData() == "largest"
        reading.close()
    finally:
        reopened.close()
        reopened.deleteLater()
        pump(app)


def test_menu_and_text_actions_are_reflected_when_reading_options_open(desktop):
    window, _app = desktop
    window._theme_actions["light"].trigger()
    window.increase_text_action.trigger()
    assert window._text_size == "large"
    window.increase_text_action.trigger()
    assert window._text_size == "larger"
    window.increase_text_action.trigger()
    window.increase_text_action.trigger()
    assert window._text_size == "largest"
    assert window._text_size_actions["largest"].isChecked()
    window.decrease_text_action.trigger()
    assert window._text_size == "larger"
    window.reset_text_action.trigger()
    window.decrease_text_action.trigger()
    assert window._text_size == "normal"
    assert window._theme == "light"
    dialog = ReadingOptionsDialog(window)
    try:
        assert dialog.theme.currentData() == "light"
        assert dialog.text_size.currentData() == "normal"
    finally:
        dialog.close()


def test_reduce_motion_stops_active_animations_and_is_saved(desktop):
    window, app = desktop
    window.motion_action.setChecked(True)
    window.motion.set_active(True)
    assert window.motion.pulse.state() == QAbstractAnimation.State.Running
    dialog = ReadingOptionsDialog(window)
    try:
        assert not dialog.reduce_motion.isChecked()
        dialog.reduce_motion.setChecked(True)
        assert not window.motion.enabled
        assert not window.motion_action.isChecked()
        assert window.motion.pulse.state() == QAbstractAnimation.State.Stopped
        assert window.motion.transition.state() == QAbstractAnimation.State.Stopped
        assert not window.motion.page_effect.isEnabled()
        assert window.motion.badge_effect.opacity() == 1
        assert str(window._ui_settings.value("ui/animations")).lower() == "false"
    finally:
        dialog.close()
        window.motion.set_active(False)
    reopened = MainWindow()
    try:
        assert not reopened.motion.enabled
        assert not reopened.motion_action.isChecked()
    finally:
        reopened.close()
        reopened.deleteLater()
        pump(app)


def test_ctrl_f_returns_from_details_and_selects_the_search_text(desktop):
    window, app = desktop
    window._show_scan_results([problem()])
    window.incident_table.setCurrentCell(0, 0)
    window.search_input.setText("DNS")
    window._open_incident_detail()
    assert window.page_stack.currentWidget() is window.incident_detail
    window.activateWindow()
    pump(app)
    QTest.keyClick(window.incident_detail.back_button, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(app)
    assert window.page_stack.currentWidget() is window.dashboard
    assert window.search_input.hasFocus()
    assert window.search_input.selectedText() == "DNS"


def test_two_hundred_percent_workspace_scrolls_and_can_return_to_normal(desktop):
    window, app = desktop
    window._show_scan_results([problem()])
    for _ in range(2):
        window._set_appearance(size="largest")
        window.resize(800, 620)
        pump(app)
        assert window.width() == 800 and window.height() == 620
        assert window.centralWidget() is window.workspace_scroll
        assert window.workspace_scroll.widget() is window.central_widget
        assert window.workspace_scroll.verticalScrollBar().maximum() > 0
        assert window.search_input.font().pixelSize() == 30
        assert window.incident_table.rowCount() == 1

        window._set_appearance(size="normal")
        pump(app)
        window.resize(800, 620)
        pump(app)
        assert window.centralWidget() is window.central_widget
        assert window.workspace_scroll.widget() is None
        assert window.workspace_scroll.isHidden()
        assert window.search_input.font().pixelSize() == 15
        assert window.incident_table.isVisible()
        assert window.incident_table.viewport().height() >= 75
        assert window.width() == 800 and window.height() == 620


def test_enlarged_workspace_keeps_a_focused_action_visible(desktop):
    window, app = desktop
    window._show_scan_results([problem()])
    window.incident_table.setCurrentCell(0, 0)
    window._set_appearance(size="largest")
    window.resize(800, 620)
    window.activateWindow()
    window.scan_button.setFocus()
    pump(app)
    window.detail_button.setFocus()
    pump(app)
    assert window.detail_button.hasFocus()
    viewport = window.workspace_scroll.viewport()
    center = window.detail_button.mapTo(viewport, window.detail_button.rect().center())
    assert viewport.rect().contains(center), center


def test_filters_preserve_compact_layout_space_for_results(desktop):
    window, app = desktop
    window._show_scan_results([problem()])
    window.resize(1000, 900)
    pump(app)
    assert window.start_here_card.isHidden()
    window.priority_filter.setCurrentText("High")
    pump(app)
    assert window.start_here_card.isHidden()
    assert window.incident_table.viewport().height() >= 75


def test_enlarged_status_actions_remain_readable_with_alerts_and_undo(desktop):
    window, app = desktop
    window._set_appearance(size="largest")
    window.resize(800, 620)
    window._unread_monitor_incidents = 12
    window.monitor_notice.show()
    window.undo_button.show()
    window._update_statusbar_labels()
    pump(app)
    assert window.monitor_button.text() == "Watch logs"
    assert window.monitor_notice.text() == "12 new"
    assert "12 new incidents" in window.monitor_notice.accessibleName()
    for button in (window.monitor_notice, window.monitor_button, window.undo_button, window.monitor_health_button):
        assert button.width() >= button.sizeHint().width()
        assert button.mapTo(window, button.rect().bottomRight()).x() < window.width()


def test_summary_actions_expose_the_current_counts(desktop):
    window, _app = desktop
    window._show_scan_results([problem()])
    assert "1 problem found" in window.problems_card.accessibleDescription()
    assert "1 High or Critical" in window.attention_card.accessibleDescription()
    assert "4 Windows log entries" in window.events_card.accessibleDescription()
    assert not window.status_label.accessibleName()  # Native label exposes its changing text.
    assert window.status_label.accessibleDescription() == "Current scan status"


def test_summary_button_layout_keeps_its_text_inside_the_card(desktop):
    window, app = desktop
    window._show_scan_results([problem()])
    window.resize(1440, 1000)
    pump(app)
    for card in (window.problems_card, window.attention_card, window.events_card):
        assert card.isVisible()
        labels = card.findChildren(QLabel)
        for label in labels:
            top_left = label.mapTo(card, label.rect().topLeft())
            bottom_right = label.mapTo(card, label.rect().bottomRight())
            assert card.rect().contains(top_left)
            assert card.rect().contains(bottom_right)
        # The caption and number remain readable in the actual dashboard row.
        assert card.height() >= card.layout().minimumSize().height()
