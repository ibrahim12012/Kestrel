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

import ai.discovery.events as discovery_events
from app.mapping_widget import MappingWidget


def test_start_button_disabled_while_empty_input(qapp):
    widget = MappingWidget()
    try:
        widget._app_input.setText("")
        widget._on_start_clicked()
        assert widget._running is False
        assert "Enter an application name" in widget._status_label.text()
    finally:
        widget._unregister_events()


def test_registers_events_callbacks_on_construction(qapp):
    before_started = list(discovery_events._started_callbacks)
    before_progress = list(discovery_events._progress_callbacks)
    before_finished = list(discovery_events._finished_callbacks)

    widget = MappingWidget()
    try:
        assert len(discovery_events._started_callbacks) == len(before_started) + 1
        assert len(discovery_events._progress_callbacks) == len(before_progress) + 1
        assert len(discovery_events._finished_callbacks) == len(before_finished) + 1
    finally:
        widget._unregister_events()


def test_unregister_events_actually_removes_the_callbacks(qapp):
    before_started = list(discovery_events._started_callbacks)
    widget = MappingWidget()
    widget._unregister_events()
    assert discovery_events._started_callbacks == before_started


def test_started_event_updates_status_and_resets_state(qapp):
    widget = MappingWidget()
    try:
        discovery_events.notify_started("Notepad")
        assert "Notepad" in widget._status_label.text()
        assert "0 screens" in widget._stats_label.text()
        assert widget._screens_list.count() == 0
    finally:
        widget._unregister_events()


def test_progress_event_updates_stats_and_list(qapp):
    widget = MappingWidget()
    try:
        discovery_events.notify_started("Notepad")
        discovery_events.notify_progress(3, 7)
        assert "3 screen" in widget._stats_label.text()
        assert "7 action" in widget._stats_label.text()
        assert widget._screens_list.count() == 1
    finally:
        widget._unregister_events()


def test_worker_finished_reenables_the_start_button(qapp):
    widget = MappingWidget()
    try:
        widget._running = True
        widget._btn_start.setEnabled(False)
        widget._app_input.setEnabled(False)

        widget._on_worker_finished("Mapped Notepad: 3 screen(s), 7 action(s) discovered.")

        assert widget._running is False
        assert widget._btn_start.isEnabled() is True
        assert widget._app_input.isEnabled() is True
        assert "Mapped Notepad" in widget._status_label.text()
    finally:
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
