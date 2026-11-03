"""Cost labels in dropdown rows while the editable value stays an exact model ID."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QStyledItemDelegate

from settings.model_catalog import cost_label, price_flag, model_tooltip
from settings.model_compatibility import compatibility_label, compatibility_hint


class ModelCostDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        model = index.data(Qt.ItemDataRole.DisplayRole) or ""
        label = cost_label(model)
        option.text = f"{price_flag(model)}  ·  {model}  ·  {compatibility_label(model)}"
        if label in ("Higher cost", "Price unknown"):
            dark = option.palette.color(option.palette.ColorRole.Base).lightness() < 128
            option.palette.setColor(option.palette.ColorRole.Text, QColor("#f4bc72" if dark else "#805000"))


def add_model_choices(combo, models):
    """Attach accessible cost hints without changing persisted IDs or selection logic."""
    for model in dict.fromkeys(models):
        combo.addItem(model)
        row = combo.count() - 1
        combo.setItemData(row, model_tooltip(model) + "\n" + compatibility_hint(model), Qt.ItemDataRole.ToolTipRole)
        combo.setItemData(row, f"{model}, {price_flag(model)}, {compatibility_label(model)}", Qt.ItemDataRole.AccessibleTextRole)
