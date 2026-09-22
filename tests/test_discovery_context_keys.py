# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_discovery_context_keys.py
====================================
Element identity in the mapper, and the context scoping that stops two
different controls from being treated as one.

The tried set is keyed by _element_key. When that key collides, the
mapper marks a control explored that it never touched - so a button
inside a dialog is skipped because a same-named button on the toolbar
was clicked earlier. "OK", "Cancel", "Close", "Open", "Save" and
"Settings" all appear more than once in a typical application, so this
is the common case rather than an exotic one.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai.discovery.accessibility import AccessibilityElement
from ai.discovery.mapper import _context_of, _element_key


def _el(path, name="", control_type="Button", automation_id=""):
    return AccessibilityElement(
        id=path, name=name, control_type=control_type, automation_id=automation_id
    )


# ── _context_of ──────────────────────────────────────────────────────

def test_context_is_the_parent_portion_of_the_path():
    assert _context_of(_el("Pane[0]/Group[1]/Button[3]")) == "Pane[0]/Group[1]"


def test_a_top_level_path_has_no_context():
    assert _context_of(_el("Button[0]")) == ""


def test_an_empty_path_has_no_context():
    assert _context_of(_el("")) == ""


# ── collisions across contexts ───────────────────────────────────────

def test_same_named_buttons_in_different_containers_get_different_keys():
    """The bug this exists for: an "Open" on the toolbar and an "Open"
    inside a dialog are two controls, and clicking one must not mark the
    other explored."""
    counts = {}
    toolbar = _element_key(_el("Pane[0]/ToolBar[0]/Button[2]", "Open"), counts)
    dialog = _element_key(_el("Window[1]/Group[0]/Button[1]", "Open"), counts)
    assert toolbar != dialog


def test_same_named_buttons_in_the_SAME_container_stay_distinguishable():
    """Two identically named siblings are still two actions - the
    ordinal handles that, and must survive the context change."""
    counts = {}
    first = _element_key(_el("Pane[0]/Group[1]/Button[0]", "Add"), counts)
    second = _element_key(_el("Pane[0]/Group[1]/Button[1]", "Add"), counts)
    assert first != second
    assert second.endswith("#1")


def test_the_same_element_read_twice_produces_the_same_key():
    """Identity has to be stable across captures, or a revisit re-clicks
    everything. Each read starts with a fresh counts dict, the way
    _build_node does."""
    el = _el("Pane[0]/Group[1]/Button[3]", "Save")
    assert _element_key(el, {}) == _element_key(el, {})


def test_a_context_change_alone_changes_the_key():
    assert _element_key(_el("A[0]/B[0]/Button[0]", "Go"), {}) != \
           _element_key(_el("A[0]/C[0]/Button[0]", "Go"), {})


# ── AutomationId wins, and carries no context ────────────────────────

def test_an_automation_id_is_used_as_is():
    key = _element_key(_el("Pane[0]/Group[1]/Button[3]", "Save", automation_id="saveBtn"), {})
    assert key == "saveBtn"


def test_an_automation_id_key_does_not_change_when_the_layout_moves():
    """An AutomationId is already unique application-wide. Scoping it by
    container would make it shift on relayout, which is the one property
    AutomationIds exist to avoid."""
    a = _element_key(_el("Pane[0]/Group[1]/Button[3]", "Save", automation_id="saveBtn"), {})
    b = _element_key(_el("Pane[0]/Group[9]/Button[0]", "Save", automation_id="saveBtn"), {})
    assert a == b


def test_two_different_automation_ids_never_collide():
    counts = {}
    assert _element_key(_el("X", "Save", automation_id="save"), counts) != \
           _element_key(_el("Y", "Save", automation_id="saveAs"), counts)


# ── unnamed controls ─────────────────────────────────────────────────

def test_an_unnamed_control_falls_back_to_its_full_path():
    """Icon buttons often have neither a name nor an AutomationId. The
    path is all there is, and it already encodes the container."""
    key = _element_key(_el("Pane[0]/ToolBar[0]/Button[4]"), {})
    assert key == "path:Pane[0]/ToolBar[0]/Button[4]"


def test_two_unnamed_controls_in_different_places_differ():
    counts = {}
    assert _element_key(_el("Pane[0]/ToolBar[0]/Button[4]"), counts) != \
           _element_key(_el("Pane[0]/StatusBar[0]/Button[4]"), counts)


def test_a_named_and_an_unnamed_control_never_share_a_key():
    counts = {}
    named = _element_key(_el("Pane[0]/Group[0]/Button[0]", "Save"), counts)
    unnamed = _element_key(_el("Pane[0]/Group[0]/Button[1]"), counts)
    assert named != unnamed
