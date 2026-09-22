# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_discovery_compiler.py
==================================
Tests for ai/discovery/compiler.py - the Atomic Macro Compilation
Pipeline (spec section 4): one function per discovered action, valid
Python, destructive actions gated behind confirmed=True.
"""

import ast
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.discovery.compiler as compiler
from ai.discovery.tree import ActionTree, AppStateNode, BacktrackAction, DiscoveredAction, ScreenStatus


def _make_tree():
    tree = ActionTree(app_name="Notepad")
    root = AppStateNode(
        app_name="Notepad",
        current_node_id="screen_notepad_001",
        screen_status=ScreenStatus.EXPLORING,
        backtrack_action=BacktrackAction(),
        discovered_actions=[
            DiscoveredAction(
                action_id="action_save_0", element_type="Button",
                accessibility_path="/Window/SaveButton",
                bounding_box={"x": 10, "y": 10, "w": 20, "h": 20},
                requires_input=False, is_destructive=False,
            ),
            DiscoveredAction(
                action_id="action_filename_1", element_type="Edit",
                accessibility_path="/Window/FilenameBox",
                bounding_box={"x": 40, "y": 10, "w": 100, "h": 20},
                requires_input=True, sample_value="test_query", is_destructive=False,
            ),
            DiscoveredAction(
                action_id="action_delete_all_2", element_type="Button",
                accessibility_path="/Window/DeleteAllButton",
                bounding_box={"x": 200, "y": 10, "w": 20, "h": 20},
                requires_input=False, is_destructive=True,
            ),
        ],
    )
    tree.add_node(root)
    tree.root_node_id = "screen_notepad_001"
    return tree


def test_render_module_is_valid_python():
    tree = _make_tree()
    source = compiler.render_module(tree)
    ast.parse(source)   # must not raise


def test_render_module_produces_one_function_per_action():
    tree = _make_tree()
    source = compiler.render_module(tree)
    assert source.count("def tool_click_") + source.count("def tool_input_") == 3


def test_non_destructive_click_has_no_confirmation_param():
    tree = _make_tree()
    source = compiler.render_module(tree)
    assert "def tool_click_screen_notepad_001_action_save_0() -> str:" in source


def test_input_action_is_parameterized_with_type_hint():
    tree = _make_tree()
    source = compiler.render_module(tree)
    assert "def tool_input_screen_notepad_001_action_filename_1(text_value: str) -> str:" in source


def test_destructive_action_requires_confirmation():
    tree = _make_tree()
    source = compiler.render_module(tree)
    assert "confirmed: bool = False" in source
    assert "if not confirmed:" in source
    assert "Refused:" in source


def test_compile_action_tree_writes_file(tmp_path):
    tree = _make_tree()
    path = compiler.compile_action_tree(tree, out_dir=str(tmp_path))
    assert os.path.isfile(path)
    assert path.endswith("notepad_actions.py")
    with open(path) as f:
        ast.parse(f.read())


def test_register_compiled_actions_execs_into_module_namespace():
    tree = _make_tree()
    fake_module = types.ModuleType("fake_actions")
    count = compiler.register_compiled_actions(fake_module, tree)
    assert count == 3
    assert hasattr(fake_module, "tool_click_screen_notepad_001_action_save_0")
    assert hasattr(fake_module, "tool_input_screen_notepad_001_action_filename_1")
    assert hasattr(fake_module, "tool_click_screen_notepad_001_action_delete_all_2")


def test_destructive_compiled_function_refuses_without_confirmation():
    tree = _make_tree()
    fake_module = types.ModuleType("fake_actions")
    compiler.register_compiled_actions(fake_module, tree)

    fn = fake_module.tool_click_screen_notepad_001_action_delete_all_2
    result = fn()   # confirmed defaults to False
    assert "Refused" in result
    assert "confirmed=True" in result


def test_duplicate_function_names_are_deduplicated():
    tree = _make_tree()
    # Add a second node whose action happens to collide on name after sanitizing.
    dup_node = AppStateNode(
        app_name="Notepad",
        current_node_id="screen_notepad_002",
        screen_status=ScreenStatus.STOP_BRANCH_MAX,
        backtrack_action=BacktrackAction(),
        discovered_actions=[
            DiscoveredAction(
                action_id="action_save_0", element_type="Button",
                accessibility_path="/Window/OtherSaveButton",
                bounding_box={"x": 1, "y": 1, "w": 1, "h": 1},
            ),
        ],
    )
    tree.add_node(dup_node)
    source = compiler.render_module(tree)
    # Two DIFFERENT node ids -> two distinct function names, no collision.
    assert "def tool_click_screen_notepad_002_action_save_0() -> str:" in source
