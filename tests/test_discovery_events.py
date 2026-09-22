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
