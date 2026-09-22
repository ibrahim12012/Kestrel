# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/unicode_input.py
===================
Typing text into another application, one character at a time, without
caring what keyboard layout is installed.

── Why this exists ──────────────────────────────────────────────────
`pyautogui.write()` cannot type an accented character. It loops per
character into `pyautogui.press()`, which looks the character up in a
table built from ASCII plus named keys; a character that is not in that
table returns early and types nothing. No exception, no warning - the
character is simply absent from the result. Typing "café" produced
"caf", and "Crème brûlée" produced "Crme brle".

The old workaround routed `string.punctuation` through the clipboard,
which is a 32-character ASCII set: `!"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~`
and nothing else. Every accent, every curly quote, every dash that is
not a hyphen, every currency symbol, every non-Latin script fell through
to `pyautogui.write()` and vanished.

── What this does instead ───────────────────────────────────────────
Windows' own SendInput, with KEYEVENTF_UNICODE. That flag means "this
event carries a UTF-16 code unit, not a virtual key code": the character
is delivered to the focused window directly, with no scan code and no
layout lookup, so it works identically on AZERTY, QWERTZ, Dvorak or a
layout with no key for the character at all. It is the mechanism the
on-screen keyboard and the emoji picker use.

Two details that are easy to get wrong and are handled here:

  - **Surrogate pairs.** SendInput carries one UTF-16 code unit per
    event. Anything above U+FFFF (emoji, some CJK extensions) is two
    code units and needs two events, sent together.
  - **Key up as well as key down.** Sending only the down event leaves
    applications that track key state confused; some drop the character
    entirely.

There are two fallbacks, in order, because this has to keep working on a
machine where ctypes or pywin32 is unavailable:

  1. SendInput with KEYEVENTF_UNICODE (this module's reason to exist)
  2. Clipboard paste, which is layout-independent but clobbers whatever
     the user had copied, and does not work in fields that block paste
  3. `pyautogui.write()`, which is where we started and drops accents

Nothing here raises. A typing helper that throws mid-macro leaves the
target application in a half-filled state, which is worse than a
character that did not arrive.
"""

from __future__ import annotations

import sys

from ai.log import log_debug

# Filled in on first use, so importing this module on a non-Windows
# machine (the test environment, a developer on Linux) costs nothing and
# fails nothing.
_send_input = None
_prepared = False

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1


def _prepare():
    """
    Build the ctypes structures for SendInput once. Returns the callable,
    or None if this platform or this Python cannot do it.

    The structures are declared here rather than at module scope so that
    importing this module never touches ctypes on a platform where the
    Windows types do not exist.
    """
    global _send_input, _prepared
    if _prepared:
        return _send_input
    _prepared = True

    if sys.platform != "win32":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        ULONG_PTR = ctypes.POINTER(wintypes.ULONG)

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR),
            ]

        class _INPUTunion(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [
                ("type", wintypes.DWORD),
                ("union", _INPUTunion),
            ]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        user32.SendInput.restype = wintypes.UINT

        def send_units(code_units) -> bool:
            """One key-down and one key-up per UTF-16 code unit, in one
            SendInput call so the sequence cannot be interleaved with
            real keyboard input."""
            events = []
            for unit in code_units:
                for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                    event = INPUT()
                    event.type = INPUT_KEYBOARD
                    event.union.ki = KEYBDINPUT(
                        wVk=0, wScan=unit, dwFlags=flags, time=0, dwExtraInfo=None
                    )
                    events.append(event)

            array = (INPUT * len(events))(*events)
            sent = user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
            return sent == len(events)

        _send_input = send_units
        return _send_input
    except Exception as e:
        log_debug(f"[unicode_input] SendInput unavailable, falling back: {e}")
        return None


def _utf16_units(ch: str):
    """The UTF-16 code units for one character. One for anything in the
    Basic Multilingual Plane, a surrogate pair above it."""
    encoded = ch.encode("utf-16-le", "ignore")
    return [
        int.from_bytes(encoded[i:i + 2], "little")
        for i in range(0, len(encoded), 2)
    ]


def send_char(ch: str) -> bool:
    """
    Type one character through SendInput. True if it went.

    False means the caller should fall back - either this platform has no
    SendInput, or the call was rejected (which happens when a
    higher-integrity window has focus and this process cannot send input
    to it).
    """
    send_units = _prepare()
    if send_units is None:
        return False
    units = _utf16_units(ch)
    if not units:
        return False
    try:
        return send_units(units)
    except Exception as e:
        log_debug(f"[unicode_input] SendInput failed for {ch!r}: {e}")
        return False


def send_text(text: str) -> bool:
    """
    Type a whole string. True only if every character went through
    SendInput; False the moment one does not, so the caller can fall
    back for the rest rather than producing half a string by one route
    and half by another.
    """
    if not text:
        return True
    if _prepare() is None:
        return False
    for ch in text:
        if not send_char(ch):
            return False
    return True


def available() -> bool:
    """Whether SendInput can be used at all, without sending anything."""
    return _prepare() is not None
