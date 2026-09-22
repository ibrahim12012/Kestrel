# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_mapping_monitor.py
================================
Tests for app/mapping_monitor.py - the small status window shown while
a mapping run (tool_discover_application_ui) is active. Drives it via
ai.discovery.events, the same pub-sub the real tool uses, rather than
calling its internal _on_* handlers directly - that's what actually
proves the registration/thread-hand-off wiring works, not just that the
handlers themselves have correct logic.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ai.discovery.events as discovery_events
from app.mapping_monitor import MappingMonitor


def test_started_event_shows_the_window_and_sets_app_label(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        assert monitor.isVisible() is True
        assert "Notepad" in monitor._app_label.text()
    finally:
        monitor._unregister_events()


def test_progress_event_updates_stats_and_adds_a_list_entry(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        discovery_events.notify_progress(3, 7)
        assert "3 screen" in monitor._stats_label.text()
        assert "7 action" in monitor._stats_label.text()
        assert monitor._tree.count() == 1
    finally:
        monitor._unregister_events()


def test_finished_event_hides_the_window(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        assert monitor.isVisible() is True
        discovery_events.notify_finished("Notepad", 5, 12)
        assert monitor.isVisible() is False
    finally:
        monitor._unregister_events()


def test_started_resets_state_from_a_previous_run(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        discovery_events.notify_progress(3, 7)
        discovery_events.notify_finished("Notepad", 3, 7)

        discovery_events.notify_started("Calculator")
        assert "Calculator" in monitor._app_label.text()
        assert "0 screen" in monitor._stats_label.text()
        assert monitor._tree.count() == 0
    finally:
        monitor._unregister_events()


def test_unregister_events_deregisters_all_callbacks(qapp):
    before_started = list(discovery_events._started_callbacks)
    before_progress = list(discovery_events._progress_callbacks)
    before_finished = list(discovery_events._finished_callbacks)

    monitor = MappingMonitor()
    assert len(discovery_events._started_callbacks) == len(before_started) + 1
    assert len(discovery_events._progress_callbacks) == len(before_progress) + 1
    assert len(discovery_events._finished_callbacks) == len(before_finished) + 1

    monitor._unregister_events()
    assert discovery_events._started_callbacks == before_started
    assert discovery_events._progress_callbacks == before_progress
    assert discovery_events._finished_callbacks == before_finished


def test_events_after_close_do_not_touch_a_dead_widget(qapp):
    """Regression guard: a leftover registration after cleanup would try to update a closed/deleted widget on the next mapping run."""
    monitor = MappingMonitor()
    monitor._unregister_events()
    discovery_events.notify_started("Notepad")  # must not raise
