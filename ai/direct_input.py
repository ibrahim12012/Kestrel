# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/direct_input.py
==================
Clicks, keys and typing that reach an app without the user's mouse.

Every click the AI made used to be a real one: pyautogui moved the
pointer to the spot and pressed the button. For as long as a run went
on, the mouse wasn't the user's, and moving it at the wrong moment sent
the next click somewhere else.

A click at a point now goes, in this order:

  1. Through UI Automation, to the control under the point. A button is
     invoked, a checkbox toggled, a tab, radio button or list item
     selected, a menu or a combo box opened or closed, an editable text
     field given the focus. Win32 controls, WPF, UWP and WinUI, Chromium
     (Chrome, Edge, and Electron apps such as VS Code or Discord) and
     Firefox all answer this. The pointer doesn't move. Each call runs on
     a thread of its own with a time limit: a WinForms button whose
     Invoke opens a dialog doesn't return until the dialog is closed,
     and that must not hold the run.
  2. As a window message: WM_LBUTTONDOWN/UP posted to the classic
     Windows control at that point, in its own client coordinates. Not
     for frameworks that ignore posted mouse messages (Chromium, UWP,
     WinUI and XAML islands, WPF, Qt, Java, games...), where the click
     would silently not happen.
  3. With the real mouse, borrowed: moved there, clicked, and put back
     where the user left it, at once, never while the user holds a mouse
     button or Ctrl, Shift or Alt, and only once they pause typing or
     moving the mouse. Only when 1 and 2 can't do it, and only while
     Settings > Mouse allows it.

Once an action may have happened, nothing else is tried: a press that
reports an error after the app acted on it isn't pressed a second time
by the next route.

Typing and keys go to the window the AI is working in, its "target":
the window it last clicked (or the window that click brought up), the
app it opened, a window its own keys brought up (Win+R's Run box, a new
window from Ctrl+N), or a window that came up in that app since (a
dialog, Ctrl+F's Find box). That window counts as in front when it is
the foreground window, or when a modal dialog it owns is (a message
box, Save As). When it is in front, the keyboard types into it, as
before. When it isn't:

  - text for a standard Windows text box (Edit, RichEdit, Scintilla)
    that has the focus in that window is inserted with EM_REPLACESEL,
    which doesn't need the window in front;
  - Enter, Delete, Escape and the other single keys are posted as key
    messages to the control with the focus, in a classic window;
  - anything else (a browser's text field, text with line breaks or
    tabs, key combinations, which need Ctrl and Alt to be really down)
    brings the window to the front first and uses the keyboard. That
    moves the focus, never the pointer. Not while the user is typing or
    using the mouse (it waits for a pause), and not for a window on
    another virtual desktop (that would switch the user's desktop). If
    the window can't be brought to the front, nothing is typed: the
    keystrokes would land in whatever the user is using.

With the keyboard, every key first checks that the window it started
typing into is still the one in front: if the user clicks into another
one partway through, or a dialog pops up, typing stops there and the
caller is told, so no Enter follows half a command. (The Start menu
turning into Search as soon as something is typed is followed.) With
no target, keys go to the window in front when typing starts, which
becomes the target, and never to Kestrel's own window or the taskbar.

The fail-safe still works: the pointer in a corner of the main screen
stops automation before the next action, the same corners pyautogui
watched.

Coordinates are screen pixels as this process sees them. Kestrel is
per-monitor DPI aware (Qt sets that up, see main.py), so they are
physical pixels, the same as UI Automation's and the screenshots'. A
window that isn't DPI aware works in scaled coordinates, and its
messages are converted for it.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from dataclasses import dataclass

from ai.log import log as print
from ai.log import log_debug

_IS_WINDOWS = sys.platform == "win32"


@dataclass
class Outcome:
    """What happened to one click, key or piece of text.

    done: it reached the app, as far as can be told.
    how: the route ("invoke", "toggle", "select", "expand", "collapse",
         "default action", "focus", "context menu", "disabled",
         "set value", "window message", "text message", "mouse"), or,
         when not done: "keyboard" (use the keyboard now: it reaches
         `window`) or "blocked" (don't: it wouldn't reach the target).
    stopped: the user's stop or the fail-safe; do nothing more.
    window: for typing and keys, the window they are for (the target).
    front: with how="keyboard", the exact window in front that the
         keyboard types into (the target, or a modal dialog of its own).
         It has to stay the foreground window until the last key: see
         keyboard_reaches.
    """
    done: bool
    how: str = ""
    control: str = ""
    note: str = ""
    stopped: bool = False
    window: "int | None" = None
    front: "int | None" = None


# ── Settings ────────────────────────────────────────────────────────────

def mouse_fallback_allowed() -> bool:
    """Settings > Mouse: whether a click that can't be made any other way
    may borrow the real mouse."""
    try:
        import ai.main as ai_main
        return bool(ai_main.get_mouse_fallback())
    except Exception:
        value = os.getenv("KESTREL_MOUSE_FALLBACK", "1")
        return value.strip().lower() in ("1", "true", "yes", "on")


# ── The window the AI is working in ────────────────────────────────────

_target_lock = threading.Lock()
_target_hwnd = None
_target_set_at = 0.0
# The window in front when the target was set, and the target's owner
# (the main window, for a dialog).
_front_when_set = None
_target_owner = None
# A target nobody clicked or typed into for this long is forgotten:
# whatever the AI types next goes to the window in front, as it used to.
_TARGET_LIFETIME = 300.0
_target_lifetime = _TARGET_LIFETIME
# A target the AI didn't pick, only typed into because it was in front,
# lasts while one run of steps uses it, not into a later chat turn.
_ANCHOR_LIFETIME = 15.0
# The AI's last key that may bring up a window (see follow_keys):
# (KeysBefore, when, system). Looked at by _target, for this long.
_keys_pending = None
_KEYS_PENDING_FOR = 15.0
# When it can't be told where keys should go (the target closed with
# nothing to take its place, or a window came up while the user was
# active): (when, why). Keys then go nowhere for a while, rather than
# into whatever is in front, until the AI clicks somewhere.
_vanished_at = None
_VANISHED_FOR = 15.0
# How long the next key waits for a window the AI's last key may be
# opening, while the user does nothing: while the window the key went
# into is still there, and once it has closed (an app starting from
# the Run box or Search).
_WAIT_FOR_SLOW_WINDOW = 2.0
_WAIT_FOR_STARTING_APP = 8.0


def new_turn() -> None:
    """A new request from the user: what was undecided about the last one
    (a window that closed, a key's window still coming) no longer holds
    keys back. The target the AI clicked stays."""
    global _keys_pending, _vanished_at
    with _target_lock:
        _keys_pending = _vanished_at = None


def set_target(hwnd, lifetime: float = _TARGET_LIFETIME) -> None:
    """Where the AI's typing and keys go: the window it last clicked or
    brought up."""
    global _target_hwnd, _target_set_at, _front_when_set, _target_owner, _pending_focus
    global _target_lifetime, _keys_pending, _vanished_at
    front = owner = None
    if hwnd and _IS_WINDOWS:
        try:
            win = _win()
            front, owner = win.foreground(), win.owner_of(hwnd)
        except Exception as e:
            log_debug(f"[direct] target set without its surroundings: {e}")
    with _target_lock:
        _target_hwnd = int(hwnd) if hwnd else None
        _front_when_set, _target_owner = front, owner
        _target_lifetime = lifetime
        _keys_pending = _vanished_at = None
        if _target_hwnd:
            _target_set_at = time.monotonic()
        else:
            _pending_focus = None


def forget_target() -> None:
    """At the start of a run, a macro or an app launch, so nothing is
    typed into a window from earlier."""
    set_target(None)


def _target():
    """The target, if it is still there, or what took its place:

      - a window the AI's own key brought up since (see follow_keys);
      - a window that came up in the same app since the target was set (a
        dialog a click opened, Ctrl+F's Find box): where a person's next
        keys would go too;
      - the window that owned it, once a dialog the AI was working in has
        closed, rather than whatever the user has in front by then.

    A target on another virtual desktop stays the target: keys can still
    reach it in the background, but it isn't brought to the front (that
    would switch the user's desktop; see _keyboard_for)."""
    global _target_set_at
    with _target_lock:
        hwnd = _target_hwnd
        now = time.monotonic()
        if hwnd and now - _target_set_at > _target_lifetime:
            hwnd = None
        if hwnd:
            _target_set_at = now           # in use: kept for another while
        front_then, owner = _front_when_set, _target_owner
    win = _win()
    came_up, settled = _came_up_after_keys(win, hwnd)
    if came_up or settled:
        return came_up
    if not hwnd:
        return None
    if _gone(win, hwnd):
        for _ in range(8):                  # a message box over a dialog over the main window
            if not owner or owner == hwnd:
                break
            if _can_be_target(win, owner):
                with _target_lock:
                    pending = _keys_pending
                set_target(owner)
                _restore_keys_pending(pending)   # a window the key opens may still come
                return owner
            owner = win.owner_of(owner)
        # Closed by the AI's own key (Enter in the Run box or in Search):
        # the app it starts may still be on its way.
        came_up, settled = _await_what_keys_open(win, hwnd, _WAIT_FOR_STARTING_APP, closing=True)
        if came_up or settled:
            return came_up
        with _target_lock:
            pending = _keys_pending
        set_target(None)
        _restore_keys_pending(pending)
        _keys_held_back("the window it was working in has closed: click where to type first")
        return None
    front = win.foreground()
    if (front and front != hwnd and front != front_then
            and win.root_owner(front) == win.root_owner(hwnd) and _can_be_target(win, front)):
        set_target(front)
        return front
    return hwnd


def _came_up_after_keys(win, hwnd):
    """The window the AI's last key brought up, if it is in front now: one
    that wasn't there before the key (a window minimized then was there,
    so one the user restores isn't taken for it). Decided at the next
    key rather than in the moment after: a new Explorer window or an app
    opening a file can take a second or two.

    Returns (window, settled): the window adopted as the target; or None
    with settled=True when the target was dropped instead (a new window
    came up but the user was active since the key, so it may be theirs;
    or the key was a Windows-key combination and what came up can't be a
    target); or None, False: nothing new in front."""
    with _target_lock:
        pending = _keys_pending
    if pending is None:
        return None, False
    before, when, system = pending
    if time.monotonic() - when > _KEYS_PENDING_FOR:
        _clear_keys_pending()
        return None, False
    front = win.foreground()
    if not front or front == before.front or front == hwnd or front in before.shown:
        return None, False
    if not _no_input_since(win, when):
        forget_target()
        _keys_held_back("a new window came up while the user was using the computer: "
                        "click where to type first")
        return None, True
    if _can_be_target(win, front):
        set_target(front)
        return front, True
    if system:
        # Start, or another piece of the shell that can't be a target:
        # keys go to the window in front, not back to the old one.
        forget_target()
        return None, True
    return None, False


def _await_what_keys_open(win, hwnd, longest: float, closing: bool = False):
    """The next key, soon after the AI's last one: wait, up to `longest`
    seconds after it and only while the user does nothing, for a window
    that key may be opening. Not after Enter in a text field (a search
    box, a document), which rarely opens anything, unless the window it
    went into has closed (`closing`: the Run box, Search). Returns what
    _came_up_after_keys does."""
    while True:
        with _target_lock:
            pending = _keys_pending
        if pending is None:
            return None, False
        before, when, _system = pending
        if before.into_text and not closing:
            return None, False
        came_up, settled = _came_up_after_keys(win, hwnd)
        if came_up or settled:
            return came_up, settled
        if time.monotonic() - when >= longest or not _no_input_since(win, when):
            return None, False
        time.sleep(0.1)


def _restore_keys_pending(pending) -> None:
    global _keys_pending
    if pending is not None:
        with _target_lock:
            _keys_pending = pending


def _keys_held_back(why: str) -> None:
    global _vanished_at
    with _target_lock:
        _vanished_at = (time.monotonic(), why)


def _exists(win, hwnd) -> bool:
    """Not closed, not hidden."""
    return bool(hwnd) and win.is_window(hwnd) and win.is_visible(hwnd)


def _gone(win, hwnd) -> bool:
    """Closed, hidden, or put away: cloaked (Windows' way of hiding a Store
    app, the Start menu or Search without hiding the window) on the
    desktop the user is on. A window on another virtual desktop is
    cloaked too, and isn't gone."""
    if not _exists(win, hwnd):
        return True
    return win.is_cloaked(hwnd) and not win.on_another_desktop(hwnd)


# Windows that are never the place the AI types into: the taskbar, menus,
# a combo box's list, tooltips, tray flyouts. Prefixes, lower case. (The
# desktop can be: F2, Delete or Enter on an icon the AI clicked.)
_NOT_TARGETS = (
    "shell_traywnd", "shell_secondarytraywnd",
    "#32768", "combolbox", "tooltips_class32", "notifyiconoverflowwindow",
    "topleveltrayflyout", "xaml_windowedpopupclass",
)
# Of those, the shell's own: the taskbar and the tray.
_SHELL = ("shell_traywnd", "shell_secondarytraywnd", "notifyiconoverflowwindow", "topleveltrayflyout")
_DESKTOP = ("progman", "workerw")


def _can_be_target(win, hwnd) -> bool:
    """A window the AI's keys may be for: on screen, not Kestrel's own, not
    a menu, a list, a tooltip or another floating popup."""
    if not _exists(win, hwnd) or win.is_cloaked(hwnd) or win.process_id(hwnd) == os.getpid():
        return False
    name = (win.class_name(hwnd) or "").lower()
    if name.startswith(_NOT_TARGETS):
        return False
    # Floating popups, flyouts and overlays are tool windows; the desktop
    # may be one too.
    return not win.is_tool_window(hwnd) or name.startswith(_DESKTOP)


def _is_shell(win, hwnd) -> bool:
    return bool(hwnd) and (win.class_name(hwnd) or "").lower().startswith(_SHELL)


def _adopt_target(win, root, pids, foreground_before, shown_before=frozenset()) -> None:
    """After a click, where the AI's next keys go:

      - the window the click brought to the front, if it came from the
        app that was clicked (a dialog, a new window). Not a window the
        user switched to while the click was being made;
      - clicked on the taskbar or in Start: the window that came up, but
        not the one behind a window the click minimized, which may be the
        user's;
      - clicked in a menu or a dropdown list: a window that wasn't there
        before the click (shown_before), if one came up;
      - else the window clicked in, or the window that owns it;
      - clicked in a dropdown list, a menu or a tooltip with nothing coming
        up: the target stays what it was, the window the list belongs to;
      - clicked on the taskbar with nothing coming up: no target, so keys
        meant for what the click did don't go to a window from earlier."""
    foreground = win.foreground()
    clicked = root if _can_be_target(win, root) else win.root_owner(root)
    if not _can_be_target(win, clicked):
        clicked = None
    if foreground and foreground != foreground_before and _can_be_target(win, foreground):
        same_app = (win.process_id(foreground) in pids
                    or (clicked and win.root_owner(foreground) == win.root_owner(clicked)))
        if same_app:
            set_target(foreground)
            return
        if not clicked and _is_shell(win, root):
            if not (foreground_before and win.is_minimized(foreground_before)):
                set_target(foreground)
            return
        if not clicked and foreground not in shown_before:
            set_target(foreground)
            return
    if clicked:
        set_target(clicked)
    elif _is_shell(win, root):
        forget_target()


# ── Stopping ────────────────────────────────────────────────────────────

def _stopped() -> "Outcome | None":
    """The user's stop, or the fail-safe: the pointer in a corner of the
    main screen, which sets the same stop. pyautogui used to check that
    corner before every move; nothing moves now, so it is checked here."""
    try:
        import ai.actions as actions
    except Exception:
        return None
    if actions.emergency_stop:
        return Outcome(False, stopped=True, note="automation is stopped")
    if not _IS_WINDOWS:
        return None
    try:
        win = _win()
        position = win.cursor_pos()
        width, height = win.primary_size()
    except Exception as e:
        log_debug(f"[direct] fail-safe check skipped: {e}")
        return None
    if position is None:
        return None                         # can't be read (a locked screen): not a corner
    if position in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        actions.emergency_stop = True
        print("[EMERGENCY STOP] Fail-safe triggered - mouse moved to a screen corner. Halting further actions.")
        return Outcome(False, stopped=True, note="the mouse is in a corner of the screen (the fail-safe)")
    return None


def is_stopped() -> bool:
    """The user's stop, or the fail-safe (see _stopped)."""
    return _stopped() is not None


# ── Clicks ──────────────────────────────────────────────────────────────

def click(x: int, y: int, button: str = "left") -> Outcome:
    """A left or right click at a screen point, without the user's mouse
    whenever there is another way."""
    x, y = int(x), int(y)
    button = "right" if button == "right" else "left"
    stop = _stopped()
    if stop is not None:
        return stop
    if not _IS_WINDOWS:
        return _borrow_mouse(x, y, button, "there is no other way outside Windows")

    win = _win()
    hwnd, root, covered = _window_at(win, x, y)
    if not root:
        return Outcome(False, "mouse", note="there is no window at that point")
    foreground_before = win.foreground()
    _drop_pending_focus()                   # a new click: an item picked earlier isn't where keys go now

    # The app at the point. Usually one process, but a Store app's frame
    # (ApplicationFrameHost) and its content are two. Never Kestrel's own.
    pids = {win.process_id(root), win.process_id(hwnd)} - {os.getpid()}
    # In a menu or a list keys never go to, what the click brings up has to
    # be told from what was already there.
    shown_before = shown_windows() if not _can_be_target(win, root) else frozenset()
    outcome = _attempt(_click_by_ui_automation, x, y, button, pids, root)
    if outcome is None:
        outcome = _by_window_message(x, y, button, hwnd)
    if outcome is None:
        # A real click lands on whatever is on top: with Kestrel's window
        # over the spot, the app has to come forward first.
        outcome = _borrow_mouse(x, y, button, f"{_app_name(win, hwnd)} only reacts to a real click there",
                                bring_forward=root if covered else None)
    else:
        _log(outcome, x, y)
    if outcome.done:
        _adopt_target(win, root, pids, foreground_before, shown_before)
    return outcome


def _window_at(win, x, y):
    """The app window a click at (x, y) is for: (window, its top-level
    window, covered). What Windows reports there, unless that is one of
    Kestrel's own windows, or a window clicks go through (Kestrel's border
    during a run, another app's overlay): then the window beneath it. The
    AI works on other apps, and its screenshots show them through the
    border; it never means Kestrel itself. (The vision click refuses a
    point where its screenshot showed Kestrel's window instead: see
    kestrel_window_at.) covered: a real click there would land on another
    window, Kestrel's, not on the app."""
    hwnd = win.window_from_point(x, y)
    root = win.root_of(hwnd) if hwnd else None
    if root and not _looked_through(win, root):
        return hwnd, root, False
    below = win.top_window_at(x, y, lambda h: _looked_through(win, h))
    if not below:
        return None, None, False
    # A real click lands on the topmost window that doesn't let clicks
    # through: under the run border, that can still be Kestrel's window.
    covered = _on_top_at(win, x, y) != below
    if root:
        log_debug(f"[direct] ({x}, {y}): looking through {_describe(win, root)} to {_describe(win, below)}")
    return win.child_at(below, x, y), below, covered


def _looked_through(win, hwnd) -> bool:
    """A top-level window a click at its spot isn't for: one of Kestrel's
    own, or one that lets clicks through."""
    return win.process_id(hwnd) == os.getpid() or win.is_click_through(hwnd)


def _describe(win, hwnd) -> str:
    """A window as the log names it: its class and its program."""
    if not hwnd:
        return "no window"
    name = win.process_name(hwnd) or f"process {win.process_id(hwnd)}"
    return f"{win.class_name(hwnd) or 'a window'} ({name})"


def describe_front() -> str:
    """The window in front now, for a log line or a note to the AI."""
    if not _IS_WINDOWS:
        return ""
    try:
        return _describe(_win(), _win().foreground())
    except Exception as e:
        log_debug(f"[direct] window in front unknown: {e}")
        return ""


def kestrel_window_at(x: int, y: int) -> bool:
    """Whether what is on top at a screen point, past windows that let
    clicks through, is one of Kestrel's own windows. A screenshot taken
    just now shows Kestrel there, not the app beneath."""
    if not _IS_WINDOWS:
        return False
    try:
        win = _win()
        top = _on_top_at(win, int(x), int(y))
        return bool(top) and win.process_id(top) == os.getpid()
    except Exception as e:
        log_debug(f"[direct] window at ({x}, {y}) unknown: {e}")
        return False


def program_of(hwnd) -> str:
    """The program file a window belongs to (notepad.exe), or ""."""
    if not (_IS_WINDOWS and hwnd):
        return ""
    try:
        return _win().process_name(hwnd)
    except Exception as e:
        log_debug(f"[direct] program of a window unknown: {e}")
        return ""


def can_be_mapped(hwnd) -> bool:
    """An app window a mapping run may take: not Kestrel's own, not the
    taskbar, the tray or the desktop, not a tool window (a floating
    palette, a tray flyout), not a menu or a tooltip, and not one on
    another virtual desktop (bringing it forward would switch desktops)."""
    if not (_IS_WINDOWS and hwnd):
        return False
    try:
        win = _win()
        name = (win.class_name(hwnd) or "").lower()
        return not (win.process_id(hwnd) == os.getpid() or name.startswith(_NOT_TARGETS)
                    or name.startswith(_DESKTOP) or win.is_tool_window(hwnd) or win.is_cloaked(hwnd))
    except Exception as e:
        log_debug(f"[direct] a window couldn't be checked: {e}")
        return False


def is_kestrel_window(hwnd) -> bool:
    """One of Kestrel's own windows."""
    if not (_IS_WINDOWS and hwnd):
        return False
    try:
        return _win().process_id(hwnd) == os.getpid()
    except Exception as e:
        log_debug(f"[direct] owner of a window unknown: {e}")
        return False


def foreground_window():
    """The window in front now, or None."""
    if not _IS_WINDOWS:
        return None
    try:
        return _win().foreground()
    except Exception as e:
        log_debug(f"[direct] window in front unknown: {e}")
        return None


def bring_forward(hwnd) -> bool:
    """Bring a window to the front, which Windows only lets the process in
    front do unless it shares input with it (see _Win32.bring_to_front)."""
    if not (_IS_WINDOWS and hwnd):
        return False
    try:
        return bool(_win().bring_to_front(hwnd))
    except Exception as e:
        log_debug(f"[direct] could not bring a window to the front: {e}")
        return False


def window_state(hwnd) -> str:
    """"minimized", "maximized" or "normal", or "" when it can't be told."""
    if not (_IS_WINDOWS and hwnd):
        return ""
    try:
        win = _win()
        if win.is_minimized(hwnd):
            return "minimized"
        return "maximized" if win.is_maximized(hwnd) else "normal"
    except Exception as e:
        log_debug(f"[direct] state of a window unknown: {e}")
        return ""


def remember_window(hwnd) -> dict:
    """How a window is shown now, for put_back(): its state
    (window_state) and, where it can be read, its exact placement."""
    remembered = {"state": window_state(hwnd), "placement": None}
    if _IS_WINDOWS and hwnd:
        try:
            remembered["placement"] = _win().placement(hwnd)
        except Exception as e:
            log_debug(f"[direct] placement of a window unknown: {e}")
    return remembered


def put_back(hwnd, before) -> None:
    """Return a window to how remember_window() (or window_state) found it
    before something maximized it: minimized again, or back to its normal
    size and place. Nothing when it was maximized already, is gone, or has
    stopped answering (the run's lock would be held while it waited)."""
    state = before.get("state") if isinstance(before, dict) else before
    placement = before.get("placement") if isinstance(before, dict) else None
    if not (_IS_WINDOWS and hwnd) or state not in ("minimized", "normal"):
        return
    try:
        win = _win()
        if not win.is_window(hwnd) or win.is_hung(hwnd):
            return
        if placement is not None:
            # Its own normal size and position, and what it restores to
            # (maximized or not) once it is minimized. Not brought to the
            # front, and posted to the app rather than waited on: a busy
            # app can't hold the run (and its lock) here.
            placement.showCmd = SW_SHOWMINNOACTIVE if state == "minimized" else SW_SHOWNOACTIVATE
            placement.flags |= WPF_ASYNCWINDOWPLACEMENT
            if win.set_placement(hwnd, placement):
                return
        if state == "minimized" and not win.is_minimized(hwnd):
            win.show_async(hwnd, SW_SHOWMINNOACTIVE)
        elif state == "normal" and win.is_maximized(hwnd):
            win.show_async(hwnd, SW_RESTORE)
    except Exception as e:
        log_debug(f"[direct] could not put a window back: {e}")


def unminimize(hwnd, settle: float = 0.2, longest: float = 3.0) -> bool:
    """Show a minimized window again, without bringing it to the front:
    its controls can't be reached while it is minimized. Waits until it
    is shown (up to `longest` seconds), then `settle` more for its
    controls to be laid out. True if it was minimized."""
    if not (_IS_WINDOWS and hwnd):
        return False
    try:
        win = _win()
        if not win.is_minimized(hwnd) or win.is_hung(hwnd):
            return False
        win.show_async(hwnd, SW_SHOWNOACTIVATE)
        deadline = time.monotonic() + longest
        while win.is_minimized(hwnd) and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(settle)
        return True
    except Exception as e:
        log_debug(f"[direct] could not show a minimized window: {e}")
        return False


def press_control(control, window=None) -> Outcome:
    """A left click on a control UI Automation already found (the mapper,
    a compiled action), in `window`, its top-level window handle."""
    stop = _stopped()
    if stop is not None:
        return stop
    _drop_pending_focus()
    if window:
        set_target(window)
    outcome = _attempt(_press, control, None, window)
    if outcome is not None:
        _log(outcome, 0, 0)
        return outcome
    rect = control.BoundingRectangle
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return Outcome(False, "mouse", _label(control), note="it has no place on the screen to click")
    x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    if not _IS_WINDOWS:
        return _borrow_mouse(x, y, "left", "there is no other way outside Windows")
    win = _win()
    # The control's own window: the one at its centre, if it belongs to
    # the same app (it may be a popup or a dropdown list), else the
    # child of `window` there, even if another window covers it.
    at_point, at_root, _covered = _window_at(win, x, y)
    if at_point and win.process_id(at_point) == control.ProcessId:
        hwnd, root = at_point, at_root
    else:
        root = window or win.root_of(_native_handle(control))
        hwnd = win.child_at(root, x, y) if root else None
    outcome = _by_window_message(x, y, "left", hwnd)
    if outcome is not None:
        _log(outcome, x, y)
        return outcome
    return _borrow_mouse(x, y, "left", f"{_label(control)} only reacts to a real click",
                         bring_forward=root)


def set_value(control, value: str, window=None) -> Outcome:
    """`value` put straight into a field UI Automation already found (the
    mapper), through its value: no focus, no keys, no pointer. Not done
    when the field has no value that can be set: click into it and type
    instead. With the same time limit as a press: a field whose change
    handler opens a dialog doesn't return until it closes."""
    stop = _stopped()
    if stop is not None:
        return stop
    if window:
        set_target(window)
    outcome = _attempt(_set_value, control, value)
    if outcome is None:
        return Outcome(False, "keyboard", note="it has no value that can be set directly")
    _log(outcome, 0, 0)
    return outcome


def _set_value(gate, control, value: str) -> "Outcome | None":
    label = _label(control)
    try:
        pattern = control.GetPattern(_VALUE)
        if pattern is None or pattern.IsReadOnly:
            return None
    except Exception as e:
        log_debug(f"[direct] no value to set on {label}: {e}")
        return None
    try:
        with gate.act("set value", label):
            accepted = pattern.SetValue(value, waitTime=0)
    except _GivenUp:
        raise
    except Exception as e:
        if _did_not_act(e):
            gate.undo()
            log_debug(f"[direct] {label} refused its value: {e}")
            return None
        return Outcome(True, "set value", label, note=f"the app answered with an error ({e})")
    if not accepted:
        gate.undo()
        return None
    return Outcome(True, "set value", label)


def _log(outcome: Outcome, x: int, y: int) -> None:
    what = f" {outcome.control}" if outcome.control else ""
    where = f" at ({x}, {y})" if (x, y) != (0, 0) else ""
    log_debug(f"[direct] {outcome.how}{what}{where}, without the mouse")


# ── UI Automation, on a thread of its own ───────────────────────────────

class _GivenUp(Exception):
    """The caller stopped waiting before this attempt acted."""


class _Gate:
    """Lets an attempt running on the UI Automation thread act only while
    its caller still waits for it, and tells the caller, when the wait
    runs out, whether it had started to act."""

    def __init__(self):
        self._lock = threading.Lock()
        self.started = False
        self.given_up = False
        self.step = ""
        self.label = ""

    def act(self, step: str, label: str):
        gate = self

        class _Acting:
            def __enter__(self):
                with gate._lock:
                    if gate.given_up:
                        raise _GivenUp()
                    gate.started = True
                    gate.step, gate.label = step, label

            def __exit__(self, *exc):
                return False

        return _Acting()

    def check(self) -> None:
        """Raise _GivenUp if the caller stopped waiting: for a step that
        isn't the action itself but mustn't happen after the run moved on."""
        with self._lock:
            if self.given_up:
                raise _GivenUp()

    def undo(self) -> None:
        """The action just tried didn't happen (the app refused it before
        doing anything): if the attempt runs out of time later, it hasn't
        started to act."""
        with self._lock:
            if not self.given_up:
                self.started = False

    def give_up(self) -> bool:
        """Close the gate: nothing more is done by this attempt, even if the
        app call it is stuck in returns later. True if an action had
        started: it did, or is doing, what it was asked, and must not be
        repeated another way."""
        with self._lock:
            self.given_up = True
            return self.started


class _UiaWorker:
    """One thread with COM set up, running UI Automation attempts one at a
    time. A worker stuck inside an app's call is left to finish and
    exit, and the next attempt gets a new one."""

    def __init__(self):
        self.jobs: queue.Queue = queue.Queue()
        self.busy = False
        self.abandoned = False
        threading.Thread(target=self._loop, name="kestrel-ui-automation", daemon=True).start()

    def _loop(self) -> None:
        global _stuck
        # Microsoft's advice for a UI Automation client thread that owns
        # no windows: the multithreaded apartment.
        ui_automation(multithreaded=True)
        while True:
            job = self.jobs.get()
            if job is None:
                break
            job()
        with _worker_lock:
            if self.abandoned:
                _stuck -= 1


_worker = None
_worker_lock = threading.Lock()
# How long one UI Automation attempt may take, reading included.
_UIA_WAIT = 3.0
# Workers left inside an app's call that hasn't returned (a hung app).
# Past this many, UI Automation isn't tried until one comes back: each
# would be one more thread blocked in that app.
_stuck = 0
_MAX_STUCK = 4


def _attempt(fn, *args) -> "Outcome | None":
    """fn(gate, *args) on the UI Automation thread, for at most _UIA_WAIT
    seconds. None: UI Automation can't do it, try another way. If the
    time runs out after fn started to act, that counts as done: the app
    is busy with what it was asked, maybe showing a dialog."""
    if not _IS_WINDOWS:
        return None
    global _worker, _stuck
    gate = _Gate()
    finished = threading.Event()
    box = {}

    with _worker_lock:
        if _worker is None or _worker.busy:
            if _worker is not None:
                if _stuck >= _MAX_STUCK:
                    log_debug("[direct] UI Automation skipped: earlier calls are still stuck "
                              "in an app that isn't answering")
                    return None
                _stuck += 1
                _worker.abandoned = True
                _worker.jobs.put(None)      # let the stuck one exit once it returns
            _worker = _UiaWorker()
        worker = _worker

        def job():
            try:
                box["outcome"] = fn(gate, *args)
            except _GivenUp:
                pass
            except Exception as e:
                box["error"] = e
            finally:
                worker.busy = False         # before the caller hears, so the next attempt can reuse it
                finished.set()

        worker.busy = True
        worker.jobs.put(job)

    if not finished.wait(_UIA_WAIT):
        started = gate.give_up()
        if not finished.is_set():
            if started:
                return Outcome(True, gate.step, gate.label,
                               note="the app was still busy with it after a few seconds; it may be showing a dialog")
            log_debug("[direct] UI Automation didn't answer in time")
            return None
        # It finished as the wait ran out: what it did is known after all.
    if "error" in box:
        if gate.started:
            # After it acted: done, as far as can be told. Another route
            # would do it a second time.
            return Outcome(True, gate.step, gate.label,
                           note=f"the app answered with an error afterwards ({box['error']})")
        log_debug(f"[direct] UI Automation attempt failed: {box['error']}")
        return None
    return box.get("outcome")


# HRESULTs that mean the call never reached the control or was refused
# before it did anything: E_NOTIMPL, E_INVALIDARG, UIA_E_ELEMENTNOTENABLED,
# UIA_E_NOTSUPPORTED, UIA_E_INVALIDOPERATION, RPC_E_WRONG_THREAD,
# CO_E_NOTINITIALIZED, and the two for an object whose connection is
# gone. Anything else may have come after the app acted.
_DID_NOT_ACT = frozenset({
    0x80004001, 0x80070057, 0x80040200, 0x80040204, 0x80131509, 0x8001010E, 0x800401F0,
    0x80010108, 0x800401FD,     # RPC_E_DISCONNECTED, CO_E_OBJNOTCONNECTED
})


def _did_not_act(error: Exception) -> bool:
    if isinstance(error, (TypeError, AttributeError, NameError)):
        return True                         # the call itself was wrong: nothing reached the app
    hresult = getattr(error, "hresult", None)
    if hresult is None and error.args and isinstance(error.args[0], int):
        hresult = error.args[0]
    return hresult is not None and (hresult & 0xFFFFFFFF) in _DID_NOT_ACT


# What a left click on each kind of control does, as UI Automation
# actions tried in order. "default" is the control's MSAA default
# action, for the ones that offer nothing else.
_PRESS = {
    "Button": ("invoke", "toggle", "expand", "default"),
    "SplitButton": ("invoke", "expand", "default"),
    "Hyperlink": ("invoke", "default"),
    "MenuItem": ("expand", "invoke", "default"),
    "CheckBox": ("toggle", "default"),
    "RadioButton": ("select", "default"),
    "TabItem": ("select", "default"),
    # A list item's default action is usually a double click: it would
    # open the file a single click only selects.
    "ListItem": ("select",),
    "TreeItem": ("select",),
    "DataItem": ("select",),
    "ComboBox": ("expand",),
    "HeaderItem": ("invoke",),
}
# A click in an editable text field puts the focus, and the caret, there.
_TEXT_FIELDS = frozenset({"Edit", "Document"})
# The point often lands on a button's label or icon, or a list item's
# text. These are passed through to the control they are part of.
_PARTS = frozenset({"Text", "Image", "Custom", "Group"})
# Items whose selection is where the keyboard goes next, as after a click.
_ITEMS = frozenset({"ListItem", "TreeItem", "DataItem"})

# UI Automation pattern ids (UIAutomationClient.h).
_INVOKE, _VALUE, _EXPAND_COLLAPSE, _SELECTION_ITEM = 10000, 10002, 10005, 10010
_TEXT, _TOGGLE, _LEGACY = 10014, 10015, 10018
_EXPANDED, _PARTIALLY_EXPANDED, _LEAF_NODE = 1, 2, 3


def _click_by_ui_automation(gate, x: int, y: int, button: str, pids: set, root) -> "Outcome | None":
    control = _control_at(x, y)
    if control is None or control.ProcessId not in pids:
        # Kestrel's window or another overlay sits over the app there (UI
        # Automation answers for what is on top): the app's own window is
        # asked instead.
        control = _control_in_window_at(root, x, y)
    if control is None or control.ProcessId not in pids:
        return None              # nothing, or not the app the point is in (a stale tree)
    if button == "right":
        return _open_context_menu(gate, control)
    return _press(gate, control, (x, y), root)


def _press(gate, control, point, root=None) -> "Outcome | None":
    """What a left click on `control` does, done through UI Automation, or
    None when it has no such action. `point`: where the click is, or None
    for a control found by the mapper. `root`: its top-level window, when
    known."""
    if _kind(control) in _TEXT_FIELDS:
        return _focus_text_field(gate, control, point)
    current = control
    for _ in range(4):                       # the control, and up to three it is part of
        kind = _kind(current)
        if kind in _PRESS:
            if not _enabled(current):
                return Outcome(True, "disabled", _label(current),
                               note="it is disabled, so clicking it does nothing")
            if kind == "ListItem" and _in_classic_list_box(current):
                # The accessibility proxy selects a classic list box's item
                # without telling the app, and leaves a dropdown open. A
                # posted click does both, like a real one.
                return None
            return _run(gate, current, _PRESS[kind], root)
        if kind not in _PARTS:
            return None
        parent = current.GetParentControl()
        if parent is None or parent.ProcessId != current.ProcessId:
            return None
        current = parent
    return None


def _run(gate, control, steps, root=None) -> "Outcome | None":
    label = _label(control)
    # Read before acting: after a Select, a list that rebuilds itself can
    # make the item unreadable.
    is_item = _kind(control) in _ITEMS
    for step in steps:
        # Reading first: an error here means nothing was done yet.
        try:
            if step == "invoke":
                pattern, call = control.GetPattern(_INVOKE), "Invoke"
            elif step == "toggle":
                pattern, call = control.GetPattern(_TOGGLE), "Toggle"
            elif step == "select":
                pattern, call = control.GetPattern(_SELECTION_ITEM), "Select"
            elif step == "expand":
                pattern = control.GetPattern(_EXPAND_COLLAPSE)
                if pattern is None:
                    continue
                state = pattern.ExpandCollapseState
                if state == _LEAF_NODE:
                    continue
                if state in (_EXPANDED, _PARTIALLY_EXPANDED):
                    step, call = "collapse", "Collapse"
                else:
                    call = "Expand"
            else:                            # "default"
                pattern, call = control.GetPattern(_LEGACY), "DoDefaultAction"
                if pattern is not None and not (pattern.DefaultAction or "").strip():
                    continue
            if pattern is None:
                continue
        except Exception as e:
            log_debug(f"[direct] {step} not available on {label}: {e}")
            continue
        # Acting.
        try:
            with gate.act(step, label):
                acted = getattr(pattern, call)(waitTime=0)
        except _GivenUp:
            raise
        except Exception as e:
            if _did_not_act(e):
                gate.undo()
                log_debug(f"[direct] {step} refused by {label}: {e}")
                continue
            # It may have acted before failing; doing it again, another way,
            # could press it twice.
            return Outcome(True, step, label, note=f"the app answered with an error ({e}), so it may not have worked")
        if not acted:
            gate.undo()
            continue
        if step == "select" and is_item:
            # Where a click leaves the keyboard: on the item, so a Delete or
            # an Enter that follows acts on it. Now, in a window already in
            # front. In one behind, SetFocus would pull it forward under the
            # user's typing (and their next Delete would hit the item), so
            # it waits until a key for that window brings it forward.
            if _window_in_front(control, root):
                try:
                    with gate.act(step, label):
                        control.SetFocus()
                except _GivenUp:
                    raise
                except Exception as e:
                    log_debug(f"[direct] focus not moved to {label}: {e}")
            elif root:
                with gate.act(step, label):
                    _keep_pending_focus(root, control)
        return Outcome(True, step, label)
    return None


def _window_in_front(control, root) -> bool:
    """`root`, the control's top-level window, is in front; without it,
    any window of the control's app."""
    try:
        win = _win()
        if root:
            return _in_front(win, root)
        foreground = win.foreground()
        return bool(foreground) and win.process_id(foreground) == control.ProcessId
    except Exception:
        return False


# An item selected in a window behind others, which gets the keyboard
# focus once a key for that window brings it to the front: (window,
# control).
_pending_focus = None


def _keep_pending_focus(window, control) -> None:
    global _pending_focus
    with _target_lock:
        _pending_focus = (window, control)


def _drop_pending_focus() -> None:
    global _pending_focus
    with _target_lock:
        _pending_focus = None


def _pending_for(window) -> bool:
    with _target_lock:
        return _pending_focus is not None and _pending_focus[0] == window


def _focus_pending_item(window) -> None:
    """The focus on the item a click selected in `window`, now that the
    window is in front for a key (see _run)."""
    global _pending_focus
    with _target_lock:
        pending, _pending_focus = _pending_focus, None
    if pending is not None and pending[0] == window:
        _attempt(_set_focus, pending[1])


def _set_focus(gate, control) -> Outcome:
    label = _label(control)
    with gate.act("focus", label):
        control.SetFocus()
    return Outcome(True, "focus", label)


def _focus_text_field(gate, control, point) -> "Outcome | None":
    label = _label(control)
    if not _editable(control):
        return None                          # a page, a read-only field: a real click, not focus
    if not _enabled(control):
        return Outcome(True, "disabled", label, note="it is disabled, so clicking it does nothing")
    # A standard Windows text box takes the click as a message, which
    # puts the caret at the point and leaves its window where it is.
    hwnd = _native_handle(control)
    if hwnd and _is_text_box(_win(), hwnd):
        return None
    try:
        with gate.act("focus", label):
            focused = control.SetFocus()
    except _GivenUp:
        raise
    except Exception as e:
        if _did_not_act(e):
            gate.undo()
            return None
        return Outcome(True, "focus", label, note=f"the app answered with an error ({e})")
    if not focused:
        gate.undo()
        return None
    # The caret where the click was, when the field can say where that is.
    if point is not None:
        try:
            text = control.GetPattern(_TEXT)
            if text is not None:
                caret = text.RangeFromPoint(*point)
                with gate.act("focus", label):
                    caret.Select(waitTime=0)
        except _GivenUp:
            raise
        except Exception as e:
            log_debug(f"[direct] caret not placed in {label}: {e}")
    return Outcome(True, "focus", label)


def _open_context_menu(gate, control) -> "Outcome | None":
    """A right click through UI Automation (ShowContextMenu, Windows 8.1
    and later), on the control under the point or the one it is part of.
    Once the app has accepted the request, nothing else is tried, even if
    no menu could be seen: another route would open a second one."""
    chain = [control]
    while _kind(chain[-1]) in _PARTS and len(chain) < 4:
        parent = chain[-1].GetParentControl()
        if parent is None or parent.ProcessId != chain[-1].ProcessId:
            break
        chain.append(parent)
    # Everything read first: once an item is selected, a list that
    # rebuilds itself can leave all of these unreadable.
    pid = control.ProcessId
    menus = []
    for current in chain:
        element = _element3(current)
        if element is not None:
            menus.append((element, _label(current)))
    if not menus:
        return None
    item = next((current for current in chain if _kind(current) in _ITEMS), None)
    select = None
    if item is not None:
        try:
            select = item.GetPattern(_SELECTION_ITEM)
            if select is not None and select.IsSelected:
                select = None
        except Exception as e:
            log_debug(f"[direct] {_label(item)} can't be selected before its menu: {e}")
            select = None
    if select is not None:
        # A right click on an item selects it first: its menu is for what
        # is selected. Not the action itself: if nothing opens a menu here,
        # the next route's right click selects it again, as a real one would.
        try:
            gate.check()
            select.Select(waitTime=0)
        except _GivenUp:
            raise
        except Exception as e:
            log_debug(f"[direct] item not selected before its menu: {e}")
    win = _win()
    for element, label in menus:
        before = win.visible_windows(pid)
        try:
            with gate.act("context menu", label):
                element.ShowContextMenu()
        except _GivenUp:
            raise
        except Exception as e:
            if _did_not_act(e):
                gate.undo()
                log_debug(f"[direct] no context menu from {label}: {e}")
                continue
            return Outcome(True, "context menu", label, note=f"the app answered with an error ({e})")
        if _menu_opened(win, pid, before):
            return Outcome(True, "context menu", label)
        return Outcome(True, "context menu", label, note="no menu could be seen opening")
    return None


def _menu_opened(win, pid: int, before: set, timeout: float = 1.5) -> bool:
    """A menu opened in process `pid`: a new menu window of its own
    (Win32, Chromium and recent Explorer menus), or the focus inside a
    menu (a Store app's flyout). A tooltip is a new window too, and
    doesn't count."""
    deadline = time.monotonic() + timeout
    while True:
        for hwnd in win.visible_windows(pid) - before:
            if _is_menu_window(win, hwnd):
                return True
        try:
            focused = _focused_control()
            if (focused is not None and _kind(focused) in ("Menu", "MenuItem")
                    and focused.ProcessId == pid):
                return True
        except Exception as e:
            log_debug(f"[direct] focus unreadable while a menu opens: {e}")
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _is_menu_window(win, hwnd) -> bool:
    if (win.class_name(hwnd) or "") == "#32768":
        return True                          # a Win32 popup menu
    auto = ui_automation()
    if auto is None:
        return False
    try:
        window = auto.ControlFromHandle(hwnd)
        if window is None:
            return False
        if _kind(window) in ("Menu", "MenuItem"):
            return True
        for control, _depth in auto.WalkControl(window, includeTop=False, maxDepth=3):
            if _kind(control) in ("Menu", "MenuItem"):
                return True
    except Exception as e:
        log_debug(f"[direct] couldn't tell whether a new window is a menu: {e}")
    return False


def _in_classic_list_box(item) -> bool:
    try:
        parent = item.GetParentControl()
        hwnd = _native_handle(parent) if parent is not None else None
        return bool(hwnd) and (_win().class_name(hwnd) or "").lower() in ("listbox", "combolbox")
    except Exception:
        return False


def _editable(control) -> bool:
    """A text field that takes typing: one with a value that isn't read
    only. A web page or a PDF is a Document too, and a click on one is a
    click, not a request for the caret."""
    try:
        value = control.GetPattern(_VALUE)
        return value is not None and not value.IsReadOnly
    except Exception:
        return False


# Window messages.
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
MK_LBUTTON, MK_RBUTTON = 0x0001, 0x0002


def _by_window_message(x: int, y: int, button: str, hwnd) -> "Outcome | None":
    """The click posted to a classic Windows control, the way the system
    would deliver a real one, without moving the pointer."""
    if not hwnd or _stopped() is not None:
        return None
    win = _win()
    if not _is_classic(win, hwnd):
        return None
    point = win.client_point(hwnd, x, y)
    if point is None:
        return None                         # the frame or the title bar, not the client area
    lparam = (point[1] & 0xFFFF) << 16 | (point[0] & 0xFFFF)
    pid = win.process_id(win.root_of(hwnd) or hwnd)
    if button == "right":
        before = win.visible_windows(pid)
        sequence = ((WM_MOUSEMOVE, 0), (WM_RBUTTONDOWN, MK_RBUTTON), (WM_RBUTTONUP, 0))
    else:
        sequence = ((WM_MOUSEMOVE, 0), (WM_LBUTTONDOWN, MK_LBUTTON), (WM_LBUTTONUP, 0))
    for message, wparam in sequence:
        if not win.post(hwnd, message, wparam, lparam):
            return None
    time.sleep(0.1)                         # handled before whatever comes next
    if button == "right" and not _menu_opened(win, pid, before):
        return None
    return Outcome(True, "window message", win.class_name(hwnd))


# ── The real mouse, borrowed ────────────────────────────────────────────

def _on_top_at(win, x, y):
    """The top-level window a real click at (x, y) reaches: the topmost one
    there, past windows that let clicks through."""
    hwnd = win.window_from_point(x, y)
    root = win.root_of(hwnd) if hwnd else None
    if root and win.is_click_through(root):
        root = win.top_window_at(x, y, win.is_click_through)
    return root


def _borrow_mouse(x: int, y: int, button: str, reason: str, bring_forward=None) -> Outcome:
    """Move the mouse there, click, and put it back where the user left
    it. The last resort, and only while Settings > Mouse allows it."""
    if not mouse_fallback_allowed():
        note = f"{reason}, and using the mouse for that is turned off in Settings"
        print(f"[direct] Skipped a {button} click at ({x}, {y}): {note}.")
        return Outcome(False, "mouse", note=note)
    if not _IS_WINDOWS:
        from ai.lazy_import import pyautogui
        pyautogui.click(x=x, y=y, button=button)
        return Outcome(True, "mouse", note=reason)

    win = _win()
    # Not in the middle of the user's own drag, and not while they hold
    # Ctrl, Shift or Alt, which would make this a Ctrl+click.
    deadline = time.monotonic() + 2.0
    while (win.buttons_down() or win.modifiers_down()) and time.monotonic() < deadline:
        time.sleep(0.05)
    if win.buttons_down() or win.modifiers_down():
        note = f"{reason}, and the user was holding a mouse button or Ctrl, Shift or Alt"
        print(f"[direct] Skipped a {button} click at ({x}, {y}): {note}.")
        return Outcome(False, "mouse", note=note)
    # Nor in the middle of their typing, or of a move of theirs: the click
    # would pull the pointer out from under them, and their next keys
    # would go into the window it clicked.
    if not _wait_for_quiet(win):
        note = f"{reason}, and the user kept typing or moving the mouse"
        print(f"[direct] Skipped a {button} click at ({x}, {y}): {note}.")
        return Outcome(False, "mouse", note=note)
    if bring_forward:
        # The control's window must be the one under the point for a
        # real click to reach it.
        if _on_top_at(win, x, y) != bring_forward:
            win.bring_to_front(bring_forward)
            time.sleep(0.15)
        on_top = _on_top_at(win, x, y)
        if on_top != bring_forward:
            note = (f"{reason}, and {_describe(win, on_top)} stays over that spot "
                    f"(a window kept on top?)")
            print(f"[direct] Skipped a {button} click at ({x}, {y}): {note}.")
            return Outcome(False, "mouse", note=note)
    stop = _stopped()
    if stop is not None:
        return stop
    saved = win.cursor_pos()
    if saved is None:
        return Outcome(False, "mouse", note=f"{reason}, and the mouse can't be used right now (a locked screen?)")
    try:
        win.mouse_click(x, y, button)
    finally:
        win.set_cursor_pos(*saved)
    print(f"[direct] Used the mouse for a {button} click at ({x}, {y}) and put it back: {reason}.")
    return Outcome(True, "mouse", note=reason)


# ── Typing and keys ─────────────────────────────────────────────────────

def type_text(text: str, window=None) -> Outcome:
    """`text` into the field the AI is working in, in `window` or the
    target. Not done: how="keyboard" means type it with the keyboard,
    into `front` of the outcome, which is in front now and has to stay
    there until the last key (see keyboard_reaches); how="blocked" means
    don't.

    Straight in, without the keyboard, only for text with no line break
    or tab in it: those are keys (Enter, Tab), and a key posted after
    text that was sent can reach the app before it."""
    if not text:
        return Outcome(True, "nothing")
    stop = _stopped()
    if stop is not None:
        return stop
    if not _IS_WINDOWS:
        return Outcome(False, "keyboard")
    win = _win()
    target = window or _target()
    if not target:
        return _keyboard_in_front(win)
    if (not _in_front(win, target) and not _pending_for(target)
            and not any(ch in "\r\n\t" for ch in text)):
        focus = _focus_in(win, target)
        if (focus and _is_text_box(win, focus) and win.text_box_accepts(focus, text)
                and win.replace_selection(focus, text)):
            log_debug(f"[direct] typed {len(text)} character(s) into {win.class_name(focus)} without the keyboard")
            return Outcome(True, "text message", win.class_name(focus), window=target)
    return keyboard_window(target)


# name: (virtual-key code, extended key)
_KEYS = {
    "enter": (0x0D, False), "return": (0x0D, False),
    "tab": (0x09, False), "space": (0x20, False),
    "escape": (0x1B, False), "esc": (0x1B, False),
    "backspace": (0x08, False),
    "delete": (0x2E, True), "del": (0x2E, True), "insert": (0x2D, True),
    "home": (0x24, True), "end": (0x23, True),
    "pageup": (0x21, True), "pagedown": (0x22, True),
    "left": (0x25, True), "up": (0x26, True), "right": (0x27, True), "down": (0x28, True),
}
_KEYS.update({f"f{n}": (0x6F + n, False) for n in range(1, 13)})


def press_key(key: str, window=None) -> Outcome:
    """One key (Enter, Delete, Escape, an arrow...) to the window the AI is
    working in. Not done: how="keyboard" means press it on the keyboard,
    which now reaches `front` of the outcome; how="blocked" means don't."""
    stop = _stopped()
    if stop is not None:
        return stop
    if not _IS_WINDOWS:
        return Outcome(False, "keyboard")
    win = _win()
    target = window or _target()
    if not target:
        return _keyboard_in_front(win)
    spec = _KEYS.get((key or "").strip().lower())
    if spec is not None and not _in_front(win, target) and not _pending_for(target):
        # Only to a control that actually has the focus: a window whose
        # app isn't active often has none, and a key posted to the window
        # itself reaches nothing.
        focus = _focus_in(win, target)
        if focus and _is_classic(win, focus) and win.post_key(focus, *spec):
            time.sleep(0.05)
            log_debug(f"[direct] {key} sent to {win.class_name(focus)} without the keyboard")
            return Outcome(True, "window message", win.class_name(focus), window=target)
    return keyboard_window(target)


def keyboard_window(window=None) -> Outcome:
    """Before real keystrokes: where they will go. how="keyboard": into
    `front` of the outcome, the window in front now, which is `window` or
    the target (or a modal dialog of its own), brought to the front if
    it wasn't there; with neither, the window in front, which becomes the
    target for the steps after. Anything else: don't send them
    (automation stopped, or they would land in another window)."""
    stop = _stopped()
    if stop is not None:
        return stop
    if not _IS_WINDOWS:
        return Outcome(False, "keyboard")
    win = _win()
    target = window or _target()
    if not target:
        return _keyboard_in_front(win)
    front = _front_for(win, target)
    if front:
        # The AI's last key may be opening a window (Win+E, Ctrl+N, Enter
        # on a file): a moment for it to come up, rather than typing on in
        # this one and raising it back over the new one.
        came_up, settled = _await_what_keys_open(win, target, _WAIT_FOR_SLOW_WINDOW)
        if came_up:
            return Outcome(False, "keyboard", window=came_up, front=came_up)
        if settled:
            return _keyboard_in_front(win)
        _clear_keys_pending()               # nothing came: keys stay in this window
        _focus_pending_item(target)
        return Outcome(False, "keyboard", window=target, front=front)
    return _keyboard_for(win, target)


def prepare_keyboard(window=None) -> bool:
    """keyboard_window, as a yes or no: whether real keystrokes may be
    sent now."""
    return keyboard_window(window).how == "keyboard"


def focus_takes_text() -> bool:
    """Whether the control with the keyboard focus is one that text goes
    into (a text field, an editable document or combo box), as UI
    Automation sees it; no when that can't be told. Anywhere else, a
    Delete acts on what is selected: a file in Explorer, an email in a
    list."""
    if not _IS_WINDOWS:
        return True
    return _attempt(_focus_takes_text) is True


def _focus_takes_text(gate) -> bool:
    control = _focused_control()
    if control is None:
        return False
    kind = _kind(control)
    if kind == "Edit":
        return True
    return kind in ("Document", "ComboBox") and _editable(control)


def keyboard_reaches(front) -> bool:
    """While typing with the keyboard: still not stopped, and `front` (the
    window keyboard_window said the keys go into) still the very window
    in front. Checked before every key: the user may click into their own
    window halfway through a sentence, and a dialog that pops up midway
    isn't where the rest was meant to go."""
    if _stopped() is not None:
        return False
    if not _IS_WINDOWS or not front:
        return True
    return _win().foreground() == front


def shown_windows() -> frozenset:
    """The top-level windows there now: visible and not cloaked, minimized
    ones included. Taken before the AI's own keys, to tell a window they
    bring up from one that was already there, such as the user's (a
    window the user restores was there, minimized)."""
    if not _IS_WINDOWS:
        return frozenset()
    try:
        return frozenset(_win().shown_windows())
    except Exception as e:
        log_debug(f"[direct] windows on screen unknown: {e}")
        return frozenset()


def handed_over(front, shown=frozenset(), last_key_at=None):
    """When `front` stopped being in front midway through typing because it
    handed over to another window, that window, which the rest goes
    into: the Start menu turns into Search as soon as something is typed.
    None otherwise: the user clicked into another window, `front` closed,
    or automation stopped.

    Only a window that was put away (cloaked) on the user's desktop hands
    over, only to one that wasn't there when typing started (`shown`),
    and only if the user hasn't typed or clicked since the AI's last key
    (`last_key_at`, time.monotonic()): a click of theirs closes the Start
    menu too, and the rest of the text isn't for their window."""
    if not _IS_WINDOWS or not front or _stopped() is not None:
        return None
    win = _win()
    if not win.is_window(front) or not win.is_cloaked(front) or win.on_another_desktop(front):
        return None
    deadline = time.monotonic() + 0.5
    while True:
        new = win.foreground()
        if new and new != front:
            if new in shown or win.process_id(new) == os.getpid() or _is_shell(win, new):
                return None
            if last_key_at is not None and not _no_input_since(win, last_key_at):
                return None
            if _can_be_target(win, new):
                set_target(new)
            return new
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


@dataclass
class KeysBefore:
    """The screen just before the AI's own key: the window it goes into,
    the windows there (see follow_keys), and whether the key went into a
    text field."""
    front: "int | None" = None
    shown: frozenset = frozenset()
    into_text: bool = False


def before_keys(front=None, into_text: bool = False) -> KeysBefore:
    """Taken right before the AI presses a key that may bring up a window."""
    if not _IS_WINDOWS:
        return KeysBefore()
    return KeysBefore(front or _win().foreground(), shown_windows(), into_text)


def follow_keys(before: KeysBefore, system: bool = False, switch: bool = False) -> None:
    """After the AI's own key or key combination: a window it brings up
    (Win+R's Run box, a new window from Ctrl+N or Win+E, Alt+Enter's
    Properties, the app Enter opened a file in) is where the next keys go.
    Decided at the next key (see _came_up_after_keys), since such a window
    can take a second or two to appear, and only for a window that wasn't
    there before this key: one the user switches to, or restores, was.

    system: a combination with the Windows key. What it brings up is the
    shell's (Start, Run); if that can't be a target, keys go to the window
    in front rather than back to the old one.
    switch: Alt+Tab or Alt+Esc, which switch to a window that was there,
    on purpose. That one is in front at once, and is taken right away."""
    global _keys_pending
    if not _IS_WINDOWS or not before.front:
        return
    if switch:
        win = _win()
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            front = win.foreground()
            if front and front != before.front:
                if _can_be_target(win, front):
                    set_target(front)
                return
            time.sleep(0.03)
        return
    with _target_lock:
        _keys_pending = (before, time.monotonic(), system)


def _clear_keys_pending() -> None:
    global _keys_pending
    with _target_lock:
        _keys_pending = None


def _no_input_since(win, moment) -> bool:
    """No keyboard or mouse input since `moment` (time.monotonic()), the
    AI's own included, give or take the clock's grain."""
    return win.idle_ms() >= (time.monotonic() - moment) * 1000 - 40


def _keyboard_in_front(win) -> Outcome:
    """No window picked to type into: the keyboard, into the window in
    front, which becomes the target for a short while, so the next steps
    of the same run go there too (not into a later chat turn). Never one
    of Kestrel's own (the AI's text would land in the chat box, and its
    Enter would send it), nor the taskbar, nor whatever is in front right
    after the AI's window closed or a window came up that may be the
    user's."""
    came_up, _settled = _await_what_keys_open(win, None, _WAIT_FOR_SLOW_WINDOW)
    if came_up:
        return Outcome(False, "keyboard", window=came_up, front=came_up)
    with _target_lock:
        held = _vanished_at
    if held is not None and time.monotonic() - held[0] < _VANISHED_FOR:
        return Outcome(False, "blocked", note=held[1])
    front = win.foreground()
    if not front:
        return Outcome(False, "blocked", note="no window is in front to type into")
    if win.process_id(front) == os.getpid():
        return Outcome(False, "blocked",
                       note="Kestrel's own window is in front and no other window was clicked: "
                            "click the field to type into first")
    if _is_shell(win, front):
        return Outcome(False, "blocked",
                       note="the taskbar is in front and no window was clicked: click the field to type into first")
    if _can_be_target(win, front):
        set_target(front, lifetime=_ANCHOR_LIFETIME)
    return Outcome(False, "keyboard", window=front, front=front)


def _keyboard_for(win, target) -> Outcome:
    """Bring `target` to the front for the keyboard, and say whether it
    got there.

    Not on another virtual desktop: that would switch the user's desktop.
    Not while the user is typing or using the mouse: their next keys
    would land in it, and a key of theirs could even move what the AI's
    key acts on. A minimized window is restored first (its dialogs are
    hidden with it). While a modal dialog of its own is up (the target is
    disabled), that dialog is what comes forward and gets the keys; a
    dialog that isn't modal, such as a Find box, stays where it is."""
    if win.is_cloaked(target):
        return Outcome(False, "blocked", window=target,
                       note="the window it is working in isn't on screen (it may be on another "
                            "virtual desktop): click where to type first")
    if not _wait_for_quiet(win):
        return Outcome(False, "blocked", window=target,
                       note="the user was typing or using the mouse, so its window wasn't brought "
                            "to the front over theirs; try again in a moment")
    win.restore(target)
    shown = target
    if not win.is_enabled(target):
        shown = win.last_active_popup(target) or target
    win.bring_to_front(shown)
    time.sleep(0.1)
    front = _front_for(win, target)
    if front:
        _focus_pending_item(target)
        return Outcome(False, "keyboard", window=target, front=front, note="its window was brought to the front")
    print("[direct] The window the AI is working in couldn't be brought to the front, "
          "so nothing was typed: it would have gone into another window.")
    return Outcome(False, "blocked", window=target, note="its window couldn't be brought to the front")


# Before taking the focus from whatever the user is in: this long with no
# input from them, waited for at most _QUIET_WAIT seconds.
_QUIET_MS = 400
_QUIET_WAIT = 3.0


def _wait_for_quiet(win) -> bool:
    deadline = time.monotonic() + _QUIET_WAIT
    while win.idle_ms() < _QUIET_MS:
        if time.monotonic() >= deadline or _stopped() is not None:
            return False
        time.sleep(0.05)
    return True


def _front_for(win, target):
    """The window in front, if it is `target` or a modal dialog it owns (a
    message box, Save As): with one up, the target takes no keys itself
    and the dialog gets them. Else None. A dialog that isn't modal doesn't
    count (see _target for one that came up after the AI acted)."""
    foreground = win.foreground()
    if not foreground:
        return None
    if foreground == target:
        return foreground
    if not win.is_enabled(target) and win.root_owner(foreground) == win.root_owner(target):
        return foreground
    return None


def _in_front(win, target) -> bool:
    return _front_for(win, target) is not None


def _focus_in(win, target):
    """The control with the keyboard focus in `target`'s thread, if it is
    in `target`, or in a modal dialog of its own (see _front_for)."""
    focus = win.focus_of(target)
    if not focus:
        return None
    if win.root_of(focus) == win.root_of(target):
        return focus
    if not win.is_enabled(target) and win.root_owner(focus) == win.root_owner(target):
        return focus
    return None


# ── Telling windows apart ───────────────────────────────────────────────

# Window classes of frameworks that draw their own controls and ignore, or
# only half handle, a click or a key posted as a message. Matched
# anywhere in the class name, lower case, on the window and every window
# it is inside: a XAML island in a classic window counts.
_NO_MESSAGES = (
    "chrome_widgetwin", "chrome_renderwidgethosthwnd", "intermediate d3d window",
    "cefbrowserwindow",
    "mozillawindowclass", "mozilladialogclass",
    "applicationframe", "windows.ui.", "microsoft.ui.", "desktopwindowxamlsource",
    "xaml", "inputsite", "winuidesktopwin32windowclass",
    "hwndwrapper[",
    "sunawt",
    "netuihwnd", "directuihwnd",
    "glfw", "sdl_app", "unitywndclass", "unrealwindow",
    "flutter", "gdkwindow", "avalonia",
)
_APP_NAMES = (
    ("chrome_", "This Chromium app"), ("intermediate d3d", "This Chromium app"),
    ("cefbrowser", "This Chromium app"), ("mozilla", "Firefox"),
    ("applicationframe", "This Store app"), ("windows.ui.", "This Store app"),
    ("microsoft.ui.", "This WinUI app"), ("hwndwrapper[", "This WPF app"),
    ("sunawt", "This Java app"), ("netuihwnd", "This Office window"),
    ("directuihwnd", "This Explorer view"),
)


def _framework_class(name: str) -> bool:
    name = (name or "").lower()
    if name.startswith("qt") and "qwindow" in name:   # Qt5QWindowIcon, Qt662QWindowOwnDCIcon...
        return True
    return any(part in name for part in _NO_MESSAGES)


def _is_classic(win, hwnd) -> bool:
    """A window that reads posted mouse and key messages like real ones:
    neither it nor any window it sits in belongs to a framework that
    doesn't."""
    if not hwnd:
        return False
    root = win.root_of(hwnd) or hwnd
    current = hwnd
    for _ in range(24):
        if _framework_class(win.class_name(current)):
            return False
        if current == root:
            return True
        current = win.parent_of(current)
        if not current:
            return True
    return True


def _is_text_box(win, hwnd) -> bool:
    """A standard Windows text box: takes EM_REPLACESEL."""
    name = (win.class_name(hwnd) or "").lower()
    return (any(part in name for part in ("edit", "scintilla", "memo", "textbox"))
            and _is_classic(win, hwnd))


def _app_name(win, hwnd) -> str:
    names = [(win.class_name(hwnd) or "").lower(), (win.class_name(win.root_of(hwnd) or hwnd) or "").lower()]
    for prefix, label in _APP_NAMES:
        if any(name.startswith(prefix) for name in names):
            return label
    if any(name.startswith("qt") and "qwindow" in name for name in names):
        return "This Qt app"
    return "This app"


# ── UI Automation ───────────────────────────────────────────────────────

_com = threading.local()


def ui_automation(multithreaded: bool = False):
    """The uiautomation module, with COM set up on this thread, or None.

    comtypes sets COM up only on the thread that first imports it, and
    UI Automation has to be called from a thread that has it: Kestrel's
    clicks come from worker threads (the AI, macros, the mapper). Once
    per thread, never undone, which is what uiautomation's own
    UIAutomationInitializerInThread amounts to minus the undoing.

    multithreaded: the multithreaded apartment, which Microsoft advises
    for a UI Automation client thread that owns no windows (the worker
    below). Otherwise the single-threaded one, as comtypes does."""
    if not _IS_WINDOWS:
        return None
    try:
        import uiautomation as auto
    except Exception as e:
        log_debug(f"[direct] UI Automation unavailable: {e}")
        return None
    _com_ready(multithreaded)
    return auto


def _com_ready(multithreaded: bool = False) -> None:
    """COM set up on this thread, once (see ui_automation)."""
    if getattr(_com, "ready", False):
        return
    _com.ready = True
    try:
        import comtypes
        if multithreaded:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        else:
            comtypes.CoInitializeEx()
    except Exception as e:
        # RPC_E_CHANGED_MODE: the thread already joined the other kind of
        # apartment, which works just as well.
        log_debug(f"[direct] COM on this thread: {e}")


def _desktops():
    """Windows' IVirtualDesktopManager, made once per thread. None where
    there are no virtual desktops (before Windows 10) or no comtypes, and
    for a minute after it couldn't be made (Explorer restarting)."""
    if getattr(_com, "desktops", None) is not None:
        return _com.desktops
    if time.monotonic() - getattr(_com, "desktops_failed_at", -1e9) < 60:
        return None
    try:
        import comtypes
        import comtypes.client
        _com_ready()
        _com.desktops = comtypes.client.CreateObject(
            comtypes.GUID("{aa509086-5ca9-4c25-8f95-589d3c07b48a}"), interface=_desktop_interface())
    except Exception as e:
        log_debug(f"[direct] no virtual desktop manager: {e}")
        _com.desktops = None
        _com.desktops_failed_at = time.monotonic()
    return _com.desktops


_IVirtualDesktopManager = None


def _desktop_interface():
    global _IVirtualDesktopManager
    if _IVirtualDesktopManager is None:
        import ctypes
        from ctypes import wintypes

        import comtypes

        class IVirtualDesktopManager(comtypes.IUnknown):
            # Only the first method: the rest of the table isn't called.
            _iid_ = comtypes.GUID("{a5cd92ff-29be-454c-8d04-d82879fb3f1b}")
            _methods_ = [
                comtypes.COMMETHOD([], ctypes.HRESULT, "IsWindowOnCurrentVirtualDesktop",
                                   (["in"], wintypes.HWND, "topLevelWindow"),
                                   (["out", "retval"], ctypes.POINTER(wintypes.BOOL), "onCurrentDesktop")),
            ]

        _IVirtualDesktopManager = IVirtualDesktopManager
    return _IVirtualDesktopManager


def _control_at(x: int, y: int):
    auto = ui_automation()
    if auto is None:
        return None
    try:
        return auto.ControlFromPoint(x, y)
    except Exception as e:
        log_debug(f"[direct] nothing from UI Automation at ({x}, {y}): {e}")
        return None


def _control_in_window_at(root, x: int, y: int, budget: float = 1.0):
    """The deepest control of window `root` whose box holds (x, y), found
    by walking down from the window itself: for when another window is on
    top of the point. The last of the children that hold it wins, since
    later ones are usually drawn over earlier ones."""
    auto = ui_automation()
    if auto is None or not root:
        return None
    deadline = time.monotonic() + budget
    try:
        control = auto.ControlFromHandle(root)
        for _ in range(40):
            if control is None or time.monotonic() > deadline:
                break
            found = None
            child = control.GetFirstChildControl()
            while child is not None and time.monotonic() <= deadline:
                try:
                    box = child.BoundingRectangle
                    # A hidden panel keeps its box: it is not what is there.
                    if box.left <= x < box.right and box.top <= y < box.bottom and not child.IsOffscreen:
                        found = child
                except Exception as e:
                    log_debug(f"[direct] a control's box unreadable: {e}")
                child = child.GetNextSiblingControl()
            if found is None:
                break
            control = found
        return control
    except Exception as e:
        log_debug(f"[direct] no control found in the window at ({x}, {y}): {e}")
        return None


def _focused_control():
    auto = ui_automation()
    if auto is None:
        return None
    try:
        return auto.GetFocusedControl()
    except Exception as e:
        log_debug(f"[direct] no focused control: {e}")
        return None


def _element3(control):
    """`control` as IUIAutomationElement3, which can open its context
    menu, or None (before Windows 8.1, or no such interface)."""
    try:
        from comtypes.gen import UIAutomationClient
        interface = getattr(UIAutomationClient, "IUIAutomationElement3", None)
        if interface is None:
            return None
        return control.Element.QueryInterface(interface)
    except Exception as e:
        log_debug(f"[direct] no IUIAutomationElement3: {e}")
        return None


def _kind(control) -> str:
    name = getattr(control, "ControlTypeName", "") or ""
    return name[:-len("Control")] if name.endswith("Control") else name


def _label(control) -> str:
    kind = _kind(control) or "control"
    try:
        name = (control.Name or "").strip()
    except Exception:
        name = ""
    if len(name) > 60:
        name = name[:57] + "..."
    return f'{kind} "{name}"' if name else kind


def _enabled(control) -> bool:
    try:
        return bool(control.IsEnabled)
    except Exception:
        return True


def _native_handle(control):
    try:
        return int(control.NativeWindowHandle or 0) or None
    except Exception:
        return None


# ── Win32 ───────────────────────────────────────────────────────────────

_win_layer = None
_win_lock = threading.Lock()


def _win():
    global _win_layer
    if _win_layer is None:
        with _win_lock:
            if _win_layer is None:
                _win_layer = _Win32()
    return _win_layer


GA_PARENT, GA_ROOT, GA_ROOTOWNER = 1, 2, 3
GW_OWNER = 4
DWMWA_CLOAKED = 14
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT, WS_EX_LAYERED = 0x00000020, 0x00080000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ES_PASSWORD, ES_READONLY, ES_NUMBER = 0x0020, 0x0800, 0x2000
WM_GETTEXT, WM_GETTEXTLENGTH, EM_REPLACESEL = 0x000D, 0x000E, 0x00C2
SMTO_ABORTIFHUNG = 0x0002
SM_CXSCREEN, SM_CYSCREEN, SM_SWAPBUTTON = 0, 1, 23
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
VK_LBUTTON, VK_RBUTTON, VK_MBUTTON = 0x01, 0x02, 0x04
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
SW_RESTORE = 9
SW_SHOWMINNOACTIVE = 7
SW_SHOWNORMAL, SW_SHOWNOACTIVATE = 1, 4
WPF_ASYNCWINDOWPLACEMENT = 0x0004
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_ABSOLUTE = 0x4000, 0x8000
MAPVK_VK_TO_VSC = 0
# Text boxes longer than this are compared by length only.
_COMPARE_TEXT_UP_TO = 200_000


class _Win32:
    """The user32 calls this module makes, typed once. Tests replace the
    instance (_win_layer) with a fake that has the same methods."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as wt

        self._c = ctypes
        self._wt = wt
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._u, self._k = u, k

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wt.DWORD), ("flags", wt.DWORD),
                ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND), ("hwndCapture", wt.HWND),
                ("hwndMenuOwner", wt.HWND), ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND),
                ("rcCaret", wt.RECT),
            ]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t),
            ]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]

        # All three, so the structure is the size SendInput checks for
        # (40 bytes on 64-bit Windows).
        class _INPUTUNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wt.DWORD), ("union", _INPUTUNION)]

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]

        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [("length", wt.UINT), ("flags", wt.UINT), ("showCmd", wt.UINT),
                        ("ptMinPosition", wt.POINT), ("ptMaxPosition", wt.POINT),
                        ("rcNormalPosition", wt.RECT)]

        self._LASTINPUTINFO = LASTINPUTINFO
        self._WINDOWPLACEMENT = WINDOWPLACEMENT

        self._GUITHREADINFO, self._MOUSEINPUT, self._INPUT = GUITHREADINFO, MOUSEINPUT, INPUT
        self._WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def proto(fn, restype, *argtypes):
            fn.restype = restype
            fn.argtypes = list(argtypes)

        HWND, UINT, BOOL, DWORD, INT = wt.HWND, wt.UINT, wt.BOOL, wt.DWORD, ctypes.c_int
        LPPOINT = ctypes.POINTER(wt.POINT)
        proto(u.WindowFromPoint, HWND, wt.POINT)
        proto(u.GetAncestor, HWND, HWND, UINT)
        proto(u.GetWindow, HWND, HWND, UINT)
        proto(u.GetLastActivePopup, HWND, HWND)
        proto(u.GetClassNameW, INT, HWND, wt.LPWSTR, INT)
        proto(u.GetWindowThreadProcessId, DWORD, HWND, ctypes.POINTER(DWORD))
        # GetWindowLongPtrW is only exported by 64-bit Windows.
        self._get_long = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW
        proto(self._get_long, ctypes.c_ssize_t, HWND, INT)
        proto(u.IsWindow, BOOL, HWND)
        proto(u.IsWindowVisible, BOOL, HWND)
        proto(u.IsWindowEnabled, BOOL, HWND)
        proto(u.IsIconic, BOOL, HWND)
        proto(u.IsZoomed, BOOL, HWND)
        proto(u.GetForegroundWindow, HWND)
        proto(u.ScreenToClient, BOOL, HWND, LPPOINT)
        proto(u.GetClientRect, BOOL, HWND, ctypes.POINTER(wt.RECT))
        proto(u.RealChildWindowFromPoint, HWND, HWND, wt.POINT)
        proto(u.PostMessageW, BOOL, HWND, UINT, wt.WPARAM, wt.LPARAM)
        proto(u.SendMessageTimeoutW, wt.LPARAM, HWND, UINT, wt.WPARAM, wt.LPARAM, UINT, UINT,
              ctypes.POINTER(ctypes.c_size_t))
        proto(u.GetGUIThreadInfo, BOOL, DWORD, ctypes.POINTER(GUITHREADINFO))
        proto(u.GetCursorPos, BOOL, LPPOINT)
        proto(u.SetCursorPos, BOOL, INT, INT)
        proto(u.GetAsyncKeyState, wt.SHORT, INT)
        proto(u.GetSystemMetrics, INT, INT)
        proto(u.MapVirtualKeyW, UINT, UINT, UINT)
        proto(u.ShowWindow, BOOL, HWND, INT)
        proto(u.ShowWindowAsync, BOOL, HWND, INT)
        proto(u.IsHungAppWindow, BOOL, HWND)
        proto(u.GetWindowPlacement, BOOL, HWND, ctypes.POINTER(WINDOWPLACEMENT))
        proto(u.SetWindowPlacement, BOOL, HWND, ctypes.POINTER(WINDOWPLACEMENT))
        proto(u.BringWindowToTop, BOOL, HWND)
        proto(u.SetForegroundWindow, BOOL, HWND)
        proto(u.AttachThreadInput, BOOL, DWORD, DWORD, BOOL)
        proto(u.EnumWindows, BOOL, self._WNDENUMPROC, wt.LPARAM)
        proto(u.SendInput, UINT, UINT, ctypes.POINTER(INPUT), INT)
        proto(u.GetLastInputInfo, BOOL, ctypes.POINTER(LASTINPUTINFO))
        proto(k.GetCurrentThreadId, DWORD)
        proto(k.GetTickCount, DWORD)
        proto(u.GetWindowRect, BOOL, HWND, ctypes.POINTER(wt.RECT))
        proto(k.OpenProcess, wt.HANDLE, DWORD, BOOL, DWORD)
        proto(k.QueryFullProcessImageNameW, BOOL, wt.HANDLE, DWORD, wt.LPWSTR, ctypes.POINTER(DWORD))
        proto(k.CloseHandle, BOOL, wt.HANDLE)
        # Windows 8 and later cloak a Store app's window, and the Start
        # menu, instead of hiding them: IsWindowVisible stays true.
        try:
            self._dwm = ctypes.WinDLL("dwmapi")
            proto(self._dwm.DwmGetWindowAttribute, ctypes.c_long, HWND, DWORD, ctypes.c_void_p, DWORD)
        except (OSError, AttributeError):
            self._dwm = None
        # Windows 10 1607 and later. Without them, a window that isn't DPI
        # aware gets its click at unscaled coordinates.
        try:
            proto(u.GetWindowDpiAwarenessContext, ctypes.c_void_p, HWND)
            proto(u.SetThreadDpiAwarenessContext, ctypes.c_void_p, ctypes.c_void_p)
            proto(u.PhysicalToLogicalPointForPerMonitorDPI, BOOL, HWND, LPPOINT)
            self._dpi = True
        except AttributeError:
            self._dpi = False
        self._wide_lparam = ctypes.sizeof(ctypes.c_void_p) == 8

    # -- windows --------------------------------------------------------

    def window_from_point(self, x, y):
        return self._u.WindowFromPoint(self._wt.POINT(x, y)) or None

    def root_of(self, hwnd):
        return (self._u.GetAncestor(hwnd, GA_ROOT) or None) if hwnd else None

    def root_owner(self, hwnd):
        """The window at the top of `hwnd`'s parents and owners: the same
        for a main window and every dialog it owns."""
        return (self._u.GetAncestor(hwnd, GA_ROOTOWNER) or hwnd) if hwnd else None

    def parent_of(self, hwnd):
        if not hwnd:
            return None
        parent = self._u.GetAncestor(hwnd, GA_PARENT) or None
        # A top-level window's parent is the desktop window: not a window
        # it sits in.
        return None if parent and self._u.GetAncestor(hwnd, GA_ROOT) == hwnd else parent

    def owner_of(self, hwnd):
        return (self._u.GetWindow(hwnd, GW_OWNER) or None) if hwnd else None

    def last_active_popup(self, hwnd):
        return self._u.GetLastActivePopup(hwnd) or None

    def class_name(self, hwnd):
        if not hwnd:
            return ""
        buffer = self._c.create_unicode_buffer(256)
        return buffer.value if self._u.GetClassNameW(hwnd, buffer, 256) else ""

    def process_id(self, hwnd):
        pid = self._wt.DWORD(0)
        self._u.GetWindowThreadProcessId(hwnd, self._c.byref(pid))
        return pid.value

    def thread_id(self, hwnd):
        return self._u.GetWindowThreadProcessId(hwnd, None)

    def is_window(self, hwnd):
        return bool(self._u.IsWindow(hwnd))

    def is_visible(self, hwnd):
        return bool(self._u.IsWindowVisible(hwnd))

    def is_enabled(self, hwnd):
        """False while a modal dialog it owns is up."""
        return bool(self._u.IsWindowEnabled(hwnd))

    def is_cloaked(self, hwnd):
        if self._dwm is None:
            return False
        value = self._wt.DWORD(0)
        result = self._dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, self._c.byref(value),
                                                 self._c.sizeof(value))
        return result == 0 and value.value != 0

    def is_tool_window(self, hwnd):
        return bool(self._get_long(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW)

    def is_click_through(self, hwnd):
        """Clicks pass through it to what is beneath (a layered window with
        WS_EX_TRANSPARENT: Kestrel's border during a run, an overlay)."""
        style = self._get_long(hwnd, GWL_EXSTYLE)
        return bool(style & WS_EX_TRANSPARENT) and bool(style & WS_EX_LAYERED)

    def _box(self, hwnd):
        """The window's visible box: without the invisible resize border
        Windows 10 and 11 add around it."""
        rect = self._wt.RECT()
        if self._dwm is not None:
            if self._dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, self._c.byref(rect),
                                               self._c.sizeof(rect)) == 0:
                return rect
        if self._u.GetWindowRect(hwnd, self._c.byref(rect)):
            return rect
        return None

    def top_window_at(self, x, y, skip=None):
        """The topmost top-level window whose box holds (x, y), visible, not
        minimized, not cloaked, and not one `skip` turns down. EnumWindows
        goes from the top of the z-order down."""
        found = []

        def each(hwnd, _lparam):
            try:
                if not self._u.IsWindowVisible(hwnd) or self._u.IsIconic(hwnd):
                    return True
                box = self._box(hwnd)
                if box is None or not (box.left <= x < box.right and box.top <= y < box.bottom):
                    return True
                if self.is_cloaked(hwnd) or (skip is not None and skip(hwnd)):
                    return True
                found.append(hwnd)
                return False
            except Exception as e:
                log_debug(f"[direct] a window skipped while looking under a point: {e}")
                return True

        self._u.EnumWindows(self._WNDENUMPROC(each), 0)
        return found[0] if found else None

    def process_name(self, hwnd):
        """The window's program file name (chrome.exe), or ""."""
        handle = self._k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, self.process_id(hwnd))
        if not handle:
            return ""
        try:
            size = self._wt.DWORD(512)
            buffer = self._c.create_unicode_buffer(512)
            if self._k.QueryFullProcessImageNameW(handle, 0, buffer, self._c.byref(size)):
                return os.path.basename(buffer.value)
            return ""
        finally:
            self._k.CloseHandle(handle)

    def foreground(self):
        return self._u.GetForegroundWindow() or None

    def child_at(self, root, x, y):
        """The deepest visible child of `root` at a screen point, even when
        another window covers that point. RealChildWindowFromPoint looks
        through group boxes, to the radio button inside."""
        hwnd = root
        for _ in range(32):
            point = self._wt.POINT(x, y)
            if not self._u.ScreenToClient(hwnd, self._c.byref(point)):
                break
            child = self._u.RealChildWindowFromPoint(hwnd, point)
            if not child or child == hwnd or not self._u.IsWindowVisible(child):
                break
            hwnd = child
        return hwnd

    def client_point(self, hwnd, x, y):
        """A screen point in `hwnd`'s own client coordinates, in the DPI
        scale it works in, or None outside its client area or when it
        can't be converted."""
        point = self._wt.POINT(x, y)
        previous = None
        if self._dpi:
            context = self._u.GetWindowDpiAwarenessContext(hwnd)
            if context:
                previous = self._u.SetThreadDpiAwarenessContext(context)
        try:
            if self._dpi and not self._u.PhysicalToLogicalPointForPerMonitorDPI(hwnd, self._c.byref(point)):
                return None
            if not self._u.ScreenToClient(hwnd, self._c.byref(point)):
                return None
            rect = self._wt.RECT()
            if not self._u.GetClientRect(hwnd, self._c.byref(rect)):
                return None
            if not (0 <= point.x < rect.right and 0 <= point.y < rect.bottom):
                return None
            return point.x, point.y
        finally:
            if previous:
                self._u.SetThreadDpiAwarenessContext(previous)

    def shown_windows(self):
        """Every top-level window that is visible and not cloaked, minimized
        or not."""
        found = []

        def each(hwnd, _lparam):
            if hwnd and self._u.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        self._u.EnumWindows(self._WNDENUMPROC(each), 0)
        return [hwnd for hwnd in found if not self.is_cloaked(hwnd)]

    def visible_windows(self, pid):
        """The visible top-level windows of process `pid`: a context menu
        opening shows up as a new one."""
        found = set()

        def each(hwnd, _lparam):
            if hwnd and self._u.IsWindowVisible(hwnd) and self.process_id(hwnd) == pid:
                found.add(hwnd)
            return True

        self._u.EnumWindows(self._WNDENUMPROC(each), 0)
        return found

    def focus_of(self, hwnd):
        info = self._GUITHREADINFO()
        info.cbSize = self._c.sizeof(info)
        if not self._u.GetGUIThreadInfo(self.thread_id(hwnd), self._c.byref(info)):
            return None
        return info.hwndFocus or None

    def is_minimized(self, hwnd):
        return bool(self._u.IsIconic(hwnd))

    def is_maximized(self, hwnd):
        return bool(self._u.IsZoomed(hwnd))

    def show(self, hwnd, how):
        """ShowWindow with one of the SW_ values."""
        return bool(self._u.ShowWindow(hwnd, how))

    def show_async(self, hwnd, how):
        """The same, posted: a window that has stopped answering can't hold
        up the caller."""
        return bool(self._u.ShowWindowAsync(hwnd, how))

    def is_hung(self, hwnd):
        return bool(self._u.IsHungAppWindow(hwnd))

    def placement(self, hwnd):
        """Where the window is and how it is shown, or None."""
        placement = self._WINDOWPLACEMENT()
        placement.length = self._c.sizeof(placement)
        return placement if self._u.GetWindowPlacement(hwnd, self._c.byref(placement)) else None

    def set_placement(self, hwnd, placement):
        return bool(self._u.SetWindowPlacement(hwnd, self._c.byref(placement)))

    def on_another_desktop(self, hwnd):
        """On a virtual desktop other than the one the user is looking at.
        When that can't be told for this window, yes: it might be, and such
        a window is neither brought forward nor given up on."""
        manager = _desktops()
        if manager is None:
            return False                       # no virtual desktops: no other one
        try:
            return not manager.IsWindowOnCurrentVirtualDesktop(hwnd)
        except Exception as e:
            log_debug(f"[direct] virtual desktop of a window unknown: {e}")
            # Start, Search and other pieces of the shell aren't on any one
            # desktop and may not be known to the call: they are on this one.
            # An ordinary window that can't be told might be elsewhere.
            name = self.class_name(hwnd).lower()
            return not (name.startswith("windows.ui.core.corewindow") or self.is_tool_window(hwnd))

    def restore(self, hwnd):
        if self._u.IsIconic(hwnd):
            self._u.ShowWindow(hwnd, SW_RESTORE)

    def bring_to_front(self, hwnd):
        u = self._u
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, SW_RESTORE)
        if u.GetForegroundWindow() == hwnd:
            return True
        # SetForegroundWindow is refused to a process that isn't in front
        # unless its thread shares input with the ones that are.
        current = self._k.GetCurrentThreadId()
        threads = {self.thread_id(hwnd)}
        foreground = u.GetForegroundWindow()
        if foreground:
            threads.add(self.thread_id(foreground))
        attached = [t for t in threads if t and t != current and u.AttachThreadInput(current, t, True)]
        try:
            u.BringWindowToTop(hwnd)
            return bool(u.SetForegroundWindow(hwnd))
        finally:
            for thread in attached:
                u.AttachThreadInput(current, thread, False)

    # -- messages -------------------------------------------------------

    def _lparam(self, value):
        if not self._wide_lparam and value >= 1 << 31:
            value -= 1 << 32
        return value

    def post(self, hwnd, message, wparam, lparam):
        return bool(self._u.PostMessageW(hwnd, message, wparam, self._lparam(lparam)))

    def _send(self, hwnd, message, wparam, lparam, timeout_ms=2000):
        result = self._c.c_size_t(0)
        ok = self._u.SendMessageTimeoutW(hwnd, message, wparam, lparam, SMTO_ABORTIFHUNG,
                                         timeout_ms, self._c.byref(result))
        return bool(ok), result.value

    def _text_length(self, hwnd):
        ok, length = self._send(hwnd, WM_GETTEXTLENGTH, 0, 0, 1000)
        return length if ok else None

    def _text(self, hwnd, length):
        buffer = self._c.create_unicode_buffer(length + 1)
        ok, _copied = self._send(hwnd, WM_GETTEXT, length + 1, self._c.addressof(buffer), 2000)
        return buffer.value if ok else None

    def text_box_accepts(self, hwnd, text):
        """Whether EM_REPLACESEL may be used: it ignores a read-only or
        digits-only style, which typing respects. A password box can't be
        read back to check, so it gets the keyboard."""
        if not self._u.IsWindowEnabled(hwnd):
            return False
        style = self._get_long(hwnd, GWL_STYLE)
        if style & (ES_READONLY | ES_PASSWORD):
            return False
        # The same bit is ES_DISABLENOSCROLL in a RichEdit box, which
        # takes any text.
        if (style & ES_NUMBER and "richedit" not in self.class_name(hwnd).lower()
                and not text.isdigit()):
            return False
        return True

    def replace_selection(self, hwnd, text):
        """`text` in place of the text box's selection, at its caret, the
        way typing would put it there. True if the box's text changed."""
        before_length = self._text_length(hwnd)
        before = None
        if before_length is not None and before_length <= _COMPARE_TEXT_UP_TO:
            before = self._text(hwnd, before_length)
        buffer = self._c.create_unicode_buffer(text)
        ok, _ = self._send(hwnd, EM_REPLACESEL, 1, self._c.addressof(buffer))
        if not ok:
            return False
        after_length = self._text_length(hwnd)
        if before_length is None or after_length is None:
            return True                       # it took the message; nothing to compare
        if after_length != before_length:
            return True
        if before is not None:
            return self._text(hwnd, after_length) != before
        return True

    def post_key(self, hwnd, vk, extended):
        scan = self._u.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC) & 0xFF
        down = 1 | (scan << 16) | ((1 << 24) if extended else 0)
        up = down | (1 << 30) | (1 << 31)
        return self.post(hwnd, WM_KEYDOWN, vk, down) and self.post(hwnd, WM_KEYUP, vk, up)

    # -- the mouse and the keyboard state -------------------------------

    def cursor_pos(self):
        """Where the pointer is, or None when it can't be read (the screen
        is locked, a secure desktop is up)."""
        point = self._wt.POINT()
        if not self._u.GetCursorPos(self._c.byref(point)):
            return None
        return point.x, point.y

    def set_cursor_pos(self, x, y):
        self._u.SetCursorPos(x, y)

    def idle_ms(self):
        """How long since the last keyboard or mouse input, in milliseconds;
        a long time when it can't be read."""
        info = self._LASTINPUTINFO()
        info.cbSize = self._c.sizeof(info)
        if not self._u.GetLastInputInfo(self._c.byref(info)):
            return 1 << 30
        return (self._k.GetTickCount() - info.dwTime) & 0xFFFFFFFF

    def primary_size(self):
        return self._u.GetSystemMetrics(SM_CXSCREEN), self._u.GetSystemMetrics(SM_CYSCREEN)

    def _down(self, keys):
        return any(self._u.GetAsyncKeyState(vk) & 0x8000 for vk in keys)

    def buttons_down(self):
        return self._down((VK_LBUTTON, VK_RBUTTON, VK_MBUTTON))

    def modifiers_down(self):
        return self._down((VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))

    def mouse_click(self, x, y, button):
        """A real click at (x, y). Every event carries the position, so the
        click lands there even if the system handles it a moment later;
        this returns once the pointer has got there."""
        u = self._u
        left = button != "right"
        if u.GetSystemMetrics(SM_SWAPBUTTON):
            # Windows swaps injected buttons too: "left" here means the
            # user's main button, whichever physical one that is.
            left = not left
        down, up = ((MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP) if left
                    else (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP))
        left_edge = u.GetSystemMetrics(SM_XVIRTUALSCREEN)
        top_edge = u.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = max(1, u.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = max(1, u.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        nx = min(65535, max(0, 65536 * (x - left_edge) // width + 1))
        ny = min(65535, max(0, 65536 * (y - top_edge) // height + 1))
        flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        events = (self._INPUT * 3)()
        for event, extra in zip(events, (0, down, up)):
            event.type = INPUT_MOUSE
            event.union.mi = self._MOUSEINPUT(nx, ny, 0, flags | extra, 0, 0)
        sent = u.SendInput(3, events, self._c.sizeof(self._INPUT))
        if sent != 3:
            raise OSError(f"SendInput took {sent} of 3 mouse events (error {self._c.get_last_error()})")
        deadline = time.monotonic() + 0.25
        while time.monotonic() < deadline:
            position = self.cursor_pos()
            if position and abs(position[0] - x) <= 1 and abs(position[1] - y) <= 1:
                break
            time.sleep(0.01)
        time.sleep(0.04)
