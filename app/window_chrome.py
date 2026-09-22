# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/window_chrome.py
====================
Client-side window decorations: a title bar with minimize, maximize and
close, and the invisible edge grips that let a frameless window still be
resized.

Why this exists at all: the app's own palette stops dead at the OS
frame, which draws its own grey bar in its own colours above a dark
window. Taking the frame off and drawing the controls here is the only
way the top of the window matches the rest of it.

── The two things that usually go wrong ─────────────────────────────
Taking off the native frame takes away everything it did, and a naive
replacement loses all of it: dragging, resizing, Aero Snap, drag-to-edge
tiling, double-click to maximize, the keyboard move/resize commands.

This does not reimplement any of that by hand. Qt 5.15+ exposes
`QWindow.startSystemMove()` and `startSystemResize()`, which hand the
gesture back to the window manager mid-drag. Windows then runs its own
move/resize loop, which means Snap, half-screen tiling, multi-monitor
DPI transitions and the rest keep working exactly as they do for any
other window. A hand-rolled `move(pos - offset)` in mouseMoveEvent -
the usual approach - gets none of that and also stutters, because it
repaints on every mouse event instead of letting the compositor do it.

The second trap is resize hit-testing. Watching mouseMoveEvent on the
window needs `setMouseTracking(True)` on every child widget too, or the
cursor never changes over anything that is not bare background - which
is most of the window. Instead there are eight thin frameless child
widgets around the perimeter (_ResizeGrip), raised above everything.
Each carries its own cursor, so Qt changes the pointer on hover with no
tracking anywhere, and each starts a native resize on press.

Usage (see app/main_window.py):
    chrome = install_window_chrome(main_window, theme_name)
    ...
    chrome.set_theme(new_theme_name)
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractButton, QHBoxLayout, QLabel, QMenu, QWidget,
)

import app.theme as theme
import branding
from ai.log import log_debug
from app.i18n import tr
from paths import BASE_PATH

# Height of the bar, and the thickness of the invisible resize border.
TITLE_BAR_HEIGHT = 38
GRIP = 6

# How far the pointer has to travel before a drag that STARTED on a
# maximized window restores it and becomes a move. Without a threshold,
# a plain click on the bar of a maximized window would unmaximize it.
_UNMAXIMIZE_DRAG_PX = 8


class _WindowButton(QAbstractButton):
    """
    One window control, drawn rather than iconified.

    The glyphs are three or four painter strokes each, which is less
    code than the SVG assets would be and stays crisp at every scale
    factor without shipping one file per size. It also means the stroke
    colour follows the theme directly rather than through a recolouring
    pass on a pixmap.
    """

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self._kind = kind                  # "min" | "max" | "restore" | "close"
        self._fg = QColor("#f2f2f2")
        self._hover_bg = QColor(255, 255, 255, 28)
        self._hover_fg = QColor("#ffffff")
        self.setFixedSize(46, TITLE_BAR_HEIGHT)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_kind(self, kind: str) -> None:
        if kind != self._kind:
            self._kind = kind
            self.update()

    def set_colors(self, fg: str, hover_bg: str, hover_fg: str) -> None:
        self._fg = QColor(fg)
        self._hover_bg = QColor(hover_bg)
        self._hover_fg = QColor(hover_fg)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        hovered = self.underMouse()
        if hovered:
            painter.fillRect(self.rect(), self._hover_bg)

        pen = QPen(self._hover_fg if hovered else self._fg)
        pen.setWidthF(1.2)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)

        # A 10x10 box centred in the button, which is what Windows' own
        # controls use and what makes these read as window controls
        # rather than as app buttons.
        side = 10
        box = QRect(
            (self.width() - side) // 2,
            (self.height() - side) // 2,
            side,
            side,
        )

        if self._kind == "min":
            mid = box.center().y()
            painter.drawLine(box.left(), mid, box.right(), mid)
        elif self._kind == "max":
            painter.drawRect(box.adjusted(0, 0, -1, -1))
        elif self._kind == "restore":
            # Front square, then the back one peeking out top-right.
            front = QRect(box.left(), box.top() + 3, side - 3, side - 3)
            painter.drawRect(front)
            painter.drawLine(front.right() + 3, front.top() - 3 + 1,
                             front.right() + 3, box.top() + side - 3)
            painter.drawLine(box.left() + 3, box.top(),
                             box.left() + side - 1, box.top())
            painter.drawLine(box.left() + side - 1, box.top(),
                             box.left() + side - 1, box.top() + 1)
        elif self._kind == "close":
            painter.drawLine(box.topLeft(), box.bottomRight())
            painter.drawLine(box.topRight(), box.bottomLeft())

        painter.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class TitleBar(QWidget):
    """
    The draggable strip along the top, plus the three controls.

    Deliberately holds no title text. The window has a real title now,
    for the taskbar and Task Manager (see branding.WINDOW_TITLE), but a
    bar that reads the product's own name back at you is the least
    useful line on screen, so it is not printed here.
    """

    always_on_top_toggled = pyqtSignal(bool)

    def __init__(self, window: QWidget, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self._window = window
        self._theme = theme_name
        self._press_pos: "QPoint | None" = None
        self._dragging_maximized = False

        self.setObjectName("titleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_system_menu)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # The brand mark, top-left, where a window's own icon goes on
        # every platform. It used to be a button in the sidebar header,
        # directly above New Chat, where it did nothing at all - no click
        # handler, just a pointing-hand cursor and a tooltip - while
        # costing a ~58px strip of sidebar height. Here it is a mark
        # rather than a control, so it is explicitly not clickable and
        # does not swallow drags: the bar behind it still moves the
        # window.
        self._logo = QLabel()
        self._logo.setObjectName("titleBarLogo")
        self._logo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._logo.setFixedSize(TITLE_BAR_HEIGHT, TITLE_BAR_HEIGHT)
        self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_logo()
        layout.addWidget(self._logo)

        # Empty, expanding: this is the draggable region. A stretch alone
        # would not receive mouse events - the bar itself does, and this
        # label just claims the space so the buttons stay right.
        self._drag_area = QLabel("")
        self._drag_area.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._drag_area, 1)

        self._btn_min = _WindowButton("min", self)
        self._btn_max = _WindowButton("max", self)
        self._btn_close = _WindowButton("close", self)

        self._btn_min.setToolTip(tr("Minimize"))
        self._btn_max.setToolTip(tr("Maximize"))
        self._btn_close.setToolTip(tr("Close"))

        self._btn_min.clicked.connect(self._window.showMinimized)
        self._btn_max.clicked.connect(self.toggle_maximized)
        self._btn_close.clicked.connect(self._window.close)

        for button in (self._btn_min, self._btn_max, self._btn_close):
            layout.addWidget(button)

        self.set_theme(theme_name)

    # ── Brand mark ────────────────────────────────────────────────────

    def _load_logo(self) -> None:
        """
        The solid purple mark, scaled to the bar.

        Scaled from the 1000px source at runtime rather than shipping a
        small variant, so it stays sharp at 150% and 200% display
        scaling - Qt picks up the device pixel ratio, and a pre-sized
        24px PNG would be resampled up and look soft on exactly the
        machines where that is most obvious.
        """
        path = os.path.join(BASE_PATH, branding.ICON_PATH)
        pixmap = QPixmap(path)
        if pixmap.isNull():
            log_debug(f"[window_chrome] brand mark not found at {path}")
            return
        side = TITLE_BAR_HEIGHT - 16
        self._logo.setPixmap(
            pixmap.scaledToHeight(side, Qt.TransformationMode.SmoothTransformation)
        )

    # ── Theme ─────────────────────────────────────────────────────────

    def set_theme(self, theme_name: str) -> None:
        self._theme = theme_name
        c = theme.colors(theme_name)
        self.setStyleSheet(
            f"QWidget#titleBar {{ background-color: {c['bg_base']};"
            f" border-bottom: 1px solid {c['bg_input']}; }}"
        )
        for button in (self._btn_min, self._btn_max):
            button.set_colors(c["text_muted"], c["bg_hover_strong"], c["text_primary"])
        # Close goes red on hover, which is the one window-control
        # convention worth keeping from the native frame: it is the
        # control people check before clicking.
        self._btn_close.set_colors(c["text_muted"], c["danger"], c["white"])

    # ── Maximize state ────────────────────────────────────────────────

    def toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.sync_maximize_button()

    def sync_maximize_button(self) -> None:
        """Swap the glyph and tooltip to match the window's real state.

        Called from MainWindow.changeEvent as well as from the button
        itself, because the window can be maximized without this bar
        knowing: a Snap gesture, Win+Up, or a double-click on the bar."""
        maximized = self._window.isMaximized()
        self._btn_max.set_kind("restore" if maximized else "max")
        self._btn_max.setToolTip(tr("Restore") if maximized else tr("Maximize"))

    # ── Dragging ──────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        if self._window.isMaximized():
            # Do not start a move yet. Restoring on press would mean a
            # plain click on the bar unmaximized the window; wait to see
            # whether this becomes a drag (see mouseMoveEvent).
            self._press_pos = event.globalPosition().toPoint()
            self._dragging_maximized = True
            return

        self._start_system_move()

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging_maximized or self._press_pos is None:
            return super().mouseMoveEvent(event)

        here = event.globalPosition().toPoint()
        if (here - self._press_pos).manhattanLength() < _UNMAXIMIZE_DRAG_PX:
            return

        # Restore, then put the window under the cursor the way Windows
        # does it: the pointer keeps its proportional position along the
        # bar, so grabbing a maximized window near its right edge does
        # not teleport it to the left.
        ratio = here.x() / max(1, self._window.width())
        self._dragging_maximized = False
        self._press_pos = None
        self._window.showNormal()
        self.sync_maximize_button()

        restored_width = self._window.width()
        self._window.move(
            here.x() - int(restored_width * ratio),
            max(0, here.y() - TITLE_BAR_HEIGHT // 2),
        )
        self._start_system_move()

    def mouseReleaseEvent(self, event) -> None:
        self._dragging_maximized = False
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
        else:
            super().mouseDoubleClickEvent(event)

    def _start_system_move(self) -> None:
        """
        Hand the drag to the window manager.

        Everything Windows does during a move - Snap, edge tiling, the
        preview outline, moving between monitors at different scale
        factors - happens inside its own modal move loop. Calling this
        gets all of it; moving the window by hand in mouseMoveEvent gets
        none of it.
        """
        handle = self._window.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    # ── System menu ───────────────────────────────────────────────────

    def _show_system_menu(self, pos: QPoint) -> None:
        """
        Right-clicking a title bar opens a window menu on Windows, and a
        frameless window has to provide its own.

        Always on top lives here. It used to be a pin icon in the
        sidebar header, which put a window-management control inside the
        app's content - the same place the OS puts it is a better home
        for it, and it keeps the top-right for the window controls.
        """
        c = theme.colors(self._theme)
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {c['bg_elevated']};
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 4px;
                color: {c['text_primary']};
                font-family: "Segoe UI";
                font-size: 12px;
            }}
            QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
            QMenu::item:selected {{ background-color: {c['bg_hover_strong']}; }}
            QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
        """)

        maximized = self._window.isMaximized()
        act_restore = menu.addAction(tr("Restore"))
        act_restore.setEnabled(maximized)
        act_min = menu.addAction(tr("Minimize"))
        act_max = menu.addAction(tr("Maximize"))
        act_max.setEnabled(not maximized)
        menu.addSeparator()

        act_top = menu.addAction(tr("Always on top"))
        act_top.setCheckable(True)
        act_top.setChecked(bool(self._window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))

        menu.addSeparator()
        act_close = menu.addAction(tr("Close"))

        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is act_restore:
            self._window.showNormal()
            self.sync_maximize_button()
        elif chosen is act_min:
            self._window.showMinimized()
        elif chosen is act_max:
            self._window.showMaximized()
            self.sync_maximize_button()
        elif chosen is act_top:
            self.always_on_top_toggled.emit(act_top.isChecked())
        elif chosen is act_close:
            self._window.close()


class _ResizeGrip(QWidget):
    """
    One invisible strip along an edge or corner that starts a native
    resize when pressed.

    Carrying the cursor on the widget rather than setting it from a
    mouseMoveEvent is the whole point: Qt applies a widget's cursor on
    hover automatically, with no mouse tracking needed anywhere, which
    is what makes this work over the top of arbitrary child widgets.
    """

    def __init__(self, edges, cursor: Qt.CursorShape, window: QWidget, parent=None):
        super().__init__(parent)
        self._edges = edges
        self._window = window
        self.setCursor(cursor)
        # Invisible, but it must still receive events, so this is not
        # setVisible(False) and not WA_TransparentForMouseEvents.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        handle = self._window.windowHandle()
        if handle is not None:
            handle.startSystemResize(self._edges)


class WindowChrome:
    """
    Owns the title bar and the eight grips, and keeps them positioned.

    Not a QWidget itself - it is the thing that installs the parts and
    holds the references, so MainWindow has one object to re-theme and
    one to ask about state.
    """

    def __init__(self, window: QWidget, title_bar: TitleBar):
        self.window = window
        self.title_bar = title_bar
        self.grips: list = []
        self._build_grips()

    def _build_grips(self) -> None:
        """
        Eight grips, each described by plain booleans rather than by
        reading the Qt flag back apart.

        The Qt.Edge value is built once here and handed straight to
        startSystemResize; the layout maths below never inspects it.
        Testing `edges & Qt.Edge.LeftEdge` would depend on how PyQt
        happens to represent a combined flag, which differs between
        PyQt5, PyQt6 and Python versions - and would fail silently by
        putting a grip in the wrong place rather than by raising.
        """
        L, R = Qt.Edge.LeftEdge, Qt.Edge.RightEdge
        T, B = Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        #      left,  right, top,   bottom, qt edges,  cursor
        spec = [
            (False, False, True,  False, T,     Qt.CursorShape.SizeVerCursor),
            (False, False, False, True,  B,     Qt.CursorShape.SizeVerCursor),
            (True,  False, False, False, L,     Qt.CursorShape.SizeHorCursor),
            (False, True,  False, False, R,     Qt.CursorShape.SizeHorCursor),
            (True,  False, True,  False, T | L, Qt.CursorShape.SizeFDiagCursor),
            (False, True,  True,  False, T | R, Qt.CursorShape.SizeBDiagCursor),
            (True,  False, False, True,  B | L, Qt.CursorShape.SizeBDiagCursor),
            (False, True,  False, True,  B | R, Qt.CursorShape.SizeFDiagCursor),
        ]
        for left, right, top, bottom, edges, cursor in spec:
            grip = _ResizeGrip(edges, cursor, self.window, self.window)
            self.grips.append(((left, right, top, bottom), grip))

    def reposition_grips(self) -> None:
        """
        Lay the grips back out around the window's current size, and
        hide them when maximized - a maximized window is not resizable,
        and leaving a live grip along the top edge there would let
        someone drag a window that has no free edge to give.
        """
        width, height = self.window.width(), self.window.height()
        maximized = self.window.isMaximized()

        for (left, right, top, bottom), grip in self.grips:
            if maximized:
                grip.hide()
                continue

            x = 0 if left else (width - GRIP if right else GRIP)
            y = 0 if top else (height - GRIP if bottom else GRIP)
            w = GRIP if (left or right) else max(0, width - 2 * GRIP)
            h = GRIP if (top or bottom) else max(0, height - 2 * GRIP)

            grip.setGeometry(x, y, w, h)
            grip.show()
            grip.raise_()

    def set_theme(self, theme_name: str) -> None:
        self.title_bar.set_theme(theme_name)

    def sync(self) -> None:
        """Called on any window state change: restack the glyph and the grips."""
        self.title_bar.sync_maximize_button()
        self.reposition_grips()
