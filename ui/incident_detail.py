"""Read-only incident details, rendered entirely from an existing summary."""

from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def _text(value, missing="Not available"):
    """Format known field values without displaying Python containers."""
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(item) for item in value) or missing
    if isinstance(value, (str, int, float, datetime, timedelta)):
        return str(value).strip() or missing
    return missing


def _body_label():
    label = QLabel()
    label.setObjectName("detailBody")
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def _section(title):
    panel = QFrame()
    panel.setObjectName("detailSection")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(12)
    heading = QLabel(title)
    heading.setObjectName("sectionTitle")
    layout.addWidget(heading)
    return panel, layout


def _field_form(fields):
    form = QFormLayout()
    form.setHorizontalSpacing(24)
    form.setVerticalSpacing(10)
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


class IncidentDetailWidget(QWidget):
    """Scrollable summary, scoring, AI analysis, and source event sections."""

    back_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.incident = None
        self.setObjectName("incidentDetail")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        navigation = QHBoxLayout()
        self.back_button = QPushButton("← Back to incidents")
        self.back_button.setObjectName("secondaryButton")
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.clicked.connect(self.back_requested.emit)
        navigation.addWidget(self.back_button)
        navigation.addStretch()
        layout.addLayout(navigation)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("detailScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("detailContent")
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 12, 0)
        self.content_layout.setSpacing(14)
        self.scroll_area.setWidget(content)
        layout.addWidget(self.scroll_area, 1)

        overview, overview_layout = _section("Incident summary")
        self.classification = _body_label()
        self.classification.setObjectName("detailTitle")
        overview_layout.addWidget(self.classification)
        headline = QHBoxLayout()
        self.priority = QLabel()
        self.priority.setObjectName("detailPriority")
        self.priority.setTextFormat(Qt.TextFormat.PlainText)
        self.score = QLabel()
        self.score.setObjectName("detailScore")
        self.score.setTextFormat(Qt.TextFormat.PlainText)
        headline.addWidget(self.priority)
        headline.addStretch()
        headline.addWidget(self.score)
        overview_layout.addLayout(headline)
        self.summary = _body_label()
        self.summary.setAccessibleName("Incident summary")
        overview_layout.addWidget(self.summary)
        self.content_layout.addWidget(overview)

        metadata, metadata_layout = _section("Incident information")
        form, self.fields = _field_form((
            ("log_type", "Log type"),
            ("providers", "Providers"),
            ("event_ids", "Event IDs"),
            ("event_count", "Event count"),
            ("incident_duration", "Duration"),
            ("is_recurring", "Recurrence status"),
            ("recurrence_count_24h", "24-hour recurrence count"),
            ("recurrence_count_7d", "7-day recurrence count"),
        ))
        metadata_layout.addLayout(form)
        self.content_layout.addWidget(metadata)

        scoring, scoring_layout = _section("Score breakdown")
        form, self.score_fields = _field_form((
            ("severity", "Severity"),
            ("event_count", "Event count"),
            ("classification_impact", "Classification impact"),
            ("recurrence", "Recurrence"),
            ("duration", "Duration"),
        ))
        scoring_layout.addLayout(form)
        self.content_layout.addWidget(scoring)

        analysis, analysis_layout = _section("AI analysis")
        self.ai_missing = _body_label()
        self.ai_missing.setText("AI analysis was not generated for this incident.")
        analysis_layout.addWidget(self.ai_missing)
        self.ai_content = QWidget()
        ai_layout = QVBoxLayout(self.ai_content)
        ai_layout.setContentsMargins(0, 0, 0, 0)
        ai_layout.setSpacing(10)
        self.ai_fields = {}
        for key, title in (
            ("explanation", "Explanation"),
            ("likely_causes", "Likely causes"),
            ("recommended_actions", "Recommended actions"),
            ("remediation_notes", "Remediation notes"),
        ):
            caption = QLabel(title)
            caption.setObjectName("detailFieldTitle")
            value = _body_label()
            value.setAccessibleName(title)
            ai_layout.addWidget(caption)
            ai_layout.addWidget(value)
            self.ai_fields[key] = value
        analysis_layout.addWidget(self.ai_content)
        self.content_layout.addWidget(analysis)

        sources, sources_layout = _section("Source events")
        self.source_events = QPlainTextEdit()
        self.source_events.setObjectName("sourceEvents")
        self.source_events.setReadOnly(True)
        self.source_events.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.source_events.setMinimumHeight(160)
        self.source_events.setMaximumHeight(260)
        self.source_events.setAccessibleName("Source event messages")
        sources_layout.addWidget(self.source_events)
        self.content_layout.addWidget(sources)
        self.content_layout.addStretch()
        self.set_incident(None)

    def set_incident(self, incident):
        """Replace every displayed value; never modify or re-analyze the input."""
        self.incident = incident
        data = incident if isinstance(incident, dict) else {}
        self.classification.setText(_text(data.get("incident_classification")))
        priority = _text(data.get("incident_priority"), "Unknown")
        self.priority.setText(f"{priority} priority")
        self.priority.setProperty("priority", priority)
        self.priority.style().unpolish(self.priority)
        self.priority.style().polish(self.priority)
        score = data.get("incident_score")
        self.score.setText(f"{_text(score)} / 100" if score is not None else "Score not available")
        self.summary.setText(_text(data.get("summary_text"), "No summary available."))
        for key, label in self.fields.items():
            label.setText(_text(data.get(key)))
        recurring = data.get("is_recurring")
        self.fields["is_recurring"].setText(
            "Recurring" if recurring is True
            else "Not recurring" if recurring is False
            else "Not available"
        )

        breakdown = data.get("incident_score_breakdown")
        breakdown = breakdown if isinstance(breakdown, dict) else {}
        for key, label in self.score_fields.items():
            label.setText(_text(breakdown.get(key)))

        analysis = data.get("llm_analysis")
        analysis = analysis if isinstance(analysis, dict) else {}
        self.ai_missing.setVisible(not analysis)
        self.ai_content.setVisible(bool(analysis))
        for key, label in self.ai_fields.items():
            value = analysis.get(key)
            if isinstance(value, (list, tuple)):
                label.setText("\n".join(f"• {_text(item)}" for item in value) or "None provided.")
            else:
                label.setText(_text(value, "None provided."))

        events = data.get("events")
        blocks = []
        for index, event in enumerate(events if isinstance(events, (list, tuple)) else [], start=1):
            if not isinstance(event, dict):
                continue
            blocks.append("\n".join((
                f"Event {index} · {_text(event.get('level'), 'Unknown level')} · "
                f"ID {_text(event.get('event_id'))}",
                f"Provider: {_text(event.get('provider'))}",
                f"Log: {_text(event.get('log_type'))}  |  "
                f"Time: {_text(event.get('time_generated'))}",
                "",
                _text(event.get("message"), "No message available."),
            )))
        self.source_events.setPlainText("\n\n------------------------\n\n".join(blocks)
                                       or "No source events available for this incident.")
        self.source_events.verticalScrollBar().setValue(0)
        self.scroll_area.verticalScrollBar().setValue(0)
