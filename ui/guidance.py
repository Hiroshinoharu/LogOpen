"""Reusable guidance and expandable controls for the desktop workflow."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.icons import decorate_buttons, icon


def problem_explanation(classification):
    """Explain known log labels without diagnosing a cause or changing the evidence."""
    if not isinstance(classification, str):
        return ""
    explanations = {
        "DNS Resolution Timeout": "Windows waited too long to look up a network address. This can help explain trouble opening websites or reaching a service.",
        "WLAN Module Stopped": "A Windows Wi-Fi component stopped running.",
        "Wi-Fi Connection Failed": "Windows recorded a Wi-Fi connection attempt that did not succeed.",
        "Wi-Fi Disconnected": "Windows recorded a Wi-Fi disconnection.",
        "Service Terminated Unexpectedly": "A background Windows service stopped unexpectedly.",
        "Defender Service Crash": "The Microsoft Defender background service stopped unexpectedly.",
        "Application Delayed System Shutdown": "An application held up Windows while the PC was shutting down.",
        "Windows Update Installation Failed": "A Windows update did not finish installing.",
        "Failed Logon": "Windows recorded an unsuccessful sign-in attempt.",
        "Account Locked Out": "Windows recorded an account lockout, which may prevent sign-in.",
        "Defender Threat Detected": "Microsoft Defender reported a possible threat. Check Windows Security for its current status.",
    }
    if classification in explanations:
        return explanations[classification]
    if "DCOM" in classification:
        return "Windows recorded a message about how apps or background services communicate. The Windows logs show which component was involved."
    return ""


class DisclosureSection(QFrame):
    """Keep optional controls available without making them the first thing to read."""

    def __init__(self, title, parent=None, *, expanded=False):
        super().__init__(parent)
        self.setObjectName("disclosureSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toggle_button = QPushButton(title, objectName="disclosureButton")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setAutoDefault(False)
        self.toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_button.toggled.connect(self.set_expanded)
        layout.addWidget(self.toggle_button)
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(12, 12, 12, 12)
        self.body.setSpacing(12)
        layout.addWidget(self.content)
        self.set_expanded(expanded)

    def set_expanded(self, expanded):
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setIcon(icon("down" if expanded else "chevron"))
        self.toggle_button.setAccessibleDescription("Expanded" if expanded else "Collapsed; activate to show more")
        self.content.setVisible(expanded)


class ScanJourney(QFrame):
    """Show the current step without offering actions that skip required work."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("scanJourney")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.steps = []
        self.captions = []
        for number, title, caption in (
            (1, "Scan", "Check recent Windows activity"),
            (2, "Review", "Find what needs attention"),
            (3, "Understand", "Read the explanation and next steps"),
        ):
            step = QFrame(objectName="journeyStep")
            row = QHBoxLayout(step)
            row.setContentsMargins(12, 9, 12, 9)
            row.setSpacing(10)
            badge = QLabel(str(number), objectName="journeyNumber")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(26, 26)
            row.addWidget(badge)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(QLabel(title, objectName="journeyTitle"))
            hint = QLabel(caption, objectName="journeyCaption")
            hint.setWordWrap(True)
            text.addWidget(hint)
            row.addLayout(text, 1)
            layout.addWidget(step, 1)
            self.steps.append(step)
            self.captions.append(hint)
        self.set_step(0)

    def set_compact(self, compact):
        for caption in self.captions:
            caption.setVisible(not compact)

    def set_step(self, current):
        self.current_step = current
        for index, step in enumerate(self.steps):
            step.setProperty("current", index == current)
            step.setAccessibleName(f"Step {index + 1}" + (", current step" if index == current else ""))
            step.style().unpolish(step)
            step.style().polish(step)


class QuickStartDialog(QDialog):
    """Explain the first scan and optional features in one short, revisitable guide."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Getting started with LogOpen")
        self.resize(510, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(QLabel("Your first scan", objectName="pageTitle"))
        for title, message in (
            ("1. Scan my PC", "LogOpen reads recent Windows activity and groups related warnings and errors. You can use the default scan options."),
            ("2. Review the results", "Start with High or Critical priority. These are clues from Windows logs, rather than a complete health check of your PC."),
            ("3. Understand a problem", "Select a row and choose Understand problem. Read the summary, then open Next steps for optional AI guidance."),
            ("Optional extras", "Previous scans are in Scan history. AI guidance needs your own OpenAI API key in Settings and may incur API charges. Scanning works without a key."),
        ):
            layout.addWidget(QLabel(title, objectName="detailFieldTitle"))
            body = QLabel(message, objectName="detailBody")
            body.setWordWrap(True)
            layout.addWidget(body)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Got it")
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        decorate_buttons(self)
