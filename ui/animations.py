"""Short, interruptible UI transitions with an explicit reduced-motion switch."""

from PyQt6.QtCore import QObject, QPropertyAnimation, QEasingCurve, QSequentialAnimationGroup
from PyQt6.QtWidgets import QGraphicsOpacityEffect


class MotionController(QObject):
    """Animate page arrival and scan activity without delaying state changes."""

    def __init__(self, stack, badge, enabled=True):
        """Own effects and animations for the supplied page stack and status badge."""
        super().__init__(stack)
        self.enabled = enabled
        self.active = False
        self.page_effect = QGraphicsOpacityEffect(stack)
        stack.setGraphicsEffect(self.page_effect)
        self.page_effect.setOpacity(1.0)
        self.transition = QPropertyAnimation(self.page_effect, b"opacity", self)
        self.transition.setDuration(180)
        self.transition.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.badge_effect = QGraphicsOpacityEffect(badge)
        badge.setGraphicsEffect(self.badge_effect)
        self.badge_effect.setOpacity(1.0)
        self.pulse = QSequentialAnimationGroup(self)
        for start, end in ((1.0, 0.55), (0.55, 1.0)):
            animation = QPropertyAnimation(self.badge_effect, b"opacity", self.pulse)
            animation.setDuration(750)
            animation.setStartValue(start)
            animation.setEndValue(end)
            animation.setEasingCurve(QEasingCurve.Type.InOutSine)
            self.pulse.addAnimation(animation)
        self.pulse.setLoopCount(-1)

    def reveal(self, *_args):
        """Fade in the current page subtly; repeated navigation restarts safely."""
        self.transition.stop()
        if self.enabled:
            self.transition.setStartValue(0.82)
            self.transition.setEndValue(1.0)
            self.transition.start()
        else:
            self.page_effect.setOpacity(1.0)

    def set_active(self, active):
        """Pulse only during running work, restoring full opacity when it ends."""
        self.active = active
        self.pulse.stop()
        self.badge_effect.setOpacity(1.0)
        if active and self.enabled:
            self.pulse.start()

    def set_enabled(self, enabled):
        """Apply the user's motion preference immediately, stopping existing effects."""
        self.enabled = bool(enabled)
        self.transition.stop()
        self.page_effect.setOpacity(1.0)
        self.set_active(self.active)
