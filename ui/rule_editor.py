"""Edit validated custom detection data and inspect the built-in rule catalog."""

import json
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QPlainTextEdit,
                            QPushButton, QTabWidget, QVBoxLayout)
from detection_rules import BUILTIN_RULES, parse_custom_rules


class RuleEditorDialog(QDialog):
    """A JSON editor whose Save action validates every rule before applying it."""

    def __init__(self, rules_json, parent=None):
        """Show custom rules, built-in rules, and a safe exact-match example."""
        super().__init__(parent)
        self.setWindowTitle("Detection rules")
        self.resize(760, 600)
        self.rules_json = rules_json
        layout = QVBoxLayout(self)
        label = QLabel("Match an exact provider and event ID, optionally in any channel (*). Matching events are included even at Information level. "
                       "Custom labels take precedence; Windows' original level is preserved. Rules run locally.")
        label.setWordWrap(True)
        layout.addWidget(label)
        tabs = QTabWidget()
        tabs.setObjectName("detailTabs")
        self.editor = QPlainTextEdit(json.dumps(parse_custom_rules(rules_json), indent=2))
        self.editor.setAccessibleName("Custom detection rules JSON")
        tabs.addTab(self.editor, "Custom rules")
        builtins = QPlainTextEdit(json.dumps(BUILTIN_RULES, indent=2))
        builtins.setReadOnly(True)
        tabs.addTab(builtins, "Built-in rules (read only)")
        layout.addWidget(tabs)
        example = QPushButton("Insert example into empty editor")
        example.clicked.connect(self._example)
        layout.addWidget(example)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _example(self):
        """Insert an inert disabled sample only when no custom rules exist."""
        if self.editor.toPlainText().strip() not in ("", "[]"):
            self.error.setText("Clear the editor to [] before inserting the example.")
            return
        self.editor.setPlainText(json.dumps([dict(id="my-app-event", channel="Application", provider="MyApplication",
                                                 event_ids=[1000], label="My Application Event", severity="Warning", enabled=False)], indent=2))

    def accept(self):
        """Keep invalid edits open; return canonical JSON only on successful validation."""
        try:
            rules = parse_custom_rules(self.editor.toPlainText())
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.rules_json = json.dumps(rules, sort_keys=True, separators=(",", ":"))
        super().accept()
