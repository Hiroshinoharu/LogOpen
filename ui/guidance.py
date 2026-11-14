"""Reusable guidance and expandable controls for the desktop workflow."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
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
        "Defender Real-Time Protection Disabled": "Windows recorded that Microsoft Defender real-time protection was turned off.",
        "Defender Protection Action": "Microsoft Defender recorded an action on a detected item. Check its current status in Windows Security.",
        "Boot Performance Report": "Windows recorded startup performance information. Compare it with a startup that felt slow.",
        "Shutdown Performance Report": "Windows recorded shutdown performance information. Compare it with a shutdown that felt slow.",
    }
    if classification in explanations:
        return explanations[classification]
    if "DCOM" in classification:
        return "Windows recorded a message about how apps or background services communicate. The Windows logs show which component was involved."
    return ""


def problem_next_steps(classification):
    """Return local, read-first checks for a known label, with a fallback for custom rules.

    Guidance is bundled with the app and never uses a model, runs a command, or
    assumes a historical event still describes the PC's current state.
    """
    guides = {
        "network": (
            "Open Settings → Network & internet and check whether your connection is currently connected.",
            "Try another website or app, then compare with another device on the same network. This helps separate a single-app problem from a wider connection problem.",
            "Compare the failure time in Windows logs with when you lost access. A historical timeout may no longer affect your connection.",
        ),
        "apps": (
            "Open Windows logs for this problem and note the application name, error message, and time.",
            "Search Start for View reliability history. Look for an application failure at the same time and open its details.",
            "Save your work before trying the affected app again. Note whether the same action causes another failure; this helps the app's support team investigate.",
        ),
        "services": (
            "Open Windows logs and note which service stopped and when.",
            "Search Start for Services and find that service. Read its current status without changing its startup type.",
            "Check whether the feature that uses the service now works. A stopped service may have recovered; repeated failures with a current symptom are useful evidence for support.",
        ),
        "defender": (
            "Open Windows Security → Virus & threat protection and read the current protection status.",
            "Open Protection history and match the item and time to this incident. Check whether action is still needed or has already been taken.",
            "Follow Windows Security's guidance for any outstanding action. On a managed PC, contact your IT team if protection settings are controlled by your organization.",
        ),
        "updates": (
            "Search Start for Windows Update settings, then open Update history. Note the failed update and error code.",
            "Check whether a later attempt succeeded or Windows says a restart is required. An earlier failure alone does not describe the current update status.",
            "If the update is still failing, search Start for Get Help and use its Windows Update troubleshooter. Keep the update name and error code for support.",
        ),
        "startup": (
            "Compare the event time in Windows logs with the startup or shutdown that felt slow.",
            "For slow startup, open Task Manager → Startup apps and inspect the enabled apps and their startup impact.",
            "Note which apps you need at sign-in and whether slowness happens every time. A performance report alone does not prove an app is the cause.",
        ),
        "signin": (
            "Open Windows logs and inspect the account, sign-in time, and failure details.",
            "Check whether this matches your own sign-in or an app using an old password. One failed sign-in does not establish that someone accessed your account.",
            "For an unexpected repeated failure or a locked work account, share the timestamps and account details with your IT team through your usual support channel.",
        ),
        "dcom": (
            "Open Windows logs and note the application or service named in the message.",
            "Check whether that application actually failed at the recorded time. A permission warning alone may have no visible effect.",
            "If the problem repeats with an app failure, check the app's support guidance using the event details. Avoid changing registry or DCOM permissions based only on this label.",
        ),
    }
    groups = {
        "DNS Resolution Timeout": "network", "WLAN Module Stopped": "network",
        "Wi-Fi Connection Failed": "network", "Wi-Fi Disconnected": "network",
        "UDP Ephemeral Port Exhaustion": "network",
        "Application Error": "apps", "Application Hang": "apps", "Windows Error Reporting": "apps",
        "Service Terminated Unexpectedly": "services",
        "Defender Service Crash": "defender", "Defender Threat Detected": "defender",
        "Defender Protection Action": "defender", "Defender Real-Time Protection Disabled": "defender",
        "Windows Update Installation Failed": "updates",
        "Boot Performance Report": "startup", "Shutdown Performance Report": "startup",
        "Application Delayed System Shutdown": "apps",
        "Failed Logon": "signin", "Account Locked Out": "signin",
        "Distributed COM": "dcom", "Codex Permission Warning": "dcom",
    }
    group = groups.get(classification) if isinstance(classification, str) else None
    if isinstance(classification, str) and "DCOM" in classification:
        group = "dcom"
    return guides.get(group, (
        "Open Windows logs for this problem. Note the provider, event ID, message, and time.",
        "Compare the event time with the symptom you noticed. The label alone does not establish a cause.",
        "If the same problem keeps happening, export the selected incident and share it with your usual support contact after reviewing the report's contents.",
    ))


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
            badge.setMinimumSize(30, 30)
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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        reading = QVBoxLayout(content)
        reading.setContentsMargins(0, 0, 12, 0)
        reading.setSpacing(16)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        reading.addWidget(QLabel("Your first scan", objectName="pageTitle"))
        for title, message in (
            ("1. Scan my PC", "Choose a symptom or keep General check, then choose Scan my PC. LogOpen reads recent Windows activity and groups related warnings and errors."),
            ("2. Review the results", "The Start here card highlights an unreviewed problem in your current view. These are clues from Windows logs, rather than a complete health check of your PC."),
            ("3. Understand a problem", "Choose Understand this problem and read Checks you can try in Summary. These built-in checks work without AI. Next steps (AI) offers optional extra guidance."),
            ("Optional extras", "Previous scans are in Scan history. AI guidance needs your own OpenAI API key in Settings and may incur API charges. Scanning works without a key."),
        ):
            heading = QLabel(title, objectName="detailFieldTitle")
            heading.setWordWrap(True)
            reading.addWidget(heading)
            body = QLabel(message, objectName="detailBody")
            body.setWordWrap(True)
            reading.addWidget(body)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Got it")
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        decorate_buttons(self)
