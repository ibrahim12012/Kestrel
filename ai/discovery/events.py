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

import threading
import time

_started_callbacks: list = []
_progress_callbacks: list = []
_finished_callbacks: list = []

# ── The last run, for a page that opens after it started ─────────────
# The Mapping page is built the first time someone opens it, which can be
# halfway through a run started from chat, or after one has ended. It
# subscribes and gets what it missed in one step (subscribe()), then
# follows the rest live, the way the overlay does.

_record_lock = threading.Lock()
_last_run: dict = {"app": "", "running": False, "nodes": [], "edges": [], "progress": (0, 0),
                   "started_at": None, "finished_at": None}


def _copy_of_last_run() -> dict:
    return {
        "app": _last_run["app"],
        "running": _last_run["running"],
        "nodes": list(_last_run["nodes"]),
        "edges": list(_last_run["edges"]),
        "progress": _last_run["progress"],
        "started_at": _last_run.get("started_at"),
        "finished_at": _last_run.get("finished_at"),
    }


def last_run() -> dict:
    """A copy of what the most recent run has sent so far: app, running,
    nodes (node_event dicts), edges ((from, action, to) tuples), progress
    ((screens, controls)), and when it started and finished
    (time.monotonic(), None until then)."""
    with _record_lock:
        return _copy_of_last_run()


def is_running() -> bool:
    """Whether a mapping run is going right now."""
    with _record_lock:
        return bool(_last_run["running"])


def subscribe(started=None, progress=None, finished=None, node=None, edge=None) -> dict:
    """
    Register any of the five callbacks and return last_run(), as one step.

    Every event is then either in the returned record or passed to the
    callbacks, never both and never neither. Registering one by one and
    reading last_run() after could lose an event: a run starting in
    between would clear a map the reader had just drawn, and the screens
    it had drawn would not come again.
    """
    with _record_lock:
        for callback, callbacks in (
            (started, _started_callbacks), (progress, _progress_callbacks),
            (finished, _finished_callbacks), (node, _node_callbacks), (edge, _edge_callbacks),
        ):
            if callback is not None and callback not in callbacks:
                callbacks.append(callback)
        return _copy_of_last_run()


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
    with _record_lock:
        _last_run.update(app=app_name, running=True, nodes=[], edges=[], progress=(0, 0),
                         started_at=time.monotonic(), finished_at=None)
        callbacks = list(_started_callbacks)
    for cb in callbacks:
        try:
            cb(app_name)
        except Exception as e:
            log_debug(f"[events] notify_started ignored: {e}")


def notify_progress(nodes: int, actions: int) -> None:
    with _record_lock:
        _last_run["progress"] = (nodes, actions)
        callbacks = list(_progress_callbacks)
    for cb in callbacks:
        try:
            cb(nodes, actions)
        except Exception as e:
            log_debug(f"[events] notify_progress ignored: {e}")


def notify_finished(app_name: str, nodes: int, actions: int) -> None:
    with _record_lock:
        _last_run.update(app=app_name, running=False, progress=(nodes, actions),
                         finished_at=time.monotonic())
        callbacks = list(_finished_callbacks)
    for cb in callbacks:
        try:
            cb(app_name, nodes, actions)
        except Exception as e:
            log_debug(f"[events] notify_finished ignored: {e}")


# ── Screens and links, for drawing the map as it is made ─────────────
# notify_node(info): a new screen, with its controls (see
# ai.discovery.tree.node_event for the shape). notify_edge(from, action,
# to): an action that leads to a screen already mapped.

_node_callbacks: list = []
_edge_callbacks: list = []


def on_node(callback) -> None:
    if callback not in _node_callbacks:
        _node_callbacks.append(callback)


def on_edge(callback) -> None:
    if callback not in _edge_callbacks:
        _edge_callbacks.append(callback)


def remove_node(callback) -> None:
    if callback in _node_callbacks:
        _node_callbacks.remove(callback)


def remove_edge(callback) -> None:
    if callback in _edge_callbacks:
        _edge_callbacks.remove(callback)


def notify_node(info: dict) -> None:
    with _record_lock:
        _last_run["nodes"].append(info)
        callbacks = list(_node_callbacks)
    for cb in callbacks:
        try:
            cb(info)
        except Exception as e:
            log_debug(f"[events] notify_node ignored: {e}")


def notify_edge(from_node: str, action_id: str, to_node: str) -> None:
    with _record_lock:
        _last_run["edges"].append((from_node, action_id, to_node))
        callbacks = list(_edge_callbacks)
    for cb in callbacks:
        try:
            cb(from_node, action_id, to_node)
        except Exception as e:
            log_debug(f"[events] notify_edge ignored: {e}")
