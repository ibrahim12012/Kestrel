# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/mapping_widget.py
========================
A dedicated page for Phase 2's structural UI mapping - previously only
reachable by asking the AI in chat to call tool_discover_application_ui.
Lets someone trigger and watch a mapping run directly, without going
through a conversation.

Runs the actual mapping off the GUI thread via MappingWorker (same
reasoning as every other worker in this app - a mapping run actively
clicks through the target application for as long as it takes). Live
progress is followed through ai/discovery/events.py's pub-sub, the same
one app/mapping_monitor.py's overlay window already uses - both can
watch the same run independently; this page and the overlay aren't
mutually exclusive.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal

import ai.main as ai_main
import app.theme as theme
import app.icons as icons
from ai.discovery import events as discovery_events
from app.workers.mapping_worker import MappingWorker
from app.i18n import tr


def _build_style(c: dict) -> str:
    return f"""
QWidget#MappingWidget {{ background-color: {c['bg_base']}; }}
QLabel#pageTitle {{
    color: {c['text_primary']};
    font-family: "Syne";
    font-size: 16px;
    font-weight: 700;
    background: transparent;
}}
QLabel#subtitle {{
    color: {c['text_muted']};
    font-family: "Segoe UI";
    font-size: 12px;
    background: transparent;
}}
QLabel#launchHint {{
    color: {c['accent']};
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    background: transparent;
}}
QLineEdit#appNameInput {{
    background-color: {c['bg_panel']};
    color: {c['text_primary']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 7px;
    padding: 8px 10px;
    font-family: "Segoe UI";
    font-size: 13px;
}}
QLineEdit#appNameInput:focus {{ border-color: {c['accent']}; }}
QPushButton#btnStartMapping {{
    background-color: {c['accent']};
    color: white;
    border: none;
    border-radius: 7px;
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 600;
    padding: 8px 18px;
}}
QPushButton#btnStartMapping:hover {{ background-color: {c['accent_soft']}; }}
QPushButton#btnStartMapping:disabled {{ background-color: {c['bg_hover_strong']}; color: {c['text_disabled']}; }}
QLabel#statusLabel {{
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    background: transparent;
    padding: 4px 0;
}}
QLabel#statsLabel {{
    color: {c['accent_soft']};
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    background: transparent;
}}
QListWidget#screensList {{
    background-color: {c['bg_panel']};
    color: {c['text_secondary']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 7px;
    font-family: "JetBrains Mono", monospace;
    font-size: 11px;
    padding: 4px;
}}
"""


class MappingWidget(QWidget):
    """
    One instance, owned by MainWindow, constructed once and reused for
    the app's whole lifetime - registers its ai.discovery.events
    callbacks exactly once, in __init__ (mirrors app/mapping_monitor.py's
    same one-registration-per-instance discipline).
    """

    _started = pyqtSignal(str)
    _progress = pyqtSignal(int, int)
    _finished = pyqtSignal(str, int, int)

    def __init__(self, parent: "QWidget | None" = None):
        super().__init__(parent)
        self.setObjectName("MappingWidget")
        self._theme = ai_main.get_theme()
        self.setStyleSheet(_build_style(theme.colors(self._theme)))

        self._mapping_thread: "QThread | None" = None
        self._mapping_worker: "MappingWorker | None" = None
        self._running = False
        self._elapsed_seconds = 0

        self._build_ui()

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)

        self._started.connect(self._on_started)
        self._progress.connect(self._on_progress)
        self._finished.connect(self._on_finished)
        # Stored as attributes so the SAME object is registered and
        # later removed - repeated attribute access on a bound
        # pyqtSignal isn't guaranteed to compare equal for list
        # membership/removal (see app/mapping_monitor.py, which hit
        # this same subtlety first).
        self._on_started_cb = self._started.emit
        self._on_progress_cb = self._progress.emit
        self._on_finished_cb = self._finished.emit
        discovery_events.on_started(self._on_started_cb)
        discovery_events.on_progress(self._on_progress_cb)
        discovery_events.on_finished(self._on_finished_cb)

    def set_theme(self, theme_name: str) -> None:
        self._theme = theme_name
        self.setStyleSheet(_build_style(theme.colors(theme_name)))

    # -- UI construction ------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel(tr("Mapping"))
        title.setObjectName("pageTitle")
        root.addWidget(title)

        # Deliberately generic - doesn't name the assistant or any AI
        # profile. Same reasoning as ChatWidget's input placeholder
        # ("Ask anything…"): this page is built once and reused for the
        # app's whole lifetime (see MainWindow._navigate), so naming a
        # specific profile here would either go stale after a rename/
        # profile switch or require refresh plumbing just to keep one
        # word current - not worth it for a static subtitle.
        subtitle = QLabel(
            tr("Maps an already-open application's screens and controls via structural "
            "accessibility parsing instead of vision, so the AI can navigate it with "
            "compiled clicks/types instead of a vision call per step. Open the app first, "
            "then start a mapping run here - this is the same tool the AI can trigger from "
            "chat, just reachable directly.")
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # Called out on its own, separate from the subtitle prose above
        # and the input's placeholder text below (which disappears the
        # moment you start typing) - this is the one thing a mapping run
        # can't do for you, so it stays visible the whole time, not just
        # until you've typed something.
        launch_hint_row = QHBoxLayout()
        launch_hint_row.setSpacing(6)
        launch_hint_icon = QLabel()
        launch_hint_icon.setPixmap(icons.pixmap("warning", theme.colors(self._theme), size=13, color_key="accent"))
        launch_hint_row.addWidget(launch_hint_icon)
        launch_hint = QLabel(tr("Launch the application first - mapping reads its live screen and can't start it for you."))
        launch_hint.setObjectName("launchHint")
        launch_hint.setWordWrap(True)
        launch_hint_row.addWidget(launch_hint, 1)
        root.addLayout(launch_hint_row)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._app_input = QLineEdit()
        self._app_input.setObjectName("appNameInput")
        self._app_input.setPlaceholderText(tr("Application name (must already be open) - e.g. Notepad"))
        self._app_input.returnPressed.connect(self._on_start_clicked)
        input_row.addWidget(self._app_input, 1)

        self._btn_start = QPushButton(tr("Start Mapping"))
        self._btn_start.setObjectName("btnStartMapping")
        self._btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_start.clicked.connect(self._on_start_clicked)
        input_row.addWidget(self._btn_start)
        root.addLayout(input_row)

        status_row = QHBoxLayout()
        self._status_label = QLabel(tr("Ready."))
        self._status_label.setObjectName("statusLabel")
        status_row.addWidget(self._status_label, 1)
        self._stats_label = QLabel("")
        self._stats_label.setObjectName("statsLabel")
        status_row.addWidget(self._stats_label)
        root.addLayout(status_row)

        self._screens_list = QListWidget()
        self._screens_list.setObjectName("screensList")
        root.addWidget(self._screens_list, 1)

    # -- triggering a run -------------------------------------------------

    def _on_start_clicked(self) -> None:
        if self._running:
            return
        app_name = self._app_input.text().strip()
        if not app_name:
            self._status_label.setText(tr("Enter an application name first."))
            return

        self._running = True
        self._btn_start.setEnabled(False)
        self._app_input.setEnabled(False)

        self._mapping_thread = QThread()
        self._mapping_worker = MappingWorker()
        self._mapping_worker.moveToThread(self._mapping_thread)
        self._mapping_thread.started.connect(lambda: self._mapping_worker.start.emit(app_name))
        self._mapping_worker.finished.connect(self._on_worker_finished)
        self._mapping_worker.error.connect(self._on_worker_error)
        self._mapping_worker.finished.connect(self._mapping_thread.quit)
        self._mapping_worker.finished.connect(self._mapping_worker.deleteLater)
        self._mapping_worker.error.connect(self._mapping_thread.quit)
        self._mapping_worker.error.connect(self._mapping_worker.deleteLater)
        self._mapping_thread.finished.connect(self._mapping_thread.deleteLater)
        # Same reasoning as MainWindow._track_thread / RecorderWidget's
        # generalize thread: don't let this be the only reference to a
        # still-running QThread once self._mapping_thread could be
        # reassigned.
        if not hasattr(self, "_active_threads"):
            self._active_threads: list = []
        self._active_threads.append(self._mapping_thread)
        self._mapping_thread.finished.connect(
            lambda t=self._mapping_thread: self._active_threads.remove(t) if t in self._active_threads else None
        )
        self._mapping_thread.start()

    def _on_worker_finished(self, result: str) -> None:
        self._running = False
        self._btn_start.setEnabled(True)
        self._app_input.setEnabled(True)
        self._status_label.setText(result)

    def _on_worker_error(self, message: str) -> None:
        self._running = False
        self._btn_start.setEnabled(True)
        self._app_input.setEnabled(True)
        self._status_label.setText(f"Mapping failed: {message}")

    # -- ai.discovery.events handlers (run on the GUI thread via the signals above) --

    def _on_started(self, app_name: str) -> None:
        self._status_label.setText(f"Mapping {app_name}…")
        self._stats_label.setText("0 screens · 0 actions")
        self._screens_list.clear()
        self._elapsed_seconds = 0
        self._clock_timer.start(1000)

    def _on_progress(self, nodes: int, actions_count: int) -> None:
        self._stats_label.setText(f"{nodes} screen(s) · {actions_count} action(s) · {self._elapsed_seconds}s")
        self._screens_list.addItem(f"Screen {nodes}")
        self._screens_list.scrollToBottom()

    def _on_finished(self, app_name: str, nodes: int, actions_count: int) -> None:
        self._clock_timer.stop()

    def _tick(self) -> None:
        self._elapsed_seconds += 1

    # -- lifecycle -------------------------------------------------------

    def _unregister_events(self) -> None:
        discovery_events.remove_started(self._on_started_cb)
        discovery_events.remove_progress(self._on_progress_cb)
        discovery_events.remove_finished(self._on_finished_cb)

    def closeEvent(self, event) -> None:
        # In practice this page lives for the app's whole lifetime (one
        # instance, never closed independently - unlike the standalone
        # ConsoleWidget/MappingMonitor windows), so this rarely fires for
        # real; kept as a defensive backstop. Tests that construct
        # throwaway instances should call _unregister_events() directly
        # instead of relying on .close() - closeEvent isn't reliably
        # delivered to a widget that was never actually shown, especially
        # under the offscreen Qt platform tests run under, and a stale
        # registration left pointing at a since-garbage-collected widget
        # segfaults the next time notify_started/_progress/_finished fires.
        self._unregister_events()
        super().closeEvent(event)
