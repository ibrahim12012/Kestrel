# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_discovery_tree.py
==============================
Tests for ai/discovery/tree.py - the Action Tree data model and its
JSON serialization (the "State Graph Matrix" artifact from spec section 5).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai.discovery.tree import (
    ActionTree, AppStateNode, BacktrackAction, DiscoveredAction, ScreenStatus,
)


def _make_node(node_id, status=ScreenStatus.EXPLORING):
    return AppStateNode(
        app_name="TestApp",
        current_node_id=node_id,
        screen_status=status,
        backtrack_action=BacktrackAction(type="NATIVE_ACCESSIBILITY_BACK", accessibility_id="btn_back", fallback_key="ESCAPE"),
        discovered_actions=[
            DiscoveredAction(
                action_id="action_open_settings",
                element_type="Button",
                accessibility_path="/Window/SettingsButton",
                bounding_box={"x": 120, "y": 45, "w": 30, "h": 30},
                requires_input=False,
                target_node_id=None,
            ),
            DiscoveredAction(
                action_id="action_input_search_query",
                element_type="Edit",
                accessibility_path="/Window/SearchBar",
                bounding_box={"x": 200, "y": 45, "w": 150, "h": 30},
                requires_input=True,
                sample_value="test_query",
                target_node_id=None,
            ),
        ],
    )


def test_node_to_dict_matches_spec_schema_field_names():
    node = _make_node("screen_main_dashboard_004")
    d = node.to_dict()

    assert d["app_name"] == "TestApp"
    assert d["current_node_id"] == "screen_main_dashboard_004"
    assert d["screen_status"] == "EXPLORING"
    assert d["backtrack_action"] == {
        "type": "NATIVE_ACCESSIBILITY_BACK",
        "accessibility_id": "btn_back",
        "fallback_key": "ESCAPE",
    }
    assert d["discovered_actions"][0]["action_id"] == "action_open_settings"
    assert d["discovered_actions"][1]["requires_input"] is True
    assert d["discovered_actions"][1]["sample_value"] == "test_query"


def test_action_tree_save_and_load_roundtrip(tmp_path):
    tree = ActionTree(app_name="TestApp")
    root = _make_node("screen_root_001")
    tree.add_node(root)
    tree.root_node_id = "screen_root_001"

    path = tree.save(str(tmp_path / "testapp_tree.json"))
    assert os.path.isfile(path)

    loaded = ActionTree.load(path)
    assert loaded.app_name == "TestApp"
    assert loaded.root_node_id == "screen_root_001"
    assert loaded.node_count() == 1
    loaded_node = loaded.nodes["screen_root_001"]
    assert loaded_node.screen_status == ScreenStatus.EXPLORING
    assert len(loaded_node.discovered_actions) == 2
    assert loaded_node.discovered_actions[0].accessibility_path == "/Window/SettingsButton"


def test_action_and_node_counts():
    tree = ActionTree(app_name="TestApp")
    tree.add_node(_make_node("n1"))
    tree.add_node(_make_node("n2"))
    assert tree.node_count() == 2
    assert tree.action_count() == 4


def test_discovered_action_defaults_not_destructive():
    action = DiscoveredAction(
        action_id="a", element_type="Button", accessibility_path="/x", bounding_box={"x": 0, "y": 0, "w": 1, "h": 1},
    )
    assert action.is_destructive is False
    assert action.to_dict()["is_destructive"] is False
