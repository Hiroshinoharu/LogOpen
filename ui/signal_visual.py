"""A quiet, decorative log-signal illustration drawn at the display's native DPI."""

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget


class SignalVisual(QWidget):
    """Decorate an empty workspace without adding controls, motion, or implied results.

    The abstract strips contain no event counts or statuses. They scale as one
    vector composition, so this widget never sets a minimum reading width.
    """

    def __init__(self, parent=None, *, light=False):
        super().__init__(parent)
        self._light = bool(light)
        self.setObjectName("signalVisual")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def sizeHint(self):
        return QSize(320, 210)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def set_theme(self, light):
        """Match the active surface without creating a second appearance setting."""
        light = bool(light)
        if self._light != light:
            self._light = light
            self.update()

    def paintEvent(self, event):
        if self.width() < 80 or self.height() < 50:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width() / 320, self.height() / 210)
        painter.translate((self.width() - 320 * scale) / 2, (self.height() - 210 * scale) / 2)
        painter.scale(scale, scale)

        def color(hex_value, alpha=255):
            value = QColor(hex_value)
            value.setAlpha(alpha)
            return value

        mint = "#086a59" if self._light else "#73e2cb"
        amber = "#a16b24" if self._light else "#e7b775"
        violet = "#6a6fb3" if self._light else "#a7b4f3"
        line = "#718c99" if self._light else "#748ca5"
        surface = "#ffffff" if self._light else "#17283a"
        bottom = "#edf6f4" if self._light else "#10232e"

        # Incomplete rings read as a signal diagram, not a progress indicator.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for bounds, start, span, alpha in (
            (QRectF(130, 13, 170, 170), 20, 236, 34),
            (QRectF(146, 29, 138, 138), -34, 241, 42),
            (QRectF(164, 47, 102, 102), 24, 266, 42),
        ):
            painter.setPen(QPen(color(line, alpha), 1))
            painter.drawArc(bounds, start * 16, span * 16)

        # A sparse alignment grid gives the cards a purposeful technical rhythm.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color(line, 43))
        for x in (26, 39, 52, 65):
            for y in (142, 155, 168, 181):
                painter.drawEllipse(QPointF(x, y), 1.1, 1.1)

        def strip(rect, accent, emphasis=False):
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0, color(surface))
            gradient.setColorAt(1, color(bottom))
            # Two fine underlines provide depth without raster shadows.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color("#173128" if self._light else "#020b12", 12 if self._light else 40))
            painter.drawRoundedRect(rect.translated(0, 6), 12, 12)
            painter.setPen(QPen(color(accent if emphasis else line, 125 if emphasis else 70), 1.2))
            painter.setBrush(gradient)
            painter.drawRoundedRect(rect, 12, 12)

            cy = rect.center().y()
            dot = QPointF(rect.left() + 22, cy)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color(accent, 25))
            painter.drawEllipse(dot, 10, 10)
            painter.setBrush(color(accent, 240))
            painter.drawEllipse(dot, 3.1, 3.1)

            painter.setPen(QPen(color(line, 100), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            left = rect.left() + 45
            painter.drawLine(QPointF(left, cy - 5), QPointF(left + rect.width() * 0.31, cy - 5))
            painter.setPen(QPen(color(line, 48), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(left, cy + 5), QPointF(left + rect.width() * 0.22, cy + 5))

            # A short abstract waveform is the recurring LogOpen signal mark.
            signal = QPainterPath(QPointF(rect.right() - 45, cy + 1))
            for dx, dy in ((-38, 1), (-34, -5), (-30, 7), (-25, -9), (-21, 1), (-13, 1)):
                signal.lineTo(rect.right() + dx, cy + dy)
            painter.setPen(QPen(color(accent, 240 if emphasis else 150), 1.6,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(signal)

        strip(QRectF(70, 36, 195, 42), violet)
        strip(QRectF(43, 84, 236, 50), mint, True)
        strip(QRectF(62, 140, 195, 42), amber)

        # Small orbit endpoints balance the composition without adding badges.
        painter.setPen(QPen(color(mint, 160), 1.3))
        painter.setBrush(color(mint, 20))
        painter.drawEllipse(QPointF(284, 52), 5, 5)
        painter.setPen(QPen(color(violet, 150), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        diamond = QPainterPath(QPointF(279, 165))
        for point in ((284, 170), (279, 175), (274, 170)):
            diamond.lineTo(*point)
        diamond.closeSubpath()
        painter.drawPath(diamond)
        painter.end()
