# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_macro_store.py
===========================
Tests for ai/macro_store.py - JSON-backed macro persistence, and
synthesizing/registering real tool_* functions from those JSON records.
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.macro_store as macro_store


@pytest.fixture(autouse=True)
def _isolated_macros_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))
    macro_store._currently_registered = set()
    yield tmp_path


def test_save_and_load_literal_macro(tmp_path):
    macro_store.save_literal_macro("my_macro", [("click", 1, 2), ("enter", "enter")])

    spec = macro_store.load_spec("my_macro")
    assert spec["kind"] == "literal"
    assert spec["name"] == "my_macro"
    assert spec["recorded_actions"] == [["click", 1, 2], ["enter", "enter"]]
    assert macro_store.macro_exists("my_macro")


def test_save_generalized_spec_uses_sanitized_function_name(tmp_path):
    spec = {
        "confident": True,
        "function_name": "Search Y\u00f6uTube",
        "description": "Search YouTube for a query.",
        "parameters": [{"name": "query", "description": "search text"}],
        "steps": [{"type": "write", "value": "{query}"}, {"type": "enter"}],
    }
    macro_store.save_generalized_spec(spec)

    names = macro_store.list_macro_names()
    assert len(names) == 1
    saved = macro_store.load_spec(names[0])
    assert saved["kind"] == "generalized"
    assert saved["parameters"] == spec["parameters"]
    assert saved["steps"] == spec["steps"]


def test_delete_macro_removes_file(tmp_path):
    macro_store.save_literal_macro("throwaway", [("click", 1, 1)])
    assert macro_store.macro_exists("throwaway")

    deleted = macro_store.delete_macro("throwaway")

    assert deleted is True
    assert not macro_store.macro_exists("throwaway")


def test_delete_macro_returns_false_when_nothing_to_delete(tmp_path):
    assert macro_store.delete_macro("never_existed") is False


def test_list_macro_names_is_sorted(tmp_path):
    macro_store.save_literal_macro("zzz_macro", [])
    macro_store.save_literal_macro("aaa_macro", [])
    assert macro_store.list_macro_names() == ["aaa_macro", "zzz_macro"]


def test_resolve_macro_name_exact_match(tmp_path):
    macro_store.save_literal_macro("open_spotify_chill", [])
    assert macro_store.resolve_macro_name("open_spotify_chill") == "open_spotify_chill"


def test_resolve_macro_name_strips_tool_prefix(tmp_path):
    macro_store.save_literal_macro("open_spotify_chill", [])
    assert macro_store.resolve_macro_name("tool_open_spotify_chill") == "open_spotify_chill"


def test_resolve_macro_name_fuzzy_matches_close_typo(tmp_path):
    macro_store.save_literal_macro("send_email_gmail", [])
    assert macro_store.resolve_macro_name("send_email_gmial") == "send_email_gmail"


def test_resolve_macro_name_returns_none_for_no_match(tmp_path):
    macro_store.save_literal_macro("send_email_gmail", [])
    assert macro_store.resolve_macro_name("completely_unrelated_thing") is None


def test_resolve_macro_name_returns_none_when_store_empty(tmp_path):
    assert macro_store.resolve_macro_name("anything") is None


# ══════════════════════════════════════════════════════════════════
# register_macro / load_and_register_all - synthesizing real callables
# ══════════════════════════════════════════════════════════════════

def _fake_actions_module():
    """
    A throwaway module standing in for ai.actions, so registering/
    unregistering macro functions during tests never touches the real
    module other test files import and rely on.
    """
    mod = types.ModuleType("fake_actions_for_tests")
    mod.__name__ = "fake_actions_for_tests"

    def play_actions(recorded):
        mod._last_played = recorded

    mod.play_actions = play_actions
    return mod


def test_register_macro_literal_creates_callable_tool_function(tmp_path):
    mod = _fake_actions_module()
    spec = {
        "kind": "literal",
        "name": "click_thing",
        "description": "Auto-generated macro: click thing",
        "recorded_actions": [["click", 5, 5]],
    }

    ok = macro_store.register_macro(mod, spec)

    assert ok is True
    assert hasattr(mod, "tool_click_thing")
    mod.tool_click_thing()
    assert mod._last_played == [["click", 5, 5]]


def test_register_macro_generalized_creates_parameterized_function(tmp_path):
    mod = _fake_actions_module()
    calls = []
    mod.click_at_target_or_position = lambda target, x, y, **k: calls.append((target, x, y))
    spec = {
        "kind": "generalized",
        "name": "click_named_button",
        "description": "Click a named button.",
        "parameters": [{"name": "label", "description": "button label"}],
        "steps": [{"type": "click", "target": "{label} button", "x": 10, "y": 20}],
    }

    ok = macro_store.register_macro(mod, spec)

    assert ok is True
    assert hasattr(mod, "tool_click_named_button")


def test_register_macro_invalid_spec_returns_false_without_raising(tmp_path):
    mod = _fake_actions_module()
    bad_spec = {"kind": "literal", "name": "broken"}  # missing recorded_actions

    ok = macro_store.register_macro(mod, bad_spec)

    assert ok is False
    assert not hasattr(mod, "tool_broken")


def test_load_and_register_all_registers_every_saved_macro(tmp_path):
    macro_store.save_literal_macro("macro_one", [("click", 1, 1)])
    macro_store.save_literal_macro("macro_two", [("enter", "enter")])
    mod = _fake_actions_module()

    count = macro_store.load_and_register_all(mod)

    assert count == 2
    assert hasattr(mod, "tool_macro_one")
    assert hasattr(mod, "tool_macro_two")


def test_load_and_register_all_removes_stale_deleted_macro(tmp_path):
    macro_store.save_literal_macro("keep_me", [("click", 1, 1)])
    macro_store.save_literal_macro("delete_me", [("click", 2, 2)])
    mod = _fake_actions_module()
    macro_store.load_and_register_all(mod)
    assert hasattr(mod, "tool_keep_me")
    assert hasattr(mod, "tool_delete_me")

    macro_store.delete_macro("delete_me")
    macro_store.load_and_register_all(mod)

    assert hasattr(mod, "tool_keep_me")
    assert not hasattr(mod, "tool_delete_me")


def test_load_and_register_all_returns_zero_when_store_is_empty(tmp_path):
    mod = _fake_actions_module()
    assert macro_store.load_and_register_all(mod) == 0


# ── Manual step editing (app/macro_editor_dialog.py's storage layer) ──────

def test_literal_action_editor_step_round_trip():
    actions = [
        ["click", 10, 20],
        ["right_click", 30, 40],
        ["hello world", "write"],
        ["enter", 1],
        ["new_page", 1],
        ["notepad", "open"],
    ]
    for action in actions:
        step = macro_store.literal_action_to_editor_step(action)
        assert macro_store.editor_step_to_literal_action(step) == action


def test_update_literal_actions_reorders_and_inserts_steps(tmp_path):
    macro_store.save_literal_macro("clicker", [["click", 1, 1], ["click", 2, 2]])
    steps = [macro_store.literal_action_to_editor_step(a)
             for a in macro_store.load_spec("clicker")["recorded_actions"]]

    # Reorder (swap the two), then insert a brand new step at the end.
    steps[0], steps[1] = steps[1], steps[0]
    steps.append({"type": "enter"})

    ok, err = macro_store.update_literal_actions("clicker", "Clicks two things then presses Enter", steps)

    assert ok, err
    spec = macro_store.load_spec("clicker")
    assert spec["recorded_actions"] == [["click", 2, 2], ["click", 1, 1], ["enter", 1]]
    assert spec["description"] == "Clicks two things then presses Enter"


def test_update_literal_actions_rejects_empty_step_list(tmp_path):
    macro_store.save_literal_macro("clicker", [["click", 1, 1]])
    ok, err = macro_store.update_literal_actions("clicker", "desc", [])
    assert ok is False
    assert "at least one step" in err
    # Spec on disk must be untouched.
    assert macro_store.load_spec("clicker")["recorded_actions"] == [["click", 1, 1]]


def test_update_literal_actions_rejects_unknown_macro(tmp_path):
    ok, err = macro_store.update_literal_actions("does_not_exist", "desc", [{"type": "enter"}])
    assert ok is False
    assert "does_not_exist" in err


def test_update_generalized_steps_saves_valid_edit(tmp_path):
    spec = {
        "confident": True,
        "function_name": "search_youtube",
        "description": "Search YouTube for a query.",
        "parameters": [{"name": "query", "description": "search text"}],
        "steps": [{"type": "write", "value": "{query}"}, {"type": "enter"}],
    }
    macro_store.save_generalized_spec(spec)

    new_steps = [
        {"type": "click", "target": "the search box", "x": 100, "y": 50},
        {"type": "write", "value": "{query}"},
        {"type": "enter"},
    ]
    ok, err = macro_store.update_generalized_steps(
        "search_youtube", "Search YouTube for a query (now clicks the box first).",
        spec["parameters"], new_steps,
    )

    assert ok, err
    saved = macro_store.load_spec("search_youtube")
    assert saved["steps"] == new_steps
    assert saved["description"].startswith("Search YouTube for a query (now")


def test_update_generalized_steps_rejects_undeclared_parameter(tmp_path):
    spec = {
        "confident": True,
        "function_name": "search_youtube",
        "description": "Search YouTube for a query.",
        "parameters": [{"name": "query", "description": "search text"}],
        "steps": [{"type": "write", "value": "{query}"}, {"type": "enter"}],
    }
    macro_store.save_generalized_spec(spec)

    bad_steps = [{"type": "write", "value": "{typo_param}"}, {"type": "enter"}]
    ok, err = macro_store.update_generalized_steps(
        "search_youtube", "desc", spec["parameters"], bad_steps
    )

    assert ok is False
    assert "typo_param" in err
    # Spec on disk must be untouched by the rejected edit.
    assert macro_store.load_spec("search_youtube")["steps"] == spec["steps"]


def test_update_generalized_steps_rejects_literal_macro():
    macro_store.save_literal_macro("clicker", [["click", 1, 1]])
    ok, err = macro_store.update_generalized_steps("clicker", "desc", [], [{"type": "enter"}])
    assert ok is False
    assert "clicker" in err


# ── Provenance ────────────────────────────────────────────────────────
# Every saved macro used to be badged "AI" on the Macros page, including
# the ones the person recorded themselves - which is most of them. A
# recording is the user's work; only the generalizer authors a macro.

def test_a_recorded_macro_is_the_users():
    macro_store.save_literal_macro("recorded_one", [["click", 1, 2]], "Does a thing.")
    spec = macro_store.load_spec("recorded_one")
    assert spec["created_by"] == macro_store.CREATED_BY_USER
    assert macro_store.created_by(spec) == "user"


def test_a_generalized_macro_is_the_ais():
    macro_store.save_generalized_spec({
        "function_name": "generalized_one",
        "description": "Searches for something.",
        "parameters": [{"name": "term", "description": "what to search"}],
        "steps": [{"type": "click", "target": "the search box", "x": 1, "y": 2}],
    })
    assert macro_store.created_by(macro_store.load_spec("generalized_one")) == "ai"


def test_provenance_is_inferred_for_specs_saved_before_the_field_existed():
    """An install upgrading from an older build has specs with no
    created_by - those must not all fall back to "AI"."""
    assert macro_store.created_by({"kind": "literal", "name": "old"}) == "user"
    assert macro_store.created_by({"kind": "generalized", "name": "old"}) == "ai"
    assert macro_store.created_by(None) == "user"


# ── Export / import / share ───────────────────────────────────────────

def test_export_then_import_round_trips_a_macro(tmp_path):
    macro_store.save_literal_macro(
        "portable", [["click", 10, 20], {"type": "wait", "seconds": 1.5}], "Portable macro."
    )
    original = macro_store.load_spec("portable")

    out = tmp_path / ("bundle" + macro_store.EXPORT_SUFFIX)
    assert macro_store.export_macros(["portable"], str(out)) == 1

    macro_store.delete_macro("portable")
    assert macro_store.load_spec("portable") is None

    imported, skipped = macro_store.import_macros(macro_store.read_export(str(out)))
    assert imported == ["portable"] and skipped == []
    # Including the blocks - a control block added on the recorder page
    # has to survive being handed to someone else.
    assert macro_store.load_spec("portable")["recorded_actions"] == original["recorded_actions"]


def test_import_will_not_silently_replace_an_existing_macro(tmp_path):
    macro_store.save_literal_macro("mine", [["click", 1, 1]], "Mine.")
    out = tmp_path / ("bundle" + macro_store.EXPORT_SUFFIX)
    macro_store.export_macros(["mine"], str(out))

    # Someone else's macro of the same name, with different steps.
    macro_store.save_literal_macro("mine", [["click", 999, 999]], "Theirs.")
    specs = macro_store.read_export(str(out))

    imported, skipped = macro_store.import_macros(specs)
    assert imported == [] and skipped == ["mine"]
    assert macro_store.load_spec("mine")["recorded_actions"] == [["click", 999, 999]]

    imported, _ = macro_store.import_macros(specs, overwrite=True)
    assert imported == ["mine"]
    assert macro_store.load_spec("mine")["recorded_actions"] == [["click", 1, 1]]


def test_reading_a_file_that_isnt_a_macro_export_fails_with_a_sentence(tmp_path):
    junk = tmp_path / "notes.json"
    junk.write_text('{"hello": "world"}', encoding="utf-8")
    with pytest.raises(macro_store.MacroTransferError) as excinfo:
        macro_store.read_export(str(junk))
    assert "isn't a Kestrel macro file" in str(excinfo.value)


def test_a_bare_spec_file_can_be_imported_too(tmp_path):
    """Someone who finds the macros folder will try to import one of
    those .json files directly - refusing it would be pedantry."""
    import json
    macro_store.save_literal_macro("solo", [["click", 5, 5]], "Solo.")
    bare = tmp_path / "solo.json"
    bare.write_text(json.dumps(macro_store.load_spec("solo")), encoding="utf-8")

    specs = macro_store.read_export(str(bare))
    assert [s["name"] for s in specs] == ["solo"]


def test_exporting_nothing_readable_is_an_error_not_an_empty_file(tmp_path):
    out = tmp_path / ("empty" + macro_store.EXPORT_SUFFIX)
    with pytest.raises(macro_store.MacroTransferError):
        macro_store.export_macros(["does_not_exist"], str(out))
    assert not out.exists()
