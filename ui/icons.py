"""Small, consistent line icons drawn from bundled SVG strings."""

from functools import lru_cache

from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QPushButton


_PATHS = {
    "logo": '<rect x="1" y="1" width="22" height="22" rx="6" fill="#63d8c4" stroke="none"/><path d="M6 7v10h6m6-10-5 12" stroke="#0d302b" stroke-width="2.4"/>',
    "scan": '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6M7 10h6M10 7v6"/>',
    "settings": '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
    "history": '<path d="M3 11a9 9 0 1 1 2.5 7M3 4v7h7M12 7v5l3 2"/>',
    "folder": '<path d="M3 7V5h6l2 3h10v12H3zM3 8h8"/>',
    "arrow": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    "back": '<path d="M20 12H4m6-6-6 6 6 6"/>',
    "chevron": '<path d="m9 5 7 7-7 7"/>',
    "down": '<path d="m5 9 7 7 7-7"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="m7 12 3 3 7-7"/>',
    "warning": '<path d="m12 3 10 18H2zM12 9v5M12 17h.01"/>',
    "critical": '<path d="m8 2-6 6v8l6 6h8l6-6V8l-6-6zM12 7v6M12 17h.01"/>',
    "spark": '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5zM20 2v4M18 4h4"/>',
    "file": '<path d="M14 2H5v20h14V7zM14 2v6h5M8 12h8M8 16h8"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
    "eye": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "hide": '<path d="m3 3 18 18M10 5a11 11 0 0 1 12 7 19 19 0 0 1-3 4M6 6a19 19 0 0 0-4 6s4 7 10 7a11 11 0 0 0 5-1M10 10a3 3 0 0 0 4 4"/>',
    "key": '<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9M16 16l3-3M19 19l3-3"/>',
    "restart": '<path d="M3 10a9 9 0 1 1 1 7M3 3v7h7"/>',
    "save": '<path d="M4 3h13l4 4v14H3V3zM7 3v6h10V3M7 21v-7h10v7"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9 8a3 3 0 1 1 5 2c-2 1-2 2-2 3M12 17h.01"/>',
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    "export": '<path d="M12 16V3m-5 5 5-5 5 5M4 14v7h16v-7"/>',
    "copy": '<path d="M9 8h12v13H9zM5 16H3V3h12v2"/>',
    "close": '<path d="m6 6 12 12M6 18 18 6"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "plus": '<path d="M12 4v16M4 12h16"/>',
    "terminal": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m6 9 3 3-3 3M12 15h5"/>',
    "desktop": '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M12 17v4M7 21h10M5 7h5v5H5z"/>',
    "sound": '<path d="M3 9h4l5-5v16l-5-5H3zM16 8a6 6 0 0 1 0 8M19 5a10 10 0 0 1 0 14"/>',
    "muted": '<path d="M3 9h4l5-5v16l-5-5H3zM17 9l5 6M17 15l5-6"/>',
    "filter": '<path d="M3 4h18l-7 8v7l-4 2v-9z"/>',
    "trash": '<path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7M14 10v7"/>',
}


@lru_cache(maxsize=128)
def icon(name, color="#b8c9df", size=20):
    """Return a high-DPI icon with a muted disabled state and no external assets."""
    result = QIcon()
    for mode, stroke in ((QIcon.Mode.Normal, color), (QIcon.Mode.Disabled, "#76869a")):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'fill="none" stroke="{stroke}" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            f'{_PATHS[name]}</svg>'
        )
        pixmap = QPixmap(QSize(size * 2, size * 2))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(2)
        result.addPixmap(pixmap, mode)
    return result


def action_icon_name(text):
    """Choose a familiar symbol for existing labeled actions and dialog buttons."""
    text = text.replace("&", "").lower()
    for words, name in (
        (("copy",), "copy"), (("cancel", "stop"), "stop"),
        (("close",), "close"), (("remove", "delete"), "trash"),
        (("download", "update"), "download"), (("export",), "export"),
        (("terminal",), "terminal"), (("reset", "restore"), "restart"),
        (("sound",), "sound"), (("shortcut", "desktop"), "desktop"),
        (("save", "apply"), "save"), (("insert", "add"), "plus"),
        (("hide", "ignore"), "hide"), (("filter", "search"), "filter"),
        (("open", "browse", "choose windows"), "folder"),
        (("history", "comparison"), "history"), (("timeline", "monitor"), "clock"),
        (("file",), "folder"), (("view", "show all"), "eye"),
        (("next", "understand"), "arrow"), (("back",), "back"),
        (("settings", "options", "rules"), "settings"),
        (("ai", "animation"), "spark"), (("guide", "help"), "help"),
        (("ok", "got it", "check", "acknowledge", "review"), "check"),
    ):
        if any(word in text for word in words):
            return name
    return "info"


def decorate_buttons(parent):
    """Fill gaps in action icons while keeping explicitly chosen icons and labels."""
    for button in parent.findChildren(QPushButton):
        if button.icon().isNull():
            button.setIcon(icon(action_icon_name(button.text())))
