"""Read-only incident presentation. This module never scans or requests analysis."""

from datetime import datetime, timedelta
from math import isfinite

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.table_layout import RoomyTable
from ui.table_sorting import SortableTableItem, numeric_key, timestamp_key
from ui.timeline import TimelineWidget


def _text(value, missing="Not available"):
    """Render supported scalar values or lists/tuples, with a fallback for missing data.

    Strings are stripped; lists/tuples recursively become comma-separated text.
    Numbers and date/duration values use str(). Unsupported objects use missing
    instead of exposing their Python representation.
    """
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(item) for item in value) or missing
    if isinstance(value, (str, int, float, datetime, timedelta)):
        return str(value).strip() or missing
    return missing


def _body_label(text="", name="detailBody"):
    """Create a selectable plain-text label that wraps without forcing horizontal width."""
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def _section(title):
    """Return a styled section frame and its vertical layout, already containing a title."""
    panel = QFrame()
    panel.setObjectName("detailSection")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(20, 18, 20, 20)
    layout.setSpacing(14)
    layout.addWidget(_body_label(title, "sectionTitle"))
    return panel, layout


def _scroll_page():
    """Return a vertically scrollable page and the layout used to populate its contents."""
    scroll = QScrollArea()
    scroll.setObjectName("detailScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    content = QWidget()
    content.setObjectName("detailContent")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 16, 12, 0)
    layout.setSpacing(16)
    scroll.setWidget(content)
    return scroll, layout


def _field_form(fields):
    """Build a wrapping metadata form and return its layout plus key-to-value-label mapping."""
    form = QFormLayout()
    form.setHorizontalSpacing(20)
    form.setVerticalSpacing(12)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    labels = {}
    for key, title in fields:
        caption = QLabel(title)
        caption.setObjectName("mutedLabel")
        value = _body_label()
        value.setAccessibleName(title)
        form.addRow(caption, value)
        labels[key] = value
    return form, labels


class DiagnosticStepWidget(QFrame):
    """A recommendation card; the only command action copies text to the clipboard."""

    def __init__(self, number, step, parent=None):
        """Render a numbered diagnostic description, risk, and optional command.

        step is a supplied analysis dictionary. Nonblank command text appears in a
        read-only box with a shell label and exact-text clipboard action. Missing
        commands create no command controls; no shell process is ever started.
        """
        super().__init__(parent)
        self.setObjectName("diagnosticStep")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        self.number = QLabel(f"Step {number}", objectName="detailFieldTitle")
        heading.addWidget(self.number)
        heading.addStretch()
        risk_labels = {"safe": "SAFE", "caution": "CAUTION", "system_change": "SYSTEM CHANGE"}
        risk = step.get("risk_level")
        risk = risk if isinstance(risk, str) and risk in risk_labels else "unknown"
        self.risk_badge = QLabel(risk_labels.get(risk, "UNKNOWN"), objectName="diagnosticRisk")
        self.risk_badge.setProperty("risk", risk)
        self.risk_badge.setAccessibleName(f"Step {number} risk: {self.risk_badge.text()}")
        heading.addWidget(self.risk_badge)
        layout.addLayout(heading)
        self.description = _body_label(_text(step.get("description"), "No description provided."))
        self.description.setAccessibleName(f"Step {number} description")
        layout.addWidget(self.description)

        self.command_box = None
        self.copy_button = None
        self.shell_label = None
        command = step.get("command")
        if isinstance(command, str) and command.strip():
            command_heading = QHBoxLayout()
            shell_labels = {"powershell": "PowerShell", "cmd": "Command Prompt", "bash": "Bash"}
            shell = step.get("shell")
            shell_name = (
                shell_labels.get(shell, "Shell not specified")
                if isinstance(shell, str) else "Shell not specified"
            )
            self.shell_label = _body_label(f"Command · {shell_name}", "diagnosticShell")
            self.shell_label.setAccessibleName(f"Step {number} command shell")
            command_heading.addWidget(self.shell_label, 1)
            self.copy_button = QPushButton("Copy command", objectName="secondaryButton")
            self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.copy_button.setAccessibleName(f"Copy command for step {number}")
            self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(command))
            command_heading.addWidget(self.copy_button)
            layout.addLayout(command_heading)
            self.command_box = QPlainTextEdit()
            self.command_box.setObjectName("diagnosticCommand")
            self.command_box.setReadOnly(True)
            self.command_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            self.command_box.setFixedHeight(72)
            self.command_box.setAccessibleName(f"Step {number} recommended command")
            self.command_box.setPlainText(command)
            layout.addWidget(self.command_box)


class SourceEventsWidget(QWidget):
    """Select a source event and read its complete, literal message."""

    def __init__(self, parent=None):
        """Build an event-selector table and read-only message reader in a vertical splitter."""
        super().__init__(parent)
        self._events = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)
        self.caption = _body_label("", "mutedLabel")
        layout.addWidget(self.caption)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(10)
        layout.addWidget(self.splitter, 1)

        self.table = QTableWidget(0, 4)
        self.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.table.setSortingEnabled(True)
        self.table.setObjectName("sourceEventTable")
        self.table.setHorizontalHeaderLabels(["Timestamp", "Provider", "Event ID", "Level"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setMinimumHeight(100)
        self.table.setAccessibleName("Source events")
        self.table.setAccessibleDescription("Select an event to read its full message below.")
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 96)
        self.roomy_table = RoomyTable(self.table, [280, 440, 110, 150], flexible=1)
        self.table.itemSelectionChanged.connect(self._show_selected_event)
        self.splitter.addWidget(self.table)

        reader = QFrame()
        reader.setObjectName("detailSection")
        reader_layout = QVBoxLayout(reader)
        reader_layout.setContentsMargins(12, 12, 12, 12)
        reader_layout.setSpacing(8)
        self.event_context = _body_label("", "mutedLabel")
        reader_layout.addWidget(self.event_context)
        self.message = QPlainTextEdit()
        self.message.setObjectName("sourceEvents")
        self.message.setReadOnly(True)
        self.message.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.message.setMinimumHeight(80)
        self.message.setAccessibleName("Selected source event message")
        reader_layout.addWidget(self.message, 1)
        self.splitter.addWidget(reader)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)

    def set_events(self, events):
        """Replace the event list and select its first valid dictionary, if present.

        Accepts a list/tuple, ignores malformed entries, clears stale selection and
        message content, and resets scroll positions. Event dictionaries are shared
        without mutation; table rows store indexes for message lookup.
        Sorting pauses during insertion, then restores the chosen column/order.
        """
        self._events = [event for event in events if isinstance(event, dict)] if isinstance(
            events, (list, tuple)
        ) else []
        # Block selection signals until every cell belongs to the new incident.
        self.table.blockSignals(True)
        sorting_enabled = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(self._events))
        for row, event in enumerate(self._events):
            for column, key in enumerate(("time_generated", "provider", "event_id", "level")):
                value = event.get(key)
                if column == 0:
                    sort_key = timestamp_key(value)
                elif column == 2:
                    sort_key = numeric_key(value)
                elif column == 3:
                    sort_key = {
                        "Information": 1, "Success Audit": 1, "Warning": 2,
                        "Error": 3, "Failure Audit": 3, "Critical": 4,
                    }.get(value, 0)
                else:
                    sort_key = _text(value).casefold()
                item = SortableTableItem(_text(value), sort_key)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(sorting_enabled)
        self.roomy_table.fit_contents()
        self.table.blockSignals(False)
        self.caption.setText(
            f"{len(self._events)} source events · Select a row to read its full message."
            if self._events else "No source events available for this incident."
        )
        self.table.setVisible(bool(self._events))
        self.table.scrollToTop()
        if self._events:
            self.table.setCurrentCell(0, 0)
        self._show_selected_event()

    def _show_selected_event(self):
        """Display the selected event metadata and literal full message, resetting message
        scroll.
        """
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.event_context.setText("")
            self.message.setPlainText("No source events available for this incident.")
            return
        index = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        event = self._events[index]
        self.event_context.setText(
            f"Event {index + 1} of {len(self._events)} · {_text(event.get('level'))} · "
            f"ID {_text(event.get('event_id'))}\n"
            f"{_text(event.get('provider'))} · {_text(event.get('log_type'))} · "
            f"{_text(event.get('time_generated'))}"
        )
        message = event.get("message")
        self.message.setPlainText(
            message if isinstance(message, str) and message.strip() else "No message available."
        )
        self.message.verticalScrollBar().setValue(0)


class IncidentDetailWidget(QWidget):
    """A full incident page with overview, existing AI analysis, and source events."""

    back_requested = pyqtSignal()
    analysis_requested = pyqtSignal()
    review_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        """Build overview, AI, and source-event tabs and wire Back keyboard shortcuts.

        Initializes the empty view. back_requested lets the owning window navigate;
        constructing this widget does not collect events or request analysis.
        """
        super().__init__(parent)
        self.incident = None
        self._wide_overview = None
        self.setObjectName("incidentDetail")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        navigation = QHBoxLayout()
        self.back_button = QPushButton("← Back to dashboard")
        self.back_button.setObjectName("secondaryButton")
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.clicked.connect(self.back_requested.emit)
        navigation.addWidget(self.back_button)
        navigation.addStretch()
        navigation.addWidget(QLabel("INCIDENT DETAILS", objectName="metricTitle"))
        layout.addLayout(navigation)
        self.back_action = QAction("Back to dashboard", self)
        self.back_action.setShortcuts([QKeySequence("Esc"), QKeySequence("Alt+Left")])
        self.back_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.back_action.triggered.connect(self.back_requested.emit)
        self.addAction(self.back_action)

        headline = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(8)
        self.classification = _body_label("", "detailTitle")
        heading.addWidget(self.classification)
        self.priority = QLabel(objectName="detailPriority")
        self.priority.setTextFormat(Qt.TextFormat.PlainText)
        heading.addWidget(self.priority, 0, Qt.AlignmentFlag.AlignLeft)
        headline.addLayout(heading, 1)
        score_column = QVBoxLayout()
        score_column.addWidget(QLabel("INCIDENT SCORE", objectName="metricTitle"))
        self.score = QLabel(objectName="detailScore")
        self.score.setTextFormat(Qt.TextFormat.PlainText)
        self.score.setAccessibleName("Incident score out of 100")
        score_column.addWidget(self.score)
        headline.addLayout(score_column)
        layout.addLayout(headline)

        actions = QHBoxLayout()
        self.review_badge = _body_label("Active", "mutedLabel")
        actions.addWidget(self.review_badge)
        actions.addStretch()
        self.review_buttons = []
        for title, state in (("Acknowledge", "acknowledged"), ("Ignore", "ignored"), ("Restore", "active")):
            button = QPushButton(title, objectName="secondaryButton")
            button.clicked.connect(lambda _checked=False, state=state: self.review_requested.emit(state))
            actions.addWidget(button)
            self.review_buttons.append(button)
        self.review_controls = QWidget()
        self.review_controls.setLayout(actions)
        self.action_feedback = _body_label("", "mutedLabel")

        self.tabs = QTabWidget()
        self.tabs.setObjectName("detailTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        layout.addWidget(self.tabs, 1)
        self.scroll_area, overview_layout = _scroll_page()
        self.tabs.addTab(self.scroll_area, "Overview")
        overview_layout.addWidget(self.review_controls)
        summary_card, summary_layout = _section("Incident summary")
        self.summary = _body_label()
        self.summary.setAccessibleName("Incident summary")
        summary_layout.addWidget(self.summary)
        overview_layout.addWidget(summary_card)

        self.overview_grid = QGridLayout()
        self.overview_grid.setSpacing(16)
        self.metadata, metadata_layout = _section("Incident information")
        form, self.fields = _field_form((
            ("log_type", "Log type"), ("computer_name", "Computer"),
            ("providers", "Providers"), ("event_ids", "Event IDs"),
            ("event_count", "Event count"), ("incident_duration", "Duration"),
            ("time_generated_start", "First event"), ("time_generated_end", "Last event"),
            ("is_recurring", "Recurrence status"),
            ("recurrence_count_24h", "Recurrences · 24 hours"),
            ("recurrence_count_7d", "Recurrences · 7 days"),
        ))
        metadata_layout.addLayout(form)
        metadata_layout.addStretch()

        self.scoring, scoring_layout = _section("Score breakdown")
        scoring_layout.addWidget(_body_label(
            "Contributions in points. The final incident score is capped at 100.", "mutedLabel"
        ))
        self.score_fields = {}
        self.score_bars = {}
        for key, title in (
            ("severity", "Severity"), ("event_count", "Event count"),
            ("classification_impact", "Classification impact"),
            ("recurrence", "Recurrence"), ("duration", "Duration"),
        ):
            row = QHBoxLayout()
            row.addWidget(_body_label(title), 1)
            value = QLabel(objectName="detailFieldTitle")
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setAccessibleName(f"{title} score contribution")
            row.addWidget(value)
            scoring_layout.addLayout(row)
            bar = QProgressBar()
            bar.setObjectName("scoreContribution")
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setAccessibleName(f"{title} points out of 100")
            scoring_layout.addWidget(bar)
            self.score_fields[key] = value
            self.score_bars[key] = bar
        scoring_layout.addStretch()
        overview_layout.addLayout(self.overview_grid)
        overview_layout.addStretch()
        self._arrange_overview()

        self.ai_scroll, ai_layout = _scroll_page()
        self.tabs.addTab(self.ai_scroll, "AI analysis")
        ai_layout.addWidget(self.action_feedback)
        self.ai_status = _body_label("", "mutedLabel")
        self.ai_status.setAccessibleName("AI analysis status")
        ai_layout.addWidget(self.ai_status)
        self.analyze_button = QPushButton("Analyze / retry AI", objectName="secondaryButton")
        self.analyze_button.setToolTip("Send this incident to OpenAI using your saved key and model. API charges may apply.")
        self.analyze_button.clicked.connect(self.analysis_requested.emit)
        ai_layout.addWidget(self.analyze_button)
        self.ai_missing, missing_layout = _section("No AI analysis")
        self.ai_missing_message = _body_label("AI analysis was not generated for this incident.")
        missing_layout.addWidget(self.ai_missing_message)
        ai_layout.addWidget(self.ai_missing)
        self.ai_content = QWidget()
        analysis_layout = QVBoxLayout(self.ai_content)
        analysis_layout.setContentsMargins(0, 0, 0, 0)
        analysis_layout.setSpacing(16)
        analysis_layout.addWidget(_body_label("Saved AI analysis", "mutedLabel"))
        self.diagnostic_section, self.diagnostic_layout = _section("Diagnostic Steps")
        self.diagnostic_empty = _body_label(
            "No structured diagnostic steps were generated for this incident.", "mutedLabel"
        )
        self.diagnostic_layout.addWidget(self.diagnostic_empty)
        self.diagnostic_cards = []
        self.ai_fields = {}
        for key, title in (
            ("explanation", "Explanation"), ("likely_causes", "Likely causes"),
            ("recommended_actions", "Recommended actions"),
            ("remediation_notes", "Remediation notes"),
        ):
            card, card_layout = _section(title)
            value = _body_label()
            value.setAccessibleName(title)
            card_layout.addWidget(value)
            analysis_layout.addWidget(card)
            self.ai_fields[key] = value
            if key == "recommended_actions":
                analysis_layout.addWidget(self.diagnostic_section)
        ai_layout.addWidget(self.ai_content)
        ai_layout.addStretch()

        self.sources = SourceEventsWidget()
        self.tabs.addTab(self.sources, "Source events")
        self.timeline = TimelineWidget()
        self.tabs.addTab(self.timeline, "Timeline")
        self.set_incident(None)

    def _set_diagnostic_steps(self, steps):
        """Replace diagnostic cards with supplied dictionary steps and update the empty state.

        Old widgets are removed and scheduled for deletion so previous incidents'
        commands cannot remain visible. Cards only display and copy suggestions.
        """
        for card in self.diagnostic_cards:
            self.diagnostic_layout.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.diagnostic_cards.clear()
        if isinstance(steps, (list, tuple)):
            for step in steps:
                if isinstance(step, dict):
                    card = DiagnosticStepWidget(len(self.diagnostic_cards) + 1, step)
                    self.diagnostic_layout.addWidget(card)
                    self.diagnostic_cards.append(card)
        self.diagnostic_empty.setVisible(not self.diagnostic_cards)

    def resizeEvent(self, event):
        """Process the Qt resize event, then adapt the overview layout to available width."""
        super().resizeEvent(event)
        self._arrange_overview()

    def _arrange_overview(self):
        """Use overview columns at widths of at least 900 pixels; otherwise stack sections."""
        wide = self.width() >= 900
        if wide == self._wide_overview:
            return
        self._wide_overview = wide
        self.overview_grid.removeWidget(self.metadata)
        self.overview_grid.removeWidget(self.scoring)
        self.overview_grid.addWidget(self.metadata, 0, 0)
        self.overview_grid.addWidget(self.scoring, 0 if wide else 1, 1 if wide else 0)
        self.overview_grid.setColumnStretch(0, 3)
        self.overview_grid.setColumnStretch(1, 2 if wide else 0)

    def update_review_state(self):
        """Refresh review controls from current metadata without rebuilding evidence tabs."""
        data = self.incident if isinstance(self.incident, dict) else {}
        state = data.get("review_state", "active")
        self.review_badge.setText(f"Review: {state.capitalize()}")
        for button, target in zip(self.review_buttons, ("acknowledged", "ignored", "active")):
            button.setEnabled(bool(data) and state != target)

    def set_incident(self, incident):
        """Replace all displayed fields from an incident dictionary, or clear the view.

        Shows supplied metadata, score contributions, AI outcome/content, diagnostic
        steps, and source events. Missing fields receive fallbacks; numeric zero
        remains distinct from missing data. Tabs and scroll positions reset for
        each incident. The source dictionary is retained without modification;
        scores and AI explanations are never recomputed by this view.
        """
        self.incident = incident
        self.action_feedback.clear()
        self.analyze_button.setEnabled(incident is not None)
        self.update_review_state()
        data = incident if isinstance(incident, dict) else {}
        self.classification.setText(_text(data.get("incident_classification")))
        priority = _text(data.get("incident_priority"), "Unknown")
        self.priority.setText(f"{priority} priority")
        self.priority.setProperty("priority", priority)
        self.priority.style().unpolish(self.priority)
        self.priority.style().polish(self.priority)
        score = data.get("incident_score")
        self.score.setText(f"{_text(score)} / 100" if score is not None else "Unavailable")
        self.summary.setText(_text(data.get("summary_text"), "No summary available."))
        for key, label in self.fields.items():
            label.setText(_text(data.get(key)))
        self.fields["log_type"].setText(_text(data.get("log_types") or data.get("log_type")))
        recurring = data.get("is_recurring")
        self.fields["is_recurring"].setText(
            "Recurring" if recurring is True
            else "Not recurring" if recurring is False else "Not available"
        )

        breakdown = data.get("incident_score_breakdown")
        breakdown = breakdown if isinstance(breakdown, dict) else {}
        for key, label in self.score_fields.items():
            value = breakdown.get(key)
            numeric = type(value) in (int, float) and isfinite(value)
            label.setText(f"{value} pts" if numeric else "Not available")
            self.score_bars[key].setValue(int(max(0, min(value, 100))) if numeric else 0)
            self.score_bars[key].setVisible(numeric)

        analysis = data.get("llm_analysis")
        analysis = analysis if isinstance(analysis, dict) else {}
        status = data.get("llm_analysis_status", "completed" if analysis else None)
        messages = {
            "disabled": "AI analysis is disabled in Settings.",
            "not_selected": "This incident was not selected for AI analysis.",
            "failed": "AI analysis failed. Check AI Settings and try again.",
            "completed": "AI analysis completed.",
        }
        self.ai_status.setText({
            "disabled": "AI disabled", "not_selected": "Not selected",
            "failed": "AI analysis failed", "completed": "AI analysis completed",
        }.get(status, ""))
        self.ai_missing_message.setText(_text(
            data.get("llm_analysis_message") or messages.get(status),
            "AI analysis was not generated for this incident.",
        ))
        self.ai_missing.setVisible(not analysis)
        self.ai_content.setVisible(bool(analysis))
        for key, label in self.ai_fields.items():
            value = analysis.get(key)
            if isinstance(value, (list, tuple)):
                label.setText("\n\n".join(
                    f"{index}. {_text(item)}" if key == "recommended_actions" else f"• {_text(item)}"
                    for index, item in enumerate(value, start=1)
                ) or "None provided.")
            else:
                label.setText(_text(value, "None provided."))
        self._set_diagnostic_steps(analysis.get("diagnostic_steps"))

        self.sources.set_events(data.get("events"))
        self.timeline.set_incidents([data] if data else [])
        self.tabs.setTabText(2, f"Source events ({self.sources.table.rowCount()})")
        self.tabs.setCurrentIndex(0)
        self.scroll_area.verticalScrollBar().setValue(0)
        self.ai_scroll.verticalScrollBar().setValue(0)
