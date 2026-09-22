# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_macro_blocks.py
=============================
Tests for ai/macro_blocks.py - the Scratch-style block vocabulary
behind the macro editor: nesting (indentation, the parsed tree),
structural validation, and the colour comparison every colour block
depends on.

No Qt here: this module is deliberately UI-free so the editor
(app/macro_editor_dialog.py), the replay interpreter (ai/actions.py's
play_actions) and the codegen (ai/macro_generalizer.py) can all share
one definition of what a block is - which also means it can be tested
without a QApplication.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.macro_blocks as blocks


def _program():
    """A macro exercising every nesting shape at once: a loop, a
    two-branch colour test inside it, and a plain step after it."""
    return [
        {"type": "repeat", "times": 2},
        {"type": "click", "x": 10, "y": 20},
        {"type": "if_color", "x": 5, "y": 5, "color": "#00ff00", "tolerance": 10},
        {"type": "wait", "seconds": 0.5},
        {"type": "else"},
        {"type": "stop"},
        {"type": "end"},
        {"type": "end"},
        {"type": "enter"},
    ]


# ── Indentation ───────────────────────────────────────────────────────

def test_block_depths_indents_contents_and_keeps_markers_with_their_opener():
    depths = blocks.block_depths(_program())
    # repeat, [click, if], [wait], else, [stop], end(if), end(repeat), enter
    assert depths == [0, 1, 1, 2, 1, 2, 1, 0, 0]


def test_block_depths_never_goes_negative_on_an_unbalanced_list():
    """A stray `end` (hand-edited spec, or mid-edit state) must still
    render - the complaint about it belongs to validate_structure."""
    assert blocks.block_depths([{"type": "end"}, {"type": "enter"}]) == [0, 0]


# ── Structural validation ─────────────────────────────────────────────

def test_validate_structure_accepts_a_balanced_program():
    assert blocks.validate_structure(_program()) == (True, "")


def test_validate_structure_rejects_an_unclosed_opener():
    ok, err = blocks.validate_structure([{"type": "repeat", "times": 2}, {"type": "enter"}])
    assert ok is False
    assert "never closed" in err


def test_validate_structure_rejects_a_stray_end():
    ok, err = blocks.validate_structure([{"type": "enter"}, {"type": "end"}])
    assert ok is False
    assert "nothing open" in err


def test_validate_structure_rejects_else_outside_an_if():
    ok, err = blocks.validate_structure([
        {"type": "repeat", "times": 1}, {"type": "else"}, {"type": "end"},
    ])
    assert ok is False
    assert "else" in err


# ── Parsed tree ───────────────────────────────────────────────────────

def test_parse_program_nests_bodies_and_splits_the_else_branch():
    tree = blocks.parse_program(_program())
    assert [n.type for n in tree] == ["repeat", "enter"]

    repeat = tree[0]
    assert [n.type for n in repeat.body] == ["click", "if_color"]

    if_node = repeat.body[1]
    assert [n.type for n in if_node.body] == ["wait"]
    assert [n.type for n in if_node.else_body] == ["stop"]


def test_parse_program_is_tolerant_of_unbalanced_input():
    """Replay and codegen both run against whatever is actually saved,
    so parsing must do something reasonable rather than raise."""
    assert blocks.parse_program([{"type": "end"}]) == []
    assert [n.type for n in blocks.parse_program([{"type": "repeat", "times": 1}])] == ["repeat"]
    # An opener left hanging still collects what followed it.
    hanging = blocks.parse_program([{"type": "repeat", "times": 1}, {"type": "enter"}])
    assert [n.type for n in hanging[0].body] == ["enter"]


# ── Defaults / description ────────────────────────────────────────────

def test_new_step_fills_every_pipe_with_its_default():
    step = blocks.new_step("wait_color_change")
    assert step["type"] == "wait_color_change"
    assert step["forbidden"] == "#ff0000"
    assert step["x"] == 0 and step["y"] == 0          # a {point} pipe writes x/y, not one key
    assert step["timeout"] == 30.0


def test_describe_substitutes_each_pipes_current_value():
    step = {"type": "wait_color_change", "x": 10, "y": 20, "forbidden": "#abcdef"}
    text = blocks.describe(step)
    assert "10, 20" in text
    assert "#abcdef" in text
    assert "not to" in text  # the "…but not to <colour>" guard reads as such


def test_describe_falls_back_for_an_unknown_step():
    assert "unrecognized" in blocks.describe({"type": "nope"})


# ── Colour matching ───────────────────────────────────────────────────

def test_colors_match_is_per_channel_within_tolerance():
    assert blocks.colors_match((255, 0, 0), (250, 4, 3), 12) is True
    # One channel beyond tolerance is a miss, even though the other two
    # are exact - a per-channel rule is what a user can reason about
    # when nudging the number after a false match.
    assert blocks.colors_match((255, 0, 0), (255, 0, 40), 12) is False


def test_colors_match_with_zero_tolerance_is_exact():
    assert blocks.colors_match((1, 2, 3), (1, 2, 3), 0) is True
    assert blocks.colors_match((1, 2, 3), (1, 2, 4), 0) is False


def test_hex_round_trip_handles_shorthand_and_junk():
    assert blocks.hex_to_rgb("#ff8000") == (255, 128, 0)
    assert blocks.hex_to_rgb("f80") == (255, 136, 0)      # 3-digit shorthand expands
    assert blocks.hex_to_rgb("not a colour") == (0, 0, 0)  # never raises - a swatch still renders
    assert blocks.rgb_to_hex((255, 128, 0)) == "#ff8000"
    assert blocks.rgb_to_hex((300, -5, 0)) == "#ff0000"    # clamped, not wrapped


# ── Dragging blocks around (app/block_list.py's move maths) ───────────
# The reordering itself lives in BlockListWidget._apply_row_move, which
# needs Qt to instantiate - but the maths it does is pure, and it's the
# part that can silently corrupt a macro (a `repeat` moved away from
# its `end` leaves a list that won't save and wouldn't replay right).
# These drive that calculation directly, against the same block_span /
# partner logic the widget uses.

def _apply_move(steps, start, row):
    """Mirror of BlockListWidget._apply_row_move - kept in step with it
    deliberately, since this is the invariant worth pinning: whatever
    is dragged, the result still balances."""
    partners = _partners(steps, start)
    span_start, span_end = min(partners), max(partners)
    moving = steps[span_start:span_end + 1]
    rest = steps[:span_start] + steps[span_end + 1:]
    insert_at = sum(
        1 for i in range(len(steps))
        if i < row and not (span_start <= i <= span_end)
    )
    insert_at = max(0, min(len(rest), insert_at))
    return rest[:insert_at] + moving + rest[insert_at:]


def _partners(steps, index):
    """The indices that travel together with steps[index] - the widget's
    own _block_partner_indices, in a form these tests can call."""
    spec = blocks.spec_for(steps[index].get("type", ""))
    if spec is None or not (spec.opens or spec.closes or spec.midpoint):
        return [index]
    if spec.opens:
        start = index
    else:
        depth, start = 0, index
        for i in range(index - 1, -1, -1):
            candidate = blocks.spec_for(steps[i].get("type", ""))
            if candidate is None:
                continue
            if candidate.closes:
                depth += 1
            elif candidate.opens:
                if depth == 0:
                    start = i
                    break
                depth -= 1
        else:
            return [index]
    out, depth = [start], 0
    for i in range(start + 1, len(steps)):
        candidate = blocks.spec_for(steps[i].get("type", ""))
        if candidate is None:
            continue
        if candidate.opens:
            depth += 1
        elif candidate.midpoint and depth == 0:
            out.append(i)
        elif candidate.closes:
            if depth == 0:
                out.append(i)
                break
            depth -= 1
    return out


def _nested():
    return [
        {"type": "click", "x": 1, "y": 1},       # 0
        {"type": "repeat", "times": 3},          # 1
        {"type": "write", "value": "inside"},    # 2
        {"type": "end"},                         # 3
        {"type": "enter"},                       # 4
    ]


def test_dragging_a_c_block_takes_its_contents_with_it():
    moved = _apply_move(_nested(), start=1, row=5)  # repeat -> bottom
    assert [s["type"] for s in moved] == ["click", "enter", "repeat", "write", "end"]
    assert blocks.validate_structure(moved) == (True, "")


def test_dragging_a_c_block_to_the_top_keeps_it_whole():
    moved = _apply_move(_nested(), start=1, row=0)
    assert [s["type"] for s in moved] == ["repeat", "write", "end", "click", "enter"]
    assert blocks.validate_structure(moved) == (True, "")


def test_dragging_a_plain_block_into_a_c_block_nests_it():
    moved = _apply_move(_nested(), start=0, row=3)  # click -> inside the repeat
    assert [s["type"] for s in moved] == ["repeat", "write", "click", "end", "enter"]
    assert blocks.block_depths(moved) == [0, 1, 1, 0, 0]
    assert blocks.validate_structure(moved) == (True, "")


def test_dragging_an_end_marker_moves_the_block_it_closes():
    """Grabbing the bottom of a C-block is grabbing the block - the two
    halves can never be separated by a drag."""
    moved = _apply_move(_nested(), start=3, row=0)
    assert [s["type"] for s in moved] == ["repeat", "write", "end", "click", "enter"]
    assert blocks.validate_structure(moved) == (True, "")


def test_every_drag_of_every_block_leaves_a_valid_structure():
    """The invariant that matters: no drag, anywhere, to anywhere, can
    produce a macro that won't save."""
    steps = _nested()
    for start in range(len(steps)):
        for row in range(len(steps) + 1):
            moved = _apply_move([dict(s) for s in steps], start, row)
            ok, err = blocks.validate_structure(moved)
            assert ok, f"drag {start} -> {row} broke the structure: {err}"
            assert len(moved) == len(steps), f"drag {start} -> {row} lost or duplicated a block"
