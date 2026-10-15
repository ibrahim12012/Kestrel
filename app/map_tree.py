# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/map_tree.py
=================
The map a mapping run makes (ai.discovery, tool_discover_application_ui),
drawn as a tree, and the outline that shows where a control is on the
real screen.

The map is a tree: each screen lists its controls, and a screen that a
control opens sits under that control, so the path to any screen reads
top to bottom. Every control shows its name and type; its tooltip has
its screen coordinates and size.

Two places show it: the see-through overlay in the corner of the screen
(app/mapping_monitor.py) and the Mapping page (app/mapping_widget.py).
Both feed a MapTree the same events (ai/discovery/events.py) and style it
their own way; this module has no idea which one it is in.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget

import app.icons as icons
from app.i18n import tr

HIGHLIGHT_COLOR = "#7c5cff"

# Item data roles. `.value` rather than int(): whether a PyQt6 enum
# converts to int depends on the enum and the version, .value never does.
_USER_ROLE = Qt.ItemDataRole.UserRole.value
ROLE_KIND = _USER_ROLE
ROLE_BOX = _USER_ROLE + 1
ROLE_LABEL = _USER_ROLE + 2


def native_to_logical(x: int, y: int) -> "tuple[QPoint, float]":
    """
    A screen point in physical pixels (what UI Automation reports) as a Qt
    position, and the scale of the screen it is on. Qt keeps each screen's
    top-left at its native position and scales only within the screen.
    """
    for screen in QGuiApplication.screens():
        geo = screen.geometry()
        ratio = screen.devicePixelRatio() or 1.0
        native = QRect(geo.x(), geo.y(), int(geo.width() * ratio), int(geo.height() * ratio))
        if native.contains(x, y):
            return QPoint(geo.x() + int((x - geo.x()) / ratio), geo.y() + int((y - geo.y()) / ratio)), ratio
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    return QPoint(int(x / ratio), int(y / ratio)), ratio


class ElementHighlight(QWidget):
    """An outline around a control on the real screen, with its name, while
    its row in a map is hovered. Never takes input or focus."""

    PAD = 3

    def __init__(self):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._label = ""
        self._box = QRect()

    def show_box(self, box: dict, label: str) -> None:
        x, y, w, h = int(box.get("x", 0)), int(box.get("y", 0)), int(box.get("w", 0)), int(box.get("h", 0))
        if w <= 0 or h <= 0:
            self.hide()
            return
        top_left, ratio = native_to_logical(x, y)
        width, height = max(4, int(w / ratio)), max(4, int(h / ratio))
        self._label = label
        tag_height = 22
        self.setGeometry(
            top_left.x() - self.PAD, top_left.y() - self.PAD - tag_height,
            max(width + 2 * self.PAD, 160), height + 2 * self.PAD + tag_height,
        )
        self._box = QRect(self.PAD, self.PAD + tag_height, width, height)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent = QColor(HIGHLIGHT_COLOR)
        fill = QColor(accent)
        fill.setAlpha(40)
        painter.setPen(QPen(accent, 2))
        painter.setBrush(fill)
        painter.drawRoundedRect(self._box.adjusted(-1, -1, 1, 1), 4, 4)
        if self._label:
            font = painter.font()
            font.setFamily("Segoe UI")
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            text_w = metrics.horizontalAdvance(self._label) + 14
            tag = QRect(self._box.x() - 1, self._box.y() - 22, text_w, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(tag, 4, 4)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(tag, int(Qt.AlignmentFlag.AlignCenter.value), self._label)
        painter.end()


class MapTree(QTreeWidget):
    """
    The map as a tree. Fed with add_screen() and add_link(), or replay()
    for a run that started before this tree existed.

    `palette` gives the icon and link colours: "fg" for controls, "accent"
    for screens, "warn" for controls that look destructive, "muted" for
    links to a screen shown elsewhere in the tree.

    Hovering a control emits controlHovered(box, label), with the box in
    physical screen pixels; leaving it emits hoverEnded(). What to do with
    that (outline the control on the screen, say) is up to the owner.
    """

    controlHovered = pyqtSignal(object, str)
    hoverEnded = pyqtSignal()

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = dict(palette)
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setAnimated(True)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QTreeWidget.ScrollMode.ScrollPerPixel)
        self.setIconSize(QSize(13, 13))
        self.itemEntered.connect(self._on_item_entered)

        self._screens: dict = {}          # node id -> QTreeWidgetItem
        self._actions: dict = {}          # (node id, action id) -> QTreeWidgetItem
        self._screen_numbers: dict = {}   # node id -> 1, 2, 3...
        self._links: set = set()          # (from node, action id, to node) already shown

    # ── Contents ────────────────────────────────────────────────────

    def set_palette(self, palette: dict) -> None:
        """Colours for rows added from now on (replay() redraws the rest)."""
        self._palette = dict(palette)

    def reset(self) -> None:
        self.clear()
        self._screens.clear()
        self._actions.clear()
        self._screen_numbers.clear()
        self._links.clear()
        self.hoverEnded.emit()

    def screen_count(self) -> int:
        return len(self._screens)

    def screen_item(self, node_id: str) -> "QTreeWidgetItem | None":
        return self._screens.get(node_id)

    def replay(self, record: dict) -> None:
        """Draw a run from ai.discovery.events.last_run(): every screen and
        link it has sent so far, in order."""
        self.reset()
        for info in record.get("nodes") or []:
            self.add_screen(info)
        for edge in record.get("edges") or []:
            self.add_link(*edge)

    def add_screen(self, info: dict) -> None:
        """A new screen, with its controls (ai.discovery.tree.node_event),
        under the control that opened it."""
        node_id = info.get("id") or ""
        if not node_id or node_id in self._screens:
            return
        number = len(self._screen_numbers) + 1
        self._screen_numbers[node_id] = number
        title = (info.get("title") or "").strip()
        label = tr("Screen {n}", n=number) + (f"  ·  {title}" if title else "")
        item = QTreeWidgetItem([label])
        item.setIcon(0, icons.icon("window", self._palette, "accent"))
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setData(0, ROLE_KIND, "screen")
        actions = info.get("actions") or []
        item.setToolTip(0, "\n".join(filter(None, [
            tr("Screen {n}", n=number),
            tr("Window: {title}", title=title) if title else "",
            tr("{count} controls", count=len(actions)),
            tr("Reached from: {via}", via=self._describe_via(info)) if info.get("parent") else tr("Where mapping started"),
        ])))
        for action in actions:
            item.addChild(self._action_item(node_id, action))

        parent_item = self._actions.get((info.get("parent"), info.get("via")))
        if parent_item is None:
            parent_item = self._screens.get(info.get("parent"))
        if parent_item is not None:
            parent_item.addChild(item)
            parent_item.setExpanded(True)
        else:
            self.addTopLevelItem(item)
        self._screens[node_id] = item
        item.setExpanded(number <= 2)
        self.scrollToItem(item)

    def add_link(self, from_node: str, action_id: str, to_node: str) -> None:
        """An action that leads to a screen already on the map: noted under
        the action, since that screen already sits elsewhere in the tree."""
        key = (from_node, action_id, to_node)
        action_item = self._actions.get((from_node, action_id))
        number = self._screen_numbers.get(to_node)
        if key in self._links or action_item is None or number is None or to_node == from_node:
            return
        self._links.add(key)
        link = QTreeWidgetItem(["→ " + tr("Screen {n}", n=number)])
        link.setForeground(0, QColor(self._palette.get("muted", "#9a9aa3")))
        link.setToolTip(0, tr("This control leads to Screen {n}, shown elsewhere in the map.", n=number))
        action_item.addChild(link)

    # ── Rows ────────────────────────────────────────────────────────

    def _action_item(self, node_id: str, action: dict) -> QTreeWidgetItem:
        name = (action.get("name") or "").strip()
        control_type = action.get("type") or "Control"
        box = action.get("box") or {}
        x, y, w, h = int(box.get("x", 0)), int(box.get("y", 0)), int(box.get("w", 0)), int(box.get("h", 0))
        cx, cy = x + w // 2, y + h // 2
        shown_name = name if name else tr("(no name)")
        item = QTreeWidgetItem([f"{shown_name}  ·  {control_type}"])
        icon_name = "warning" if action.get("destructive") else ("edit" if action.get("input") else "target")
        item.setIcon(0, icons.icon(icon_name, self._palette, "warn" if action.get("destructive") else "fg"))
        lines = [
            f"{shown_name} ({control_type})",
            tr("Centre: ({x}, {y}) px", x=cx, y=cy),
            tr("Box: {x}, {y} · {w} × {h}", x=x, y=y, w=w, h=h),
            tr("On: Screen {n}", n=self._screen_numbers.get(node_id, "?")),
        ]
        if action.get("destructive"):
            lines.append(tr("Looks destructive: recorded, never clicked while mapping."))
        elif action.get("input"):
            lines.append(tr("Text field: a test value was typed into it."))
        item.setToolTip(0, "\n".join(lines))
        item.setData(0, ROLE_KIND, "action")
        item.setData(0, ROLE_BOX, box)
        item.setData(0, ROLE_LABEL, f"{shown_name}  ({cx}, {cy})")
        self._actions[(node_id, action.get("id"))] = item
        return item

    def _describe_via(self, info: dict) -> str:
        parent_number = self._screen_numbers.get(info.get("parent"), "?")
        action_item = self._actions.get((info.get("parent"), info.get("via")))
        control = action_item.text(0).split("  ·  ")[0] if action_item is not None else info.get("via") or "?"
        return tr("\"{control}\" on Screen {n}", control=control, n=parent_number)

    # ── Hover ───────────────────────────────────────────────────────

    def _on_item_entered(self, item: QTreeWidgetItem, column: int) -> None:
        if item is None or item.data(0, ROLE_KIND) != "action":
            self.hoverEnded.emit()
            return
        self.controlHovered.emit(item.data(0, ROLE_BOX) or {}, item.data(0, ROLE_LABEL) or "")

    def leaveEvent(self, event) -> None:
        self.hoverEnded.emit()
        super().leaveEvent(event)
