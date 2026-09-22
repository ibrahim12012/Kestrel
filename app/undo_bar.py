# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/undo_bar.py
=================
One undo affordance, shared by every page that can destroy something.

Deleting a macro, a recorded step or a block used to be final, and a
confirmation dialog is not a substitute: it interrupts the person every
time to guard against the once in fifty they got it wrong. A bar that
appears after the fact costs nothing when the delete was intended and
costs one click when it was not.

Usage from a page:

    self._undo = UndoBar(self)
    ...
    self._undo.offer("Deleted \"Open inbox\".", self._restore_that_macro)

`offer` replaces whatever the bar was already showing. It floats over the
bottom of its parent rather than sitting in the layout, so showing it
never reflows the page underneath.
"""

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

import app.theme as theme
from app.i18n import tr

# How long an offer stays up. Long enough to notice and act on, short
# enough that a stale bar is never mistaken for a current one.
DEFAULT_TIMEOUT_MS = 9000


class UndoBar(QFrame):
    """A floating "done, and here is the way back" bar."""

    def __init__(self, parent, theme_name: str = theme.DEFAULT_THEME,
                 timeout_ms: int = DEFAULT_TIMEOUT_MS):
        super().__init__(parent)
        self.setObjectName("undoBar")
        self._theme = theme_name
        self._timeout_ms = timeout_ms
        self._callback = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 10, 10)
        row.setSpacing(12)

        self._label = QLabel("")
        self._label.setObjectName("undoBarLabel")
        row.addWidget(self._label, 1)

        self._btn_undo = QPushButton(tr("Undo"))
        self._btn_undo.setObjectName("undoBarButton")
        self._btn_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_undo.clicked.connect(self._on_undo)
        row.addWidget(self._btn_undo)

        self._btn_close = QPushButton("×")
        self._btn_close.setObjectName("undoBarClose")
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.setFixedSize(22, 22)
        self._btn_close.clicked.connect(self.dismiss)
        row.addWidget(self._btn_close)

        self.set_theme(theme_name)
        self.hide()
        # Following the parent's size here rather than asking every page
        # to remember a resizeEvent override.
        parent.installEventFilter(self)

    # -- api ---------------------------------------------------------

    def offer(self, message: str, undo_callback, timeout_ms: "int | None" = None) -> None:
        """
        Show `message` with an Undo button that calls `undo_callback`.
        Replaces any offer already showing, because only the most recent
        destructive act is the one an Undo button can plausibly mean.
        """
        self._callback = undo_callback
        self._label.setText(message)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._timer.start(timeout_ms if timeout_ms is not None else self._timeout_ms)

    def dismiss(self) -> None:
        self._timer.stop()
        self._callback = None
        self.hide()

    def set_theme(self, theme_name: str) -> None:
        self._theme = theme_name
        c = theme.colors(theme_name)
        self.setStyleSheet(f"""
            QFrame#undoBar {{
                background-color: {c['bg_elevated']};
                border: 1px solid {c['border_strong']};
                border-radius: 10px;
            }}
            QLabel#undoBarLabel {{
                color: {c['text_secondary']};
                background: transparent;
                font-size: 13px;
            }}
            QPushButton#undoBarButton {{
                color: {c['accent']};
                background: transparent;
                border: none;
                font-size: 13px;
                font-weight: 600;
                padding: 2px 6px;
            }}
            QPushButton#undoBarButton:hover {{ color: {c['accent_hover']}; }}
            QPushButton#undoBarClose {{
                color: {c['text_faint']};
                background: transparent;
                border: none;
                font-size: 16px;
            }}
            QPushButton#undoBarClose:hover {{ color: {c['text_primary']}; }}
        """)

    # -- internals -----------------------------------------------------

    def _on_undo(self) -> None:
        callback = self._callback
        self.dismiss()
        if callback is not None:
            callback()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(max(self.sizeHint().width(), 280), max(280, parent.width() - 48))
        height = self.sizeHint().height()
        self.setGeometry(
            (parent.width() - width) // 2,
            max(0, parent.height() - height - 24),
            width,
            height,
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and watched is self.parentWidget() and self.isVisible():
            self._reposition()
        return super().eventFilter(watched, event)
