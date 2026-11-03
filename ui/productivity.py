"""Local review workflow, grouped display, coverage, and focused rechecks."""

from datetime import datetime, timezone

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QSplitter, QWidget

from history_store import incident_key
from incident_followup import focused_options, followup_summary
from ui.appearance import themed_stylesheet
from ui.icons import icon, retint_icons
from ui.insights import CoverageDialog, IncidentPreview, relative_time
from ui.table_sorting import numeric_key, timestamp_key


class ProductivityMixin:
    """Add optional display aids while keeping original incident dictionaries authoritative."""

    def _setup_productivity(self, results_layout):
        self._expanded_groups = set()
        self._group_members = {}
        self._undo_review = []
        self._check_again_context = None
        self._focused_scan_options = None
        self._displayed_stamp = None
        self.problems_card.activated.connect(lambda: self.priority_filter.setCurrentIndex(0))
        self.problems_card.setAccessibleName("Show all priorities")
        self.problems_card.setToolTip("Show all priorities in the current view")
        self.attention_card.activated.connect(lambda: self.priority_filter.setCurrentText("High and Critical"))
        self.attention_card.setAccessibleName("Filter to High and Critical priority")
        self.attention_card.setToolTip("Show High and Critical problems in the current view")
        self.events_card.activated.connect(self._show_timeline)
        self.events_card.setAccessibleName("Show timeline of filtered problems")
        self.events_card.setToolTip("Inspect the Windows records behind the filtered problems")
        self.group_action = QAction("Group repeated problems", self, checkable=True)
        self.group_action.setChecked(str(self._ui_settings.value("ui/group_problems", False)).lower() in ("true", "1"))
        self.view_menu.addAction(self.group_action)
        self.group_action.toggled.connect(self._grouping_changed)
        self.expand_group_action = self.view_menu.addAction("Expand / collapse selected occurrences", self._toggle_selected_group)
        self.incident_table.addAction(self.expand_group_action)
        self.incident_table.cellClicked.connect(self._group_name_clicked)
        self.preview_action = QAction("Show selection preview on wide windows", self, checkable=True)
        self.preview_action.setChecked(str(self._ui_settings.value("ui/preview", True)).lower() in ("true", "1"))
        self.view_menu.addAction(self.preview_action)
        self.preview_action.toggled.connect(self._preview_changed)
        self.results_splitter = QSplitter(Qt.Orientation.Horizontal)
        results_layout.replaceWidget(self.results_stack, self.results_splitter)
        self.results_splitter.addWidget(self.results_stack)
        self.preview = IncidentPreview()
        self.results_splitter.addWidget(self.preview)
        self.results_splitter.setChildrenCollapsible(False)
        self.results_splitter.setStretchFactor(0, 3)
        self.results_splitter.setStretchFactor(1, 1)
        self.preview.open_requested.connect(self._open_incident_detail)
        self.preview.expand_requested.connect(self._toggle_selected_group)
        self.preview.hide()

        self.filter_chips = QWidget()
        self.filter_chip_layout = QHBoxLayout(self.filter_chips)
        self.filter_chip_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_chip_layout.setSpacing(5)
        results_layout.insertWidget(2, self.filter_chips)
        self.filter_chips.hide()
        self.undo_button = QPushButton("Undo", objectName="secondaryButton")
        self.undo_button.setToolTip("Undo the last review or temporary hiding change (Ctrl+Z)")
        self.undo_button.clicked.connect(self._undo_review_change)
        self.statusBar().addPermanentWidget(self.undo_button)
        self.undo_button.hide()
        self.undo_review_action = QAction("Undo last review change", self)
        self.undo_review_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_review_action.setEnabled(False)
        self.undo_review_action.triggered.connect(self._undo_review_change)
        self.review_menu.addAction(self.undo_review_action)
        self.incident_detail.note_saved.connect(self._save_incident_note)
        self.incident_detail.snooze_requested.connect(self._snooze_incident)
        self.incident_detail.check_requested.connect(self._check_again)
        self.coverage_button.clicked.connect(self._show_coverage)
        self.coverage_button.setEnabled(False)
        self.view_menu.addAction("What was checked?", self._show_coverage)

        appearance = self.view_menu.addMenu("Appearance")
        theme_group = QActionGroup(self)
        self._theme = self._ui_settings.value("ui/theme", "dark")
        if self._theme not in ("dark", "light"):
            self._theme = "dark"
        for key, label in (("dark", "Dark theme"), ("light", "Light theme")):
            action = appearance.addAction(label)
            action.setCheckable(True)
            theme_group.addAction(action)
            action.setChecked(key == self._theme)
            action.triggered.connect(lambda _checked=False, value=key: self._set_appearance(theme=value))
        appearance.addSeparator()
        size_group = QActionGroup(self)
        self._text_size = self._ui_settings.value("ui/text_size", "normal")
        if self._text_size not in ("normal", "large", "larger"):
            self._text_size = "normal"
        for key, label in (("normal", "Normal text"), ("large", "Large text"), ("larger", "Larger text")):
            action = appearance.addAction(label)
            action.setCheckable(True)
            size_group.addAction(action)
            action.setChecked(key == self._text_size)
            action.triggered.connect(lambda _checked=False, value=key: self._set_appearance(size=value))
        self._set_appearance(save=False)
        self._review_timer = QTimer(self)
        self._review_timer.setInterval(60_000)
        self._review_timer.timeout.connect(self._refresh_local_preferences)
        self._review_timer.start()
        self._setup_assistance()

    def _set_appearance(self, *, theme=None, size=None, save=True):
        self._theme = theme or self._theme
        self._text_size = size or self._text_size
        self.setStyleSheet(themed_stylesheet(self._base_stylesheet, self._theme, self._text_size))
        retint_icons(self, self._theme == "light")
        if save:
            self._ui_settings.setValue("ui/theme", self._theme)
            self._ui_settings.setValue("ui/text_size", self._text_size)
            self._ui_settings.sync()
        self._update_layout_density()

    def _grouping_changed(self, enabled):
        self._ui_settings.setValue("ui/group_problems", enabled)
        self._ui_settings.sync()
        self._apply_filters()

    def _preview_changed(self, enabled):
        self._ui_settings.setValue("ui/preview", enabled)
        self._ui_settings.sync()
        self._update_layout_density()

    def _refresh_groups(self):
        """Collapse display rows only; keep every matched occurrence available to export and review."""
        groups = {}
        for index, item in enumerate(self._incidents):
            if self._matches_filters(item):
                groups.setdefault(incident_key(item), []).append(index)
        self._group_members = groups
        ranks = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
        representatives = {key: max(members, key=lambda i: (
            ranks.get(self._incidents[i].get("incident_priority"), 0),
            numeric_key(self._incidents[i].get("incident_score")),
            timestamp_key(self._incidents[i].get("time_generated_end")),
        )) for key, members in groups.items()}
        grouped = self.group_action.isChecked()
        for row in range(self.incident_table.rowCount()):
            cell = self.incident_table.item(row, 0)
            if cell is None:
                continue
            index = cell.data(Qt.ItemDataRole.UserRole)
            incident = self._incidents[index]
            key = incident_key(incident)
            members = groups.get(key, [])
            matched = self._matches_filters(incident)
            collapsed = grouped and key not in self._expanded_groups
            self.incident_table.setRowHidden(row, not matched or (collapsed and representatives.get(key) != index))
            name = self.incident_table.item(row, 1)
            label = str(incident.get("incident_classification", "Unknown"))
            if grouped and len(members) > 1 and representatives[key] == index:
                latest = max((self._incidents[i].get("time_generated_end") or self._incidents[i].get("time_generated_start") for i in members), key=timestamp_key)
                label += f" · {len(members)} occurrences · {relative_time(latest)}"
                name.setIcon(icon("chevron" if collapsed else "down", light=self._theme == "light"))
                name.setToolTip("Click this problem name to expand or collapse its occurrences. Open full details to inspect one occurrence.")
            else:
                name.setIcon(icon("file", light=self._theme == "light") if grouped else QIcon())
            name.setText(label)
        self._update_preview()

    def _group_name_clicked(self, row, column):
        if column == 1 and self.group_action.isChecked():
            self._toggle_selected_group()

    def _toggle_selected_group(self):
        incident = self._selected_incident()
        if incident is None or not self.group_action.isChecked():
            return
        key = incident_key(incident)
        if len(self._group_members.get(key, [])) < 2:
            return
        if key in self._expanded_groups:
            self._expanded_groups.remove(key)
        else:
            self._expanded_groups.add(key)
        self._apply_filters()

    def _update_preview(self):
        if not hasattr(self, "preview"):
            return
        incident = self._selected_incident()
        key = incident_key(incident) if incident is not None else ""
        count = len(self._group_members.get(key, [])) if self.group_action.isChecked() else 1
        self.preview.set_incident(incident, count, key in self._expanded_groups)
        self.expand_group_action.setEnabled(self.group_action.isChecked() and count > 1)

    def _refresh_filter_chips(self):
        while self.filter_chip_layout.count():
            item = self.filter_chip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        chips = []
        query = self.search_input.text().strip()
        if query:
            chips.append((f"Search: {query}", lambda: self.search_input.clear()))
        if self.priority_filter.currentIndex() != 0:
            chips.append((self.priority_filter.currentText(), lambda: self.priority_filter.setCurrentIndex(0)))
        if self.review_filter.currentIndex() != 0:
            chips.append((self.review_filter.currentText(), lambda: self.review_filter.setCurrentIndex(0)))
        for key in ("provider", "classification", "dates"):
            if self._extra_filters.get(key):
                label = "Date range" if key == "dates" else f"{key.title()}: {self._extra_filters[key]}"
                chips.append((label, lambda name=key: self._remove_extra_filter(name)))
        for label, callback in chips:
            button = QPushButton((label[:24] + "…" if len(label) > 25 else label) + " ×", objectName="filterChip")
            button.setToolTip(f"Remove filter: {label}")
            button.setAccessibleName(button.toolTip())
            button.clicked.connect(lambda _checked=False, remove=callback: (remove(), self._apply_filters()))
            self.filter_chip_layout.addWidget(button)
        self.filter_chip_layout.addStretch()
        self.filter_chips.setVisible(bool(chips) and self.height() >= 760)

    def _remove_extra_filter(self, key):
        self._extra_filters.pop(key, None)

    def _show_coverage(self):
        if self._current_result is not None:
            CoverageDialog(self._current_result, self).exec()

    def _productivity_result_shown(self, result):
        self.compare_scans_action.setEnabled(True)
        self.coverage_button.setEnabled(True)
        self._displayed_stamp = getattr(result, "created_at", datetime.now(timezone.utc))
        self._update_relative_scan_time()
        self._update_preview()
        self._update_layout_density()
        if self._theme == "light":
            retint_icons(self, True)
        if self._check_again_context is not None:
            incident, baseline = self._check_again_context
            self._check_again_context = None
            message = followup_summary(incident, baseline, result)
            self.status_label.setText(message)
            try:
                self.history_store.record_activity(incident, "Check again result", message)
            except RuntimeError:
                self.status_label.setText(message + " Could not save this check to local history.")

    def _update_relative_scan_time(self):
        if self._displayed_stamp is not None:
            self.last_scan.setText(f"Scanned {relative_time(self._displayed_stamp).lower()}")
            self.last_scan.setToolTip(f"Exact scan time: {self._displayed_stamp}")
        else:
            self.last_scan.setText("Scan time unavailable")
            self.last_scan.setToolTip("This report did not record a scan timestamp.")

    def _refresh_local_preferences(self):
        self._update_relative_scan_time()
        if not self._incidents or self._scan_thread is not None or self._analysis_thread is not None or self.monitor.thread is not None:
            return
        try:
            self.history_store.apply_states(self._incidents)
            self._apply_filters()
            if self.incident_detail.incident is not None:
                self.history_store.apply_states([self.incident_detail.incident])
                self.incident_detail.update_review_state()
        except Exception:
            self.status_label.setText("Local review preferences could not be refreshed.")

    def _remember_review_undo(self, snapshots):
        self._undo_review = snapshots
        self.undo_button.setVisible(bool(snapshots))
        self.undo_review_action.setEnabled(bool(snapshots))

    def _undo_review_change(self):
        if not self._undo_review:
            return
        try:
            self.history_store.restore_preferences(self._undo_review)
            self.history_store.apply_states(self._incidents)
            current = self.incident_detail.incident
            if current is not None:
                self.history_store.apply_states([current])
                self.incident_detail.update_review_state()
        except Exception:
            self.status_label.setText("Could not undo the review change.")
            return
        self._remember_review_undo([])
        self._apply_filters()
        self.status_label.setText("Review change undone.")

    def _snooze_incident(self):
        incident = self.incident_detail.incident
        if incident is None:
            return
        try:
            snapshot = self.history_store.issue_preferences(incident)
            self.history_store.snooze(incident)
            self.history_store.apply_states(self._incidents)
        except Exception:
            self.incident_detail.note_feedback.setText("Could not save temporary hiding.")
            return
        self._remember_review_undo([snapshot])
        self.incident_detail.update_review_state()
        self.incident_detail.note_feedback.setText("Hidden for 24 hours across scans. Use Undo to restore the previous decision.")
        self._apply_filters()

    def _save_incident_note(self):
        incident = self.incident_detail.incident
        if incident is None:
            return
        try:
            self.history_store.save_note(incident, self.incident_detail.note_editor.toPlainText())
            self.history_store.apply_states(self._incidents)
        except (ValueError, RuntimeError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "Could not save the note. Check local application data access."
            self.incident_detail.note_feedback.setText(message)
            return
        self.incident_detail.note_feedback.setText("Note saved locally for this issue across scans. It is not sent to AI.")
        self._update_preview()

    def _check_again(self):
        incident = self.incident_detail.incident
        if incident is None or self._scan_thread is not None or self._analysis_thread is not None or self.monitor.thread is not None:
            return
        try:
            self._focused_scan_options = focused_options(incident, self._current_result, self.scan_options)
        except ValueError as exc:
            self.incident_detail.note_feedback.setText(str(exc))
            return
        self._check_again_context = (incident, self._current_result if self._current_result is not None else [])
        self.run_scan()
