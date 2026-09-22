# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/smooth_scroll.py
=====================
Qt's native QAbstractScrollArea wheel scrolling jumps the scrollbar by a
fixed step the instant a wheel notch fires - no interpolation, no easing,
nothing between "before" and "after". That's what reads as "weird"/less
fluid than a browser: browsers animate each scroll with a deceleration
curve, so the content visibly glides rather than teleporting.

This installs an event filter on a QAbstractScrollArea (QScrollArea,
QTextBrowser/QTextEdit, QListWidget, QTreeView, ... anything with a
verticalScrollBar()) that intercepts wheel events and animates the
scrollbar's `value` property to the new position with an easing curve,
instead of letting Qt jump it instantly. Successive wheel ticks while an
animation is still running extend the existing target rather than
restarting from zero, so fast scrolling still feels continuous instead of
stuttering.

Usage:
    from app.smooth_scroll import install_smooth_scroll
    install_smooth_scroll(self.messagesScroll)   # QScrollArea
    install_smooth_scroll(self._view)            # QTextBrowser, etc.

Not real physical momentum (nothing continues moving after the wheel
stops) - deliberately: momentum after the cursor leaves the content is a
much bigger behavior change (can overshoot, needs its own stop/interrupt
handling) than what was actually reported ("scrolling isn't fluid"), which
is about the per-tick motion itself, not about inertia after release.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QEvent, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import QAbstractScrollArea, QScrollBar


class _SmoothScrollFilter(QObject):
    def __init__(self, area: QAbstractScrollArea, duration_ms: int = 220):
        super().__init__(area)
        self._area = area
        self._bar = area.verticalScrollBar()
        self._anim = QPropertyAnimation(self._bar, b"value", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._duration_ms = duration_ms
        self._target = self._bar.value()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Wheel and obj is self._area.viewport():
            delta = event.angleDelta().y()
            if delta == 0:
                return False  # horizontal-only wheel event - let Qt handle it natively

            # Base step mirrors Qt's own default (roughly one "line" per
            # 120 delta units) so this doesn't feel faster/slower overall,
            # just smoother getting there.
            step = int(self._bar.singleStep() * 2 * (delta / 120))

            # Extend the in-flight target instead of restarting from the
            # bar's current (still-animating) value - otherwise rapid
            # ticks would keep truncating each other's motion short.
            base = self._target if self._anim.state() == QPropertyAnimation.State.Running else self._bar.value()
            self._target = max(0, min(self._bar.maximum(), base - step))

            self._anim.stop()
            self._anim.setDuration(self._duration_ms)
            self._anim.setStartValue(self._bar.value())
            self._anim.setEndValue(self._target)
            self._anim.start()
            return True  # consumed - don't let Qt also apply its own instant jump

        return False


def install_smooth_scroll(area: QAbstractScrollArea, duration_ms: int = 220) -> None:
    """
    Call once per scroll area, any time after it's constructed (viewport()
    already exists by then). Safe no-op if called twice on the same area
    (the second filter is simply never installed, since Qt widgets can
    have any number of event filters - but there's no reason to).
    """
    area.viewport().installEventFilter(_SmoothScrollFilter(area, duration_ms))


class SmoothScroller:
    """
    Eases a scrollbar's value toward a target instead of jumping to it
    instantly - built for callers that RETARGET rapidly, like chat
    auto-scroll during a streaming reply: every few milliseconds, as the
    reply grows by a few characters, "where the bottom is" (or "where the
    row being centered now sits") shifts slightly, and the naive fix -
    just setValue()'ing the scrollbar there each time - makes the whole
    conversation visibly teleport in little steps in sync with the text,
    which reads as jerky/nervous rather than as the reply calmly growing.

    A plain QPropertyAnimation retargeted that often would mostly just
    restart from scratch on every call and never really get anywhere
    before being redirected again - same stutter, different mechanism.
    scroll_to() instead always takes the scrollbar's CURRENT value (i.e.
    wherever the in-flight easing has already carried it to) as the new
    start point, so a rapid stream of retargets composes into one
    continuous glide toward whatever the latest target is, rather than a
    string of independent little jumps. In practice this is what makes
    it look like the conversation is smoothly sliding up to make room
    for the reply, instead of the scrollbar visibly hopping down after
    every chunk.
    """

    def __init__(self, bar: QScrollBar, duration_ms: int = 180):
        self._bar = bar
        self._anim = QPropertyAnimation(bar, b"value", bar)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._duration_ms = duration_ms

    def scroll_to(self, target: int) -> None:
        target = max(self._bar.minimum(), min(self._bar.maximum(), target))
        running = self._anim.state() == QPropertyAnimation.State.Running
        if not running and self._bar.value() == target:
            return  # already there and not mid-glide - nothing to animate
        if running and self._anim.endValue() == target:
            return  # already easing toward this exact target - let it continue undisturbed
        self._anim.stop()
        self._anim.setDuration(self._duration_ms)
        self._anim.setStartValue(self._bar.value())
        self._anim.setEndValue(target)
        self._anim.start()
