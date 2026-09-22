# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/log.py
==========
Every print() call site across ai/*.py already followed a bracketed-tag
convention - print(f"[autonomous] ..."), print(f"[macro_store] ..."),
print(f"[tool_open_application] ...") - naming which subsystem logged it.
This module doesn't change that convention; it just prefixes a consistent
timestamp + level ahead of it, so raw console output (and, once built,
whatever renders it in Settings' planned admin/debug console - see the
note in ai/main.py's module docstring) reads as an actual ordered log
instead of a stream of bare, unordered prints. That distinction matters
once several background threads (autonomous mode, voice, wake word,
macro generalization) can all be printing to the same console at the
same time - interleaved lines with no timestamp are very hard to
reconstruct into what actually happened, and in what order, when
debugging something that went wrong.

Usage - each module that wants this imports log AS print, so every
EXISTING print(f"[tag] ...") call site in that file benefits with zero
per-call-site changes:

    from ai.log import log as print

ai.actions, ai.main, ai.macro_store, ai.macro_generalizer, and every
ai/discovery/*.py module all do this already. New modules should too,
rather than using the bare builtin - it costs nothing (identical call
signature: a single string, same as every print() call in this codebase
already is) and keeps console output consistent app-wide.

Two additions on top of the plain timestamp+level prefix:

- Color, when the console can actually show it. A real terminal
  (`sys.stdout.isatty()`) gets the level color-coded (cyan/yellow/red)
  and the leading [tag] bolded, so a long autonomous run or a busy
  macro playback is scannable at a glance instead of a wall of
  identical-looking gray text. Anything that isn't a real terminal -
  piped output, a packaged windowed .exe with no console attached,
  or (crucially) pytest's capsys during tests - gets the exact same
  plain text as before; nothing here changes what test_log.py already
  asserts byte-for-byte. Also skipped entirely if KESTREL_NO_COLOR is
  set, for anyone who'd rather redirect to a file.

- An optional input_data kwarg. A lot of the call sites this module
  feeds - play_actions() stepping through a literal macro, a
  generalized macro's compiled steps, tool_discover_application_ui's
  compiled tool_click_*/tool_input_* - log a RESULT ("Clicked at
  (120, 45)") but not what the call was actually given ("text_value=
  'youtube'"), which makes it hard to tell, after the fact, whether a
  macro did what was recorded or silently drifted. Passing
  input_data=... appends it to the line, distinctly formatted, without
  every call site needing to hand-format it themselves.
"""

import datetime
import os
import re
import sys

# Recognized severities, purely for the fixed-width column below
# anything else passed as `level` still prints fine, just unaligned.
_LEVELS = ("debug", "info", "warn", "error")

_COLOR_BY_LEVEL = {
    "debug": "\033[90m",   # grey
    "info": "\033[36m",    # cyan
    "warn": "\033[33m",    # yellow
    "error": "\033[31m",   # red
}

# Debug lines are off unless asked for. They exist for the handlers that
# would otherwise swallow an exception without trace: a failure nobody
# can see is a failure nobody can fix, but a failure printed on every
# one of ten thousand accessibility nodes buries everything else. This
# is the middle: recorded when wanted, invisible when not.
#
# Set KESTREL_DEBUG=1 to see them. They still reach the in-app Console
# and the history buffer either way, so turning it on is not the only
# way to get at them after the fact.
def _debug_enabled() -> bool:
    return os.getenv("KESTREL_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

_TAG_RE = re.compile(r"^(\[[^\]]+\])(.*)$", re.DOTALL)

# Computed once, lazily - isatty()/os.system() aren't expensive, but
# there's no reason to redo it on every single log() call.
_ansi_enabled = None


def _supports_ansi() -> bool:
    global _ansi_enabled
    if _ansi_enabled is not None:
        return _ansi_enabled

    if os.environ.get("KESTREL_NO_COLOR"):
        _ansi_enabled = False
        return False

    is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
    if not is_tty:
        # Piped output, a packaged windowed .exe with no console, or a
        # test harness capturing stdout (pytest's capsys) - never emit
        # escape codes into something that isn't rendering them.
        _ansi_enabled = False
        return False

    if sys.platform == "win32":
        # Modern Windows consoles support ANSI/VT100 escapes, but the
        # mode has to be turned on first - os.system("") is the
        # well-known zero-side-effect way to do that (it's a no-op
        # command, but importing the console host as a side effect
        # flips ConsoleMode's ENABLE_VIRTUAL_TERMINAL_PROCESSING bit).
        # Harmless to call even on a terminal that already supports it.
        try:
            os.system("")
        except Exception:
            pass

    _ansi_enabled = True
    return True


def log(message: str = "", *, level: str = "info", input_data=None) -> None:
    """
    Drop-in replacement for print(message) - same single-positional-arg
    calling convention every print() in ai/*.py already uses. Prefixes
    "HH:MM:SS.mmm LEVEL " ahead of the message's own existing [tag].

    input_data: optional - anything reprable (a dict of call kwargs, a
    typed string, a target description). Rendered dimmed, after the
    message, only on terminals that support color; on plain output it's
    appended as plain text instead of being silently dropped, so nothing
    is lost, it just isn't visually distinguished.
    """
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    level_norm = level if level in _LEVELS else level

    if level_norm == "debug" and not _debug_enabled():
        # Kept in the history buffer and handed to the sinks, so the
        # in-app Console can still show it; just not printed.
        _record_history(ts, level_norm, message, input_data)
        _notify_sinks(ts, level_norm, message, input_data)
        return

    body = message
    if input_data is not None:
        body = f"{body}  (input: {input_data!r})"

    if not _supports_ansi():
        try:
            print(f"{ts} {level_norm.upper():<5} {body}")
        except Exception:
            # stdout can be entirely unavailable (a packaged windowed
            # .exe has no console at all) - logging must never be able
            # to crash whatever called it.
            pass
        _record_history(ts, level_norm, message, input_data)
        _notify_sinks(ts, level_norm, message, input_data)
        return

    color = _COLOR_BY_LEVEL.get(level_norm, "")
    level_display = f"{color}{level_norm.upper():<5}{_RESET}" if color else f"{level_norm.upper():<5}"
    ts_display = f"{_DIM}{ts}{_RESET}"

    display_body = message
    m = _TAG_RE.match(display_body)
    if m:
        display_body = f"{_BOLD}{m.group(1)}{_RESET}{m.group(2)}"
    if input_data is not None:
        display_body = f"{display_body}  {_DIM}(input: {input_data!r}){_RESET}"

    try:
        print(f"{ts_display} {level_display} {display_body}")
    except Exception:
        pass

    _record_history(ts, level_norm, message, input_data)
    _notify_sinks(ts, level_norm, message, input_data)


# ══════════════════════════════════════════════════════════════════
# History - a small ring buffer of recent log() calls, kept regardless
# of whether anything is listening via add_sink(). Exists specifically
# for app/console_widget.py: it's only constructed lazily, the first
# time someone opens it from the tray menu - by then, everything that
# happened since app launch (startup, tool calls, an entire autonomous
# run) has already been logged and lost, since a sink only receives
# calls made AFTER it registers. get_history() lets a freshly-built
# console replay the backlog instead of opening to a blank window that
# only starts filling in from whatever happens next.
# ══════════════════════════════════════════════════════════════════

_MAX_HISTORY = 500
_history: list = []   # list of (ts, level, message, input_data), oldest first


def get_history() -> list:
    """Returns a copy of the recent log() call history (oldest first, up to _MAX_HISTORY entries)."""
    return list(_history)


def _record_history(ts: str, level: str, message: str, input_data) -> None:
    _history.append((ts, level, message, input_data))
    if len(_history) > _MAX_HISTORY:
        del _history[: len(_history) - _MAX_HISTORY]


# ══════════════════════════════════════════════════════════════════
# Sinks - anything that wants a copy of every log() call as it happens,
# not just as text printed to stdout. Built for app/console_widget.py's
# in-app console (the "catches print on the app" feature): rather than
# hijacking sys.stdout (risky - other code may read/redirect it too, and
# it would mean re-parsing our own formatted text back apart), log()
# hands out the raw pieces directly to anyone who's registered.
#
# Sinks are called synchronously, on whatever thread called log()
# ai.main/ai.actions/macro playback all run log() from background
# threads (autonomous mode, macro execution, voice), not just the GUI
# thread. A sink that touches Qt widgets MUST NOT do so directly here;
# it needs to marshal to the GUI thread itself (a pyqtSignal.emit() is
# thread-safe for this and is what ConsoleWidget's sink does - see
# app/console_widget.py). log() has no idea what a "GUI thread" is and
# deliberately doesn't try to.
# ══════════════════════════════════════════════════════════════════

_sinks: list = []


def add_sink(callback) -> None:
    """
    Register callback(timestamp: str, level: str, message: str,
    input_data) to be called on every future log() call. Safe to call
    the same callback twice - it's de-duplicated, not queued twice.
    """
    if callback not in _sinks:
        _sinks.append(callback)


def remove_sink(callback) -> None:
    """No-op if callback was never registered (or already removed)."""
    if callback in _sinks:
        _sinks.remove(callback)


def _notify_sinks(ts: str, level: str, message: str, input_data) -> None:
    for callback in list(_sinks):   # copy - a sink removing itself mid-notify must not skip others
        try:
            callback(ts, level, message, input_data)
        except Exception:
            # A broken sink must never be able to take down logging
            # itself - that would cascade into silent app-wide failures.
            pass


def log_debug(message: str, input_data=None) -> None:
    """For a failure that is handled but still worth a record. See
    _debug_enabled for when these are printed."""
    log(message, level="debug", input_data=input_data)


def log_warn(message: str) -> None:
    log(message, level="warn")


def log_error(message: str) -> None:
    log(message, level="error")


def log_input(text: str) -> None:
    """
    Distinct from log(): a line representing the USER's own message, not
    a system/tool/AI print - printed with a "› " marker and no
    timestamp/level (a person's own input doesn't need one), and
    notifies sinks with level="input" so a UI console (see
    app/console_widget.py) can render it as its own visually distinct
    line rather than folding it into the regular tool/reply/plain
    styling. Call sites (chat_widget.py's send path) don't need to know
    a console exists at all - same decoupling as every other log() call.
    """
    try:
        print(f"\u203a {text}")
    except Exception:
        pass
    _record_history(None, "input", text, None)
    _notify_sinks(None, "input", text, None)
