# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_discovery_tool_wiring.py
=====================================
ai/actions.py's way into the mapping engine: run_mapping (the Mapping
page's Start and tool_discover_application_ui both run it) and
tool_use_mapped_control, which presses a mapped control by name. No real
accessibility backend: ai/discovery has its own tests for that.
"""

import os
import sys
import threading
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.actions as actions
import ai.risk_gate as risk_gate
import ai.safety as safety
from ai import automation_lock
from ai.discovery import tree as trees
from ai.discovery.accessibility import AccessibilityBackend, AccessibilityElement
from ai.discovery.tree import ActionTree, AppStateNode, BacktrackAction, DiscoveredAction, ScreenStatus


@pytest.fixture(autouse=True)
def maps_folder(tmp_path, monkeypatch):
    """Saved maps go to a folder of the test's own, and every test starts
    with nothing stopped and a fresh action budget."""
    monkeypatch.setattr(trees, "TREES_DIR", str(tmp_path))
    actions.emergency_stop = False
    safety.limiter.reset()
    yield tmp_path
    actions.single_fcn = False
    actions.emergency_stop = False


_BACK = BacktrackAction(type="KEY_FALLBACK", accessibility_id=None, fallback_key="ESCAPE")


def _tree(app="Notepad", ending="complete", screen_title="Untitled - Notepad", actions_=()):
    tree = ActionTree(app_name=app, root_node_id="screen_notepad_001", ending=ending,
                      window_title=screen_title, program="notepad.exe")
    node = AppStateNode(app_name=app, current_node_id="screen_notepad_001",
                        screen_status=ScreenStatus.EXPLORING, backtrack_action=_BACK,
                        discovered_actions=list(actions_), title=screen_title)
    tree.add_node(node)
    return tree


def _action(action_id, name, path, element_type="Button", destructive=False, requires_input=False):
    return DiscoveredAction(
        action_id=action_id, element_type=element_type, accessibility_path=path,
        bounding_box={"x": 10, "y": 10, "w": 40, "h": 20}, requires_input=requires_input,
        is_destructive=destructive, name=name,
    )


# ── the tools are there ──────────────────────────────────────────────────

def test_both_mapping_tools_are_offered_to_the_ai():
    names = {t.__name__ for t in actions.get_all_tools(actions)}
    assert {"tool_discover_application_ui", "tool_use_mapped_control"} <= names


# ── run_mapping and tool_discover_application_ui ─────────────────────────

def test_a_stop_refuses_the_run():
    actions.emergency_stop = True
    result = actions.tool_discover_application_ui("Notepad")
    assert "EMERGENCY STOP" in result


def test_the_ai_is_held_to_its_action_budget():
    safety.limiter.set_limit(safety.RiskLevel.MEDIUM, 0)
    try:
        assert "SAFETY LIMIT" in actions.tool_discover_application_ui("Notepad")
    finally:
        safety.limiter.set_limit(safety.RiskLevel.MEDIUM, safety.DEFAULT_MAX_ACTIONS[safety.RiskLevel.MEDIUM])


def test_the_mapping_page_is_not():
    safety.limiter.set_limit(safety.RiskLevel.MEDIUM, 0)
    try:
        with patch("ai.discovery.map_application_ui", return_value=_tree()) as mapped:
            result = actions.run_mapping("Notepad", force_remap=True, ask_limiter=False)
        mapped.assert_called_once()
        assert result.ok and result.ending == "complete"
    finally:
        safety.limiter.set_limit(safety.RiskLevel.MEDIUM, safety.DEFAULT_MAX_ACTIONS[safety.RiskLevel.MEDIUM])


def test_a_window_that_isnt_there_is_reported():
    from ai.discovery.errors import DiscoveryError
    with patch("ai.discovery.map_application_ui", side_effect=DiscoveryError("no window found")):
        result = actions.run_mapping("NoSuchApp")
    assert result.ok is False and result.error == "no window found"
    assert actions.tool_discover_application_ui.__name__  # still a tool
    with patch("ai.discovery.map_application_ui", side_effect=DiscoveryError("no window found")):
        text = actions.tool_discover_application_ui("NoSuchApp")
    assert text == "Could not map NoSuchApp: no window found"


def test_a_run_that_fails_partway_still_says_which_window_it_was():
    """The Mapping page brings Kestrel back over that app with the error."""
    def fails_after_finding_the_window(app_name, **kwargs):
        kwargs["on_window"]("Untitled - Notepad", "notepad.exe")
        raise RuntimeError("the window went away")

    with patch("ai.discovery.map_application_ui", side_effect=fails_after_finding_the_window):
        result = actions.run_mapping("Notepad")
    assert result.error == "the window went away"
    assert (result.window, result.program) == ("Untitled - Notepad", "notepad.exe")


def test_the_overlay_hears_the_run_ended_even_when_it_failed():
    from ai.discovery.errors import DiscoveryError
    from ai.discovery import events as discovery_events

    finished_calls = []
    discovery_events.on_finished(lambda app, nodes, count: finished_calls.append((app, nodes, count)))
    try:
        with patch("ai.discovery.map_application_ui", side_effect=DiscoveryError("no window found")):
            actions.tool_discover_application_ui("NoSuchApp")
    finally:
        discovery_events._finished_callbacks.clear()
    assert finished_calls == [("NoSuchApp", 0, 0)]


def test_a_finished_run_is_saved_and_described(maps_folder):
    tree = _tree(actions_=[_action("action_save_0", "Save", "save-button")])
    with patch("ai.discovery.map_application_ui", return_value=tree):
        text = actions.tool_discover_application_ui("Notepad")
    assert os.path.exists(trees.map_path("Notepad"))
    assert text.startswith("Mapped Notepad (window 'Untitled - Notepad'): 1 screen(s), 1 control(s).")
    assert "tool_use_mapped_control" in text
    assert "Button 'Save'" in text
    assert actions.single_fcn is True


def test_no_tools_are_compiled_per_control_any_more():
    tree = _tree(actions_=[_action("action_save_0", "Save", "save-button")])
    with patch("ai.discovery.map_application_ui", return_value=tree):
        actions.tool_discover_application_ui("Notepad")
    names = {t.__name__ for t in actions.get_all_tools(actions)}
    assert not any(n.startswith("tool_click_") or n.startswith("tool_input_") for n in names)


def test_a_finished_map_is_reused_without_clicking():
    _tree().save(trees.map_path("Notepad"))
    with patch("ai.discovery.map_application_ui") as mapped:
        text = actions.tool_discover_application_ui("Notepad")
    mapped.assert_not_called()
    assert "was mapped before" in text and "force_remap=True" in text
    assert actions.single_fcn is False


def test_a_map_that_was_stopped_is_not_reused():
    _tree(ending="stopped").save(trees.map_path("Notepad"))
    with patch("ai.discovery.map_application_ui", return_value=_tree()) as mapped:
        result = actions.run_mapping("Notepad")
    mapped.assert_called_once()
    assert result.reused is False and result.ending == "complete"


def test_a_map_saved_before_runs_recorded_their_ending_is_mapped_again():
    data = _tree().to_dict()
    del data["ending"]
    import json
    with open(trees.map_path("Notepad"), "w", encoding="utf-8") as f:
        json.dump(data, f)
    with patch("ai.discovery.map_application_ui", return_value=_tree()) as mapped:
        actions.run_mapping("Notepad")
    mapped.assert_called_once()


def test_force_remap_maps_again():
    _tree().save(trees.map_path("Notepad"))
    with patch("ai.discovery.map_application_ui", return_value=_tree()) as mapped:
        text = actions.tool_discover_application_ui("Notepad", force_remap=True)
    mapped.assert_called_once()
    assert text.startswith("Mapped Notepad")


def test_a_run_stopped_short_keeps_the_finished_map():
    path = trees.map_path("Notepad")
    _tree(actions_=[_action("a_0", "Save", "p1"), _action("a_1", "Open", "p2")]).save(path)
    partial = _tree(ending="stopped", actions_=[_action("a_0", "Save", "p1")])
    with patch("ai.discovery.map_application_ui", return_value=partial):
        result = actions.run_mapping("Notepad", force_remap=True)
    assert result.ok and result.kept_earlier and result.ending == "stopped"
    assert ActionTree.load(path).action_count() == 2
    text = actions._mapping_report(result)
    assert "The earlier full map of Notepad is kept" in text


def test_a_run_stopped_short_with_nothing_earlier_is_saved():
    with patch("ai.discovery.map_application_ui", return_value=_tree(ending="screen_limit")):
        result = actions.run_mapping("Notepad")
    assert result.ok and not result.kept_earlier
    assert ActionTree.load(trees.map_path("Notepad")).ending == "screen_limit"
    assert "ended before it finished" in actions._mapping_report(result)


def test_one_automation_at_a_time():
    held, release = threading.Event(), threading.Event()

    def ai_run():
        with automation_lock.hold("an AI run"):
            held.set()
            release.wait(5)

    thread = threading.Thread(target=ai_run)
    thread.start()
    try:
        held.wait(5)
        with patch("ai.discovery.map_application_ui") as mapped:
            result = actions.run_mapping("Notepad")
        mapped.assert_not_called()
        assert "Another automation is running (an AI run)" in result.error
    finally:
        release.set()
        thread.join(5)


def test_an_ai_run_can_map_an_app_itself():
    with automation_lock.hold("an AI run"):
        with patch("ai.discovery.map_application_ui", return_value=_tree()):
            result = actions.run_mapping("Notepad")
    assert result.ok
    assert automation_lock.running() == ""


# ── tool_use_mapped_control ──────────────────────────────────────────────

class _LiveWindow(AccessibilityBackend):
    """The app's window as it is now: `live` maps a path to the element
    found there, or to nothing."""

    def __init__(self, live):
        self.live = live
        self.pressed = []
        self.typed = []
        self.unminimized = 0

    def show_if_minimized(self):
        self.unminimized += 1

    def find_by_path(self, path):
        return self.live.get(path)

    def activate(self, element):
        self.pressed.append(element.id)
        return True

    def input_value(self, element, value):
        self.typed.append((element.id, value))
        return True


def _element(path, name, control_type="Button", enabled=True):
    return AccessibilityElement(id=path, name=name, control_type=control_type, is_enabled=enabled)


@pytest.fixture
def live_window(monkeypatch):
    window = _LiveWindow({})
    monkeypatch.setattr("ai.discovery.accessibility.get_backend", lambda app, take=True: window)
    return window


def test_no_saved_map():
    assert "has no saved map" in actions.tool_use_mapped_control("Notepad", "Save")


def test_a_control_that_isnt_in_the_map():
    _tree(actions_=[_action("a_0", "Save", "save")]).save(trees.map_path("Notepad"))
    assert "has no control named 'Print'" in actions.tool_use_mapped_control("Notepad", "Print")


def test_part_of_a_name_is_not_enough(live_window):
    """"Save" is not "AutoSave": the names close to it are offered instead."""
    _tree(actions_=[_action("a_0", "AutoSave", "autosave", element_type="CheckBox"),
                    _action("a_1", "Save As", "save-as")]).save(trees.map_path("Notepad"))
    live_window.live = {"autosave": _element("autosave", "AutoSave", control_type="CheckBox")}
    text = actions.tool_use_mapped_control("Notepad", "Save")
    assert "has no control named 'Save'" in text and "'AutoSave'" in text and "'Save As'" in text
    assert live_window.pressed == []


def test_a_control_on_screen_is_pressed(live_window):
    _tree(actions_=[_action("a_0", "Save", "save")]).save(trees.map_path("Notepad"))
    live_window.live = {"save": _element("save", "Save")}
    assert actions.tool_use_mapped_control("Notepad", "save") == "Pressed 'Save' in Notepad."
    assert live_window.pressed == ["save"]
    # A minimized window is shown first: its controls are all off screen.
    assert live_window.unminimized == 1


def test_a_control_not_on_screen_is_not_clicked_at_its_old_position(live_window):
    _tree(actions_=[_action("a_0", "Save", "save")]).save(trees.map_path("Notepad"))
    text = actions.tool_use_mapped_control("Notepad", "Save")
    assert "isn't in Notepad's window now, so nothing was pressed" in text
    assert 'screen_notepad_001 "Untitled - Notepad"' in text
    assert live_window.pressed == []


def test_the_answer_says_which_control_opens_the_screen_it_is_on(live_window):
    tree = _tree(actions_=[_action("a_0", "Font...", "font-menu")])
    dialog = AppStateNode(app_name="Notepad", current_node_id="screen_notepad_002",
                          screen_status=ScreenStatus.EXPLORING, backtrack_action=_BACK, title="Font",
                          parent_node_id="screen_notepad_001", via_action_id="a_0",
                          discovered_actions=[_action("a_1", "Bold", "bold", element_type="CheckBox")])
    tree.add_node(dialog)
    tree.save(trees.map_path("Notepad"))
    text = actions.tool_use_mapped_control("Notepad", "Bold")
    assert "screen_notepad_002 \"Font\", opened by 'Font...' on screen_notepad_001" in text


def test_a_position_now_holding_another_control_is_refused(live_window):
    _tree(actions_=[_action("a_0", "Save", "Pane[0]/Button[3]")]).save(trees.map_path("Notepad"))
    live_window.live = {"Pane[0]/Button[3]": _element("Pane[0]/Button[3]", "Delete")}
    assert "nothing was pressed" in actions.tool_use_mapped_control("Notepad", "Save")
    assert live_window.pressed == []


def test_a_greyed_out_control(live_window):
    _tree(actions_=[_action("a_0", "Save", "save")]).save(trees.map_path("Notepad"))
    live_window.live = {"save": _element("save", "Save", enabled=False)}
    assert "greyed out" in actions.tool_use_mapped_control("Notepad", "Save")
    assert live_window.pressed == []


def test_a_destructive_control_asks_first(live_window, monkeypatch):
    asked = []
    monkeypatch.setattr(risk_gate, "require_approval", lambda prompt: asked.append(prompt) or False)
    _tree(actions_=[_action("a_0", "Delete", "del", destructive=True)]).save(trees.map_path("Notepad"))
    live_window.live = {"del": _element("del", "Delete")}
    assert actions.tool_use_mapped_control("Notepad", "Delete").startswith("Refused")
    assert asked == ['Press "Delete" in Notepad?']
    assert live_window.pressed == []


def test_a_field_takes_text(live_window):
    _tree(actions_=[_action("a_0", "Search", "find", element_type="Edit", requires_input=True)]).save(
        trees.map_path("Notepad"))
    live_window.live = {"find": _element("find", "Search", control_type="Edit")}
    assert "Pass the text" in actions.tool_use_mapped_control("Notepad", "Search")
    assert actions.tool_use_mapped_control("Notepad", "Search", text="hello").startswith("Typed")
    assert live_window.typed == [("find", "hello")]


def test_a_button_takes_no_text(live_window):
    _tree(actions_=[_action("a_0", "Save", "save")]).save(trees.map_path("Notepad"))
    live_window.live = {"save": _element("save", "Save")}
    assert "takes no text" in actions.tool_use_mapped_control("Notepad", "Save", text="x")
    assert live_window.pressed == []


def test_the_screen_picks_between_two_controls_of_the_same_name(live_window):
    tree = _tree(actions_=[_action("a_0", "OK", "ok-main")])
    dialog = AppStateNode(app_name="Notepad", current_node_id="screen_notepad_002",
                          screen_status=ScreenStatus.EXPLORING, backtrack_action=_BACK, title="Font",
                          discovered_actions=[_action("a_1", "OK", "ok-font")])
    tree.add_node(dialog)
    tree.save(trees.map_path("Notepad"))
    live_window.live = {"ok-main": _element("ok-main", "OK"), "ok-font": _element("ok-font", "OK")}
    actions.tool_use_mapped_control("Notepad", "OK", screen="Font")
    assert live_window.pressed == ["ok-font"]
