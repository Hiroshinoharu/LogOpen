"""A persistent, readable monitoring indicator independent of the open results page."""

from PyQt6.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QBoxLayout, QFrame, QLabel, QLayout, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ui.icons import icon


class MonitoringStatus(QFrame):
    """Present controller state and notification availability without implying delivery.

    The owner supplies truthful state text from its monitoring controller. The
    panel never starts a scan, sends an alert, or infers that Windows displayed
    a notification. Its actions remain separate from its written status.
    """

    test_requested = pyqtSignal()
    preferences_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("monitoringStatus")
        self.setProperty("tone", "off")
        self.setProperty("active", False)
        self.setAccessibleDescription("Live monitoring and desktop notification status")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._compact = False
        self._detail = ""

        self.row = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.row.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.row.setContentsMargins(14, 10, 14, 10)
        self.row.setSpacing(12)
        self.content = QWidget()
        self.content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        column = QVBoxLayout(self.content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)
        self.title_label = self._label("Live monitoring is off", "monitoringTitle")
        self.detail_label = self._label("", "monitoringDetail")
        self.notification_label = self._label("", "monitoringNotifications")
        for label in (self.title_label, self.detail_label, self.notification_label):
            column.addWidget(label)
        self.row.addWidget(self.content, 1)

        self.actions = QWidget()
        self.action_row = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.actions)
        self.action_row.setContentsMargins(0, 0, 0, 0)
        self.action_row.setSpacing(8)
        self.test_button = QToolButton()
        self.test_button.setText("Test notification")
        self.test_button.setAccessibleName("Send a test desktop notification")
        self.test_button.setToolTip("Send one sample, even during LogOpen quiet hours or with alerts off. Your saved preferences stay unchanged; Windows settings still apply.")
        self.test_button.setIcon(icon("info"))
        self.test_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.test_button.clicked.connect(self.test_requested.emit)
        self.preferences_button = QToolButton()
        self.preferences_button.setText("Notification options")
        self.preferences_button.setAccessibleName("Open monitoring notification preferences")
        self.preferences_button.setToolTip("Choose desktop alerts, priorities, quiet hours, and grouping.")
        self.preferences_button.setIcon(icon("settings"))
        self.preferences_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.preferences_button.clicked.connect(self.preferences_requested.emit)
        for button in (self.test_button, self.preferences_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.action_row.addWidget(button)
        self.row.addWidget(self.actions)
        self.detail_label.hide()
        self.notification_label.hide()

    def minimumSizeHint(self):
        # A row can become a column; its previous horizontal minimum must not
        # enlarge a top-level window before the resize event gets to reflow it.
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    @staticmethod
    def _label(text, name):
        label = QLabel(text, objectName=name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        # The text must wrap rather than forcing a minimum window width.
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return label

    def update_status(self, title, detail="", notification_text="", *, tone="off", active=False):
        """Apply written controller state; active is a semantic state, never a pulse."""
        self.title_label.setText(str(title))
        self._detail = str(detail)
        self.detail_label.setText(self._detail)
        self.detail_label.setVisible(bool(self._detail) and not self._compact)
        self.notification_label.setText(str(notification_text))
        self.notification_label.setVisible(bool(notification_text))
        tone = tone if tone in ("active", "warning", "error", "off") else "off"
        changed = self.property("tone") != tone or self.property("active") != bool(active)
        self.setProperty("tone", tone)
        self.setProperty("active", bool(active))
        self.setToolTip("\n".join(str(value) for value in (title, detail, notification_text) if value))
        if changed:
            self.style().unpolish(self)
            self.style().polish(self)
        self._arrange()

    def set_compact(self, compact):
        """Let short windows omit secondary detail while retaining state and alert status."""
        self._compact = bool(compact)
        self.detail_label.setVisible(bool(self._detail) and not self._compact)
        self._arrange()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and hasattr(self, "actions"):
            self._arrange()

    def _arrange(self):
        available = max(0, self.width() - 28)
        action_width = self.test_button.sizeHint().width() + self.preferences_button.sizeHint().width() + 8
        # Reserve a useful reading column; otherwise place actions below it.
        reading_width = self.title_label.fontMetrics().horizontalAdvance("Live monitoring is on")
        stacked = action_width + reading_width + 28 > available
        self.row.setDirection(QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)
        self.action_row.setDirection(QBoxLayout.Direction.TopToBottom if action_width > available
                                     else QBoxLayout.Direction.LeftToRight)
