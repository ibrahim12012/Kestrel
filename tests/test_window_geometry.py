# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_window_geometry.py
=============================
MainWindow._apply_default_geometry's sizing rule, as arithmetic.

The rule it enforces is that the window fits the screen it opens on.
The .ui file's design-time geometry (1280x800) was being applied
verbatim, which is taller than the usable height of a 1366x768 laptop
once the taskbar is subtracted - so the window opened with its bottom
edge, and the chat input with it, below the screen.

Reproduced here rather than imported because the real method needs a
QScreen. Kept in step with it by hand.
"""

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
MAX_SCREEN_FRACTION = 0.92
MIN_WIDTH = 900
MIN_HEIGHT = 560

# Real available areas: full resolution minus a ~40px Windows taskbar.
SCREENS = {
    "1920x1080": (1920, 1040),
    "1366x768":  (1366, 728),
    "1600x900":  (1600, 860),
    "2560x1440": (2560, 1400),
    "1280x1024": (1280, 984),
}


def _size_for(avail_w, avail_h):
    """Verbatim from MainWindow._apply_default_geometry."""
    width = min(DEFAULT_WIDTH, int(avail_w * MAX_SCREEN_FRACTION))
    height = min(DEFAULT_HEIGHT, int(avail_h * MAX_SCREEN_FRACTION))
    width = max(width, MIN_WIDTH)
    height = max(height, MIN_HEIGHT)
    return width, height


def _centred(avail_w, avail_h):
    w, h = _size_for(avail_w, avail_h)
    return ((avail_w - w) // 2, (avail_h - h) // 2, w, h)


def test_the_case_this_was_written_for():
    """1366x768: 800px tall does not fit in 728px of usable height."""
    w, h = _size_for(*SCREENS["1366x768"])
    assert h < 728, "window is still taller than the usable screen"
    assert h == int(728 * MAX_SCREEN_FRACTION)


def test_a_big_screen_gets_the_preferred_size_untouched():
    assert _size_for(*SCREENS["1920x1080"]) == (DEFAULT_WIDTH, DEFAULT_HEIGHT)
    assert _size_for(*SCREENS["2560x1440"]) == (DEFAULT_WIDTH, DEFAULT_HEIGHT)


def test_it_fits_every_common_screen():
    for name, (aw, ah) in SCREENS.items():
        w, h = _size_for(aw, ah)
        assert w <= aw, f"{name}: {w} wider than {aw}"
        assert h <= ah, f"{name}: {h} taller than {ah}"


def test_it_never_covers_the_whole_screen():
    """Leaves the taskbar reachable and keeps the window reading as a
    window rather than as the desktop."""
    for name, (aw, ah) in SCREENS.items():
        w, h = _size_for(aw, ah)
        if h < DEFAULT_HEIGHT:          # only when the fraction actually bit
            assert h <= ah * MAX_SCREEN_FRACTION + 1, name


def test_it_is_centred_and_fully_on_screen():
    for name, (aw, ah) in SCREENS.items():
        x, y, w, h = _centred(aw, ah)
        assert x >= 0 and y >= 0, f"{name}: negative origin ({x},{y})"
        assert x + w <= aw, name
        assert y + h <= ah, name


def test_the_floor_wins_on_a_screen_too_small_for_the_layout():
    """A window slightly larger than a tiny screen is a better failure
    than one too narrow for the sidebar plus a conversation column."""
    w, h = _size_for(800, 600)
    assert w == MIN_WIDTH
    assert h == MIN_HEIGHT


def test_the_aspect_ratio_stays_landscape():
    """Whatever the clamp does, it must not produce a portrait window -
    that is the shape the complaint was about."""
    for name, (aw, ah) in SCREENS.items():
        w, h = _size_for(aw, ah)
        assert w > h, f"{name}: {w}x{h} is not landscape"


def test_height_never_exceeds_width_times_the_intended_ratio():
    """16:10 is the target. Allow some slack for the clamp, but never
    let it drift toward square."""
    for name, (aw, ah) in SCREENS.items():
        w, h = _size_for(aw, ah)
        assert h / w <= 0.75, f"{name}: {w}x{h} is too tall for its width"
