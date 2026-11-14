"""Reading preferences and keyboard guidance using standard Qt controls."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from ui.icons import decorate_buttons


TEXT_SIZES = (
    ("normal", "Comfortable · 100%"),
    ("large", "Large · 115%"),
    ("larger", "Larger · 130%"),
    ("largest", "Extra large · 200%"),
)


class ReadingOptionsDialog(QDialog):
    """Apply saved reading and motion choices immediately, without a restart."""

    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("Reading and accessibility")
        self.resize(600, 550)
        self.setMinimumSize(360, 300)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        intro = QLabel("Make LogOpen easier to read and use. Changes apply immediately and are saved for next time.")
        intro.setWordWrap(True)
        body.addWidget(intro)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.theme = QComboBox()
        self.theme.addItem("Dark", "dark")
        self.theme.addItem("Light", "light")
        self.theme.setCurrentIndex(self.theme.findData(window._theme))
        self.theme.setAccessibleName("Colour theme")
        form.addRow("&Colour theme", self.theme)
        self.text_size = QComboBox()
        for value, label in TEXT_SIZES:
            self.text_size.addItem(label, value)
        self.text_size.setCurrentIndex(self.text_size.findData(window._text_size))
        self.text_size.setAccessibleName("Text size")
        form.addRow("&Text size", self.text_size)
        body.addLayout(form)
        self.reduce_motion = QCheckBox("&Reduce motion")
        self.reduce_motion.setChecked(not window.motion.enabled)
        self.reduce_motion.setAccessibleDescription("Turn off page fades and the pulsing scan badge.")
        body.addWidget(self.reduce_motion)
        motion_hint = QLabel("Reduce motion stops page fades and the pulsing scan indicator. Written progress messages remain available.", objectName="mutedLabel")
        motion_hint.setWordWrap(True)
        body.addWidget(motion_hint)
        heading = QLabel("Using a keyboard", objectName="sectionTitle")
        body.addWidget(heading)
        guide = QLabel(
            "Tab / Shift+Tab: move between controls.\n"
            "Space: press a button or change a checkbox.\n"
            "Arrow keys: move through results and choices.\n"
            "Enter on a result: open the problem.\n"
            "Ctrl+F: search problems.\n"
            "Ctrl++ / Ctrl+−: increase or decrease text size.\n"
            "Ctrl+0: restore comfortable text.\n"
            "Ctrl+Shift+A: open these reading options.\n"
            "Alt: access the menu bar."
        )
        guide.setTextFormat(Qt.TextFormat.PlainText)
        guide.setWordWrap(True)
        body.addWidget(guide)
        body.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)
        self.theme.currentIndexChanged.connect(lambda: window._set_appearance(theme=self.theme.currentData()))
        self.text_size.currentIndexChanged.connect(lambda: window._set_appearance(size=self.text_size.currentData()))
        self.reduce_motion.toggled.connect(lambda reduced: window.motion_action.setChecked(not reduced))
        decorate_buttons(self)
