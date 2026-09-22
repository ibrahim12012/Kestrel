# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_discovery_tool_wiring.py
=====================================
Tests for tool_discover_application_ui in ai/actions.py - the AI-facing
entry point into the Phase 2 discovery engine. Doesn't exercise a real
accessibility backend (that's ai/discovery's own tests); just checks the
tool follows the same conventions every other tool_* in ai/actions.py
does (emergency stop, safety limiter, get_all_tools discovery) and wires
correctly through to ai.discovery on success/failure.
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.actions as actions
import ai.safety as safety


def test_tool_discover_application_ui_is_registered_as_a_tool():
    tools = actions.get_all_tools(actions)
    assert any(t.__name__ == "tool_discover_application_ui" for t in tools)


def test_tool_discover_application_ui_respects_emergency_stop():
    actions.emergency_stop = True
    try:
        result = actions.tool_discover_application_ui("Notepad")
        assert "EMERGENCY STOP" in result
    finally:
        actions.emergency_stop = False


def test_tool_discover_application_ui_respects_safety_limiter():
    safety.limiter.reset()
    safety.limiter.set_limit(safety.RiskLevel.MEDIUM, 0)
    try:
        result = actions.tool_discover_application_ui("Notepad")
        assert "SAFETY LIMIT" in result
    finally:
        safety.limiter.set_limit(safety.RiskLevel.MEDIUM, safety.DEFAULT_MAX_ACTIONS[safety.RiskLevel.MEDIUM])
        safety.limiter.reset()


def test_tool_discover_application_ui_reports_discovery_error_cleanly():
    from ai.discovery.errors import DiscoveryError
    safety.limiter.reset()

    with patch("ai.discovery.map_application_ui", side_effect=DiscoveryError("no window found")):
        result = actions.tool_discover_application_ui("NoSuchApp")

    assert "Could not discover" in result
    assert "no window found" in result


def test_tool_discover_application_ui_notifies_finished_even_on_discovery_error():
    """
    Regression test: notify_finished() must fire on every exit path, not
    just success - otherwise a Mapping Monitor window (app/mapping_monitor.py)
    that's watching on_started/on_finished would stay shown/spinning
    forever after a failed run.
    """
    from ai.discovery.errors import DiscoveryError
    from ai.discovery import events as discovery_events
    safety.limiter.reset()

    finished_calls = []
    discovery_events.on_finished(lambda app_name, nodes, actions_count: finished_calls.append((app_name, nodes, actions_count)))
    try:
        with patch("ai.discovery.map_application_ui", side_effect=DiscoveryError("no window found")):
            actions.tool_discover_application_ui("NoSuchApp")
    finally:
        discovery_events._finished_callbacks.clear()

    assert finished_calls == [("NoSuchApp", 0, 0)]


def test_tool_discover_application_ui_success_path_saves_and_compiles():
    from ai.discovery.tree import ActionTree
    safety.limiter.reset()

    fake_tree = ActionTree(app_name="Notepad")
    fake_tree.root_node_id = "screen_notepad_001"

    with patch("ai.discovery.map_application_ui", return_value=fake_tree) as mock_map, \
         patch.object(ActionTree, "save", return_value="/fake/path/notepad_tree.json") as mock_save, \
         patch("ai.discovery.compile_action_tree", return_value="/fake/path/notepad_actions.py") as mock_compile, \
         patch("ai.discovery.compiler.register_compiled_actions", return_value=2) as mock_register:
        result = actions.tool_discover_application_ui("Notepad")

    # on_progress is discovery_events.notify_progress - a real function
    # object we didn't create, so match on the positional call and the
    # keyword's presence rather than its exact identity.
    assert mock_map.call_args.args == ("Notepad",)
    assert "on_progress" in mock_map.call_args.kwargs
    mock_save.assert_called_once()
    mock_compile.assert_called_once_with(fake_tree)
    mock_register.assert_called_once()
    assert "Mapped Notepad" in result
    assert actions.single_fcn is True
    actions.single_fcn = False


def test_tool_discover_application_ui_reuses_existing_map_instead_of_reclicking(tmp_path):
    """
    Regression test: this used to click through the ENTIRE app from
    scratch on every call, even when a saved state graph from a
    previous run already existed on disk for this app_name - the
    visible symptom being "it keeps going back into the same buttons
    even though it already mapped them", every single time. Now, with
    a saved tree present and force_remap left at its default (False),
    map_application_ui() (the part that actually clicks through the
    app) must NOT be called at all - the existing tree is loaded and
    its actions re-registered instead.
    """
    from ai.discovery.tree import ActionTree
    safety.limiter.reset()

    tree_path = tmp_path / "notepad_tree.json"
    saved_tree = ActionTree(app_name="Notepad")
    saved_tree.root_node_id = "screen_notepad_001"
    saved_tree.save(str(tree_path))

    with patch.object(ActionTree, "_default_path", return_value=str(tree_path)), \
         patch("ai.discovery.map_application_ui") as mock_map, \
         patch("ai.discovery.compile_action_tree", return_value="/fake/path/notepad_actions.py") as mock_compile, \
         patch("ai.discovery.compiler.register_compiled_actions", return_value=3) as mock_register:
        result = actions.tool_discover_application_ui("Notepad")

    mock_map.assert_not_called()
    mock_compile.assert_called_once()
    mock_register.assert_called_once()
    assert "already mapped" in result
    assert "force_remap=True" in result


def test_tool_discover_application_ui_force_remap_ignores_existing_map(tmp_path):
    """force_remap=True must skip the reuse path and click through the
    app again, same as if no saved map existed at all."""
    from ai.discovery.tree import ActionTree
    safety.limiter.reset()

    tree_path = tmp_path / "notepad_tree.json"
    saved_tree = ActionTree(app_name="Notepad")
    saved_tree.save(str(tree_path))

    fresh_tree = ActionTree(app_name="Notepad")
    fresh_tree.root_node_id = "screen_notepad_002"

    with patch.object(ActionTree, "_default_path", return_value=str(tree_path)), \
         patch("ai.discovery.map_application_ui", return_value=fresh_tree) as mock_map, \
         patch.object(ActionTree, "save", return_value=str(tree_path)), \
         patch("ai.discovery.compile_action_tree", return_value="/fake/path/notepad_actions.py"), \
         patch("ai.discovery.compiler.register_compiled_actions", return_value=1):
        result = actions.tool_discover_application_ui("Notepad", force_remap=True)

    mock_map.assert_called_once()
    assert "Mapped Notepad" in result
    actions.single_fcn = False  # see test_tool_discover_application_ui_success_path_saves_and_compiles's own cleanup - this test hits the same success path (force_remap bypasses the new reuse branch) and needs the same reset, missing it leaked into test_smoke.py's unrelated tests
