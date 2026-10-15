# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_discovery_outline.py
=================================
The mapped app as the model reads it (ActionTree.outline): screens as a
tree, each control with its name and centre, and the fields that let
the overlay draw the tree surviving a save and load.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai.discovery.tree import ActionTree, AppStateNode, BacktrackAction, DiscoveredAction, ScreenStatus


def _action(action_id, name, x, y, target=None, destructive=False, kind="Button"):
    return DiscoveredAction(
        action_id=action_id, element_type=kind, accessibility_path=f"/w/{action_id}",
        bounding_box={"x": x, "y": y, "w": 40, "h": 20}, target_node_id=target,
        is_destructive=destructive, name=name,
    )


def _node(node_id, actions, title="", parent=None, via=None):
    return AppStateNode(
        app_name="Notes", current_node_id=node_id, screen_status=ScreenStatus.EXPLORING,
        backtrack_action=BacktrackAction(), discovered_actions=actions,
        title=title, parent_node_id=parent, via_action_id=via,
    )


def _tree():
    tree = ActionTree(app_name="Notes", root_node_id="home")
    tree.add_node(_node("home", [
        _action("open_settings", "Settings", 100, 10, target="settings"),
        _action("delete_all", "Delete all", 300, 10, destructive=True),
    ], title="Notes"))
    tree.add_node(_node("settings", [
        _action("back", "Back", 10, 10, target="home"),
        _action("dark_mode", "Dark mode", 50, 80, kind="CheckBox"),
    ], title="Settings", parent="home", via="open_settings"))
    return tree


def test_screens_nest_under_the_control_that_opens_them():
    lines = _tree().outline().splitlines()
    assert lines[0] == 'Screen home "Notes"'
    assert lines[1] == "  - Button 'Settings' at (120, 20)"
    assert lines[2] == '    Screen settings "Settings"'
    assert "  - CheckBox 'Dark mode' at (70, 90)" in lines[4]


def test_a_way_back_to_a_known_screen_points_at_it_instead_of_repeating_it():
    text = _tree().outline()
    assert "Button 'Back' at (30, 20) -> home" in text
    assert text.count("Screen home") == 1


def test_destructive_controls_are_marked():
    assert "Button 'Delete all' at (320, 20) [destructive, never clicked while mapping]" in _tree().outline()


def test_a_long_map_is_cut_short_and_says_so():
    tree = ActionTree(app_name="Big", root_node_id="n0")
    tree.add_node(_node("n0", [_action(f"a{i}", f"Item {i}", i, i) for i in range(50)]))
    lines = tree.outline(max_lines=10).splitlines()
    assert len(lines) == 11
    assert "map continues" in lines[-1]


def test_names_titles_and_parents_survive_a_save(tmp_path):
    path = _tree().save(str(tmp_path / "notes.json"))
    loaded = ActionTree.load(path)
    settings = loaded.nodes["settings"]
    assert (settings.title, settings.parent_node_id, settings.via_action_id) == ("Settings", "home", "open_settings")
    assert settings.discovered_actions[1].name == "Dark mode"
    assert settings.discovered_actions[1].center() == (70, 90)


def test_a_tree_saved_before_names_existed_still_loads():
    old = {"app_name": "Old", "root_node_id": "a", "nodes": {"a": {
        "app_name": "Old", "current_node_id": "a", "screen_status": "EXPLORING",
        "backtrack_action": {}, "discovered_actions": [{
            "action_id": "x", "element_type": "Button", "accessibility_path": "/x",
            "bounding_box": {"x": 0, "y": 0, "w": 10, "h": 10}}]}}}
    tree = ActionTree.from_dict(old)
    node = tree.nodes["a"]
    assert node.title == "" and node.parent_node_id is None
    assert "Button '(no name)' at (5, 5)" in tree.outline()
