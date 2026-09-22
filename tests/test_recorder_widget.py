# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_recorder_widget.py
===============================
Tests for the Save-button gating in app/recorder_widget.py - the other
half of the macro-creation-race fix (see
tests/test_main_window_helpers.py's test_reload_macros_* for the
_reload_macros side). Editing or deleting an already-recorded action
used to re-enable Save purely based on "are there any actions", with no
regard for whether a recording was still in progress - letting the user
click Save mid-recording and trigger a macro_saved -> _reload_macros() ->
importlib.reload(actions) race against RecorderController's still-live
keyboard/mouse hooks.

That gate now sits in _on_blocks_changed, which is what the block strip
(app/block_list.py) emits into on every edit - delete, reorder, an
inserted control block, a changed value. These drive it the same way
the UI does: set what the strip holds, then let the widget write it
back to ai.actions.actions_list and re-gate Save.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.actions as actions
import ai.macro_store as macro_store
from app.recorder_widget import RecorderWidget


def _stage(widget, recorded):
    """Puts `recorded` (legacy action tuples) into both the shared
    actions list and the widget's block strip, the state the recorder
    is in right after capturing them."""
    actions.actions_list.clear()
    actions.actions_list.extend(recorded)
    widget._refresh_actions_list()


def _delete_block(widget, index):
    """Removes one block through the strip and lets the widget react - what clicking a block's trash button ends up doing, minus the
    confirmation dialog."""
    steps = widget._blocks.steps()
    del steps[index]
    widget._blocks.set_steps(steps)
    widget._on_blocks_changed()


def test_delete_action_does_not_enable_save_while_still_recording(qapp):
    widget = RecorderWidget()
    _stage(widget, [("click", 10, 20), ("hello", "write")])
    widget._controller._recording = True   # simulate an active recording

    _delete_block(widget, 0)

    assert len(actions.actions_list) == 1   # the delete itself still happened
    assert widget._btn_save.isEnabled() is False   # but Save stays off - still recording


def test_delete_action_enables_save_once_recording_has_stopped(qapp):
    widget = RecorderWidget()
    _stage(widget, [("click", 10, 20), ("hello", "write")])
    widget._controller._recording = False  # recording already stopped

    _delete_block(widget, 0)

    assert len(actions.actions_list) == 1
    assert widget._btn_save.isEnabled() is True


def test_delete_last_action_disables_save_regardless_of_recording_state(qapp):
    widget = RecorderWidget()
    _stage(widget, [("click", 10, 20)])
    widget._controller._recording = False

    _delete_block(widget, 0)

    assert actions.actions_list == []
    assert widget._btn_save.isEnabled() is False


# ── Blocks on the recorder page ───────────────────────────────────────
# The point of the block strip living here: control blocks get built
# around the clicks as they're recorded, and the mixed list that
# produces has to survive the round trip into ai.actions.actions_list
# (which is what tool_name_action saves and play_actions replays).

def test_recorded_actions_show_up_as_blocks(qapp):
    widget = RecorderWidget()
    _stage(widget, [("click", 10, 20), ("hello", "write"), ("enter", 1)])

    types = [s["type"] for s in widget._blocks.steps()]
    assert types == ["click", "write", "enter"]


def test_control_blocks_written_back_alongside_recorded_tuples(qapp):
    widget = RecorderWidget()
    _stage(widget, [("click", 10, 20)])
    widget._controller._recording = False

    # Wrap the recorded click in a repeat, the way the palette does.
    steps = widget._blocks.steps()
    steps = [{"type": "repeat", "times": 3}] + steps + [{"type": "end"}]
    widget._blocks.set_steps(steps)
    widget._on_blocks_changed()

    # Recorded actions stay legacy tuples; added blocks are dicts
    # one list, both shapes, exactly what play_actions accepts.
    assert actions.actions_list == [
        {"type": "repeat", "times": 3},
        ["click", 10, 20],
        {"type": "end"},
    ]
    # And it comes back as the same blocks it was built from.
    assert [macro_store.literal_action_to_editor_step(a) for a in actions.actions_list] == steps


def test_unbalanced_blocks_are_reported_rather_than_saved(qapp):
    widget = RecorderWidget()
    _stage(widget, [("click", 10, 20)])
    widget._blocks.set_steps([{"type": "repeat", "times": 2}] + widget._blocks.steps())

    error = widget._blocks.structure_error()

    assert error  # a repeat with no end never reaches the storage layer
    assert "never closed" in error
