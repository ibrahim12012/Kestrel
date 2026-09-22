# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/mapping_monitor.py
========================
A small, professional status window shown while ai.discovery maps an
application's UI (tool_discover_application_ui) - sits in the screen's
top-left corner so it stays out of the way of whatever app is actually
being mapped, and shows how long the run has been going plus a live list
of what's been discovered so far.

This window used to mark itself invisible to screen-capture APIs, via
SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE), so it could never end
up in a screenshot the AI took for an unrelated reason while a mapping
run was going in the background. That call is gone. It is the API a
piece of malware uses to keep an overlay out of a screen recording, it
is scored as such by antivirus heuristics, and what it bought here was
cosmetic: a 300px status panel in the corner of a screenshot is noise
the model ignores, not a wrong answer. An app that injects input and
takes screenshots for a living cannot also afford to look like it is
hiding from the screen. See docs/ANTIVIRUS.md.

Follows a mapping run via ai/discovery/events.py's pub-sub - not by
importing ai.discovery.mapper directly - the same GUI/business-logic
boundary app/console_widget.py keeps with ai.log's sink mechanism.
Callbacks arrive on whatever thread the mapping run is executing on
(never the GUI thread), so each one just emits a signal; Qt marshals the
actual widget update onto this window's own (GUI) thread automatically.
"""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QListWidget, QVBoxLayout, QWidget,
)

from ai.discovery import events as discovery_events
from app.i18n import tr

ACCENT_COLOR = "#7c5cff"


class MappingMonitor(QWidget):
    """
    One instance, owned by MainWindow (see main_window.py's
    self._mapping_monitor) and reused across every mapping run for the
    app's whole lifetime - registers its event callbacks exactly once in
    __init__, the same one-registration-per-instance discipline
    ConsoleWidget follows with ai.log's sinks.
    """

    MARGIN = 20
    WIDTH = 300
    HEIGHT = 300

    _started = pyqtSignal(str)
    _progress = pyqtSignal(int, int)
    _finished = pyqtSignal(str, int, int)

    def __init__(self, parent: "QWidget | None" = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        self._container = QWidget(self)
        self._container.setObjectName("mappingMonitor")
        self._container.setGeometry(0, 0, self.WIDTH, self.HEIGHT)
        self._container.setStyleSheet(f"""
            QWidget#mappingMonitor {{
                background-color: rgba(18, 18, 18, 235);
                border: 1px solid rgba(124, 92, 255, 0.4);
                border-radius: 10px;
            }}
            QLabel {{ background: transparent; border: none; color: #f2f2f2; font-family: "Segoe UI"; }}
            QListWidget {{
                background: transparent;
                border: none;
                color: #cfcfcf;
                font-family: "JetBrains Mono", monospace;
                font-size: 11px;
            }}
            QListWidget::item {{ padding: 1px 0px; }}
        """)
        shadow = QGraphicsDropShadowEffect(self._container)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        self._container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self._container)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel(tr("Structural UI Mapping"))
        title.setStyleSheet("font-size: 13px; font-weight: 700;")
        self._timer_label = QLabel("00:00")
        self._timer_label.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ACCENT_COLOR};")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._timer_label)
        layout.addLayout(header)

        self._app_label = QLabel("")
        self._app_label.setStyleSheet("font-size: 11px; color: #9a9a9a;")
        layout.addWidget(self._app_label)

        self._stats_label = QLabel("0 screens · 0 actions")
        self._stats_label.setStyleSheet("font-size: 11px; color: #cfcfcf;")
        layout.addWidget(self._stats_label)

        self._tree = QListWidget()
        layout.addWidget(self._tree, 1)

        self._elapsed_seconds = 0
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)

        self._started.connect(self._on_started)
        self._progress.connect(self._on_progress)
        self._finished.connect(self._on_finished)
        # Stored as attributes (not re-accessed via self._started.emit
        # each time) so the SAME object reference is what's registered
        # and later removed in closeEvent - repeated attribute access on
        # a bound pyqtSignal isn't guaranteed to return an object that
        # compares equal for list membership/removal purposes.
        self._on_started_cb = self._started.emit
        self._on_progress_cb = self._progress.emit
        self._on_finished_cb = self._finished.emit
        discovery_events.on_started(self._on_started_cb)
        discovery_events.on_progress(self._on_progress_cb)
        discovery_events.on_finished(self._on_finished_cb)

    # -- event pub-sub handlers (run on the GUI thread via the signals above) --

    def _on_started(self, app_name: str) -> None:
        self._app_label.setText(f"Target: {app_name}")
        self._stats_label.setText("0 screens · 0 actions")
        self._tree.clear()
        self._elapsed_seconds = 0
        self._timer_label.setText("00:00")
        self._position_top_left()
        self.show()
        self._clock_timer.start(1000)

    def _on_progress(self, nodes: int, actions: int) -> None:
        self._stats_label.setText(f"{nodes} screen(s) · {actions} action(s)")
        self._tree.addItem(f"Screen {nodes}")
        self._tree.scrollToBottom()

    def _on_finished(self, app_name: str, nodes: int, actions: int) -> None:
        self._clock_timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._elapsed_seconds += 1
        m, s = divmod(self._elapsed_seconds, 60)
        self._timer_label.setText(f"{m:02d}:{s:02d}")

    def _position_top_left(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.left() + self.MARGIN, geo.top() + self.MARGIN)

    # -- lifecycle -------------------------------------------------------

    def _unregister_events(self) -> None:
        discovery_events.remove_started(self._on_started_cb)
        discovery_events.remove_progress(self._on_progress_cb)
        discovery_events.remove_finished(self._on_finished_cb)

    def closeEvent(self, event) -> None:
        # closeEvent isn't reliably delivered to a widget that was never
        # actually shown (notably under tests, running the offscreen Qt
        # platform) - tests should call _unregister_events() directly
        # rather than relying on .close() alone; a stale registration
        # left pointing at a since-garbage-collected widget segfaults
        # the next time notify_started/_progress/_finished fires.
        self._unregister_events()
        super().closeEvent(event)
