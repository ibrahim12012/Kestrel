# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/dpi_guard.py
================
Keeps the main window drawn at the scale Windows has it at.

What it is for: after Alt+Tab the window could show twice, a smaller copy
of the whole interface over the top-left of the real one. Qt had dropped
the window's scale to 100% while Windows still had it at 125%, so it drew
everything at 100% into the top-left part of a window that stayed its
125% size, and the rest of the window kept the last frame drawn at 125%.

The likely cause is fixed at startup (main.py sets the DPI mode Qt is
built for before anything else can set another one; see
app/win_runtime.py). This is the net under it, for whatever else gets
Qt and Windows out of step: when the window is activated or Qt changes
its scale, the two are compared, and if they disagree and the drawing no
longer covers the window, Qt is told which screen the window is on and
asked to read the screens again, as it does when Windows reports a
display change. If that doesn't do it, the window is resized to what Qt
draws, so it is at least drawn once. Each step is logged.
"""

from __future__ import annotations

import math
import os
import sys
import time

from ai.log import log, log_debug

# Set by someone who wants a scale of their own: then Qt's scale is
# meant to differ from Windows', and nothing here second-guesses it.
_OVERRIDES = (
    "QT_SCALE_FACTOR", "QT_SCREEN_SCALE_FACTORS", "QT_ENABLE_HIGHDPI_SCALING",
    "QT_USE_PHYSICAL_DPI", "QT_SCALE_FACTOR_ROUNDING_POLICY",
)

# How long after an activation or a scale change to look: long enough for
# a real DPI change to finish (Windows resizes the window right after it
# tells Qt), short enough to repair before anyone reads the window.
CHECK_DELAY_MS = 250
# One repair per this many seconds, so a cause that comes straight back
# can't turn this into a loop.
_REPAIR_GAP = 2.0

WM_SETTINGCHANGE = 0x001A
MONITOR_DEFAULTTONEAREST = 2


def rounded_scale(raw: float, policy: str) -> float:
    """The scale Qt uses for a raw Windows scale (1.25 at 125%) under its
    rounding policy, as QHighDpiScaling::roundScaleFactor computes it."""
    if policy == "Round":
        value = math.floor(raw + 0.5)
    elif policy == "Ceil":
        value = math.ceil(raw)
    elif policy == "Floor":
        value = math.floor(raw)
    elif policy == "RoundPreferFloor":
        value = math.floor(raw) if raw - math.floor(raw) < 0.75 else math.ceil(raw)
    else:                                   # PassThrough, Qt 6's default
        value = raw
    return max(float(value), 1.0)


def out_of_step(qt_scale: float, logical_size, client_size, windows_scale: float, policy: str) -> bool:
    """
    True when Qt draws the window at a scale Windows doesn't have it at
    AND that drawing no longer covers the window: the doubled window.

    Both, because each alone happens for a moment in a normal DPI change
    (moving to another monitor, changing the scale in Settings): one side
    has heard about it and the other hasn't yet.
    """
    if not qt_scale or not windows_scale:
        return False
    if abs(qt_scale - rounded_scale(windows_scale, policy)) < 0.01:
        return False
    width, height = logical_size
    client_width, client_height = client_size
    if min(width, height, client_width, client_height) <= 0:
        return False                        # minimized, or not laid out yet
    slack = 2 + qt_scale
    return (abs(client_width - width * qt_scale) > slack
            or abs(client_height - height * qt_scale) > slack)


class _Native:
    """The few user32 calls this needs, typed once."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as wt

        self._c, self._wt = ctypes, wt
        u = ctypes.WinDLL("user32", use_last_error=True)
        self._u = u

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                        ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

        self._MONITORINFO = MONITORINFO
        u.GetDpiForWindow.restype = wt.UINT
        u.GetDpiForWindow.argtypes = [wt.HWND]
        u.GetClientRect.restype = wt.BOOL
        u.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        u.MonitorFromWindow.restype = wt.HMONITOR
        u.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
        u.GetMonitorInfoW.restype = wt.BOOL
        u.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
        u.SendMessageW.restype = wt.LPARAM
        u.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

    def scale(self, hwnd) -> float:
        dpi = self._u.GetDpiForWindow(hwnd)
        return dpi / 96.0 if dpi else 0.0

    def client_size(self, hwnd):
        rect = self._wt.RECT()
        if not self._u.GetClientRect(hwnd, self._c.byref(rect)):
            return 0, 0
        return rect.right - rect.left, rect.bottom - rect.top

    def monitor_origin(self, hwnd):
        """Top-left corner of the monitor the window is on, in physical
        pixels, or None."""
        monitor = self._u.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return None
        info = self._MONITORINFO()
        info.cbSize = self._c.sizeof(info)
        if not self._u.GetMonitorInfoW(monitor, self._c.byref(info)):
            return None
        return info.rcMonitor.left, info.rcMonitor.top

    def reread_screens(self, hwnd) -> None:
        """What Windows sends when a display setting changes; Qt answers
        it by reading every screen's size and DPI again."""
        self._u.SendMessageW(hwnd, WM_SETTINGCHANGE, 0, 0)


class DpiGuard:
    """Watches one top-level window. MainWindow calls schedule() when it
    is activated or shown; a screen changing size (which is what a scale
    change looks like in Qt's coordinates), appearing or going away
    schedules a check too."""

    def __init__(self, window):
        from PyQt6.QtCore import QTimer
        from PyQt6.QtGui import QGuiApplication

        self._window = window
        self._native = None
        self._last_repair = 0.0
        self.enabled = sys.platform == "win32" and not any(os.environ.get(name) for name in _OVERRIDES)
        self._timer = QTimer(window)
        self._timer.setSingleShot(True)
        self._timer.setInterval(CHECK_DELAY_MS)
        self._timer.timeout.connect(self.check)
        app = QGuiApplication.instance()
        if self.enabled and app is not None:
            app.screenAdded.connect(self._watch_screen)
            app.screenRemoved.connect(lambda _screen: self.schedule())
            for screen in app.screens():
                self._watch_screen(screen, check=False)

    def _watch_screen(self, screen, check: bool = True) -> None:
        screen.geometryChanged.connect(lambda _rect: self.schedule())
        if check:
            self.schedule()

    def schedule(self) -> None:
        if self.enabled:
            self._timer.start()

    # -- reading ------------------------------------------------------------

    def _native_calls(self):
        if self._native is None:
            self._native = _Native()
        return self._native

    def _hwnd(self) -> int:
        return int(self._window.winId())

    def read(self):
        """(Qt's scale, logical size, client size, Windows' scale, rounding
        policy) for the window now, or None when it can't be read."""
        from PyQt6.QtGui import QGuiApplication

        window = self._window
        if not window.isVisible() or window.isMinimized():
            return None
        native = self._native_calls()
        hwnd = self._hwnd()
        policy = QGuiApplication.highDpiScaleFactorRoundingPolicy()
        return (window.devicePixelRatioF(), (window.width(), window.height()),
                native.client_size(hwnd), native.scale(hwnd), getattr(policy, "name", str(policy)))

    # -- checking and repairing ---------------------------------------------

    def check(self) -> bool:
        """Compare, and repair when out of step. True if it repaired."""
        try:
            state = self.read()
        except Exception as e:
            log_debug(f"[window] scale check skipped: {e}")
            return False
        if state is None or not out_of_step(*state):
            return False
        now = time.monotonic()
        if now - self._last_repair < _REPAIR_GAP:
            return False
        self._last_repair = now
        qt_scale, _, client, windows_scale, _ = state
        log(f"[window] Qt was drawing the window at {qt_scale:.2f}x while Windows has it at "
            f"{windows_scale:.2f}x ({client[0]}x{client[1]} pixels). Asking Qt to read the screens again.",
            level="warn")
        try:
            self.repair()
        except Exception as e:
            log(f"[window] Could not redraw the window at the right scale: {e}", level="warn")
        return True

    def repair(self) -> None:
        native = self._native_calls()
        hwnd = self._hwnd()
        self._move_to_its_screen(native, hwnd)
        native.reread_screens(hwnd)
        self._window.update()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(CHECK_DELAY_MS, self._after_repair)

    def _move_to_its_screen(self, native, hwnd) -> None:
        """With more than one screen, Qt may have the window on the wrong
        one. Each screen keeps its physical top-left corner in Qt's
        coordinates, so the window's monitor is found by that."""
        from PyQt6.QtGui import QGuiApplication

        screens = QGuiApplication.screens()
        if len(screens) < 2:
            return
        origin = native.monitor_origin(hwnd)
        handle = self._window.windowHandle()
        if origin is None or handle is None:
            return
        for screen in screens:
            corner = screen.geometry().topLeft()
            if (corner.x(), corner.y()) != origin:
                continue
            current = handle.screen()
            if current is None or current.name() != screen.name() or current.geometry() != screen.geometry():
                was = current.name() if current is not None else "no screen"
                log(f"[window] Qt had the window on {was!r}; it is on {screen.name()!r}.")
                handle.setScreen(screen)
            return

    def _after_repair(self) -> None:
        try:
            state = self.read()
        except Exception as e:
            log_debug(f"[window] scale recheck skipped: {e}")
            return
        if state is None or not out_of_step(*state):
            log("[window] The window is drawn at the right scale again.")
            return
        qt_scale, _, client, windows_scale, _ = state
        if self._window.isMaximized() or self._window.isFullScreen():
            log(f"[window] Still drawn at {qt_scale:.2f}x against {windows_scale:.2f}x. Moving or "
                "resizing the window should redraw it.", level="warn")
            return
        # Last resort: make Qt's window as big as the real one at the scale
        # Qt is using, so the drawing covers it. Smaller text until Windows
        # next reports a DPI change, but drawn once.
        width = max(1, round(client[0] / qt_scale))
        height = max(1, round(client[1] / qt_scale))
        log(f"[window] Still drawn at {qt_scale:.2f}x against {windows_scale:.2f}x; resizing the "
            f"window to {width}x{height} so the drawing covers it.", level="warn")
        self._window.resize(width, height)
