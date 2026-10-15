# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_discovery_mapper_stability.py
==========================================
Regression tests for the mapper clicking the same controls over and over.

The fake in test_discovery_mapper.py hands back a distinct, stable root
element per screen. The real Windows backend never does that: get_root()
always returns the target window, so the root's name is the window title
and it changes whenever the app feels like it. _RealisticBackend here
behaves the way the real one does, which is the only way these failures
show up at all.

The assertion that matters in most of them is that no element is
activated twice from the same screen, leaving aside a screen's own Back
control, which is how the mapper leaves.
"""

import collections
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai.discovery.accessibility import AccessibilityBackend, AccessibilityElement
from ai.discovery.mapper import _screen_shape, _similarity, map_application_ui


def el(id_, name, control_type):
    return AccessibilityElement(
        id=id_, name=name, control_type=control_type,
        bounding_box={"x": 0, "y": 0, "w": 10, "h": 10},
    )


class _RealisticBackend(AccessibilityBackend):
    """
    Models the real backend's defining trait: get_root() returns the
    WINDOW, not the screen, so the root element is the same object with a
    title that drifts. Screens are told apart by their children alone.
    """

    def __init__(self, screens, start_screen, back_map, title_for=None):
        self.screens = screens
        self.current = start_screen
        self.back_map = back_map
        self.activated = []
        self.hovered = []
        self.reads = 0
        self._title_for = title_for or (lambda screen, reads: screen)

    # -- the window, not the screen ------------------------------------

    def get_root(self):
        self.reads += 1
        return AccessibilityElement(
            id="window",
            name=self._title_for(self.current, self.reads),
            control_type="Window",
            bounding_box={"x": 0, "y": 0, "w": 1000, "h": 800},
        )

    def capture_state(self):
        return self.get_root()

    def list_interactables(self, element):
        return [child for child, _ in self.screens[self.current]["children"]]

    def activate(self, element):
        for child, target in self.screens[self.current]["children"]:
            if child.id == element.id:
                self.activated.append((self.current, element.id))
                if target == "__BACK__":
                    self.current = self.back_map[self.current]
                elif target:
                    self.current = target
                return True
        return False

    def hover(self, element):
        self.hovered.append(element.id)
        return True

    def input_value(self, element, value):
        return self.activate(element)

    def field_text(self, element):
        return ""      # every field here is empty

    def find_back_control(self, element):
        for child, target in self.screens[self.current]["children"]:
            if target == "__BACK__":
                return child
        return None

    def send_key(self, key):
        if key == "ESCAPE" and self.current in self.back_map:
            self.current = self.back_map[self.current]

    def find_by_path(self, path):
        for child, _ in self.screens[self.current]["children"]:
            if child.id == path:
                return child
        return None

    # -- assertions ------------------------------------------------------

    def repeat_activations(self):
        """(screen, element) pairs triggered more than once."""
        counts = collections.Counter(self.activated)
        return {pair: n for pair, n in counts.items() if n > 1}


def _menu_screens():
    """A screen big enough for shape similarity to be consulted (>= 5
    elements), which is where a drifting title used to split it."""
    toolbar = [
        (el("el:new", "New", "Button"), None),
        (el("el:open", "Open", "Button"), None),
        (el("el:save", "Save", "Button"), None),
        (el("el:print", "Print", "Button"), None),
        (el("el:help", "Help", "Button"), None),
    ]
    return {
        "home": {"children": toolbar + [
            (el("el:tools", "Tools", "Button"), "tools"),
        ]},
        "tools": {"children": [
            (el("el:opt1", "Option One", "CheckBox"), None),
            (el("el:opt2", "Option Two", "CheckBox"), None),
            (el("el:back", "Back", "Button"), "__BACK__"),
        ]},
    }


def test_drifting_window_title_does_not_reclick_the_same_screen():
    # The title carries a counter, so every single reading of the window
    # differs. Before the rework this allocated a fresh node on each read
    # and re-clicked the whole toolbar each time.
    backend = _RealisticBackend(
        _menu_screens(), "home", {"tools": "home"},
        title_for=lambda screen, reads: f"* Document{reads} - Editor ({reads}%)",
    )
    tree = map_application_ui("Editor", backend=backend, max_nodes=50, max_depth=6)

    assert backend.repeat_activations() == {}
    assert tree.node_count() == 2


def test_churning_list_rows_do_not_reclick_the_same_screen():
    # A results list that gains and renames rows between visits. Row names
    # are content, so they must not take part in identifying the screen.
    rows = {"n": 0}

    class _Churning(_RealisticBackend):
        def list_interactables(self, element):
            base = [child for child, _ in self.screens[self.current]["children"]]
            if self.current != "home":
                return base
            rows["n"] += 1
            return base + [
                el(f"row:{rows['n']}:{i}", f"Result {rows['n']}-{i}", "ListItem")
                for i in range(rows["n"] % 4 + 1)
            ]

    backend = _Churning(_menu_screens(), "home", {"tools": "home"})
    tree = map_application_ui("Browser", backend=backend, max_nodes=50, max_depth=6)

    assert backend.repeat_activations() == {}
    # The churning rows must not each become their own screen.
    assert tree.node_count() <= 3


def test_returning_to_a_screen_resumes_instead_of_restarting():
    # Two branches that both end at the same "shared" screen. The second
    # arrival must pick up where the first left off.
    screens = {
        "home": {"children": [
            (el("el:a", "Branch A", "Button"), "a"),
            (el("el:b", "Branch B", "Button"), "b"),
            (el("el:x1", "X1", "Button"), None),
            (el("el:x2", "X2", "Button"), None),
            (el("el:x3", "X3", "Button"), None),
        ]},
        "a": {"children": [
            (el("el:a2shared", "Go", "Button"), "shared"),
            (el("el:backa", "Back", "Button"), "__BACK__"),
        ]},
        "b": {"children": [
            (el("el:b2shared", "Go", "Button"), "shared"),
            (el("el:backb", "Back", "Button"), "__BACK__"),
        ]},
        "shared": {"children": [
            (el("el:s1", "S1", "Button"), None),
            (el("el:s2", "S2", "Button"), None),
            (el("el:s3", "S3", "Button"), None),
            (el("el:s4", "S4", "Button"), None),
            (el("el:s5", "S5", "Button"), None),
            (el("el:backs", "Back", "Button"), "__BACK__"),
        ]},
    }
    back_map = {"a": "home", "b": "home", "shared": "a"}
    backend = _RealisticBackend(screens, "home", back_map)
    map_application_ui("Shared", backend=backend, max_nodes=50, max_depth=6)

    # Leaving a screen means clicking its Back control, so that one is
    # allowed to repeat. Nothing else is.
    explored = [e for s, e in backend.activated if s == "shared" and e != "el:backs"]
    assert len(explored) == len(set(explored)), f"re-clicked on shared: {explored}"


def test_action_cap_bounds_a_run_that_never_settles():
    # A screen whose every click lands somewhere structurally new. Without
    # an action ceiling this runs until the node cap, having clicked far
    # more times than nodes allocated.
    screens = {f"s{i}": {"children": [
        (el(f"el:{i}", f"Go {i}", "Button"), f"s{i + 1}"),
    ]} for i in range(200)}
    screens["s200"] = {"children": []}
    backend = _RealisticBackend(screens, "s0", {f"s{i}": f"s{i - 1}" for i in range(1, 201)})

    map_application_ui("Endless", backend=backend, max_nodes=1000, max_depth=1000, max_actions=25)
    assert len(backend.activated) <= 40   # 25 explorations plus their backtracks


def test_root_is_never_backed_out_of():
    # Backing out of the root sends ESCAPE to the target window itself,
    # which on a real app closes or disturbs it.
    backend = _RealisticBackend(_menu_screens(), "home", {"tools": "home"})
    map_application_ui("Editor", backend=backend, max_nodes=50, max_depth=6)
    assert backend.current == "home"


# -- the pieces, directly ---------------------------------------------


def test_screen_shape_ignores_the_window_title():
    a = _screen_shape(el("window", "* Report.docx - Editor (45%)", "Window"),
                      [el("b", "Save", "Button")])
    b = _screen_shape(el("window", "(3) Notes - Editor", "Window"),
                      [el("b", "Save", "Button")])
    assert a == b, "the window title must not take part in identifying a screen"


def test_screen_shape_keeps_element_ids_apart():
    # Two wizard steps that look identical but are different controls.
    root = el("window", "Wizard", "Window")
    step1 = _screen_shape(root, [el("el:next1", "Next", "Button")])
    step2 = _screen_shape(root, [el("el:next2", "Next", "Button")])
    assert step1 != step2, "identically labelled controls are still different screens"


def test_screen_shape_ignores_content_names():
    root = el("window", "Anything", "Window")
    a = _screen_shape(root, [el("r1", "Alice", "ListItem"), el("b", "Save", "Button")])
    b = _screen_shape(root, [el("r9", "Zebra", "ListItem"), el("b", "Save", "Button")])
    assert a == b, "list row names must not take part in identifying a screen"

    c = _screen_shape(root, [el("b", "Save", "Button")])
    d = _screen_shape(root, [el("b", "Delete", "Button")])
    assert c != d, "button names are structure and must be kept"


def test_screen_shape_is_insensitive_to_row_count():
    root = el("window", "List", "Window")
    few = _screen_shape(root, [el(f"r{i}", f"Row {i}", "ListItem") for i in range(3)])
    many = _screen_shape(root, [el(f"r{i}", f"Row {i}", "ListItem") for i in range(40)])
    assert few == many, "a list loading more rows is the same screen"


def test_similarity_edges():
    assert _similarity(frozenset(), frozenset()) == 1.0
    assert _similarity(frozenset({"a"}), frozenset()) == 0.0
    assert _similarity(frozenset({"a", "b"}), frozenset({"a", "b"})) == 1.0
    assert _similarity(frozenset({"a", "b"}), frozenset({"a", "c"})) == 1 / 3
