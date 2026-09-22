# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/risk_gate.py
================
Code-enforced confirmation for HIGH-risk actions (see ai.safety.RiskLevel).

Before this module existed, the only thing telling the model to check
with a human before running something destructive was a sentence in
tool_run_command's own docstring ("always describe what you're about to
run and get a ... confirmation first if it deletes, overwrites, or
uninstalls anything"). That's an instruction the model can follow - and
usually does - but it isn't a guarantee, and a commercial release
shouldn't rely on a docstring for its liability story. This makes the
check happen in code, unconditionally, for every HIGH-risk call,
regardless of what the model did or didn't say first.

How it blocks safely across threads: tool_run_command/tool_close_application
always run on a background worker thread (AIWorker or AutonomousWorker's
QThread - see app/workers/), never the UI thread. require_approval()
emits a signal connected with Qt.ConnectionType.BlockingQueuedConnection,
which blocks the CALLING (background) thread until the slot - running on
the UI thread, showing an actual dialog - returns. This is the same
primitive Qt itself recommends for "ask the GUI thread something and
wait for the answer" from a worker thread.

Fails CLOSED: if nothing has connected a handler (no MainWindow yet,
running headless, or in a test), require_approval() returns False -
denies the action - rather than assuming approval. A missing UI is not
consent.
"""

from PyQt6.QtCore import QObject, pyqtSignal, Qt


class _Bridge(QObject):
    # (description, out) - `out` is a mutable 1-item list used as an
    # out-parameter: the connected slot must set out[0] = True/False
    # before returning, since a normal return value can't come back
    # through a signal.
    approval_needed = pyqtSignal(str, list)


_bridge = _Bridge()
_handler_connected = False


def connect_handler(slot) -> None:
    """
    Call once, from the UI thread, after MainWindow exists - see
    MainWindow.__init__. `slot(description: str, out: list)` must show
    a blocking confirmation to the user and set out[0] before returning.
    """
    global _handler_connected
    _bridge.approval_needed.connect(slot, Qt.ConnectionType.BlockingQueuedConnection)
    _handler_connected = True


def require_approval(description: str) -> bool:
    """
    Blocks the calling thread until a human approves or denies
    `description`. Safe to call from any thread. Returns False
    (denied) if no handler is connected - see module docstring.
    """
    if not _handler_connected:
        return False
    out = [False]
    _bridge.approval_needed.emit(description, out)
    return bool(out[0])
