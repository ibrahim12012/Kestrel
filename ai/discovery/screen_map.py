# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/discovery/screen_map.py
============================
Names and coordinates for what is on screen, so the model is not
guessing from pixels alone.

A screenshot shows a button; it does not say which button. Two
screenshots a step apart can show a button in the same place, and
nothing in the pixels says whether it is the same button or a different
one that took its place: a "Save" that became "Delete" after a dialog
opened, a list that scrolled under the pointer. This reads the same
screen through Windows UI Automation, which does know: every control's
name, type and position.

  capture()           the controls of the window in front (not
                      Kestrel's own), with a time and size budget
  diff(before, after) what changed between two captures, by position:
                      same control, a DIFFERENT control at the same
                      coordinates, moved, new, gone
  describe(...)       both, as text for the model; kept per window, so
                      each call is compared with the previous one
  control_at(x, y)    the control at a screen point right now

Autonomous mode sends describe() with every step's screenshot, and the
chat's screenshot tool returns it. Coordinates are screen pixels, the
same ones clicks use, whatever size the screenshot is shown at.

Everything here is best effort: on another platform, without the
uiautomation package, or on an app that exposes nothing, it returns
nothing rather than raising, and the model works from the screenshot as
before.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass, field

from ai.log import log_debug

try:
    import uiautomation as auto
except ImportError:
    auto = None

from ai.discovery.accessibility import INTERACTABLE_CONTROL_TYPES

# Enough to cover a window's real controls, few enough to stay a short
# paragraph of text. A browser page can expose thousands.
MAX_CONTROLS = 60
# Reading UI Automation is one cross-process call per property; a big
# window is slow to walk. The step waits for this at most.
TIME_BUDGET = 0.45
MAX_DEPTH = 9
# Two controls whose centres are this close are "at the same place".
SAME_PLACE_PX = 8


@dataclass
class ScreenControl:
    name: str
    control_type: str
    x: int
    y: int
    w: int
    h: int
    automation_id: str = ""

    @property
    def center(self) -> "tuple[int, int]":
        return self.x + self.w // 2, self.y + self.h // 2

    @property
    def identity(self) -> tuple:
        return self.control_type, self.name, self.automation_id

    def label(self) -> str:
        name = self.name.replace("\n", " ").strip()
        if len(name) > 60:
            name = name[:57] + "..."
        return f"{self.control_type} '{name}'" if name else f"{self.control_type} (no name)"


@dataclass
class ScreenMap:
    window_title: str = ""
    window_handle: int = 0
    controls: list = field(default_factory=list)
    partial: bool = False
    taken_at: float = 0.0


_lock = threading.Lock()
_previous: "dict[int, ScreenMap]" = {}


def available() -> bool:
    if sys.platform != "win32" or auto is None:
        return False
    # UI Automation needs COM set up on the calling thread, and the
    # screenshots this describes are taken on worker threads.
    from ai.direct_input import ui_automation
    ui_automation()
    return True


def _foreground_window_handle() -> int:
    """The window in front, skipping Kestrel's own windows (its chat, its
    overlays): the thing being worked on is the app behind them."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    GW_HWNDNEXT = 2
    own_pid = os.getpid()
    hwnd = user32.GetForegroundWindow()
    for _ in range(64):
        if not hwnd:
            return 0
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        visible = user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd)
        big_enough = (rect.right - rect.left) > 80 and (rect.bottom - rect.top) > 60
        if pid.value != own_pid and visible and big_enough:
            return int(hwnd)
        hwnd = user32.GetWindow(hwnd, GW_HWNDNEXT)
    return 0


def capture(max_controls: int = MAX_CONTROLS, time_budget: float = TIME_BUDGET) -> "ScreenMap | None":
    """The front window's interactable controls, or None when that can't
    be read. Stops at max_controls or time_budget and says it did."""
    if not available():
        return None
    try:
        handle = _foreground_window_handle()
        if not handle:
            return None
        window = auto.ControlFromHandle(handle)
        if window is None:
            return None
        result = ScreenMap(window_title=(window.Name or "").strip(), window_handle=handle, taken_at=time.time())
        deadline = time.monotonic() + time_budget
        for control, _depth in auto.WalkControl(window, includeTop=False, maxDepth=MAX_DEPTH):
            if time.monotonic() > deadline or len(result.controls) >= max_controls:
                result.partial = True
                break
            try:
                control_type = (control.ControlTypeName or "").replace("Control", "")
                if control_type not in INTERACTABLE_CONTROL_TYPES:
                    continue
                if not control.IsEnabled or control.IsOffscreen:
                    continue
                rect = control.BoundingRectangle
                w, h = rect.right - rect.left, rect.bottom - rect.top
                if w <= 1 or h <= 1:
                    continue
                result.controls.append(ScreenControl(
                    name=(control.Name or "").strip(),
                    control_type=control_type,
                    x=rect.left, y=rect.top, w=w, h=h,
                    automation_id=(getattr(control, "AutomationId", "") or "").strip(),
                ))
            except Exception as e:  # a control that vanished mid-walk
                log_debug(f"[screen_map] skipped a control: {e}")
        return result
    except Exception as e:
        log_debug(f"[screen_map] capture failed: {e}")
        return None


def control_at(x: int, y: int) -> "ScreenControl | None":
    """The control under screen point (x, y) right now."""
    if not available():
        return None
    try:
        control = auto.ControlFromPoint(int(x), int(y))
        if control is None:
            return None
        rect = control.BoundingRectangle
        return ScreenControl(
            name=(control.Name or "").strip(),
            control_type=(control.ControlTypeName or "").replace("Control", "") or "Unknown",
            x=rect.left, y=rect.top, w=rect.right - rect.left, h=rect.bottom - rect.top,
            automation_id=(getattr(control, "AutomationId", "") or "").strip(),
        )
    except Exception as e:
        log_debug(f"[screen_map] control_at failed: {e}")
        return None


def _near(a: "tuple[int, int]", b: "tuple[int, int]", tolerance: int = SAME_PLACE_PX) -> bool:
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


def diff(before: "ScreenMap | None", after: "ScreenMap | None") -> dict:
    """
    What changed between two captures of the same window, matched by
    position first: that is the question that matters before a click.

      replaced   (old, new)  a different control now sits where old was
      same       [control]   the same control at the same place
      moved      (old, new)  the same control, somewhere else
      appeared   [control]
      gone       [control]
    """
    result = {"replaced": [], "same": [], "moved": [], "appeared": [], "gone": []}
    if before is None or after is None:
        return result
    unmatched_after = list(after.controls)

    def take(predicate):
        for i, candidate in enumerate(unmatched_after):
            if predicate(candidate):
                return unmatched_after.pop(i)
        return None

    leftovers = []
    for old in before.controls:
        same = take(lambda c: c.identity == old.identity and _near(c.center, old.center))
        if same is not None:
            result["same"].append(same)
        else:
            leftovers.append(old)
    still_unmatched = []
    for old in leftovers:
        moved = take(lambda c: c.identity == old.identity)
        if moved is not None:
            result["moved"].append((old, moved))
        else:
            still_unmatched.append(old)
    for old in still_unmatched:
        replacement = take(lambda c: _near(c.center, old.center))
        if replacement is not None:
            result["replaced"].append((old, replacement))
        else:
            result["gone"].append(old)
    result["appeared"] = unmatched_after
    return result


def _format_controls(screen: ScreenMap, limit: int) -> "list[str]":
    lines = []
    for i, c in enumerate(screen.controls[:limit], 1):
        cx, cy = c.center
        lines.append(f"  [{i}] {c.label()} at ({cx}, {cy}), box {c.x},{c.y} {c.w}x{c.h}")
    if len(screen.controls) > limit:
        lines.append(f"  ... and {len(screen.controls) - limit} more")
    return lines


def _format_diff(changes: dict, limit: int = 8) -> "list[str]":
    lines = []
    for old, new in changes["replaced"][:limit]:
        cx, cy = new.center
        lines.append(f"  DIFFERENT control at ({cx}, {cy}): was {old.label()}, now {new.label()}")
    for old, new in changes["moved"][:limit]:
        lines.append(f"  moved: {new.label()} from {old.center} to {new.center}")
    if changes["appeared"]:
        shown = ", ".join(f"{c.label()} at {c.center}" for c in changes["appeared"][:limit])
        more = len(changes["appeared"]) - limit
        lines.append(f"  new: {shown}" + (f" (+{more} more)" if more > 0 else ""))
    if changes["gone"]:
        shown = ", ".join(f"{c.label()} (was at {c.center})" for c in changes["gone"][:limit])
        more = len(changes["gone"]) - limit
        lines.append(f"  gone: {shown}" + (f" (+{more} more)" if more > 0 else ""))
    if changes["same"]:
        lines.append(f"  unchanged: {len(changes['same'])} control(s) are the same ones in the same places")
    return lines


def describe(limit: int = 40, screen: "ScreenMap | None" = None) -> str:
    """
    The front window's controls as text for the model, with what changed
    since the previous call for the same window. "" when nothing could be
    read (the model then works from the screenshot alone).
    """
    screen = screen if screen is not None else capture()
    if screen is None or not screen.controls:
        return ""
    with _lock:
        previous = _previous.get(screen.window_handle)
        _previous[screen.window_handle] = screen
    title = screen.window_title or "the window in front"
    lines = [
        f"Controls in \"{title}\" (read from Windows UI Automation; coordinates are screen "
        f"pixels, the ones clicks use{', list cut short' if screen.partial else ''}):"
    ]
    lines += _format_controls(screen, limit)
    if previous is not None:
        changes = diff(previous, screen)
        change_lines = _format_diff(changes)
        if changes["replaced"] or changes["moved"] or changes["appeared"] or changes["gone"]:
            lines.append("Compared with the previous screenshot of this window:")
            lines += change_lines
        else:
            lines.append("Compared with the previous screenshot of this window: the same controls, "
                         "in the same places.")
    return "\n".join(lines)


def check_point(x: int, y: int) -> str:
    """
    What is at (x, y) now, compared with what the last description of
    that window said was there. For the model to call before trusting
    coordinates from an earlier screenshot.
    """
    now = control_at(x, y)
    if now is None:
        return f"Nothing readable is at ({x}, {y}) (or UI Automation isn't available)."
    text = f"At ({x}, {y}) now: {now.label()}, box {now.x},{now.y} {now.w}x{now.h}."
    with _lock:
        maps = list(_previous.values())
    for screen in maps:
        for c in screen.controls:
            if c.x <= x <= c.x + c.w and c.y <= y <= c.y + c.h:
                if c.identity == now.identity:
                    return text + f" Same control as in the last screenshot ({c.label()})."
                return text + f" DIFFERENT from the last screenshot, which had {c.label()} there."
    return text
