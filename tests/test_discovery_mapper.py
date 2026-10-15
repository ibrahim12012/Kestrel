# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_discovery_mapper.py
=================================
Tests for ai/discovery/mapper.py's DFS traversal - against a small,
fully in-memory FakeAccessibilityBackend (no real OS accessibility APIs,
no uiautomation dependency, runs anywhere). Mirrors the diagram from
Phase 2 spec section 1:

    Root -> (Action 1) -> Search  -> (Type) self-loop, (Filter) -> Filtered
         -> (Action 2) -> Profile (terminal)
         -> (Action 3) -> Settings -> (Delete Account) [destructive, never triggered]
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai.discovery.accessibility import AccessibilityBackend, AccessibilityElement
from ai.discovery.mapper import map_application_ui
from ai.discovery.tree import ScreenStatus


def el(id_, name, control_type):
    return AccessibilityElement(id=id_, name=name, control_type=control_type, bounding_box={"x": 0, "y": 0, "w": 10, "h": 10})


class FakeAccessibilityBackend(AccessibilityBackend):
    """
    `screens`: screen_id -> {"root": AccessibilityElement, "children": [(child_element, target)]}
    target is a screen_id, "__BACK__" (a structural back control), or None
    (an action that goes nowhere observable - a no-op click).
    `back_map`: screen_id -> parent screen_id, used when a "__BACK__" child is activated.
    """

    def __init__(self, screens, start_screen, back_map):
        self.screens = screens
        self.current = start_screen
        self.back_map = back_map
        self.activated = []   # log of every element id actually triggered, for assertions
        self.hovered = []     # log of every element id hovered (never triggered), for assertions

    def get_root(self):
        return self.screens[self.current]["root"]

    def list_interactables(self, element):
        screen = self.screens.get(element.id, self.screens[self.current])
        return [child for child, _ in screen["children"]]

    def activate(self, element):
        for child, target in self.screens[self.current]["children"]:
            if child.id == element.id:
                self.activated.append(element.id)
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
        for child, target in self.screens[element.id]["children"]:
            if target == "__BACK__":
                return child
        return None

    def send_key(self, key):
        if key == "ESCAPE" and self.current in self.back_map:
            self.current = self.back_map[self.current]

    def capture_state(self):
        return self.get_root()

    def find_by_path(self, path):
        for child, _ in self.screens[self.current]["children"]:
            if child.id == path:
                return child
        return None


def _build_spec_shaped_backend():
    screens = {
        "screen:root": {
            "root": el("screen:root", "Main", "Window"),
            "children": [
                (el("el:action1", "Search", "Button"), "screen:search"),
                (el("el:action2", "Profile", "Button"), "screen:profile"),
                (el("el:action3", "Settings", "Button"), "screen:settings"),
            ],
        },
        "screen:search": {
            "root": el("screen:search", "Search", "Window"),
            "children": [
                (el("el:type", "Search Query", "Edit"), "screen:search"),   # self-loop
                (el("el:filter", "Filter", "Button"), "screen:filtered"),
                (el("el:back1", "Back", "Button"), "__BACK__"),
            ],
        },
        "screen:filtered": {
            "root": el("screen:filtered", "Filtered Results", "Window"),
            "children": [
                (el("el:back2", "Back", "Button"), "__BACK__"),
            ],
        },
        "screen:profile": {
            "root": el("screen:profile", "Profile", "Window"),
            "children": [],   # terminal node
        },
        "screen:settings": {
            "root": el("screen:settings", "Settings", "Window"),
            "children": [
                (el("el:delete", "Delete Account", "Button"), "screen:deleted"),
            ],
        },
        "screen:deleted": {
            "root": el("screen:deleted", "Account Deleted", "Window"),
            "children": [],
        },
    }
    # Every non-root screen needs a back_map entry so the ESCAPE fallback
    # can recover it even when (as with Profile/Settings here) the screen
    # has no NATIVE_ACCESSIBILITY_BACK control of its own to try first.
    back_map = {
        "screen:search": "screen:root",
        "screen:filtered": "screen:search",
        "screen:profile": "screen:root",
        "screen:settings": "screen:root",
    }
    return FakeAccessibilityBackend(screens, "screen:root", back_map)


def test_mapping_visits_every_reachable_screen():
    backend = _build_spec_shaped_backend()
    tree = map_application_ui("TestApp", backend=backend, max_nodes=50, max_depth=10)

    # 5 real screens reachable: root, search, filtered, profile, settings.
    # "screen:deleted" must NEVER be reached (destructive action skipped).
    assert tree.node_count() == 5


def test_destructive_action_is_recorded_but_never_triggered():
    backend = _build_spec_shaped_backend()
    tree = map_application_ui("TestApp", backend=backend, max_nodes=50, max_depth=10)

    settings_node = next(n for n in tree.nodes.values() if n.current_node_id != tree.root_node_id and
                          any(a.accessibility_path == "el:delete" for a in n.discovered_actions))
    delete_action = next(a for a in settings_node.discovered_actions if a.accessibility_path == "el:delete")

    assert delete_action.is_destructive is True
    assert delete_action.target_node_id is None   # never traversed
    assert "el:delete" not in backend.activated    # never actually clicked


def test_deleted_screen_never_becomes_a_node():
    backend = _build_spec_shaped_backend()
    tree = map_application_ui("TestApp", backend=backend, max_nodes=50, max_depth=10)
    # "screen:deleted" exists in the fake's world model but must never be
    # reached - the mapper never triggers the destructive action leading to it.
    assert all(n.current_node_id != "screen:deleted" for n in tree.nodes.values())
    assert tree.node_count() == 5


def test_self_loop_is_detected_via_node_hashing_and_marked_stop_branch_max():
    backend = _build_spec_shaped_backend()
    tree = map_application_ui("TestApp", backend=backend, max_nodes=50, max_depth=10)

    search_node = next(n for n in tree.nodes.values() if any(a.accessibility_path == "el:type" for a in n.discovered_actions))
    type_action = next(a for a in search_node.discovered_actions if a.accessibility_path == "el:type")

    # The "Type" input loops back to the same screen - node-hashing should
    # recognize it as the already-seen search node, not allocate a new one.
    assert type_action.target_node_id == search_node.current_node_id


def test_root_node_has_multiple_branches_and_status_exploring_then_single_choice():
    backend = _build_spec_shaped_backend()
    tree = map_application_ui("TestApp", backend=backend, max_nodes=50, max_depth=10)

    root = tree.nodes[tree.root_node_id]
    assert len(root.discovered_actions) == 3
    # By the time the mapping run finishes, the root's last-tried action left it
    # at SINGLE_CHOICE_LEFT (no more unvisited branches).
    assert root.screen_status == ScreenStatus.SINGLE_CHOICE_LEFT


def test_terminal_node_with_no_actions_is_stop_branch_max():
    backend = _build_spec_shaped_backend()
    tree = map_application_ui("TestApp", backend=backend, max_nodes=50, max_depth=10)

    profile_node = next(n for n in tree.nodes.values() if n.current_node_id != tree.root_node_id and not n.discovered_actions)
    assert profile_node.screen_status == ScreenStatus.STOP_BRANCH_MAX


def test_max_depth_truncates_and_marks_stop_branch_max():
    # A long linear chain, deeper than max_depth.
    screens = {"screen:0": {"root": el("screen:0", "S0", "Window"),
                             "children": [(el("el:next0", "Next", "Button"), "screen:1")]}}
    for i in range(1, 6):
        screens[f"screen:{i}"] = {
            "root": el(f"screen:{i}", f"S{i}", "Window"),
            "children": [(el(f"el:next{i}", "Next", "Button"), f"screen:{i + 1}")],
        }
    screens["screen:6"] = {"root": el("screen:6", "S6", "Window"), "children": []}
    back_map = {f"screen:{i}": f"screen:{i - 1}" for i in range(1, 7)}

    backend = FakeAccessibilityBackend(screens, "screen:0", back_map)
    tree = map_application_ui("Chain", backend=backend, max_nodes=50, max_depth=2)

    # depth 0 = root, depth 1, depth 2 reached; depth 2's node hits the cap.
    assert tree.node_count() == 3
    deepest = max(tree.nodes.values(), key=lambda n: int(n.current_node_id.rsplit("_", 1)[-1]))
    assert deepest.screen_status == ScreenStatus.STOP_BRANCH_MAX


def test_max_nodes_stops_the_mapping_run_early():
    screens = {"screen:0": {"root": el("screen:0", "S0", "Window"),
                             "children": [(el("el:next0", "Next", "Button"), "screen:1")]}}
    for i in range(1, 20):
        screens[f"screen:{i}"] = {
            "root": el(f"screen:{i}", f"S{i}", "Window"),
            "children": [(el(f"el:next{i}", "Next", "Button"), f"screen:{i + 1}")],
        }
    screens["screen:20"] = {"root": el("screen:20", "S20", "Window"), "children": []}
    back_map = {f"screen:{i}": f"screen:{i - 1}" for i in range(1, 21)}

    backend = FakeAccessibilityBackend(screens, "screen:0", back_map)
    tree = map_application_ui("LongChain", backend=backend, max_nodes=5, max_depth=50)

    assert tree.node_count() <= 5
