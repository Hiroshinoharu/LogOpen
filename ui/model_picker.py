"""Cost labels in dropdown rows while the editable value stays an exact model ID."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QStyledItemDelegate

from settings.model_catalog import cost_label, model_tooltip


class ModelCostDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        label = cost_label(index.data(Qt.ItemDataRole.DisplayRole) or "")
        option.text += f"  ·  {label}"
        if label in ("Higher cost", "Price unknown"):
            option.palette.setColor(option.palette.ColorRole.Text, QColor("#f4bc72"))


def add_model_choices(combo, models):
    """Attach accessible cost hints without changing persisted IDs or selection logic."""
    for model in dict.fromkeys(models):
        combo.addItem(model)
        row = combo.count() - 1
        combo.setItemData(row, model_tooltip(model), Qt.ItemDataRole.ToolTipRole)
        combo.setItemData(row, f"{model}, {cost_label(model)}", Qt.ItemDataRole.AccessibleTextRole)
