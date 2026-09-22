# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

from ai.log import log_debug
"""
ai/discovery/events.py
========================
Lightweight pub-sub for mapping-run lifecycle events - same pattern as
ai.log's add_sink/remove_sink (see that module's docstring for the full
rationale). Lets app/mapping_monitor.py's GUI window follow a mapping
run in real time without ai/discovery importing PyQt6, and without
tool_discover_application_ui (ai/actions.py) needing to know a GUI even
exists.

Callbacks run synchronously, on whatever thread the mapping run is
executing on - in practice never the GUI thread, since
tool_discover_application_ui runs inside the AI's tool-calling loop. A
callback that touches Qt widgets must marshal to the GUI thread itself
via a signal, exactly like ai.log.add_sink's callers already do (see
app/mapping_monitor.py).
"""

_started_callbacks: list = []
_progress_callbacks: list = []
_finished_callbacks: list = []


def on_started(callback) -> None:
    if callback not in _started_callbacks:
        _started_callbacks.append(callback)


def on_progress(callback) -> None:
    if callback not in _progress_callbacks:
        _progress_callbacks.append(callback)


def on_finished(callback) -> None:
    if callback not in _finished_callbacks:
        _finished_callbacks.append(callback)


def remove_started(callback) -> None:
    if callback in _started_callbacks:
        _started_callbacks.remove(callback)


def remove_progress(callback) -> None:
    if callback in _progress_callbacks:
        _progress_callbacks.remove(callback)


def remove_finished(callback) -> None:
    if callback in _finished_callbacks:
        _finished_callbacks.remove(callback)


def notify_started(app_name: str) -> None:
    for cb in list(_started_callbacks):
        try:
            cb(app_name)
        except Exception as e:
            log_debug(f"[events] notify_started ignored: {e}")


def notify_progress(nodes: int, actions: int) -> None:
    for cb in list(_progress_callbacks):
        try:
            cb(nodes, actions)
        except Exception as e:
            log_debug(f"[events] notify_progress ignored: {e}")


def notify_finished(app_name: str, nodes: int, actions: int) -> None:
    for cb in list(_finished_callbacks):
        try:
            cb(app_name, nodes, actions)
        except Exception as e:
            log_debug(f"[events] notify_finished ignored: {e}")
