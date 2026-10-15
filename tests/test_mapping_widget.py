# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_mapping_widget.py
===============================
Tests for app/mapping_widget.py - the dedicated page for triggering and
watching a structural UI mapping run directly from the app, without
going through chat.

Every test that constructs a MappingWidget explicitly calls
widget._unregister_events() in a finally block, rather than relying on
.close() to trigger closeEvent - that isn't reliably delivered to a
widget that was never actually shown (especially under the offscreen Qt
platform tests run under), and a stale registration left pointing at a
since-garbage-collected widget segfaults the whole test process the next
time notify_started/_progress/_finished fires from a later test.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import ai.discovery.events as discovery_events
from app.mapping_widget import MappingWidget


@pytest.fixture(autouse=True)
def no_earlier_run(monkeypatch):
    """A page shows the last run it finds (ai.discovery.events.last_run),
    and other tests leave runs behind that never finished."""
    monkeypatch.setattr(discovery_events, "_last_run",
                        {"app": "", "running": False, "nodes": [], "edges": [], "progress": (0, 0),
                         "started_at": None, "finished_at": None})


def test_start_button_disabled_while_empty_input(qapp):
    widget = MappingWidget()
    try:
        widget._app_input.setText("")
        widget._on_start_clicked()
        assert widget._running is False
        assert "Enter an application name" in widget._status_label.text()
    finally:
        widget._unregister_events()


_ALL_CALLBACK_LISTS = ("_started_callbacks", "_progress_callbacks", "_finished_callbacks",
                       "_node_callbacks", "_edge_callbacks")


def _node(node_id, parent=None, via=None, actions=()):
    return {
        "id": node_id, "title": "", "parent": parent, "via": via,
        "actions": [
            {"id": a[0], "name": a[1], "type": "Button", "box": {"x": 10, "y": 10, "w": 40, "h": 20},
             "destructive": False, "input": False}
            for a in actions
        ],
    }


def test_registers_events_callbacks_on_construction(qapp):
    before = {name: list(getattr(discovery_events, name)) for name in _ALL_CALLBACK_LISTS}

    widget = MappingWidget()
    try:
        for name, callbacks in before.items():
            assert len(getattr(discovery_events, name)) == len(callbacks) + 1, name
    finally:
        widget._unregister_events()


def test_unregister_events_actually_removes_the_callbacks(qapp):
    before = {name: list(getattr(discovery_events, name)) for name in _ALL_CALLBACK_LISTS}
    widget = MappingWidget()
    widget._unregister_events()
    for name, callbacks in before.items():
        assert getattr(discovery_events, name) == callbacks, name


def test_started_event_updates_status_and_resets_state(qapp):
    widget = MappingWidget()
    try:
        discovery_events.notify_started("Notepad")
        assert "Notepad" in widget._status_label.text()
        assert "0 screens" in widget._stats_label.text()
        assert widget._tree.topLevelItemCount() == 0
        # Any run can be stopped from here, whoever started it.
        assert widget._btn_stop.isVisibleTo(widget) is True
        assert widget._btn_start.isEnabled() is False
    finally:
        discovery_events.notify_finished("Notepad", 0, 0)
        widget._unregister_events()


def test_progress_event_updates_stats(qapp):
    widget = MappingWidget()
    try:
        discovery_events.notify_started("Notepad")
        discovery_events.notify_progress(3, 7)
        assert "3 screens" in widget._stats_label.text()
        assert "7 controls" in widget._stats_label.text()
    finally:
        discovery_events.notify_finished("Notepad", 3, 7)
        widget._unregister_events()


def test_the_page_shows_the_same_tree_as_the_overlay(qapp):
    widget = MappingWidget()
    try:
        discovery_events.notify_started("Notepad")
        discovery_events.notify_node(_node("s1", actions=[("a_file", "File")]))
        discovery_events.notify_node(_node("s2", parent="s1", via="a_file", actions=[("a_save", "Save")]))
        root = widget._tree.topLevelItem(0)
        file_item = root.child(0)
        assert file_item.text(0).startswith("File")
        assert "Screen 2" in file_item.child(0).text(0)
        assert "(30, 20)" in file_item.toolTip(0)      # the centre of its box
        discovery_events.notify_finished("Notepad", 2, 2)
        assert widget._btn_stop.isVisibleTo(widget) is False
        assert widget._btn_start.isEnabled() is True
        assert "Notepad" in widget._status_label.text()
    finally:
        widget._unregister_events()


def test_a_page_opened_after_a_run_shows_its_map(qapp):
    discovery_events.notify_started("Notepad")
    discovery_events.notify_node(_node("s1", actions=[("a_file", "File")]))
    discovery_events.notify_node(_node("s2", parent="s1", via="a_file"))
    discovery_events.notify_progress(2, 1)
    discovery_events.notify_finished("Notepad", 2, 1)
    widget = MappingWidget()
    try:
        assert widget._tree.screen_count() == 2
        assert "2 screens" in widget._stats_label.text()
        assert "Notepad" in widget._status_label.text()
    finally:
        widget._unregister_events()


def test_the_clock_counts_from_when_the_run_started(qapp, monkeypatch):
    """Also for a page opened halfway through a run, and it keeps the
    run's length once it ends."""
    import time
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])     # the record's clock and the page's
    discovery_events.notify_started("Notepad")
    clock[0] = 1075.0
    widget = MappingWidget()
    try:
        assert widget._time_label.text() == "01:15"
        clock[0] = 1090.0
        discovery_events.notify_finished("Notepad", 0, 0)
        clock[0] = 1500.0
        widget._tick()
        assert widget._time_label.text() == "01:30"
    finally:
        widget._unregister_events()


def test_the_overlay_switch_is_saved_and_passed_on(qapp, monkeypatch):
    import ai.main as ai_main
    saved = []
    monkeypatch.setattr(ai_main, "set_show_mapping_overlay", lambda on: saved.append(on))
    widget = MappingWidget()
    heard = []
    widget.overlayToggled.connect(lambda on: heard.append(on))
    try:
        widget._overlay_switch.setChecked(True)
        widget._overlay_switch.setChecked(False)
        assert saved[-1] is False and heard[-1] is False
    finally:
        widget._unregister_events()


def test_stop_sets_the_shared_stop_flag(qapp, monkeypatch):
    import ai.main as ai_main
    stopped = []
    monkeypatch.setattr(ai_main, "stop_automation_now", lambda: stopped.append(True))
    widget = MappingWidget()
    try:
        discovery_events.notify_started("Notepad")
        widget._on_stop_clicked()
        assert stopped == [True]
        assert widget._btn_stop.isEnabled() is False
    finally:
        discovery_events.notify_finished("Notepad", 0, 0)
        widget._unregister_events()


def test_worker_finished_reenables_the_start_button(qapp):
    widget = MappingWidget()
    try:
        widget._running = True
        widget._btn_start.setEnabled(False)
        widget._app_input.setEnabled(False)

        from ai.actions import MappingResult
        widget._on_worker_finished(MappingResult(app="Notepad", ok=True, ending="complete",
                                                 screens=3, controls=7))

        assert widget._running is False
        assert widget._btn_start.isEnabled() is True
        assert widget._app_input.isEnabled() is True
        assert widget._status_label.text() == "Mapped Notepad: 3 screens, 7 controls."
        assert not widget._status_label.property("error")
    finally:
        widget._unregister_events()


def test_the_status_line_says_how_a_run_ended():
    from ai.actions import MappingResult
    from app.mapping_widget import describe_result

    assert describe_result(MappingResult(app="Notepad", error="No open window matches 'x'.")) == (
        "Couldn't map Notepad: No open window matches 'x'.", True)
    assert describe_result(MappingResult(app="Notepad", ok=True, ending="stopped", screens=4, controls=9))[0] == (
        "Stopped. 4 screens and 9 controls were mapped and saved.")
    assert describe_result(MappingResult(app="Notepad", ok=True, kept_earlier=True, ending="stopped",
                                         screens=2))[0] == (
        "Stopped after 2 screens. The earlier full map of Notepad is kept.")
    assert "most screens" in describe_result(MappingResult(app="N", ok=True, ending="screen_limit"))[0]
    assert "most clicks" in describe_result(MappingResult(app="N", ok=True, ending="action_limit"))[0]


def test_start_is_refused_while_another_automation_runs(qapp):
    from ai import automation_lock
    import ai.actions as actions
    import threading

    held, release = threading.Event(), threading.Event()

    def macro():
        with automation_lock.hold("a macro"):
            held.set()
            release.wait(5)

    thread = threading.Thread(target=macro)
    thread.start()
    held.wait(5)
    actions.emergency_stop = True
    widget = MappingWidget()
    try:
        widget._app_input.setText("Notepad")
        widget._on_start_clicked()
        assert widget._running is False
        assert "Another automation is running (a macro)" in widget._status_label.text()
        assert widget._status_label.property("error") is True
        # A stop pressed for that macro stays pressed.
        assert actions.emergency_stop is True
    finally:
        release.set()
        thread.join(5)
        actions.emergency_stop = False
        widget._unregister_events()


def test_worker_error_reenables_the_start_button(qapp):
    widget = MappingWidget()
    try:
        widget._running = True
        widget._btn_start.setEnabled(False)
        widget._app_input.setEnabled(False)

        widget._on_worker_error("no window found")

        assert widget._running is False
        assert widget._btn_start.isEnabled() is True
        assert "Mapping failed" in widget._status_label.text()
        assert "no window found" in widget._status_label.text()
    finally:
        widget._unregister_events()


def test_start_clicked_marks_running_and_disables_input(qapp, monkeypatch):
    """
    Verifies the button/input state transition directly rather than
    actually running a real QThread - starting one in a test and
    stopping it again (even with .quit()/.wait()) proved fragile when
    combined with the rest of the suite in one process, occasionally
    crashing unrelated later tests. Stubs out QThread.start() itself
    (a real QThread object is still required - moveToThread() type-checks
    it at the C++ level) so nothing actually launches, while still
    letting the widget's own state-transition logic run for real.
    """
    from PyQt6.QtCore import QThread as RealQThread
    widget = MappingWidget()
    try:
        widget._app_input.setText("Notepad")
        monkeypatch.setattr(RealQThread, "start", lambda self: None)

        widget._on_start_clicked()

        assert widget._running is True
        assert widget._btn_start.isEnabled() is False
        assert widget._app_input.isEnabled() is False
        assert widget._mapping_thread is not None
    finally:
        widget._unregister_events()


def test_start_clicked_is_a_no_op_while_already_running(qapp, monkeypatch):
    widget = MappingWidget()
    try:
        widget._app_input.setText("Notepad")
        widget._running = True
        monkeypatch.setattr(widget, "_mapping_thread", None)

        widget._on_start_clicked()

        # still marked running, no new thread stashed on the widget from this call
        assert widget._running is True
    finally:
        widget._unregister_events()
