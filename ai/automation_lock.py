# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/automation_lock.py
=====================
One automation at a time: an AI run, a macro playing back, a mapping run.

Two at once click into the same windows and type into the same fields,
and a stop meant for one ended up cleared by the other starting (each
clears the stop flag as it starts). Whichever comes second is refused,
with what is running named, instead.

The thread that holds it can take it again: an AI run that maps an app
or plays a macro carries on.
"""

from __future__ import annotations

import contextlib
import threading

_state = threading.Lock()
_owner = None       # thread ident of the holder
_depth = 0
_what = ""


class Busy(Exception):
    """Another automation is running; the message says which."""

    def __init__(self, what: str):
        self.what = what
        super().__init__(f"Another automation is running ({what}). Stop it first, or wait for it to end.")


@contextlib.contextmanager
def hold(what: str):
    """Hold the lock for `what` ("an AI run", "a macro", "a mapping run")
    for the length of a with block. Raises Busy when another thread
    holds it."""
    global _owner, _depth, _what
    me = threading.get_ident()
    with _state:
        if _owner is not None and _owner != me:
            raise Busy(_what)
        if _owner is None:
            _owner, _what = me, what
        _depth += 1
    try:
        yield
    finally:
        with _state:
            _depth -= 1
            if _depth == 0:
                _owner, _what = None, ""


def running() -> str:
    """What holds the lock, or "" when nothing does."""
    with _state:
        return _what if _owner is not None else ""
