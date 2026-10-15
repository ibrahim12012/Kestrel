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

The page shows the same map the overlay does, screens and their
controls as a tree (app/map_tree.py), including for a run started from
chat or before the page was first opened (ai.discovery.events.subscribe()
hands it what it missed). Hovering a control outlines it on the screen.
The page also has what the overlay can't offer while it lets clicks
through: a Stop button, and the switch that hides the overlay, now and
for later runs.

Start always maps afresh, outside the AI's action budget, and only when
no other automation is running (ai/automation_lock.py). The outcome
comes back as ai.actions.MappingResult and is worded here. Saved maps
can be shown again or deleted from the row under the controls.
"""

from __future__ import annotations

import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QCheckBox, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal

import ai.main as ai_main
import app.theme as theme
import app.icons as icons
from ai import automation_lock, direct_input
from ai.discovery import events as discovery_events
from ai.discovery import tree as saved_trees
from app.map_tree import ElementHighlight, MapTree, native_to_logical
from app.styled_dropdown import StyledDropdown
from app.workers.mapping_worker import MappingWorker
from app.i18n import tr


def _build_style(c: dict) -> str:
    # The switch's tick: a style sheet takes an image file, not a QIcon.
    tick = icons.tinted_file("check", "#ffffff")
    tick_rule = f'image: url("{tick}");' if tick else ""
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
QPushButton#btnStopMapping {{
    background-color: transparent;
    color: {c['danger']};
    border: 1px solid rgba({c['danger_rgb']},0.55);
    border-radius: 7px;
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 600;
    padding: 8px 18px;
}}
QPushButton#btnStopMapping:hover {{ background-color: rgba({c['danger_rgb']},0.12); }}
QPushButton#btnStopMapping:disabled {{ color: {c['text_disabled']}; border-color: {c['bg_hover_strong']}; }}
QCheckBox#overlaySwitch {{
    color: {c['text_secondary']};
    font-family: "Segoe UI";
    font-size: 12px;
    background: transparent;
    spacing: 8px;
}}
QCheckBox#overlaySwitch::indicator {{
    width: 16px; height: 16px;
    border-radius: 4px;
    border: 1px solid {c['border_strong']};
    background-color: {c['bg_panel']};
}}
QCheckBox#overlaySwitch::indicator:checked {{
    background-color: {c['accent']};
    border-color: {c['accent']};
    {tick_rule}
}}
QLabel#statusLabel {{
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    background: transparent;
    padding: 4px 0;
}}
QLabel#statusLabel[error="true"] {{ color: {c['danger']}; }}
QLabel#savedMapsLabel {{
    color: {c['text_secondary']};
    font-family: "Segoe UI";
    font-size: 12px;
    background: transparent;
}}
QPushButton#btnShowMap, QPushButton#btnDeleteMap {{
    background-color: transparent;
    color: {c['text_secondary']};
    border: 1px solid {c['border']};
    border-radius: 7px;
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 6px 12px;
}}
QPushButton#btnShowMap:hover {{ border-color: {c['accent']}; color: {c['text_primary']}; }}
QPushButton#btnDeleteMap:hover {{ border-color: {c['danger']}; color: {c['danger']}; }}
QPushButton#btnShowMap:disabled, QPushButton#btnDeleteMap:disabled {{
    color: {c['text_disabled']}; border-color: {c['bg_hover_strong']};
}}
QLabel#statsLabel {{
    color: {c['accent_soft']};
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    background: transparent;
}}
QLabel#timeLabel {{
    color: {c['accent']};
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    background: transparent;
    padding-left: 10px;
}}
QTreeWidget#mapTree {{
    background-color: {c['bg_panel']};
    color: {c['text_secondary']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 7px;
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 4px;
    outline: none;
}}
QTreeWidget#mapTree::item {{ padding: 2px 0px; border-radius: 4px; }}
QTreeWidget#mapTree::item:hover {{ background-color: rgba({c['accent_rgb']},0.14); }}
QTreeWidget#mapTree::item:selected {{ background-color: rgba({c['accent_rgb']},0.24); color: {c['text_primary']}; }}
QTreeWidget#mapTree::branch {{ background: transparent; }}
QLabel#mapHint {{
    color: {c['text_faint']};
    font-family: "Segoe UI";
    font-size: 11px;
    background: transparent;
}}
"""


def _tree_palette(c: dict) -> dict:
    return {"fg": c["text_secondary"], "accent": c["accent"], "warn": c["warning"], "muted": c["text_muted"]}


def automation_name(what: str) -> str:
    """ai/automation_lock.py's name for what is running, translated."""
    return {
        "an AI run": tr("an AI run"),
        "a macro": tr("a macro"),
        "a mapping run": tr("a mapping run"),
    }.get(what, what)


def _saved_map_label(entry: dict) -> str:
    if entry.get("ending") in ("stopped", "screen_limit", "action_limit"):
        return tr("{app} · {screens} screens · ended early", app=entry["app"], screens=entry["screens"])
    return tr("{app} · {screens} screens", app=entry["app"], screens=entry["screens"])


def describe_result(result) -> "tuple[str, bool]":
    """An ai.actions.MappingResult as the page's status line, and whether
    it is an error. The text the AI gets has the whole map in it; this is
    one line."""
    if result.error:
        return tr("Couldn't map {app}: {error}", app=result.app, error=result.error), True
    if result.kept_earlier:
        return tr("Stopped after {screens} screens. The earlier full map of {app} is kept.",
                  screens=result.screens, app=result.app), False
    counts = dict(screens=result.screens, controls=result.controls)
    if result.ending == "stopped":
        return tr("Stopped. {screens} screens and {controls} controls were mapped and saved.", **counts), False
    if result.ending == "screen_limit":
        return tr("Stopped at the most screens one run maps. {screens} screens and {controls} controls are saved.",
                  **counts), False
    if result.ending == "action_limit":
        return tr("Stopped at the most clicks one run makes. {screens} screens and {controls} controls are saved.",
                  **counts), False
    if result.reused:
        return tr("{app} was mapped before: {screens} screens, {controls} controls.", app=result.app, **counts), False
    return tr("Mapped {app}: {screens} screens, {controls} controls.", app=result.app, **counts), False


class MappingWidget(QWidget):
    """
    One instance, owned by MainWindow, constructed once and reused for
    the app's whole lifetime - registers its ai.discovery.events
    callbacks exactly once, in __init__ (mirrors app/mapping_monitor.py's
    same one-registration-per-instance discipline).
    """

    # The "Show the map on screen" switch changed: MainWindow passes it
    # on to the overlay (MappingMonitor.set_user_visible).
    overlayToggled = pyqtSignal(bool)

    _started = pyqtSignal(str)
    _progress = pyqtSignal(int, int)
    _finished = pyqtSignal(str, int, int)
    _node = pyqtSignal(object)
    _edge = pyqtSignal(str, str, str)

    def __init__(self, parent: "QWidget | None" = None):
        super().__init__(parent)
        self.setObjectName("MappingWidget")
        self._theme = ai_main.get_theme()
        self.setStyleSheet(_build_style(theme.colors(self._theme)))

        self._mapping_thread: "QThread | None" = None
        self._mapping_worker: "MappingWorker | None" = None
        self._running = False           # a run this page started, until its worker reports back
        self._run_going = False         # any mapping run, whoever started it
        # time.monotonic() when the run shown started and finished, for
        # the clock next to the counts (the overlay has the same one).
        self._started_at: "float | None" = None
        self._finished_at: "float | None" = None
        self._highlight: "ElementHighlight | None" = None
        # A saved map shown with Show, as a run record; None while the
        # tree shows the latest run.
        self._shown: "dict | None" = None
        self._saved_entries: dict = {}

        self._build_ui()

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)

        self._started.connect(self._on_started)
        self._progress.connect(self._on_progress)
        self._finished.connect(self._on_finished)
        self._node.connect(self._tree.add_screen)
        self._node.connect(lambda _info: self._update_map_hint())
        self._edge.connect(self._tree.add_link)
        # Stored as attributes so the SAME object is registered and
        # later removed - repeated attribute access on a bound
        # pyqtSignal isn't guaranteed to compare equal for list
        # membership/removal (see app/mapping_monitor.py, which hit
        # this same subtlety first).
        self._on_started_cb = self._started.emit
        self._on_progress_cb = self._progress.emit
        self._on_finished_cb = self._finished.emit
        self._on_node_cb = self._node.emit
        self._on_edge_cb = self._edge.emit
        # Registered and caught up in one step, so that a run going on
        # right now is drawn whole: what it sent before is in `record`,
        # everything after comes through the callbacks.
        record = discovery_events.subscribe(
            started=self._on_started_cb, progress=self._on_progress_cb,
            finished=self._on_finished_cb, node=self._on_node_cb, edge=self._on_edge_cb,
        )
        self._show_last_run(record)

    def set_theme(self, theme_name: str) -> None:
        self._theme = theme_name
        c = theme.colors(theme_name)
        self.setStyleSheet(_build_style(c))
        self._saved_maps.apply_theme(theme_name)
        # Icons are drawn in the theme's colours when a row is added.
        self._tree.set_palette(_tree_palette(c))
        self._tree.replay(self._shown or discovery_events.last_run())

    # -- UI construction ------------------------------------------------

    def _build_ui(self) -> None:
        c = theme.colors(self._theme)
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
            tr("Maps an open app's screens and controls through Windows UI Automation, so the AI "
               "can press them by name instead of reading a screenshot for every click. The AI "
               "can start a mapping run from the chat too.")
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
        launch_hint_icon.setPixmap(icons.pixmap("warning", c, size=13, color_key="accent"))
        launch_hint_row.addWidget(launch_hint_icon)
        launch_hint = QLabel(tr("Open the app first. Mapping reads what is on screen; it can't start the app for you."))
        launch_hint.setObjectName("launchHint")
        launch_hint.setWordWrap(True)
        launch_hint_row.addWidget(launch_hint, 1)
        root.addLayout(launch_hint_row)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._app_input = QLineEdit()
        self._app_input.setObjectName("appNameInput")
        self._app_input.setPlaceholderText(tr("App name or window title, for example Notepad"))
        self._app_input.returnPressed.connect(self._on_start_clicked)
        input_row.addWidget(self._app_input, 1)

        self._btn_start = QPushButton(tr("Start Mapping"))
        self._btn_start.setObjectName("btnStartMapping")
        self._btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_start.clicked.connect(self._on_start_clicked)
        input_row.addWidget(self._btn_start)

        self._btn_stop = QPushButton(tr("Stop"))
        self._btn_stop.setObjectName("btnStopMapping")
        self._btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_stop.setToolTip(tr("Stop the mapping run. What it mapped so far is kept."))
        self._btn_stop.clicked.connect(self._on_stop_clicked)
        self._btn_stop.hide()
        input_row.addWidget(self._btn_stop)
        root.addLayout(input_row)

        self._overlay_switch = QCheckBox(tr("Show the map in a panel on screen while mapping"))
        self._overlay_switch.setObjectName("overlaySwitch")
        self._overlay_switch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._overlay_switch.setToolTip(tr("The see-through panel in the corner of the screen. Turning this off hides it now and for the next runs."))
        self._overlay_switch.setChecked(ai_main.get_show_mapping_overlay())
        self._overlay_switch.toggled.connect(self._on_overlay_toggled)
        root.addWidget(self._overlay_switch)

        # Maps saved by earlier runs: shown again in the tree, or deleted.
        saved_row = QHBoxLayout()
        saved_row.setSpacing(8)
        saved_label = QLabel(tr("Saved maps"))
        saved_label.setObjectName("savedMapsLabel")
        saved_row.addWidget(saved_label)
        self._saved_maps = StyledDropdown([], "", self._theme, min_width=220)
        self._saved_maps.setObjectName("savedMapsPicker")
        saved_row.addWidget(self._saved_maps, 1)
        self._btn_show_map = QPushButton(tr("Show"))
        self._btn_show_map.setObjectName("btnShowMap")
        self._btn_show_map.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_show_map.clicked.connect(self._on_show_saved_map)
        saved_row.addWidget(self._btn_show_map)
        self._btn_delete_map = QPushButton(tr("Delete"))
        self._btn_delete_map.setObjectName("btnDeleteMap")
        self._btn_delete_map.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_delete_map.clicked.connect(self._on_delete_saved_map)
        saved_row.addWidget(self._btn_delete_map)
        root.addLayout(saved_row)
        self._refresh_saved_maps()

        status_row = QHBoxLayout()
        self._status_label = QLabel(tr("Ready."))
        self._status_label.setObjectName("statusLabel")
        self._status_label.setWordWrap(True)
        status_row.addWidget(self._status_label, 1)
        self._stats_label = QLabel("")
        self._stats_label.setObjectName("statsLabel")
        status_row.addWidget(self._stats_label)
        self._time_label = QLabel("")
        self._time_label.setObjectName("timeLabel")
        status_row.addWidget(self._time_label)
        root.addLayout(status_row)

        self._tree = MapTree(_tree_palette(c))
        self._tree.setObjectName("mapTree")
        self._tree.controlHovered.connect(self._on_control_hovered)
        self._tree.hoverEnded.connect(self._on_hover_ended)
        root.addWidget(self._tree, 1)

        self._map_hint = QLabel(tr("Hover a control to see where it is on screen."))
        self._map_hint.setObjectName("mapHint")
        self._map_hint.hide()           # until there is a map to hover
        root.addWidget(self._map_hint)

    # -- triggering a run -------------------------------------------------

    def _on_start_clicked(self) -> None:
        if self._running:
            return
        app_name = self._app_input.text().strip()
        if not app_name:
            self._set_status(tr("Enter an application name first."))
            return
        # One automation at a time (ai/automation_lock.py). Asked here too,
        # so the stop flag below isn't cleared under an AI run or a macro
        # that was just stopped and is still winding down.
        running = automation_lock.running()
        if running:
            self._set_status(tr("Another automation is running ({what}). Stop it first.",
                                what=automation_name(running)), error=True)
            return

        self._running = True
        self._btn_start.setEnabled(False)
        self._app_input.setEnabled(False)

        # A stop from an earlier run (the Stop button, the stop shortcut)
        # leaves the shared stop flag set, and the run would refuse to
        # start. Pressing Start is the go-ahead to clear it, the same way
        # sending a chat message is.
        import ai.actions as actions
        actions.emergency_stop = False

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

    def _on_stop_clicked(self) -> None:
        """The same stop as the stop shortcut: the run ends before its next
        click and keeps what it mapped. (Esc doesn't stop a mapping run:
        the mapper presses Esc itself to back out of menus.)"""
        ai_main.stop_automation_now()
        self._btn_stop.setEnabled(False)
        self._set_status(tr("Stopping…"))

    def _on_worker_finished(self, result) -> None:
        self._running = False
        self._refresh_buttons()
        text, error = describe_result(result)
        self._set_status(text, error=error)
        self._refresh_saved_maps()
        self._bring_kestrel_back(result.program)

    def _on_worker_error(self, message: str) -> None:
        self._running = False
        self._refresh_buttons()
        self._set_status(tr("Mapping failed: {error}", error=message), error=True)

    def _set_status(self, text: str, error: bool = False) -> None:
        """The line under the controls; in the error colour for a run that
        couldn't happen or failed."""
        self._status_label.setText(text)
        if bool(self._status_label.property("error")) != error:
            self._status_label.setProperty("error", error)
            self._status_label.style().unpolish(self._status_label)
            self._status_label.style().polish(self._status_label)

    def _bring_kestrel_back(self, program: str) -> None:
        """A run started here had the mapped app in front. It ends with
        Kestrel back in front, showing how it went: only while the app
        mapped (`program`) is still the one in front, not when Kestrel
        was put away or you have moved on to something else."""
        window = self.window()
        if window is None or not window.isVisible() or window.isMinimized():
            return
        front = direct_input.foreground_window()
        if front and not direct_input.is_kestrel_window(front) and \
                direct_input.program_of(front).lower() != (program or "").lower():
            return
        window.raise_()
        if not direct_input.bring_forward(int(window.winId())):
            window.activateWindow()

    def _refresh_buttons(self) -> None:
        busy = self._running or self._run_going
        self._btn_start.setEnabled(not busy)
        self._app_input.setEnabled(not busy)
        self._btn_stop.setVisible(self._run_going)
        self._btn_stop.setEnabled(self._run_going)
        # Showing a saved map would replace the map of the run going on.
        has_saved = bool(getattr(self, "_saved_entries", None))
        self._btn_show_map.setEnabled(has_saved and not busy)
        self._btn_delete_map.setEnabled(has_saved and not busy)

    # -- saved maps -------------------------------------------------------

    def _refresh_saved_maps(self) -> None:
        try:
            maps = saved_trees.saved_maps()
        except Exception:
            maps = []
        self._saved_entries = {entry["path"]: entry for entry in maps}
        options = [(entry["path"], _saved_map_label(entry)) for entry in maps]
        current = self._saved_maps.current_value()
        if options:
            self._saved_maps.set_options(options)
            paths = [path for path, _ in options]
            self._saved_maps.set_value(current if current in paths else paths[0])
        else:
            self._saved_maps.set_options([("", tr("No saved maps yet."))])
            self._saved_maps.set_value("")
        self._refresh_buttons()

    def _on_show_saved_map(self) -> None:
        entry = self._saved_entries.get(self._saved_maps.current_value())
        if not entry or self._running or self._run_going:
            return
        try:
            saved = saved_trees.ActionTree.load(entry["path"])
            nodes, edges = saved.events()
        except Exception as e:
            self._set_status(tr("That map couldn't be read: {error}", error=str(e)), error=True)
            return
        self._shown = {"app": saved.app_name, "nodes": nodes, "edges": edges,
                       "progress": (saved.node_count(), saved.action_count()), "running": False}
        self._tree.replay(self._shown)
        self._update_map_hint()
        self._stats_label.setText(tr("{screens} screens · {controls} controls",
                                     screens=saved.node_count(), controls=saved.action_count()))
        self._started_at = self._finished_at = None
        self._show_elapsed()
        self._set_status(tr("Saved map of {app}", app=saved.app_name))

    def _on_delete_saved_map(self) -> None:
        entry = self._saved_entries.get(self._saved_maps.current_value())
        if not entry or self._running or self._run_going:
            return
        answer = QMessageBox.question(
            self, tr("Delete map"),
            tr("Delete the saved map of {app}? The AI can't press its controls by name until it is mapped again.",
               app=entry["app"]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            saved_trees.delete_map(entry["app"], path=entry["path"])
        except OSError as e:
            self._set_status(tr("That map couldn't be deleted: {error}", error=str(e)), error=True)
            return
        if self._shown and self._shown.get("app") == entry["app"]:
            self._shown = None
            self._tree.reset()
            self._update_map_hint()
            self._stats_label.setText("")
        self._refresh_saved_maps()
        self._set_status(tr("Deleted the map of {app}.", app=entry["app"]))

    # -- the overlay ------------------------------------------------------

    def _on_overlay_toggled(self, checked: bool) -> None:
        ai_main.set_show_mapping_overlay(checked)
        self.overlayToggled.emit(checked)

    # -- hover: outline the control on the real screen ---------------------

    def _on_control_hovered(self, box: dict, label: str) -> None:
        box = box or {}
        if self._behind_this_window(box):
            # The outline would be drawn on Kestrel, around nothing.
            self._hide_highlight()
            self._map_hint.setText(tr("That control is behind this window."))
            return
        self._map_hint.setText(tr("Hover a control to see where it is on screen."))
        if self._highlight is None:
            self._highlight = ElementHighlight()
        self._highlight.show_box(box, label or "")

    def _on_hover_ended(self) -> None:
        self._hide_highlight()
        self._map_hint.setText(tr("Hover a control to see where it is on screen."))

    def _behind_this_window(self, box: dict) -> bool:
        width, height = int(box.get("w", 0)), int(box.get("h", 0))
        window = self.window()
        if width <= 0 or height <= 0 or window is None or not window.isVisible() or window.isMinimized():
            return False
        centre, _ = native_to_logical(int(box.get("x", 0)) + width // 2, int(box.get("y", 0)) + height // 2)
        return window.frameGeometry().contains(centre)

    def _hide_highlight(self) -> None:
        if self._highlight is not None:
            self._highlight.hide()

    def _update_map_hint(self) -> None:
        self._map_hint.setVisible(self._tree.screen_count() > 0)

    def hideEvent(self, event) -> None:
        # Leaving the page (another page, the tray) with a row hovered
        # would otherwise leave its outline on the screen.
        self._hide_highlight()
        super().hideEvent(event)

    # -- ai.discovery.events handlers (run on the GUI thread via the signals above) --

    def _show_last_run(self, record: dict) -> None:
        if not record["app"]:
            return
        self._tree.replay(record)
        self._update_map_hint()
        screens, controls = record["progress"]
        self._stats_label.setText(tr("{screens} screens · {controls} controls", screens=screens, controls=controls))
        self._started_at = record.get("started_at")
        self._finished_at = record.get("finished_at")
        if record["running"]:
            self._run_going = True
            self._set_status(tr("Mapping {app}…", app=record["app"]))
            self._clock_timer.start(1000)
        else:
            self._set_status(tr("Map of {app}", app=record["app"]))
        self._show_elapsed()
        self._refresh_buttons()

    def _on_started(self, app_name: str) -> None:
        self._run_going = True
        self._shown = None
        self._refresh_buttons()
        self._set_status(tr("Mapping {app}…", app=app_name))
        self._stats_label.setText(tr("{screens} screens · {controls} controls", screens=0, controls=0))
        self._tree.reset()
        self._update_map_hint()
        self._started_at = time.monotonic()
        self._finished_at = None
        self._show_elapsed()
        self._clock_timer.start(1000)

    def _on_progress(self, nodes: int, actions_count: int) -> None:
        self._stats_label.setText(tr("{screens} screens · {controls} controls", screens=nodes, controls=actions_count))

    def _on_finished(self, app_name: str, nodes: int, actions_count: int) -> None:
        self._clock_timer.stop()
        if self._started_at is not None:
            self._finished_at = time.monotonic()
            self._show_elapsed()        # stays up: how long the run took
        self._run_going = False
        self._refresh_buttons()
        self._stats_label.setText(tr("{screens} screens · {controls} controls", screens=nodes, controls=actions_count))
        if not self._running:
            # A run started from chat: its worker isn't this page's, so
            # nothing else will say it's over.
            self._set_status(tr("Map of {app}", app=app_name))
            self._refresh_saved_maps()

    def _tick(self) -> None:
        self._show_elapsed()

    def _show_elapsed(self) -> None:
        if self._started_at is None:
            self._time_label.setText("")
            return
        end = self._finished_at if self._finished_at is not None else time.monotonic()
        minutes, seconds = divmod(max(0, int(end - self._started_at)), 60)
        self._time_label.setText(f"{minutes:02d}:{seconds:02d}")

    # -- lifecycle -------------------------------------------------------

    def _unregister_events(self) -> None:
        discovery_events.remove_started(self._on_started_cb)
        discovery_events.remove_progress(self._on_progress_cb)
        discovery_events.remove_finished(self._on_finished_cb)
        discovery_events.remove_node(self._on_node_cb)
        discovery_events.remove_edge(self._on_edge_cb)
        self._hide_highlight()

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
