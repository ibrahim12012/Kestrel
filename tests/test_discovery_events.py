# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_discovery_events.py
=================================
Tests for ai/discovery/events.py - the pub-sub app/mapping_monitor.py's
GUI window uses to follow a mapping run without ai/discovery importing
PyQt6.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.discovery.events as events


def _clear():
    events._started_callbacks.clear()
    events._progress_callbacks.clear()
    events._finished_callbacks.clear()


def test_started_callback_is_notified():
    calls = []
    events.on_started(lambda app_name: calls.append(app_name))
    try:
        events.notify_started("Notepad")
    finally:
        _clear()
    assert calls == ["Notepad"]


def test_progress_callback_is_notified():
    calls = []
    events.on_progress(lambda nodes, actions: calls.append((nodes, actions)))
    try:
        events.notify_progress(3, 7)
    finally:
        _clear()
    assert calls == [(3, 7)]


def test_finished_callback_is_notified():
    calls = []
    events.on_finished(lambda app_name, nodes, actions: calls.append((app_name, nodes, actions)))
    try:
        events.notify_finished("Notepad", 5, 12)
    finally:
        _clear()
    assert calls == [("Notepad", 5, 12)]


def test_callbacks_are_idempotent_on_registration():
    calls = []
    cb = lambda app_name: calls.append(app_name)
    events.on_started(cb)
    events.on_started(cb)
    try:
        events.notify_started("Notepad")
    finally:
        _clear()
    assert calls == ["Notepad"]


def test_remove_callback_stops_notifications():
    calls = []
    cb = lambda app_name: calls.append(app_name)
    events.on_started(cb)
    events.remove_started(cb)
    events.notify_started("Notepad")
    assert calls == []


def test_a_broken_callback_does_not_break_others():
    calls = []
    events.on_finished(lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    events.on_finished(lambda app_name, nodes, actions: calls.append(app_name))
    try:
        events.notify_finished("Notepad", 1, 1)
    finally:
        _clear()
    assert calls == ["Notepad"]


def test_the_last_run_is_kept_for_a_page_that_opens_later():
    """The Mapping page is built the first time it's opened, which can be
    in the middle of a run started from chat, or after one ended."""
    events.notify_started("Notepad")
    events.notify_node({"id": "s1", "actions": []})
    events.notify_node({"id": "s2", "parent": "s1", "via": "a", "actions": []})
    events.notify_edge("s2", "b", "s1")
    events.notify_progress(2, 5)
    record = events.last_run()
    assert record["app"] == "Notepad" and record["running"] is True
    assert [n["id"] for n in record["nodes"]] == ["s1", "s2"]
    assert record["edges"] == [("s2", "b", "s1")]
    assert record["progress"] == (2, 5)

    events.notify_finished("Notepad", 2, 5)
    assert events.last_run()["running"] is False

    # A copy: changing it changes nothing in the module.
    record["nodes"].clear()
    assert len(events.last_run()["nodes"]) == 2


def test_the_record_says_when_the_run_started_and_finished(monkeypatch):
    clock = [50.0]
    monkeypatch.setattr(events.time, "monotonic", lambda: clock[0])
    events.notify_started("Notepad")
    assert events.last_run()["started_at"] == 50.0
    assert events.last_run()["finished_at"] is None
    clock[0] = 80.0
    events.notify_finished("Notepad", 0, 0)
    assert events.last_run()["finished_at"] == 80.0
    clock[0] = 90.0
    events.notify_started("Notepad")
    assert events.last_run()["started_at"] == 90.0
    assert events.last_run()["finished_at"] is None


def test_is_running_follows_the_run():
    events.notify_started("Notepad")
    assert events.is_running() is True
    events.notify_finished("Notepad", 0, 0)
    assert events.is_running() is False


def test_subscribe_registers_and_returns_what_was_missed():
    events.notify_started("Notepad")
    events.notify_node({"id": "s1", "actions": []})
    seen = []

    def on_node(info):
        seen.append(info["id"])

    try:
        record = events.subscribe(node=on_node)
        assert [n["id"] for n in record["nodes"]] == ["s1"]
        events.notify_node({"id": "s2", "actions": []})
        assert seen == ["s2"]
    finally:
        events.remove_node(on_node)
        events.notify_finished("Notepad", 2, 0)


def test_subscribing_during_a_run_misses_nothing_and_repeats_nothing():
    """What the page draws from the record plus what reaches it live is
    the run, exactly, wherever the subscription falls in it."""
    import threading
    total = 20000
    events.notify_started("Notepad")
    under_way = threading.Event()

    def run():
        for i in range(total):
            events.notify_node({"id": f"s{i}", "actions": []})
            if i == 500:
                under_way.set()

    worker = threading.Thread(target=run)
    worker.start()
    seen = []

    def on_node(info):
        seen.append(info["id"])

    try:
        under_way.wait()
        record = events.subscribe(node=on_node)
        worker.join()
        assert [n["id"] for n in record["nodes"]] + seen == [f"s{i}" for i in range(total)]
    finally:
        events.remove_node(on_node)
        events.notify_finished("Notepad", total, 0)


def test_a_new_run_starts_a_new_record():
    events.notify_started("Notepad")
    events.notify_node({"id": "s1", "actions": []})
    events.notify_finished("Notepad", 1, 0)
    events.notify_started("Calculator")
    record = events.last_run()
    assert record["app"] == "Calculator" and record["nodes"] == [] and record["edges"] == []
    assert record["progress"] == (0, 0)
