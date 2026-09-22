# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_macro_calls.py
=========================
One macro calling another - the `call_macro` block.

This is what makes a macro library composable: a login recorded once
becomes the first step of everything that needs a login. The risk it
introduces is cycles. A macro that calls itself, directly or through a
chain, would recurse until the interpreter's stack gave out, and the
symptom would be the app vanishing mid-macro with no message at all.

So the guard is the thing under test here, more than the happy path.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.actions as actions
import ai.macro_store as macro_store
from ai import macro_blocks as blocks


def _save_blocks(name, steps):
    """Save a macro whose steps include editor blocks.

    Goes through the real save path: save_literal_macro writes the
    record, then update_literal_actions converts the editor blocks into
    the recorded_actions shape play_actions replays. A block with no
    legacy tuple form - call_macro, repeat, wait - round-trips as its own
    dict, which is what makes play_actions route the macro through the
    block interpreter."""
    macro_store.save_literal_macro(name, [], description="")
    ok, err = macro_store.update_literal_actions(name, "", steps)
    assert ok, err


# ── the block itself ─────────────────────────────────────────────────

def test_the_block_exists_and_reads_as_a_sentence():
    assert blocks.describe({"type": "call_macro", "macro": "daily_report"}) \
        == "run macro daily_report"


def test_it_is_an_action_block():
    """Blue, like clicking and typing - it does something to the machine
    rather than controlling flow or looking at the screen."""
    assert blocks.BLOCK_SPECS["call_macro"].category == blocks.CATEGORY_ACTION


def test_it_can_be_added_from_the_palette():
    assert "call_macro" in blocks.ADDABLE_TYPES


# ── running one ──────────────────────────────────────────────────────

def test_calling_a_saved_macro_runs_its_steps(mock_pyautogui, monkeypatch):
    _save_blocks("inner", [{"type": "click", "x": 5, "y": 6}])
    monkeypatch.setattr(actions, "_get_action_delay", lambda: 0.0)

    actions._run_saved_macro("inner")

    assert mock_pyautogui.click.called


def test_an_unknown_name_is_skipped_not_raised(monkeypatch):
    """One missing step should not abandon the rest of a macro that was
    otherwise fine."""
    monkeypatch.setattr(actions, "_get_action_delay", lambda: 0.0)
    actions._run_saved_macro("no_such_macro")      # must not raise


def test_an_empty_name_is_skipped():
    actions._run_saved_macro("")


def test_the_name_resolves_at_run_time(mock_pyautogui, monkeypatch):
    """Fuzzy resolution, the same as everywhere else - so a block added
    before a rename still finds its macro."""
    _save_blocks("open_the_report", [{"type": "enter"}])
    monkeypatch.setattr(actions, "_get_action_delay", lambda: 0.0)

    actions._run_saved_macro("open_the_repor")     # one character short

    # Resolved and ran rather than silently doing nothing.
    assert "open_the_report" not in actions._macro_call_stack


# ── cycles ───────────────────────────────────────────────────────────

def test_a_macro_that_calls_itself_is_refused(mock_pyautogui, monkeypatch):
    """Without the guard this recurses until the stack gives out and the
    process dies with no message."""
    _save_blocks("loop", [{"type": "call_macro", "macro": "loop"}])
    monkeypatch.setattr(actions, "_get_action_delay", lambda: 0.0)

    actions._run_saved_macro("loop")               # must return, not hang

    assert actions._macro_call_stack == []


def test_an_indirect_cycle_is_refused(mock_pyautogui, monkeypatch):
    """a -> b -> a. The direct case is the obvious one; this is the one
    that actually happens, when two macros each turn out to be useful
    inside the other."""
    _save_blocks("a", [{"type": "call_macro", "macro": "b"}])
    _save_blocks("b", [{"type": "call_macro", "macro": "a"}])
    monkeypatch.setattr(actions, "_get_action_delay", lambda: 0.0)

    actions._run_saved_macro("a")

    assert actions._macro_call_stack == []


def test_the_depth_ceiling_stops_a_long_chain(mock_pyautogui, monkeypatch):
    """Not a safety limit - the cycle check handles runaway recursion.
    A legibility one: a macro nested eight deep is one nobody can reason
    about, and a clear refusal beats a confusing failure."""
    for i in range(12):
        _save_blocks(f"chain_{i}", [{"type": "call_macro", "macro": f"chain_{i + 1}"}])
    _save_blocks("chain_12", [{"type": "enter"}])
    monkeypatch.setattr(actions, "_get_action_delay", lambda: 0.0)

    actions._run_saved_macro("chain_0")

    assert actions._macro_call_stack == []


def test_the_stack_is_cleared_even_when_a_nested_macro_stops(mock_pyautogui, monkeypatch):
    """A stop inside a called macro must not leave the stack dirty, or
    every later call to that macro looks like recursion and refuses."""
    _save_blocks("stops", [{"type": "stop"}])
    _save_blocks("outer", [{"type": "call_macro", "macro": "stops"}])
    monkeypatch.setattr(actions, "_get_action_delay", lambda: 0.0)

    try:
        actions._run_saved_macro("outer")
    except Exception:
        pass

    assert actions._macro_call_stack == []


def test_the_same_macro_can_be_called_twice_in_sequence(mock_pyautogui, monkeypatch):
    """Sequential is not recursive. Calling one macro, finishing it, then
    calling it again is ordinary use and must not trip the cycle check."""
    _save_blocks("step", [{"type": "enter"}])
    monkeypatch.setattr(actions, "_get_action_delay", lambda: 0.0)

    actions._run_saved_macro("step")
    actions._run_saved_macro("step")

    assert actions._macro_call_stack == []
