# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

import math

from PyQt6.QtWidgets import QWidget, QLabel, QHBoxLayout, QApplication, QGraphicsDropShadowEffect
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QPen, QColor

from paths import BASE_PATH
root_path = BASE_PATH

# Shared brand accent - same purple used for the Accept button, the wake-word
# border, and everywhere else in the app that needs a "this is Kestrel" color.
ACCENT_COLOR = QColor(124, 92, 255)


class VoiceStatusOverlay(QWidget):
    """
    Small floating status pill shown in the screen's top-right corner while
    Kestrel is listening, thinking, or speaking. Lives on the desktop itself
    rather than inside the chat window, since voice mode (and especially the
    wake word) is often used while the chat isn't even focused or visible.
    """

    MARGIN = 24
    DOT_SIZE = 8
    PULSE_STEP_MS = 30
    PULSE_PERIOD_MS = 1100

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            # Never the window in front: the AI's typing checks which
            # window is (ai/direct_input.py).
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self._container = QWidget(self)
        self._container.setObjectName("statusPill")
        self._container.setStyleSheet("""
            QWidget#statusPill {
                background-color: rgba(20, 20, 20, 235);
                border: 1px solid rgba(124, 92, 255, 0.35);
                border-radius: 11px;
            }
        """)
        shadow = QGraphicsDropShadowEffect(self._container)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 160))
        self._container.setGraphicsEffect(shadow)

        self._dot = QLabel(self._container)
        self._dot.setFixedSize(self.DOT_SIZE, self.DOT_SIZE)
        self._dot.setStyleSheet(self._dot_style(255))

        self._label = QLabel(self._container)
        self._label.setStyleSheet("""
            QLabel {
                background: transparent;
                border: none;
                color: #f2f2f2;
                font-family: "Segoe UI";
                font-size: 12.5px;
                font-weight: 600;
                letter-spacing: 0.1px;
            }
        """)

        layout = QHBoxLayout(self._container)
        layout.setContentsMargins(14, 10, 16, 10)
        layout.setSpacing(10)
        layout.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._pulse_elapsed = 0
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_tick)

    def _dot_style(self, alpha: int) -> str:
        return (
            f"background-color: rgba(124, 92, 255, {alpha / 255:.2f});"
            f"border-radius: {self.DOT_SIZE // 2}px;"
        )

    def show_status(self, text: str) -> None:
        self._label.setText(text)
        self._container.adjustSize()
        self.resize(self._container.size())
        self._container.move(0, 0)
        self._position_top_right()
        self._pulse_elapsed = 0
        self._pulse_timer.start(self.PULSE_STEP_MS)
        self.show()
        self.raise_()

    def hide_status(self) -> None:
        self._pulse_timer.stop()
        self.hide()

    def _pulse_tick(self) -> None:
        # Gentle breathing dot - mirrors the same calm pulse as the
        # full-screen border (see OverlayWindow) rather than a hard blink,
        # so "Kestrel is working" reads as steady activity, not an alarm.
        self._pulse_elapsed = (self._pulse_elapsed + self.PULSE_STEP_MS) % self.PULSE_PERIOD_MS
        phase = self._pulse_elapsed / self.PULSE_PERIOD_MS
        t = (math.sin(2 * math.pi * phase - math.pi / 2) + 1) / 2  # 0..1, smooth
        alpha = int(110 + t * 145)
        self._dot.setStyleSheet(self._dot_style(alpha))

    def _position_top_right(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.right() - self.width() - self.MARGIN
        y = geo.top() + self.MARGIN
        self.move(x, y)


class OverlayWindow(QWidget):
    """
    Transparent, always-on-top, click-through overlay window. Shows a thin
    border in the app's own accent color over the whole screen (or the main
    window) while Kestrel is actively working - a slow, even "breathing"
    pulse rather than a hard on/off blink, so it reads as "busy", not
    "alarm". Rounded corners and the brand purple keep it consistent with
    the rest of the UI instead of looking like a warning overlay.
    """

    BORDER_COLOR = ACCENT_COLOR
    BORDER_WIDTH = 3
    CORNER_RADIUS = 12
    MIN_ALPHA = 70
    MAX_ALPHA = 210
    PULSE_STEP_MS = 30
    PULSE_PERIOD_MS = 1600  # one full breathe in/out cycle

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)

        # ── Flags ──────────────────────────────────────────────────────────
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint        # no title bar
            | Qt.WindowType.WindowStaysOnTopHint     # always on top
            | Qt.WindowType.Tool                     # not in the taskbar
            | Qt.WindowType.WindowTransparentForInput  # click-through
            # Never the window in front, which the AI's clicks and typing
            # go by. Windows still reports this window at every point it
            # covers; ai/direct_input.py looks through it.
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        # ── Pulse state ────────────────────────────────────────────────────
        self._pulse_alpha: int = self.MAX_ALPHA
        self._pulse_elapsed: int = 0
        self._pulsing: bool = False
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._pulse_tick)

    # ------------------------------------------------------------------ #
    #  Positioning                                                          #
    # ------------------------------------------------------------------ #

    def cover_widget(self, widget: QWidget) -> None:
        """Positions the overlay exactly over the given widget (global coordinates)."""
        geo = widget.frameGeometry()
        top_left = widget.mapToGlobal(widget.rect().topLeft())
        self.setGeometry(
            top_left.x(), top_left.y(),
            geo.width(), geo.height()
        )

    def cover_screen(self) -> None:
        """Covers the whole screen (or the primary screen if screen=None)."""
        screen = self.screen()
        self.setGeometry(screen.geometry())

    # ------------------------------------------------------------------ #
    #  Rendering                                                            #
    # ------------------------------------------------------------------ #

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        color = QColor(self.BORDER_COLOR)
        color.setAlpha(self._pulse_alpha)
        pen = QPen(color, self.BORDER_WIDTH)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        half = max(self.BORDER_WIDTH // 2, 1)
        painter.drawRoundedRect(
            half, half,
            self.width() - self.BORDER_WIDTH,
            self.height() - self.BORDER_WIDTH,
            self.CORNER_RADIUS, self.CORNER_RADIUS
        )
        painter.end()

    # ------------------------------------------------------------------ #
    #  Pulse (formerly a hard blink)                                       #
    # ------------------------------------------------------------------ #

    def start_blink(self, interval_ms: int | None = None) -> None:
        """Starts the border's breathing pulse."""
        self._pulsing = True
        self._pulse_elapsed = 0
        self._blink_timer.start(interval_ms or self.PULSE_STEP_MS)

    def stop_blink(self) -> None:
        """Stops the pulse and shows the border at full, steady opacity."""
        self._pulsing = False
        self._blink_timer.stop()
        self._pulse_alpha = self.MAX_ALPHA
        self.update()

    def _pulse_tick(self) -> None:
        self._pulse_elapsed = (self._pulse_elapsed + self.PULSE_STEP_MS) % self.PULSE_PERIOD_MS
        phase = self._pulse_elapsed / self.PULSE_PERIOD_MS
        t = (math.sin(2 * math.pi * phase - math.pi / 2) + 1) / 2  # 0..1, smooth
        self._pulse_alpha = int(self.MIN_ALPHA + t * (self.MAX_ALPHA - self.MIN_ALPHA))
        self.update()

    # ------------------------------------------------------------------ #
    #  Show / Hide helpers                                                 #
    # ------------------------------------------------------------------ #

    def activate(self, blink: bool = False) -> None:
        """Shows the overlay (with an optional breathing pulse)."""
        self.show()
        if blink:
            self.start_blink()
        else:
            self.stop_blink()

    def deactivate(self) -> None:
        """Hides the overlay and stops the pulse."""
        self.stop_blink()
        self.hide()
