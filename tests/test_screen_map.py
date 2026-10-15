# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_screen_map.py
==========================
ai/discovery/screen_map.py: telling the model which control is where,
and whether what sits at a coordinate is still the control it saw last
time or a different one. UI Automation itself is replaced by hand-built
captures.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ai.discovery import screen_map
from ai.discovery.screen_map import ScreenControl, ScreenMap


def _control(name, x, y, kind="Button", w=80, h=30, aid=""):
    return ScreenControl(name=name, control_type=kind, x=x, y=y, w=w, h=h, automation_id=aid)


def _screen(*controls, handle=100, title="Editor"):
    return ScreenMap(window_title=title, window_handle=handle, controls=list(controls))


@pytest.fixture(autouse=True)
def no_history(monkeypatch):
    monkeypatch.setattr(screen_map, "_previous", {})


def test_a_control_knows_its_centre():
    assert _control("Save", 100, 200, w=80, h=30).center == (140, 215)


def test_the_same_control_in_the_same_place_is_the_same():
    save = _control("Save", 100, 200)
    changes = screen_map.diff(_screen(save), _screen(_control("Save", 102, 201)))
    assert len(changes["same"]) == 1
    assert not (changes["replaced"] or changes["moved"] or changes["appeared"] or changes["gone"])


def test_a_different_control_at_the_same_place_is_called_out():
    changes = screen_map.diff(_screen(_control("Save", 100, 200)), _screen(_control("Delete", 100, 200)))
    (old, new), = changes["replaced"]
    assert (old.name, new.name) == ("Save", "Delete")
    assert not changes["gone"] and not changes["appeared"]


def test_a_control_that_moved_is_matched_by_what_it_is():
    changes = screen_map.diff(_screen(_control("Save", 100, 200)), _screen(_control("Save", 400, 200)))
    (old, new), = changes["moved"]
    assert old.center == (140, 215) and new.center == (440, 215)


def test_new_and_vanished_controls_are_listed():
    before = _screen(_control("Save", 100, 200), _control("Cancel", 300, 500))
    after = _screen(_control("Save", 100, 200), _control("Help", 600, 40))
    changes = screen_map.diff(before, after)
    assert [c.name for c in changes["gone"]] == ["Cancel"]
    assert [c.name for c in changes["appeared"]] == ["Help"]


def test_no_capture_means_no_changes():
    assert screen_map.diff(None, _screen(_control("Save", 1, 1))) == {
        "replaced": [], "same": [], "moved": [], "appeared": [], "gone": []}


def test_describe_lists_names_and_screen_coordinates():
    text = screen_map.describe(screen=_screen(_control("Save", 100, 200), _control("File name", 20, 60, kind="Edit")))
    assert 'Controls in "Editor"' in text
    assert "Button 'Save' at (140, 215)" in text
    assert "Edit 'File name' at (60, 75)" in text
    assert "Compared with" not in text          # nothing to compare with yet


def test_describe_says_when_a_different_control_took_the_place_of_one():
    screen_map.describe(screen=_screen(_control("Save", 100, 200)))
    text = screen_map.describe(screen=_screen(_control("Delete", 100, 200)))
    assert "DIFFERENT control at (140, 215): was Button 'Save', now Button 'Delete'" in text


def test_describe_says_when_nothing_changed():
    screen_map.describe(screen=_screen(_control("Save", 100, 200)))
    text = screen_map.describe(screen=_screen(_control("Save", 100, 200)))
    assert "the same controls, in the same places" in text


def test_each_window_is_compared_with_itself_only():
    screen_map.describe(screen=_screen(_control("Save", 100, 200), handle=1))
    text = screen_map.describe(screen=_screen(_control("Delete", 100, 200), handle=2))
    assert "Compared with" not in text


def test_nothing_readable_describes_as_nothing():
    assert screen_map.describe(screen=_screen()) == ""


def test_a_long_list_is_cut_short_and_says_so():
    many = [_control(f"Item {n}", 10, 10 + n * 20) for n in range(50)]
    text = screen_map.describe(limit=5, screen=_screen(*many))
    assert "Item 4" in text and "Item 5'" not in text
    assert "... and 45 more" in text


def test_check_point_compares_with_the_last_description(monkeypatch):
    screen_map.describe(screen=_screen(_control("Save", 100, 200)))
    monkeypatch.setattr(screen_map, "control_at", lambda x, y: _control("Save", 100, 200))
    assert "Same control as in the last screenshot" in screen_map.check_point(140, 215)
    monkeypatch.setattr(screen_map, "control_at", lambda x, y: _control("Delete", 100, 200))
    assert "DIFFERENT from the last screenshot, which had Button 'Save' there" in screen_map.check_point(140, 215)


def test_check_point_without_ui_automation_says_so(monkeypatch):
    monkeypatch.setattr(screen_map, "control_at", lambda x, y: None)
    assert "Nothing readable" in screen_map.check_point(5, 5)


def test_labels_are_kept_short_and_on_one_line():
    label = _control("A very long name\nthat goes on " + "and on " * 20, 0, 0).label()
    assert "\n" not in label and len(label) < 80
    assert _control("", 0, 0).label() == "Button (no name)"


def test_without_ui_automation_nothing_is_captured(monkeypatch):
    monkeypatch.setattr(screen_map, "available", lambda: False)
    assert screen_map.capture() is None
    assert screen_map.control_at(1, 1) is None
    assert screen_map.describe() == ""
