# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/mapping_monitor.py
========================
The see-through panel in the screen's top-left corner while ai.discovery
maps an application's UI (tool_discover_application_ui): how long the
run has been going, and the map as it is made.

The map is a tree: each screen lists its controls, and a screen that a
control opens sits under that control, so the path to any screen reads
top to bottom. It used to be a flat list of "Screen 1", "Screen 2"...
that said nothing about what was on them or how they connect.

Every control shows its name and type; hovering it shows its screen
coordinates and size, and outlines the control itself on the screen. The
same names and coordinates go to the model: the finished map is part of
what tool_discover_application_ui returns (ActionTree.outline), and every
screenshot the model takes now comes with the window's controls by name
and position (ai/discovery/screen_map.py), so it can tell the same
button at the same place from a different button that took its place.

While the run is going the panel lets every click through: the mapper
clicks real controls, and one under the panel must not land on it. When
the run ends the panel stays, becomes hoverable, and closes with its X.
It can be hidden at any time from the Mapping page, which shows the same
map, has a Stop button, and can turn the panel off for later runs.

The tree itself is app/map_tree.py's MapTree, which the Mapping page
uses too.

This window used to mark itself invisible to screen-capture APIs, via
SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE), so it could never end
up in a screenshot the AI took for an unrelated reason while a mapping
run was going in the background. That call is gone. It is the API a
piece of malware uses to keep an overlay out of a screen recording, it
is scored as such by antivirus heuristics, and what it bought here was
cosmetic: a status panel in the corner of a screenshot is noise the
model ignores, not a wrong answer. See docs/ANTIVIRUS.md.

Follows a mapping run via ai/discovery/events.py's pub-sub - not by
importing ai.discovery.mapper directly - the same GUI/business-logic
boundary app/console_widget.py keeps with ai.log's sink mechanism.
Callbacks arrive on whatever thread the mapping run is executing on
(never the GUI thread), so each one just emits a signal; Qt marshals the
actual widget update onto this window's own (GUI) thread automatically.
"""

import sys

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from ai.discovery import events as discovery_events
from ai.log import log_debug
import app.icons as icons
from app.i18n import tr
from app.map_tree import ElementHighlight, MapTree


def _stop_shortcut() -> str:
    """The stop shortcut as it is written on a key cap: Ctrl+Alt+X."""
    import ai.main as ai_main

    combo = ai_main.get_shortcut("stop_automation") or ""
    return "+".join(part.upper() if len(part) == 1 else part.capitalize()
                    for part in combo.split("+") if part)

ACCENT_COLOR = "#7c5cff"
_ICON_COLORS = {"fg": "#cfcfd4", "accent": ACCENT_COLOR, "warn": "#e0b35c", "muted": "#9a9aa3"}


def _overlay_enabled() -> bool:
    """The Mapping page's "Show the map on screen" switch."""
    try:
        import ai.main as ai_main
        return ai_main.get_show_mapping_overlay()
    except Exception:
        return True


class MappingMonitor(QWidget):
    """
    One instance, owned by MainWindow (see main_window.py's
    self._mapping_monitor) and reused across every mapping run for the
    app's whole lifetime - registers its event callbacks exactly once in
    __init__, the same one-registration-per-instance discipline
    ConsoleWidget follows with ai.log's sinks.
    """

    MARGIN = 20
    WIDTH = 360
    HEIGHT = 470

    _started = pyqtSignal(str)
    _progress = pyqtSignal(int, int)
    _finished = pyqtSignal(str, int, int)
    _node = pyqtSignal(object)
    _edge = pyqtSignal(str, str, str)

    def __init__(self, parent: "QWidget | None" = None):
        super().__init__(parent, Qt.WindowType.Window)
        self._interactive = False
        self._running = False
        # Turned off from the Mapping page: the panel stays hidden, and
        # keeps following runs so it can come back with the map.
        self._user_visible = _overlay_enabled()
        self._apply_window_flags(interactive=False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # Qt only shows tooltips in the active window unless told otherwise,
        # and this one is never activated (it must not take focus from the
        # app being mapped). The tooltips are where the coordinates are.
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        self._container = QWidget(self)
        self._container.setObjectName("mappingMonitor")
        self._container.setGeometry(0, 0, self.WIDTH, self.HEIGHT)
        self._container.setStyleSheet("""
            QWidget#mappingMonitor {
                background-color: rgba(18, 18, 20, 238);
                border: 1px solid rgba(124, 92, 255, 0.45);
                border-radius: 10px;
            }
            QLabel { background: transparent; border: none; color: #f2f2f2; font-family: "Segoe UI"; }
            QTreeWidget {
                background: transparent;
                border: none;
                color: #d6d6da;
                font-family: "Segoe UI";
                font-size: 11.5px;
                outline: none;
            }
            QTreeWidget::item { padding: 2px 0px; border-radius: 4px; }
            QTreeWidget::item:hover { background-color: rgba(124, 92, 255, 0.18); }
            QTreeWidget::item:selected { background-color: rgba(124, 92, 255, 0.28); color: #ffffff; }
            QTreeWidget::branch { background: transparent; }
            QScrollBar:vertical { background: transparent; width: 6px; margin: 2px; }
            QScrollBar::handle:vertical { background: rgba(255,255,255,0.18); border-radius: 3px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QToolTip {
                background-color: #1c1c21; color: #f2f2f4; border: 1px solid #3a3a44;
                padding: 6px 8px; font-family: "Segoe UI"; font-size: 11.5px;
            }
            QPushButton#monitorClose {
                background: transparent; border: none; border-radius: 6px;
                min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px;
            }
            QPushButton#monitorClose:hover { background-color: rgba(255,255,255,0.1); }
        """)
        shadow = QGraphicsDropShadowEffect(self._container)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        self._container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self._container)
        layout.setContentsMargins(14, 12, 10, 12)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(6)
        tree_icon = QLabel()
        tree_icon.setPixmap(icons.pixmap("tree", _ICON_COLORS, 14, "accent"))
        header.addWidget(tree_icon)
        self._title = QLabel(tr("Mapping the app"))
        self._title.setStyleSheet("font-size: 13px; font-weight: 700;")
        header.addWidget(self._title)
        header.addStretch()
        self._timer_label = QLabel("00:00")
        self._timer_label.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {ACCENT_COLOR};")
        header.addWidget(self._timer_label)
        self._close_btn = QPushButton()
        self._close_btn.setObjectName("monitorClose")
        self._close_btn.setIcon(icons.icon("close", _ICON_COLORS, "fg"))
        self._close_btn.setIconSize(QSize(12, 12))
        self._close_btn.setToolTip(tr("Close"))
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.clicked.connect(self._dismiss)
        self._close_btn.hide()
        header.addWidget(self._close_btn)
        layout.addLayout(header)

        self._app_label = QLabel("")
        self._app_label.setStyleSheet("font-size: 11px; color: #9a9aa3;")
        layout.addWidget(self._app_label)

        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet("font-size: 11px; color: #cfcfd4;")
        layout.addWidget(self._stats_label)

        self._tree = MapTree(_ICON_COLORS)
        self._tree.controlHovered.connect(self._on_control_hovered)
        self._tree.hoverEnded.connect(self._hide_highlight)
        layout.addWidget(self._tree, 1)

        self._hint = QLabel("")
        self._hint.setStyleSheet("font-size: 10.5px; color: #7a7a82;")
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._highlight = ElementHighlight()

        self._elapsed_seconds = 0
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)

        self._started.connect(self._on_started)
        self._progress.connect(self._on_progress)
        self._finished.connect(self._on_finished)
        self._node.connect(self._on_node)
        self._edge.connect(self._on_edge)
        # Stored as attributes (not re-accessed via self._started.emit
        # each time) so the SAME object reference is what's registered
        # and later removed in closeEvent - repeated attribute access on
        # a bound pyqtSignal isn't guaranteed to return an object that
        # compares equal for list membership/removal purposes.
        self._on_started_cb = self._started.emit
        self._on_progress_cb = self._progress.emit
        self._on_finished_cb = self._finished.emit
        self._on_node_cb = self._node.emit
        self._on_edge_cb = self._edge.emit
        discovery_events.on_started(self._on_started_cb)
        discovery_events.on_progress(self._on_progress_cb)
        discovery_events.on_finished(self._on_finished_cb)
        discovery_events.on_node(self._on_node_cb)
        discovery_events.on_edge(self._on_edge_cb)

    # -- window mode -----------------------------------------------------

    def _apply_window_flags(self, interactive: bool) -> None:
        """Click-through while the mapper is clicking; hoverable after."""
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if not interactive:
            flags |= Qt.WindowType.WindowTransparentForInput
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not interactive)
        self._interactive = interactive
        if was_visible:
            self.show()   # changing window flags hides a window
            self._set_native_click_through(not interactive)

    def _set_native_click_through(self, enabled: bool) -> None:
        """
        The same click-through setting, made on the native window too.
        Clearing Qt's WindowTransparentForInput flag on a window that
        already exists doesn't reliably clear Windows' own flag for it
        (WS_EX_TRANSPARENT), and when it stays, the finished map, close
        button included, keeps letting every click through to whatever
        is under it: there is then no way to close it.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_style = user32.GetWindowLongPtrW
            get_style.restype = ctypes.c_ssize_t
            get_style.argtypes = [wintypes.HWND, ctypes.c_int]
            set_style = user32.SetWindowLongPtrW
            set_style.restype = ctypes.c_ssize_t
            set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            GWL_EXSTYLE, WS_EX_TRANSPARENT = -20, 0x20
            hwnd = wintypes.HWND(int(self.winId()))
            style = get_style(hwnd, GWL_EXSTYLE)
            wanted = (style | WS_EX_TRANSPARENT) if enabled else (style & ~WS_EX_TRANSPARENT)
            if wanted != style:
                set_style(hwnd, GWL_EXSTYLE, wanted)
        except Exception as e:
            log_debug(f"[mapping_monitor] click-through not changed natively: {e}")

    def _show_panel(self) -> None:
        if not self.isVisible():
            self.show()
            self._set_native_click_through(not self._interactive)

    def set_user_visible(self, visible: bool) -> None:
        """The Mapping page's switch. Off hides the panel now and keeps it
        hidden for later runs; on brings it back while a run is going. Not
        after one: the map of a finished run may have been closed with the
        X already, and the Mapping page shows it anyway."""
        self._user_visible = visible
        if not visible:
            self._dismiss()
        elif self._running:
            self._position_top_left()
            self._show_panel()

    # -- event pub-sub handlers (run on the GUI thread via the signals above) --

    def _on_started(self, app_name: str) -> None:
        if self._interactive:
            self._apply_window_flags(interactive=False)
        self._running = True
        self._title.setText(tr("Mapping the app"))
        self._app_label.setText(tr("Target: {app}", app=app_name))
        self._stats_label.setText(tr("{screens} screens · {controls} controls", screens=0, controls=0))
        self._tree.reset()
        self._close_btn.hide()
        # Not Esc: the mapper presses Esc itself to back out of menus and
        # dialogs, so Esc can't be what stops it. The panel lets clicks
        # through while the run goes, so the stop shortcut is the way.
        self._hint.setText(tr("To stop, press {shortcut}. The Mapping page can hide this panel.",
                              shortcut=_stop_shortcut()))
        self._hint.show()
        self._timer_label.show()
        self._elapsed_seconds = 0
        self._timer_label.setText("00:00")
        self._clock_timer.start(1000)
        if self._user_visible:
            self._position_top_left()
            self._show_panel()

    def _on_progress(self, nodes: int, actions: int) -> None:
        self._stats_label.setText(tr("{screens} screens · {controls} controls", screens=nodes, controls=actions))

    def _on_node(self, info: dict) -> None:
        self._tree.add_screen(info)

    def _on_edge(self, from_node: str, action_id: str, to_node: str) -> None:
        self._tree.add_link(from_node, action_id, to_node)

    def _on_finished(self, app_name: str, nodes: int, actions: int) -> None:
        self._running = False
        self._clock_timer.stop()
        self._highlight.hide()
        if not self._tree.screen_count():
            self.hide()
            return
        # Stays up, now hoverable, until closed.
        self._title.setText(tr("Map of {app}", app=app_name))
        self._stats_label.setText(tr("{screens} screens · {controls} controls", screens=nodes, controls=actions))
        self._timer_label.hide()
        self._close_btn.show()
        self._hint.setText(tr("Hover a control to see where it is on screen."))
        self._apply_window_flags(interactive=True)

    # -- hover -------------------------------------------------------------

    def _on_control_hovered(self, box: dict, label: str) -> None:
        # Only once the run is over: while it runs the panel is
        # click-through and the screen is the mapper's.
        if not self._interactive:
            self._highlight.hide()
            return
        self._highlight.show_box(box or {}, label or "")

    def _hide_highlight(self) -> None:
        self._highlight.hide()

    def leaveEvent(self, event) -> None:
        self._highlight.hide()
        super().leaveEvent(event)

    def _dismiss(self) -> None:
        self._highlight.hide()
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
        discovery_events.remove_node(self._on_node_cb)
        discovery_events.remove_edge(self._on_edge_cb)

    def closeEvent(self, event) -> None:
        # closeEvent isn't reliably delivered to a widget that was never
        # actually shown (notably under tests, running the offscreen Qt
        # platform) - tests should call _unregister_events() directly
        # rather than relying on .close() alone; a stale registration
        # left pointing at a since-garbage-collected widget segfaults
        # the next time notify_started/_progress/_finished fires.
        self._highlight.hide()
        self._unregister_events()
        super().closeEvent(event)
