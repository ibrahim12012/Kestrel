# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_recorder_worker.py
===============================
Tests for app/workers/recorder_worker.py's RecorderController - the
trigger-matching and typing-mode state machine, using fake events
instead of the real OS-level keyboard/mouse hooks (see that module's
docstring for why those can't be exercised end-to-end here).
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.actions as actions
import ai.main as ai_main
from app.workers.recorder_worker import RecorderController
import keyboard
import mouse


def _key_event(name: str):
    return SimpleNamespace(event_type=keyboard.KEY_DOWN, name=name)


def _key_up_event(name: str):
    return SimpleNamespace(event_type=keyboard.KEY_UP, name=name)


def _mouse_down_event(button: str):
    return SimpleNamespace(event_type=mouse.DOWN, button=button)


def _mouse_up_event(button: str):
    return SimpleNamespace(event_type=mouse.UP, button=button)


@pytest.fixture
def controller(monkeypatch):
    monkeypatch.setattr(keyboard, "hook", lambda cb: None)
    monkeypatch.setattr(keyboard, "unhook_all", lambda: None)
    monkeypatch.setattr(mouse, "hook", lambda cb: None)
    monkeypatch.setattr(mouse, "unhook_all", lambda: None)
    monkeypatch.setattr("app.workers.recorder_worker.pyautogui.position", lambda: (321, 654))
    actions.actions_list.clear()
    ctrl = RecorderController()
    ctrl.start()
    yield ctrl
    actions.actions_list.clear()


def test_default_triggers_match_the_terminal_recorder(controller):
    assert controller._triggers == {
        "recorder_right_click": "f6",
        "recorder_click": "f7",
        "recorder_toggle_typing": "f8",
        "recorder_enter": "f9",
        "recorder_stop": "f10",
    }


def test_f7_records_a_click_at_current_mouse_position(controller):
    controller._on_key_event(_key_event("f7"))
    assert actions.actions_list == [("click", 321, 654)]


def test_f6_records_a_right_click(controller):
    controller._on_key_event(_key_event("f6"))
    assert actions.actions_list == [("right_click", 321, 654)]


def test_f9_records_enter(controller):
    controller._on_key_event(_key_event("f9"))
    assert actions.actions_list == [("enter", "enter")]


def test_key_up_events_are_ignored(controller):
    controller._on_key_event(_key_up_event("f7"))
    assert actions.actions_list == []


def test_typing_mode_captures_characters_until_toggle_key_again(controller):
    signals = []
    controller.typing_started.connect(lambda: signals.append("started"))
    controller.typing_stopped.connect(lambda text: signals.append(("stopped", text)))

    controller._on_key_event(_key_event("f8"))
    assert controller._typing is True
    assert signals == ["started"]

    for ch in "hi":
        controller._on_key_event(_key_event(ch))
    controller._on_key_event(_key_event("space"))
    for ch in "there":
        controller._on_key_event(_key_event(ch))

    controller._on_key_event(_key_event("f8"))

    assert controller._typing is False
    assert actions.actions_list == [("hi there", "write")]
    assert signals == ["started", ("stopped", "hi there")]


def test_typing_mode_can_be_ended_with_escape(controller):
    controller._on_key_event(_key_event("f8"))
    controller._on_key_event(_key_event("x"))
    controller._on_key_event(_key_event("esc"))
    assert controller._typing is False
    assert actions.actions_list == [("x", "write")]


def test_typing_mode_backspace_edits_the_buffer(controller):
    controller._on_key_event(_key_event("f8"))
    for ch in "abc":
        controller._on_key_event(_key_event(ch))
    controller._on_key_event(_key_event("backspace"))
    controller._on_key_event(_key_event("f8"))
    assert actions.actions_list == [("ab", "write")]


def test_typing_mode_empty_buffer_records_nothing(controller):
    controller._on_key_event(_key_event("f8"))
    controller._on_key_event(_key_event("f8"))  # toggle straight back off, nothing typed
    assert actions.actions_list == []


def test_f10_stops_recording(controller):
    stopped = []
    controller.stopped.connect(lambda: stopped.append(1))
    controller._on_key_event(_key_event("f10"))
    assert controller.is_recording is False
    assert stopped == [1]


def test_unbound_key_does_nothing(controller):
    controller._on_key_event(_key_event("q"))
    assert actions.actions_list == []


# ══════════════════════════════════════════════════════════════════
# Mouse-button triggers
# ══════════════════════════════════════════════════════════════════

def test_mouse_button_can_be_bound_as_a_trigger(monkeypatch, controller):
    controller._triggers["recorder_click"] = "mouse:middle"
    controller._on_mouse_event(_mouse_down_event("middle"))
    assert actions.actions_list == [("click", 321, 654)]


def test_mouse_up_events_are_ignored(controller):
    controller._triggers["recorder_click"] = "mouse:middle"
    controller._on_mouse_event(_mouse_up_event("middle"))
    assert actions.actions_list == []


def test_unbound_mouse_button_does_nothing(controller):
    controller._on_mouse_event(_mouse_down_event("middle"))
    assert actions.actions_list == []


def test_mouse_button_can_toggle_typing_mode(controller):
    controller._triggers["recorder_toggle_typing"] = "mouse:x"
    controller._on_mouse_event(_mouse_down_event("x"))
    assert controller._typing is True

    controller._on_key_event(_key_event("h"))
    controller._on_key_event(_key_event("i"))
    controller._on_mouse_event(_mouse_down_event("x"))

    assert controller._typing is False
    assert actions.actions_list == [("hi", "write")]


def test_other_mouse_clicks_during_typing_mode_are_swallowed_not_buffered(controller):
    controller._on_key_event(_key_event("f8"))
    controller._on_mouse_event(_mouse_down_event("left"))  # not the toggle trigger, not F8/Esc
    controller._on_key_event(_key_event("f8"))
    assert actions.actions_list == []  # nothing got typed - the click didn't add anything


# ══════════════════════════════════════════════════════════════════
# start()/stop() lifecycle
# ══════════════════════════════════════════════════════════════════

def test_start_rereads_triggers_from_settings(monkeypatch):
    monkeypatch.setattr(keyboard, "hook", lambda cb: None)
    monkeypatch.setattr(keyboard, "unhook_all", lambda: None)
    monkeypatch.setattr(mouse, "hook", lambda cb: None)
    monkeypatch.setattr(mouse, "unhook_all", lambda: None)
    monkeypatch.setattr(ai_main, "get_shortcut", lambda sid: "mouse:x2" if sid == "recorder_stop" else ai_main.DEFAULT_SHORTCUTS[sid])

    ctrl = RecorderController()
    ctrl.start()
    try:
        assert ctrl._triggers["recorder_stop"] == "mouse:x2"
    finally:
        ctrl.stop()


def test_start_is_idempotent_while_already_recording(monkeypatch):
    monkeypatch.setattr(keyboard, "hook", lambda cb: None)
    monkeypatch.setattr(keyboard, "unhook_all", lambda: None)
    monkeypatch.setattr(mouse, "hook", lambda cb: None)
    monkeypatch.setattr(mouse, "unhook_all", lambda: None)

    actions.actions_list.clear()
    ctrl = RecorderController()
    ctrl.start()
    actions.actions_list.append(("click", 1, 1))
    ctrl.start()  # must NOT clear actions_list again - already recording
    try:
        assert actions.actions_list == [("click", 1, 1)]
    finally:
        ctrl.stop()
        actions.actions_list.clear()

def test_starting_again_keeps_what_was_already_recorded(monkeypatch):
    """
    Building a macro is rarely one sitting: record a few steps, stop, go
    and work out the rest, come back. start() used to clear the list, so
    that pause silently cost you everything recorded so far.
    """
    monkeypatch.setattr(keyboard, "hook", lambda cb: None)
    monkeypatch.setattr(keyboard, "unhook_all", lambda: None)
    monkeypatch.setattr(mouse, "hook", lambda cb: None)
    monkeypatch.setattr(mouse, "unhook_all", lambda: None)

    actions.actions_list.clear()
    ctrl = RecorderController()
    ctrl.start()
    actions.actions_list.append(("click", 10, 20))
    ctrl.stop()

    ctrl.start()
    assert actions.actions_list == [("click", 10, 20)], "the first session was wiped"

    actions.actions_list.append(("click", 30, 40))
    ctrl.stop()
    assert actions.actions_list == [("click", 10, 20), ("click", 30, 40)]
    actions.actions_list.clear()


def test_clearing_is_what_empties_the_list(monkeypatch):
    """Only an explicit Clear should lose work, and that one is undoable
    from the recorder page."""
    monkeypatch.setattr(keyboard, "hook", lambda cb: None)
    monkeypatch.setattr(keyboard, "unhook_all", lambda: None)
    monkeypatch.setattr(mouse, "hook", lambda cb: None)
    monkeypatch.setattr(mouse, "unhook_all", lambda: None)

    actions.actions_list.clear()
    ctrl = RecorderController()
    ctrl.start()
    actions.actions_list.append(("click", 1, 2))
    ctrl.stop()
    actions.actions_list.clear()          # what the Clear button does
    ctrl.start()
    assert actions.actions_list == []
    ctrl.stop()


# ── stop() must not disarm the rest of the app ───────────────────────────
# RecorderController.stop() used to call keyboard.unhook_all() and
# mouse.unhook_all(). Both libraries document those as removing EVERY hook
# and hotkey registered in the process, which includes the four
# application-wide shortcuts MainWindow registers with keyboard.add_hotkey
# at startup. Stopping a recording silently killed all of them until the
# app was restarted.

def test_stop_removes_only_its_own_hooks(monkeypatch):
    """The fix: stop() takes down the two hooks start() installed, by the
    remover each hook() call handed back, and nothing else."""
    removed = []

    def fake_keyboard_hook(cb):
        return lambda: removed.append("keyboard")

    def fake_mouse_hook(cb):
        return lambda: removed.append("mouse")

    def explode():
        raise AssertionError(
            "unhook_all() clears every global hotkey in the process, "
            "including MainWindow's - stop() must never call it."
        )

    monkeypatch.setattr(keyboard, "hook", fake_keyboard_hook)
    monkeypatch.setattr(mouse, "hook", fake_mouse_hook)
    monkeypatch.setattr(keyboard, "unhook_all", explode)
    monkeypatch.setattr(mouse, "unhook_all", explode)

    ctrl = RecorderController()
    ctrl.start()
    ctrl.stop()

    assert sorted(removed) == ["keyboard", "mouse"]


def test_stop_falls_back_to_unhook_by_callback(monkeypatch):
    """Older versions of either library return None from hook() rather
    than a remover. unhook(callback) is the documented alternative, and
    is what stop() tries next - still never unhook_all()."""
    unhooked = []

    monkeypatch.setattr(keyboard, "hook", lambda cb: None)
    monkeypatch.setattr(mouse, "hook", lambda cb: None)
    monkeypatch.setattr(keyboard, "unhook", lambda cb: unhooked.append(("keyboard", cb)))
    monkeypatch.setattr(mouse, "unhook", lambda cb: unhooked.append(("mouse", cb)))
    monkeypatch.setattr(keyboard, "unhook_all", lambda: unhooked.append("KEYBOARD_ALL"))
    monkeypatch.setattr(mouse, "unhook_all", lambda: unhooked.append("MOUSE_ALL"))

    ctrl = RecorderController()
    ctrl.start()
    ctrl.stop()

    assert [name for name, _ in unhooked] == ["keyboard", "mouse"]
    assert unhooked[0][1] == ctrl._on_key_event
    assert unhooked[1][1] == ctrl._on_mouse_event


def test_stop_is_quiet_when_neither_removal_path_works(monkeypatch):
    """A hook that can't be removed is a stale listener, which is a
    nuisance. Raising out of stop() would leave _recording True and the
    recorder page stuck mid-recording, which is worse."""
    def boom(*a, **k):
        raise RuntimeError("no listener")

    monkeypatch.setattr(keyboard, "hook", lambda cb: boom)
    monkeypatch.setattr(mouse, "hook", lambda cb: boom)
    monkeypatch.setattr(keyboard, "unhook", boom)
    monkeypatch.setattr(mouse, "unhook", boom)

    ctrl = RecorderController()
    ctrl.start()
    ctrl.stop()

    assert ctrl.is_recording is False
