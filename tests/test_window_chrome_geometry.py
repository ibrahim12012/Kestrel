# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_window_chrome_geometry.py
====================================
The resize-grip layout from app/window_chrome.py, as pure arithmetic.

Importing the real module needs PyQt6, which the sandbox does not have,
so the placement rule is reproduced here and checked against the
properties that matter. That is worth doing because a wrong grip is not
a crash - it is an invisible 6px strip in the wrong place, which shows
up only as an edge that does not resize, or a corner that resizes the
wrong way. Nothing else would catch it.

Kept in step with WindowChrome.reposition_grips by hand; if that
function's maths changes, this has to change with it.
"""

GRIP = 6

# (left, right, top, bottom) per grip, matching _build_grips' spec order.
SPEC = [
    (False, False, True,  False),   # top
    (False, False, False, True),    # bottom
    (True,  False, False, False),   # left
    (False, True,  False, False),   # right
    (True,  False, True,  False),   # top-left
    (False, True,  True,  False),   # top-right
    (True,  False, False, True),    # bottom-left
    (False, True,  False, True),    # bottom-right
]


def _place(left, right, top, bottom, width, height):
    """Verbatim from WindowChrome.reposition_grips."""
    x = 0 if left else (width - GRIP if right else GRIP)
    y = 0 if top else (height - GRIP if bottom else GRIP)
    w = GRIP if (left or right) else max(0, width - 2 * GRIP)
    h = GRIP if (top or bottom) else max(0, height - 2 * GRIP)
    return x, y, w, h


def _all(width, height):
    return [_place(*flags, width, height) for flags in SPEC]


def test_every_grip_stays_inside_the_window():
    width, height = 1280, 800
    for (x, y, w, h) in _all(width, height):
        assert x >= 0 and y >= 0
        assert x + w <= width, (x, w, width)
        assert y + h <= height, (y, h, height)


def test_corners_are_grip_sized_squares():
    width, height = 1280, 800
    corners = [flags for flags in SPEC if (flags[0] or flags[1]) and (flags[2] or flags[3])]
    assert len(corners) == 4
    for flags in corners:
        _, _, w, h = _place(*flags, width, height)
        assert (w, h) == (GRIP, GRIP)


def test_edges_span_the_gap_between_the_corners():
    """An edge strip must not overlap the corners, or the corner cursor
    would depend on stacking order rather than on position."""
    width, height = 1280, 800
    x, y, w, h = _place(False, False, True, False, width, height)   # top edge
    assert (x, y) == (GRIP, 0)
    assert w == width - 2 * GRIP

    x, y, w, h = _place(True, False, False, False, width, height)   # left edge
    assert (x, y) == (0, GRIP)
    assert h == height - 2 * GRIP


def test_the_four_corners_land_in_four_different_places():
    placed = {_place(*flags, 1280, 800)[:2] for flags in SPEC
              if (flags[0] or flags[1]) and (flags[2] or flags[3])}
    assert placed == {(0, 0), (1280 - GRIP, 0), (0, 800 - GRIP), (1280 - GRIP, 800 - GRIP)}


def test_no_two_grips_share_a_rectangle():
    assert len({_all(1280, 800)[i] for i in range(len(SPEC))}) == len(SPEC)


def test_a_window_smaller_than_the_grips_does_not_produce_negative_sizes():
    """Qt refuses a negative width, and a window can briefly be tiny
    during construction or a fast drag."""
    for width, height in ((10, 10), (1, 1), (0, 0), (GRIP, GRIP)):
        for (x, y, w, h) in _all(width, height):
            assert w >= 0 and h >= 0, (width, height, w, h)


def test_grips_track_a_resize():
    """The right and bottom strips have to move, not just restretch."""
    before = _place(False, True, False, False, 800, 600)     # right edge
    after = _place(False, True, False, False, 1200, 600)
    assert after[0] > before[0]
    assert after[0] == 1200 - GRIP
