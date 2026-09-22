# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_macro_generalizer.py
=================================
Tests for ai/macro_generalizer.py - everything except the actual Gemini
call (_call_generalizer), which needs real network/credentials. The
important thing to test hard here is validate_spec() and render_function():
this is the boundary where AI-authored text turns into real Python source
that gets written to ai/actions.py and imported, so it needs to be robust
against malformed or actively adversarial-looking model output.
"""

import ast
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.macro_generalizer as gen  # noqa: E402


# ══════════════════════════════════════════════════════════════════
# _build_actions_description / _describe_action
# ══════════════════════════════════════════════════════════════════

def test_describe_action_click():
    assert gen._describe_action(("click", 100, 200)) == "click at (100, 200)"


def test_describe_action_right_click():
    assert gen._describe_action(("right_click", 100, 200)) == "right-click at (100, 200)"


def test_describe_action_write():
    assert gen._describe_action(("hello", "write")) == "type text: 'hello'"


def test_describe_action_enter():
    assert gen._describe_action(("enter", "enter")) == "press Enter"


def test_build_actions_description_numbers_each_line():
    actions_list = [("click", 1, 2), ("hi", "write"), ("enter", "enter")]
    description = gen._build_actions_description(actions_list)
    lines = description.split("\n")
    assert lines[0].startswith("1. ")
    assert lines[1].startswith("2. ")
    assert lines[2].startswith("3. ")


# ══════════════════════════════════════════════════════════════════
# _parse_json_response
# ══════════════════════════════════════════════════════════════════

def test_parse_json_response_plain_json():
    result = gen._parse_json_response('{"confident": true, "function_name": "x"}')
    assert result == {"confident": True, "function_name": "x"}


def test_parse_json_response_strips_markdown_fences():
    text = '```json\n{"confident": false, "reason": "test"}\n```'
    result = gen._parse_json_response(text)
    assert result == {"confident": False, "reason": "test"}


def test_parse_json_response_invalid_json_returns_none():
    assert gen._parse_json_response("not json at all") is None


def test_parse_json_response_empty_returns_none():
    assert gen._parse_json_response("") is None
    assert gen._parse_json_response(None) is None


# ══════════════════════════════════════════════════════════════════
# validate_spec
# ══════════════════════════════════════════════════════════════════

VALID_SPEC = {
    "confident": True,
    "function_name": "search_youtube_and_play_first",
    "description": "Searches YouTube for a query and plays the first result.",
    "parameters": [{"name": "query", "description": "the search term"}],
    "steps": [
        {"type": "click", "target": "address bar", "x": 640, "y": 30},
        {"type": "write", "value": "https://youtube.com"},
        {"type": "enter"},
        {"type": "click", "target": "search bar", "x": 640, "y": 60},
        {"type": "write", "value": "{query}"},
        {"type": "enter"},
        {"type": "click", "target": "first video thumbnail", "x": 400, "y": 300},
    ],
}


def test_validate_spec_accepts_a_well_formed_spec():
    ok, reason = gen.validate_spec(VALID_SPEC)
    assert ok is True
    assert reason == ""


def test_validate_spec_rejects_non_dict():
    ok, reason = gen.validate_spec("not a dict")
    assert ok is False


def test_validate_spec_rejects_not_confident():
    ok, reason = gen.validate_spec({"confident": False, "reason": "too specific"})
    assert ok is False
    assert reason == "too specific"


def test_validate_spec_rejects_missing_function_name():
    spec = dict(VALID_SPEC)
    spec = {**spec, "function_name": ""}
    ok, reason = gen.validate_spec(spec)
    assert ok is False
    assert "function_name" in reason


def test_validate_spec_rejects_missing_description():
    spec = {**VALID_SPEC, "description": ""}
    ok, reason = gen.validate_spec(spec)
    assert ok is False


def test_validate_spec_rejects_invalid_parameter_name():
    spec = {**VALID_SPEC, "parameters": [{"name": "not a valid name!", "description": "x"}]}
    ok, reason = gen.validate_spec(spec)
    assert ok is False


def test_validate_spec_rejects_python_keyword_as_parameter_name():
    spec = {**VALID_SPEC, "parameters": [{"name": "class", "description": "x"}]}
    ok, reason = gen.validate_spec(spec)
    assert ok is False
    assert "keyword" in reason


def test_validate_spec_rejects_undeclared_placeholder():
    spec = {
        **VALID_SPEC,
        "parameters": [{"name": "query", "description": "x"}],
        "steps": [{"type": "write", "value": "{undeclared_param}"}],
    }
    ok, reason = gen.validate_spec(spec)
    assert ok is False
    assert "undeclared" in reason


def test_validate_spec_rejects_unknown_step_type():
    spec = {**VALID_SPEC, "steps": [{"type": "delete_system32"}]}
    ok, reason = gen.validate_spec(spec)
    assert ok is False


def test_validate_spec_rejects_click_without_coordinates():
    spec = {**VALID_SPEC, "steps": [{"type": "click", "target": "something"}]}
    ok, reason = gen.validate_spec(spec)
    assert ok is False


def test_validate_spec_accepts_right_click_with_coordinates():
    spec = {**VALID_SPEC, "steps": [{"type": "right_click", "target": "this file", "x": 10, "y": 20}]}
    ok, reason = gen.validate_spec(spec)
    assert ok is True


def test_validate_spec_rejects_right_click_without_coordinates():
    spec = {**VALID_SPEC, "steps": [{"type": "right_click", "target": "something"}]}
    ok, reason = gen.validate_spec(spec)
    assert ok is False


def test_validate_spec_accepts_open_app_with_app_name():
    spec = {**VALID_SPEC, "steps": [{"type": "open_app", "app": "chrome"}]}
    ok, reason = gen.validate_spec(spec)
    assert ok is True


def test_validate_spec_rejects_open_app_without_app_name():
    spec = {**VALID_SPEC, "steps": [{"type": "open_app"}]}
    ok, reason = gen.validate_spec(spec)
    assert ok is False


def test_validate_spec_rejects_empty_steps():
    spec = {**VALID_SPEC, "steps": []}
    ok, reason = gen.validate_spec(spec)
    assert ok is False


# ══════════════════════════════════════════════════════════════════
# _reconcile_click_coordinates - the model's x/y must never survive;
# only the actually-recorded coordinates should end up in the spec.
# ══════════════════════════════════════════════════════════════════

def test_reconcile_overwrites_model_coordinates_with_recorded_ones():
    # The model hallucinated the same (x, y) for both clicks - the exact
    # bug this function exists to catch and correct.
    spec = {
        "steps": [
            {"type": "click", "target": "search bar", "x": 640, "y": 60},
            {"type": "write", "value": "{query}"},
            {"type": "click", "target": "first result", "x": 640, "y": 60},
        ]
    }
    actions_list = [
        ("click", 640, 60),
        ("hello", "write"),
        ("click", 400, 300),
    ]
    gen._reconcile_click_coordinates(spec, actions_list)
    assert (spec["steps"][0]["x"], spec["steps"][0]["y"]) == (640, 60)
    assert (spec["steps"][2]["x"], spec["steps"][2]["y"]) == (400, 300)


def test_reconcile_matches_clicks_positionally_ignoring_other_step_types():
    # An "open_app" step the model inserts per rule 7 (not present in the
    # original recording at all) must not shift the matching - click
    # steps are matched against recorded clicks only, by their own order.
    spec = {
        "steps": [
            {"type": "open_app", "app": "chrome"},
            {"type": "click", "target": "address bar", "x": 1, "y": 1},
            {"type": "right_click", "target": "this file", "x": 1, "y": 1},
        ]
    }
    actions_list = [
        ("click", 111, 222),
        ("right_click", 333, 444),
    ]
    gen._reconcile_click_coordinates(spec, actions_list)
    assert (spec["steps"][1]["x"], spec["steps"][1]["y"]) == (111, 222)
    assert (spec["steps"][2]["x"], spec["steps"][2]["y"]) == (333, 444)


def test_reconcile_leaves_extra_unmatched_click_steps_untouched():
    # More click steps than were ever recorded (a malformed/hallucinated
    # spec) - shouldn't crash, and steps with nothing to match against
    # simply keep whatever x/y they already had.
    spec = {
        "steps": [
            {"type": "click", "target": "a", "x": 5, "y": 5},
            {"type": "click", "target": "b", "x": 9, "y": 9},
        ]
    }
    actions_list = [("click", 100, 200)]
    gen._reconcile_click_coordinates(spec, actions_list)
    assert (spec["steps"][0]["x"], spec["steps"][0]["y"]) == (100, 200)
    assert (spec["steps"][1]["x"], spec["steps"][1]["y"]) == (9, 9)


def test_reconcile_ignores_non_click_actions_when_matching():
    spec = {"steps": [{"type": "click", "target": "a", "x": 0, "y": 0}]}
    actions_list = [
        ("hello", "write"),
        ("enter", "enter"),
        ("click", 42, 84),
    ]
    gen._reconcile_click_coordinates(spec, actions_list)
    assert (spec["steps"][0]["x"], spec["steps"][0]["y"]) == (42, 84)


# ══════════════════════════════════════════════════════════════════
# _write_value_expression - must be injection-safe
# ══════════════════════════════════════════════════════════════════

def test_write_value_expression_plain_literal():
    expr = gen._write_value_expression("hello world", set())
    assert expr == repr("hello world")


def test_write_value_expression_single_param():
    expr = gen._write_value_expression("{query}", {"query"})
    assert expr == "str(query)"


def test_write_value_expression_mixed():
    expr = gen._write_value_expression("search {query} now", {"query"})
    assert expr == "'search ' + str(query) + ' now'"


def test_write_value_expression_is_safe_against_quote_breakout():
    malicious = "'; import os; os.system('rm -rf /'); x = '"
    expr = gen._write_value_expression(malicious, set())
    # The whole thing must come out as a single safely-escaped literal,
    # not be split into executable-looking chunks.
    assert expr == repr(malicious)
    # And it must actually compile back to exactly that string, proving
    # repr() escaped it correctly rather than us just eyeballing it.
    assert ast.literal_eval(expr) == malicious


def test_write_value_expression_unknown_placeholder_treated_as_literal_text():
    # If a {placeholder} isn't a declared param, validate_spec() would have
    # already rejected the spec - but render-side, it must never be treated
    # as a bare code reference just because it looks like one.
    expr = gen._write_value_expression("{not_a_param}", set())
    assert "not_a_param" not in expr.replace(repr("{not_a_param}"), "")
    assert ast.literal_eval(expr) == "{not_a_param}"


# ══════════════════════════════════════════════════════════════════
# render_function - must always produce valid, safe Python
# ══════════════════════════════════════════════════════════════════

def test_render_function_produces_valid_python():
    source = gen.render_function(VALID_SPEC)
    ast.parse(source)  # must not raise


def test_render_function_uses_sanitized_name():
    spec = {**VALID_SPEC, "function_name": "Écouter du Café !!"}
    source = gen.render_function(spec)
    ast.parse(source)
    assert "def tool_ecouter_du_cafe(" in source


def test_render_function_includes_parameters_in_signature():
    source = gen.render_function(VALID_SPEC)
    assert "def tool_search_youtube_and_play_first(query: str) -> str:" in source


def test_render_function_returns_a_descriptive_string_not_none():
    """
    Regression test: generated functions used to declare -> None and
    never return anything. MacroRunWorker (the Macros page's Run button)
    explicitly tolerates a None result, which is why running one from
    there always worked - but Gemini's automatic function calling
    expects every tool call to produce a real function-response value,
    and a bare None is what caused the AI to report a generalized macro
    as "hitting a technical error" when asked to use it directly, even
    though the macro itself worked fine.
    """
    source = gen.render_function(VALID_SPEC)
    ast.parse(source)
    assert "-> str:" in source
    assert "-> None:" not in source
    assert re.search(r"^\s+return .+$", source, re.MULTILINE), "generated function has no return statement"


def test_render_function_actually_returns_a_string_when_called():
    """
    Same regression as above, verified by actually executing the
    generated function (with its helper calls stubbed out) rather than
    just inspecting the source text - proves the return value a real
    caller (Gemini's function-calling machinery, or MacroRunWorker) gets
    back is a genuine, truthy string, not None.
    """
    source = gen.render_function(VALID_SPEC)
    namespace = {
        "click_at_target_or_position": lambda *a, **k: None,
        "_type_text": lambda *a, **k: None,
        "keyboard": type("FakeKeyboard", (), {"press_and_release": staticmethod(lambda *a: None)})(),
        "pyautogui": type("FakePyautogui", (), {"hotkey": staticmethod(lambda *a: None)})(),
        "time": type("FakeTime", (), {"sleep": staticmethod(lambda *a: None)})(),
        "tool_open_application": lambda *a: None,
    }
    exec(compile(source, "<test>", "exec"), namespace)
    result = namespace["tool_search_youtube_and_play_first"](query="soprano")

    assert isinstance(result, str)
    assert result != ""


def test_render_function_parameters_are_type_annotated():
    """
    Regression test: generated parameters used to have no type annotation
    at all (e.g. `def tool_x(search_query):`), which made Gemini's
    automatic function calling fail with "Failed to parse the parameter
    ... for automatic function calling" - it introspects the Python
    signature to build the tool's JSON schema and needs a type hint on
    every parameter.
    """
    import inspect

    spec = {
        **VALID_SPEC,
        "function_name": "multi_param_macro",
        "parameters": [
            {"name": "query", "description": "a"},
            {"name": "target_app", "description": "b"},
        ],
        "steps": [{"type": "write", "value": "{query} {target_app}"}],
    }
    source = gen.render_function(spec)
    ast.parse(source)

    namespace = {"click_at_target_or_position": lambda *a, **k: None,
                 "_type_text": lambda *a, **k: None,
                 "keyboard": type("K", (), {"press_and_release": staticmethod(lambda *a: None)})(),
                 "pyautogui": type("P", (), {"hotkey": staticmethod(lambda *a: None)})(),
                 "tool_open_application": lambda *a, **k: None,
                 "time": __import__("time")}
    exec(source, namespace)
    func = namespace["tool_multi_param_macro"]

    sig = inspect.signature(func)
    for param in sig.parameters.values():
        assert param.annotation is str, (
            f"parameter {param.name!r} has no (or wrong) type annotation - "
            "this is exactly what breaks Gemini's automatic function calling"
        )


def test_render_function_uses_click_at_target_or_position():
    source = gen.render_function(VALID_SPEC)
    assert "click_at_target_or_position('address bar', 640, 30)" in source
    assert "click_at_target_or_position('search bar', 640, 60)" in source


def test_render_function_right_click_uses_right_button():
    spec = {**VALID_SPEC, "steps": [{"type": "right_click", "target": "this file", "x": 10, "y": 20}]}
    source = gen.render_function(spec)
    ast.parse(source)
    assert "click_at_target_or_position('this file', 10, 20, button=\"right\")" in source


def test_render_function_open_app_calls_tool_open_application():
    spec = {**VALID_SPEC, "steps": [{"type": "open_app", "app": "chrome"}]}
    source = gen.render_function(spec)
    ast.parse(source)
    assert "tool_open_application('chrome')" in source


def test_render_function_open_app_is_safe_against_injection():
    spec = {**VALID_SPEC, "steps": [{"type": "open_app", "app": "'); os.system('evil'); ('"}]}
    source = gen.render_function(spec)
    ast.parse(source)  # must still be valid, safely-escaped Python


def test_render_function_is_safe_against_malicious_target_text():
    malicious_spec = {
        **VALID_SPEC,
        "steps": [
            {"type": "click", "target": "'); os.system('evil'); ('", "x": 1, "y": 2},
        ],
    }
    source = gen.render_function(malicious_spec)
    ast.parse(source)  # must still be valid, single, safely-escaped Python
    assert "os.system" not in source.replace(repr(malicious_spec["steps"][0]["target"]), "")


def test_render_function_is_safe_against_malicious_description():
    malicious_spec = {**VALID_SPEC, "description": '"""\nimport os\nos.system("evil")\n"""'}
    source = gen.render_function(malicious_spec)
    ast.parse(source)  # must still parse as one safely-escaped docstring literal


# ══════════════════════════════════════════════════════════════════
# GENERALIZER_INSTRUCTIONS_PATH - regression test for the exact bug class
# found earlier in ai/main.py: a wrong path silently makes load_file()
# auto-create a generic fallback stub instead of using the real, carefully
# written instructions, with no error to notice.
# ══════════════════════════════════════════════════════════════════

def test_generalizer_instructions_path_points_at_the_real_file():
    assert os.path.isfile(gen.GENERALIZER_INSTRUCTIONS_PATH)


def test_generalizer_instructions_file_is_not_the_generic_fallback_stub():
    with open(gen.GENERALIZER_INSTRUCTIONS_PATH, encoding="utf-8") as f:
        content = f.read()
    # The generic fallback ai/actions.py's load_file() would silently create
    # is a short "IDENTITY & ROLE / System Automator" stub - assert we're
    # reading the real, generalizer-specific instructions instead.
    assert "MACRO GENERALIZER" in content
    assert "confident" in content
    assert "IDENTITY & ROLE" not in content


# ══════════════════════════════════════════════════════════════════
# generalize_and_rewrite_macro - keeps the literal macro alongside
# ══════════════════════════════════════════════════════════════════

def test_generalize_and_rewrite_macro_keeps_literal_alongside_generalized(monkeypatch, tmp_path):
    """
    The literal macro is now deliberately kept alongside the generalized
    version, not replaced by it, even when the generalizer picks a
    different function_name than the literal macro's own name (the
    common case) - both are available afterward, giving a choice between
    the exact-recorded literal version and the more flexible generalized
    one.
    """
    import ai.macro_store as macro_store
    import ai.actions as actions_module

    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))
    macro_store._currently_registered = set()

    # The literal macro, saved under its original recording-time name.
    macro_store.save_literal_macro("search_soprano_youtube", [("click", 1, 2)])
    assert macro_store.macro_exists("search_soprano_youtube")

    generalized_spec = {
        "confident": True,
        "function_name": "search_youtube_and_play_first",
        "description": "Search YouTube for a query and play the first result.",
        "parameters": [{"name": "query", "description": "search term"}],
        "steps": [
            {"type": "click", "target": "search bar", "x": 100, "y": 100},
            {"type": "write", "value": "{query}"},
            {"type": "enter"},
        ],
    }
    monkeypatch.setattr(gen, "_call_generalizer", lambda goal, actions_list: generalized_spec)

    ok = gen.generalize_and_rewrite_macro(
        goal="search soprano on youtube",
        name="search_soprano_youtube",
        actions_list=[("click", 1, 2)],
    )

    assert ok is True
    assert macro_store.macro_exists("search_soprano_youtube"), (
        "the literal macro should still exist - generalizing no longer removes it"
    )
    assert macro_store.macro_exists("search_youtube_and_play_first")
    assert hasattr(actions_module, "tool_search_youtube_and_play_first")
    assert hasattr(actions_module, "tool_search_soprano_youtube")


def test_generalize_and_rewrite_macro_saves_recorded_coordinates_not_the_models(monkeypatch, tmp_path):
    """
    End-to-end regression test for the "generalizing scrambles pixel
    positions" bug: even when the model's own JSON gets the x/y wrong
    (here, duplicating the first click's coordinates onto the second),
    the macro actually saved to disk must carry the real recorded
    coordinates - see _reconcile_click_coordinates.
    """
    import ai.macro_store as macro_store
    import ai.actions as actions_module

    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))
    macro_store._currently_registered = set()

    recorded_actions = [
        ("click", 640, 60),
        ("hello", "write"),
        ("click", 400, 300),  # the model will wrongly echo (640, 60) here
    ]
    macro_store.save_literal_macro("do_the_thing", recorded_actions)

    model_spec = {
        "confident": True,
        "function_name": "do_the_thing_generalized",
        "description": "Does the thing.",
        "parameters": [{"name": "query", "description": "search term"}],
        "steps": [
            {"type": "click", "target": "search bar", "x": 640, "y": 60},
            {"type": "write", "value": "{query}"},
            {"type": "click", "target": "first result", "x": 640, "y": 60},
        ],
    }
    monkeypatch.setattr(gen, "_call_generalizer", lambda goal, actions_list: model_spec)

    ok = gen.generalize_and_rewrite_macro(
        goal="do the thing",
        name="do_the_thing",
        actions_list=recorded_actions,
    )
    assert ok is True

    import json
    saved_path = macro_store._path_for("do_the_thing_generalized")
    with open(saved_path, encoding="utf-8") as f:
        saved = json.load(f)

    click_steps = [s for s in saved["steps"] if s["type"] == "click"]
    assert (click_steps[0]["x"], click_steps[0]["y"]) == (640, 60)
    assert (click_steps[1]["x"], click_steps[1]["y"]) == (400, 300), (
        "the second click must keep its OWN recorded coordinates, "
        "not silently inherit the first click's"
    )
