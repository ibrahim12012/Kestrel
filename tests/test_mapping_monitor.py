# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_mapping_monitor.py
================================
Tests for app/mapping_monitor.py - the panel shown while a mapping run
(tool_discover_application_ui) is active, which draws the map as a tree.
Drives it via ai.discovery.events, the same pub-sub the real tool uses,
rather than calling its internal _on_* handlers directly - that's what
actually proves the registration/thread-hand-off wiring works, not just
that the handlers themselves have correct logic.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import ai.discovery.events as discovery_events
from app.mapping_monitor import MappingMonitor


@pytest.fixture(autouse=True)
def overlay_switched_on(monkeypatch):
    """The Mapping page's switch is saved in .env, and a source checkout
    where someone turned it off would hide the panel in every test."""
    monkeypatch.setenv("KESTREL_SHOW_MAPPING_OVERLAY", "1")


def _node(node_id, title="", parent=None, via=None, actions=()):
    return {
        "id": node_id, "title": title, "parent": parent, "via": via,
        "actions": [
            {"id": a[0], "name": a[1], "type": a[2], "box": {"x": a[3], "y": a[4], "w": 40, "h": 20},
             "destructive": False, "input": False}
            for a in actions
        ],
    }


def test_started_event_shows_the_window_and_sets_app_label(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        assert monitor.isVisible() is True
        assert "Notepad" in monitor._app_label.text()
    finally:
        monitor._unregister_events()


def test_progress_event_updates_stats(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        discovery_events.notify_progress(3, 7)
        assert "3 screens" in monitor._stats_label.text()
        assert "7 controls" in monitor._stats_label.text()
    finally:
        monitor._unregister_events()


def test_screens_nest_under_the_control_that_opens_them(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        discovery_events.notify_node(_node("s1", "Untitled - Notepad", actions=[("a_file", "File", "MenuItem", 10, 5)]))
        discovery_events.notify_node(_node("s2", "File menu", parent="s1", via="a_file",
                                           actions=[("a_save", "Save", "MenuItem", 20, 40)]))

        assert monitor._tree.topLevelItemCount() == 1
        root = monitor._tree.topLevelItem(0)
        assert "Untitled - Notepad" in root.text(0)
        file_item = root.child(0)
        assert file_item.text(0).startswith("File")
        # Screen 2 hangs off the "File" control, not off the root screen.
        assert "Screen 2" in file_item.child(0).text(0)
    finally:
        monitor._unregister_events()


def test_a_control_tooltip_has_its_name_and_coordinates(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        discovery_events.notify_node(_node("s1", actions=[("a_save", "Save", "Button", 100, 200)]))
        tip = monitor._tree.topLevelItem(0).child(0).toolTip(0)
        assert "Save" in tip and "Button" in tip
        assert "(120, 210)" in tip       # the centre: x + w/2, y + h/2
    finally:
        monitor._unregister_events()


def test_a_link_to_a_known_screen_is_noted_under_the_control(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        discovery_events.notify_node(_node("s1", actions=[("a_back", "Back", "Button", 0, 0)]))
        discovery_events.notify_node(_node("s2", parent="s1", via="a_back", actions=[("a_home", "Home", "Button", 0, 0)]))
        discovery_events.notify_edge("s2", "a_home", "s1")
        s2 = monitor._tree.screen_item("s2")
        home = s2.child(0)
        assert "Screen 1" in home.child(0).text(0)
    finally:
        monitor._unregister_events()


def test_finished_without_a_map_hides_the_window(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        assert monitor.isVisible() is True
        discovery_events.notify_finished("Notepad", 0, 0)
        assert monitor.isVisible() is False
    finally:
        monitor._unregister_events()


def test_finished_with_a_map_stays_open_and_becomes_hoverable(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        discovery_events.notify_node(_node("s1", actions=[("a", "OK", "Button", 0, 0)]))
        discovery_events.notify_finished("Notepad", 1, 1)
        assert monitor.isVisible() is True
        assert monitor._interactive is True
        assert "Notepad" in monitor._title.text()
    finally:
        monitor._unregister_events()


def test_started_resets_state_from_a_previous_run(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        discovery_events.notify_node(_node("s1", actions=[("a", "OK", "Button", 0, 0)]))
        discovery_events.notify_progress(3, 7)
        discovery_events.notify_finished("Notepad", 3, 7)

        discovery_events.notify_started("Calculator")
        assert "Calculator" in monitor._app_label.text()
        assert "0 screens" in monitor._stats_label.text()
        assert monitor._tree.topLevelItemCount() == 0
        assert monitor._interactive is False
    finally:
        monitor._unregister_events()


def test_unregister_events_deregisters_all_callbacks(qapp):
    before = {
        name: list(getattr(discovery_events, name))
        for name in ("_started_callbacks", "_progress_callbacks", "_finished_callbacks",
                     "_node_callbacks", "_edge_callbacks")
    }

    monitor = MappingMonitor()
    for name, callbacks in before.items():
        assert len(getattr(discovery_events, name)) == len(callbacks) + 1

    monitor._unregister_events()
    for name, callbacks in before.items():
        assert getattr(discovery_events, name) == callbacks


def test_events_after_close_do_not_touch_a_dead_widget(qapp):
    """Regression guard: a leftover registration after cleanup would try to update a closed/deleted widget on the next mapping run."""
    monitor = MappingMonitor()
    monitor._unregister_events()
    discovery_events.notify_started("Notepad")  # must not raise
    discovery_events.notify_node(_node("s1"))  # must not raise


def test_the_panel_can_be_turned_off_and_back_on(qapp):
    """From the Mapping page's switch: off hides it now and for the next
    run, on brings it back with the map."""
    monitor = MappingMonitor()
    try:
        monitor.set_user_visible(True)
        discovery_events.notify_started("Notepad")
        discovery_events.notify_node(_node("s1", actions=[("a", "OK", "Button", 0, 0)]))
        assert monitor.isVisible() is True

        monitor.set_user_visible(False)
        assert monitor.isVisible() is False
        discovery_events.notify_finished("Notepad", 1, 1)
        discovery_events.notify_started("Notepad")
        assert monitor.isVisible() is False      # still off for the next run
        discovery_events.notify_node(_node("s1", actions=[("a", "OK", "Button", 0, 0)]))

        monitor.set_user_visible(True)
        assert monitor.isVisible() is True
        assert monitor._tree.screen_count() == 1  # it kept following the run
    finally:
        discovery_events.notify_finished("Notepad", 1, 1)
        monitor._unregister_events()


def test_switching_it_back_on_after_a_run_does_not_bring_back_a_closed_map(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        discovery_events.notify_node(_node("s1", actions=[("a", "OK", "Button", 0, 0)]))
        discovery_events.notify_finished("Notepad", 1, 1)
        monitor._close_btn.click()
        assert monitor.isVisible() is False
        monitor.set_user_visible(False)
        monitor.set_user_visible(True)
        assert monitor.isVisible() is False
    finally:
        monitor._unregister_events()


def test_while_running_the_hint_says_where_to_stop_it(qapp):
    monitor = MappingMonitor()
    try:
        discovery_events.notify_started("Notepad")
        assert "Mapping page" in monitor._hint.text()
        discovery_events.notify_node(_node("s1", actions=[("a", "OK", "Button", 0, 0)]))
        discovery_events.notify_finished("Notepad", 1, 1)
        assert "Hover a control" in monitor._hint.text()
    finally:
        monitor._unregister_events()
