# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/splash_screen.py
=====================
The "starting up" page shown first inside MainWindow's own shell stack
(see MainWindow._build_shell / show_main_content), for however long
MainWindow's construction takes: DB init, five full page widgets, the
system tray, the wake-word worker thread. Before a page like this
existed that time ran with nothing on screen at all, no window and
nothing in the taskbar, which reads as a hang rather than a slow load.

This and the name gate used to be separate frameless top-level windows
shown before MainWindow existed. Both are plain pages inside the ONE
MainWindow shell QStackedWidget now, so the app is one OS window from
the first frame to the last, repainting between steps rather than
closing one window and opening another.

Usage (see MainWindow._build_shell / run_name_gate / show_main_content):
    splash = SplashPage(theme_name)
    shell_stack.addWidget(splash)
    splash.set_status("Signing in…")
    ...
    splash.ensure_minimum_display_time()  # see its own docstring
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar
from PyQt6.QtCore import Qt, QEventLoop, QTimer
from PyQt6.QtGui import QPixmap

import os
import time
import app.theme as theme
from paths import BASE_PATH
import branding
from app.i18n import tr


class SplashPage(QWidget):
    def __init__(self, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self._created_at = time.monotonic()

        c = theme.colors(theme_name)
        self.setStyleSheet(f"QWidget#splashPage {{ background-color: {c['bg_base']}; }}")
        self.setObjectName("splashPage")

        # A fixed-size card centered in whatever size this page actually
        # ends up at (MainWindow's own size, ~1280x800 by default)
        # same "small card floating in the app's real window" look the
        # name gate uses, rather than stretching this content
        # across the whole window.
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)

        card = QWidget()
        card.setObjectName("splashCard")
        card.setFixedSize(340, 220)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 32, 32, 28)
        layout.setSpacing(14)
        layout.addStretch()

        logo = QLabel()
        logo_path = os.path.join(BASE_PATH, branding.ICON_PATH)
        pixmap = QPixmap(logo_path)
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaledToHeight(56, Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)

        title = QLabel(branding.APP_NAME)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setObjectName("splashTitle")
        layout.addWidget(title)

        self._status = QLabel(tr("Starting up…"))
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setObjectName("splashStatus")
        layout.addWidget(self._status)

        # Indeterminate (range 0,0) - Qt's own Fusion style animates this
        # on its own via an internal timer, no extra code needed here to
        # get a moving "still working" indicator.
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        bar.setObjectName("splashBar")
        layout.addWidget(bar)

        layout.addStretch()

        card.setStyleSheet(f"""
            QWidget#splashCard {{
                background-color: {c['bg_elevated']};
                border: 1px solid {c['bg_hover_strong']};
                border-radius: 14px;
            }}
            QLabel#splashTitle {{
                color: {c['text_primary']};
                font-family: "Syne";
                font-size: 18px;
                font-weight: 700;
                background: transparent;
            }}
            QLabel#splashStatus {{
                color: {c['text_muted']};
                font-family: "Segoe UI";
                font-size: 11px;
                background: transparent;
            }}
            QProgressBar#splashBar {{
                background-color: {c['bg_base']};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar#splashBar::chunk {{
                background-color: {c['accent']};
                border-radius: 2px;
            }}
        """)

    def set_status(self, text: str) -> None:
        self._status.setText(text)
        # Force an immediate repaint - the caller is about to do
        # synchronous, potentially slow work (MainWindow construction)
        # on this same thread, which would otherwise leave
        # the new status text queued but unpainted until that work
        # finishes, defeating the point of updating it at all.
        self.repaint()

    def ensure_minimum_display_time(self, min_seconds: float = 2.6) -> None:
        """
        Blocks - via a local QEventLoop, which keeps painting/processing
        events, NOT a plain time.sleep, which would freeze the window -
        until at least `min_seconds` have passed since this page was
        constructed. On a fast machine MainWindow's own construction plus this page's time on screen
        could pass in well under a second, which read as an abrupt flash
        rather than an intentional loading screen - this makes sure it's
        never up for less than a moment even then, without slowing
        anything down on a machine where it's already legitimately busy
        for longer than this.

        Was 1.4s, which still read as an instant flash-and-gone on a
        fast machine, over almost as soon as it appeared. Bumped to 2.6s so the logo/progress bar
        actually register as an intro instead of a flicker, without
        being so long it feels like a stall on its own.
        """
        elapsed = time.monotonic() - self._created_at
        remaining_ms = int(max(0.0, min_seconds - elapsed) * 1000)
        if remaining_ms <= 0:
            return
        loop = QEventLoop()
        QTimer.singleShot(remaining_ms, loop.quit)
        loop.exec()
