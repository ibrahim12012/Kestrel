# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/macro_generalizer.py
=========================
Turns a one-off recorded macro (exact clicks/keystrokes, saved by
tool_name_action in ai/actions.py) into a reusable, parameterized one.

A recording of "search for Soprano on YouTube and play the first
result" becomes tool_search_youtube_and_play_first(query), with its
clicks pointed at described elements rather than fixed coordinates.
The prompt lives in ai/generalizer_instructions.txt.

The model never outputs Python that reaches disk. It outputs validated
JSON, that JSON is what gets stored, and the callable is synthesized
from it at load time with every AI-authored string escaped through
repr(). A bad response can produce a wrong macro, not arbitrary code.

generalize_and_rewrite_macro never raises past its own boundary and
always leaves the literal macro intact, so calling it speculatively is
safe: on success the generalized version is saved alongside it.
"""

from __future__ import annotations

import ast
import json
import keyword
import os
import re

from paths import BASE_PATH
from ai.log import log as print

GENERALIZER_MODEL = "gemini-3-flash-preview"
GENERALIZER_INSTRUCTIONS_PATH = os.path.join(BASE_PATH, "ai", "generalizer_instructions.txt")

# The recorded action types the MODEL is allowed to author, plus every
# block the macro editor can add by hand (ai/macro_blocks.py) - a
# hand-edited spec is re-validated through this same function on save,
# so the two sets have to live in one place.
_RECORDED_STEP_TYPES = {"click", "right_click", "write", "enter", "new_tab", "open_app"}


def _all_valid_step_types() -> set:
    import ai.macro_blocks as blocks
    return set(_RECORDED_STEP_TYPES) | set(blocks.ALL_TYPES)


_VALID_STEP_TYPES = _RECORDED_STEP_TYPES  # kept: existing callers/tests reference this name


# ══════════════════════════════════════════════════════════════════
# Building the prompt
# ══════════════════════════════════════════════════════════════════

def _describe_action(action) -> str:
    # A macro edited in the block editor can carry block dicts
    # alongside the recorder's own tuples (see play_actions' docstring
    # for why both shapes live in one list). Describing one positionally
    # would read len() on a dict and silently mis-describe it, so blocks
    # are handed to their own vocabulary instead.
    if isinstance(action, dict):
        import ai.macro_blocks as blocks
        return blocks.describe(action)
    if len(action) == 3 and action[0] == "click":
        _, x, y = action
        return f"click at ({x}, {y})"
    if len(action) == 3 and action[0] == "right_click":
        _, x, y = action
        return f"right-click at ({x}, {y})"
    if len(action) == 2 and action[1] == "write":
        text, _ = action
        return f"type text: {text!r}"
    if len(action) == 2 and action[0] == "enter":
        return "press Enter"
    if len(action) == 2 and action[0] == "hotkey":
        return f"press the key combo: {action[1]}"
    if len(action) == 2 and action[0] == "new_page":
        return "open new browser tab"
    if len(action) == 2 and action[1] == "open":
        app_name, _ = action
        return f"open application: {app_name}"
    return f"unknown action: {action!r}"


def _build_actions_description(actions_list: list) -> str:
    return "\n".join(f"{i}. {_describe_action(a)}" for i, a in enumerate(actions_list, 1))


# ══════════════════════════════════════════════════════════════════
# Calling Gemini
# ══════════════════════════════════════════════════════════════════

def _parse_json_response(text: str):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _call_generalizer(goal: str, actions_list: list) -> dict | None:
    """
    Runs the request through its own fresh Gemini chat session - a separate
    "sub-agent" with its own system prompt (ai/generalizer_instructions.txt),
    the same pattern ai/main.py uses for the main chat (ai/instructions.txt)
    and autonomous mode (ai/autonomous_instructions.txt). A fresh session is
    created per call (like run_autonomous_mode()'s auto_chat) rather than
    reused across calls - each macro is generalized independently, so there's
    no reason for one macro's context to bleed into the next.
    """
    from google import genai
    from google.genai import types
    from ai.actions import load_file
    import ai.main as ai_main

    api_key = ai_main.get_api_key()
    if not api_key:
        return None

    client = genai.Client(api_key=api_key)
    # {{ASSISTANT_NAME}} in generalizer_instructions.txt tracks whatever
    # name is currently configured (ai.main.get_assistant_name()/
    # set_assistant_name(), same as the main chat and autonomous-mode
    # system prompts) - see ai/instructions.txt's own docstring note on
    # why this substitution happens at load time rather than being
    # baked into the file on disk.
    system_instructions = load_file(GENERALIZER_INSTRUCTIONS_PATH).replace(
        "{{ASSISTANT_NAME}}", ai_main.get_assistant_name()
    )

    generalizer_chat = client.chats.create(
        model=GENERALIZER_MODEL,
        config=types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
            system_instruction=system_instructions,
        ),
    )

    prompt = f"GOAL: {goal}\n\nACTIONS:\n{_build_actions_description(actions_list)}"
    response = generalizer_chat.send_message(prompt)
    return _parse_json_response(response.text)


# ══════════════════════════════════════════════════════════════════
# Validation - never trust the model's JSON blindly
# ══════════════════════════════════════════════════════════════════

def validate_spec(spec) -> tuple[bool, str]:
    """Pure validation, no I/O - kept separate so it's easy to unit test."""
    if not isinstance(spec, dict):
        return False, "response was not a JSON object"

    if spec.get("confident") is not True:
        return False, str(spec.get("reason", "model was not confident"))

    function_name = spec.get("function_name")
    if not isinstance(function_name, str) or not function_name.strip():
        return False, "missing function_name"

    description = spec.get("description")
    if not isinstance(description, str) or not description.strip():
        return False, "missing description"

    parameters = spec.get("parameters")
    if not isinstance(parameters, list):
        return False, "parameters must be a list"

    param_names = set()
    for p in parameters:
        if not isinstance(p, dict):
            return False, f"invalid parameter entry: {p!r}"
        pname = p.get("name")
        if not isinstance(pname, str) or not pname.isidentifier():
            return False, f"invalid parameter name: {pname!r}"
        if keyword.iskeyword(pname):
            return False, f"parameter name is a Python keyword: {pname!r}"
        param_names.add(pname)

    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        return False, "steps must be a non-empty list"

    for step in steps:
        if not isinstance(step, dict):
            return False, f"invalid step: {step!r}"
        step_type = step.get("type")
        if step_type not in _all_valid_step_types():
            return False, f"unknown step type: {step_type!r}"

        if step_type in ("click", "right_click"):
            target = step.get("target")
            if not isinstance(target, str) or not target.strip():
                return False, f"{step_type} step missing target"
            if not isinstance(step.get("x"), (int, float)) or not isinstance(step.get("y"), (int, float)):
                return False, f"{step_type} step missing numeric x/y"

        if step_type == "write":
            value = step.get("value")
            if not isinstance(value, str):
                return False, "write step missing value"
            for placeholder in re.findall(r"\{(\w+)\}", value):
                if placeholder not in param_names:
                    return False, f"write step references undeclared parameter {{{placeholder}}}"

        if step_type == "open_app":
            app = step.get("app")
            if not isinstance(app, str) or not app.strip():
                return False, "open_app step missing app"

        # ── Editor-added blocks (ai/macro_blocks.py) ─────────────────
        # The model never authors these - the generalizer prompt only
        # ever describes recorded actions - but a hand-edited macro
        # goes back through this same validation on save (see
        # ai.macro_store.save_edited_spec), so their own required
        # fields are checked here rather than in a second, looser set
        # of rules for the manual path.
        if step_type in ("repeat", "forever"):
            key = "times" if step_type == "repeat" else "max_loops"
            count = step.get(key)
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                return False, f"{step_type} step needs a positive whole number of {key}"

        if step_type == "wait":
            seconds = step.get("seconds")
            if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds < 0:
                return False, "wait step needs a non-negative number of seconds"

        if step_type == "hotkey":
            keys = step.get("keys")
            if not isinstance(keys, str) or not keys.strip():
                return False, "hotkey step needs a key combo"

        if step_type == "repeat_until_color":
            cap = step.get("max_loops")
            if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
                return False, "repeat_until_color step needs a positive give-up count"

        if step_type in ("if_color", "wait_color_change", "repeat_until_color"):
            if not isinstance(step.get("x"), (int, float)) or not isinstance(step.get("y"), (int, float)):
                return False, f"{step_type} step missing numeric x/y"
            color_key = "forbidden" if step_type == "wait_color_change" else "color"
            color = step.get(color_key, "")
            # wait_color_change's colour is optional ("wait for any
            # change at all"); if_color's is what it compares against,
            # so that one has to be there.
            if step_type in ("if_color", "repeat_until_color") and not _is_hex_color(color):
                return False, f"{step_type} step needs a #rrggbb colour"
            if color and not _is_hex_color(color):
                return False, f"{step_type} step has an invalid colour: {color!r}"

    ok, structure_error = _structure_ok(steps)
    if not ok:
        return False, structure_error

    return True, ""


def _is_hex_color(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"#?[0-9a-fA-F]{3}|#?[0-9a-fA-F]{6}", value.strip()))


def _structure_ok(steps: list) -> "tuple[bool, str]":
    """Control blocks have to balance before a spec can be saved - otherwise render_function below would emit Python with a `for`
    whose body never closes. Delegated to ai.macro_blocks so the editor
    and the compiler enforce one definition of "balanced"."""
    import ai.macro_blocks as blocks
    return blocks.validate_structure(steps)


def _reconcile_click_coordinates(spec: dict, actions_list: list) -> None:
    """
    Overwrites every click/right_click step's x/y in `spec` with the
    ACTUAL recorded coordinates from `actions_list`, matched positionally
    (the Nth click/right_click step in the spec gets the Nth recorded
    click/right_click's coordinates) - never trusting whatever numbers
    the model put in its JSON response for these fields.

    Rule 1 in ai/generalizer_instructions.txt already requires the model
    to re-describe the SAME actions in the SAME order, so "the Nth click
    step corresponds to the Nth recorded click" is a safe assumption, not
    a guess - the model's own "target" description and step ordering are
    still exactly what it produced, only the numeric x/y ever gets
    replaced.

    This exists because the model is asked to copy several small integers
    out of its own prompt verbatim (see ACTIONS in _build_actions_description),
    and occasionally doesn't: the observed failure mode is two consecutive
    click steps ending up with identical (x, y) - i.e. the second click's
    coordinates get silently replaced by the first's - which then plays
    back as a real, wrong click every time the generalized macro runs
    (click_at_target_or_position only falls back to (x, y) if vision
    fails, so a bad fallback isn't harmless - it's a second chance to
    click the wrong thing). Since the true coordinates are already known
    exactly (they're what was actually recorded), there's no reason to
    depend on the model reproducing them correctly at all.
    """
    original_clicks = [
        (a[0], a[1], a[2])
        for a in actions_list
        if len(a) == 3 and a[0] in ("click", "right_click")
    ]
    idx = 0
    for step in spec.get("steps", []):
        if step.get("type") in ("click", "right_click"):
            if idx < len(original_clicks):
                _, orig_x, orig_y = original_clicks[idx]
                step["x"] = orig_x
                step["y"] = orig_y
            idx += 1


# ══════════════════════════════════════════════════════════════════
# Code generation - every piece of AI-authored text goes through repr(),
# every inserted bare identifier is pre-validated. No f-string built from
# untrusted text, no format-into-source-code of raw values.
# ══════════════════════════════════════════════════════════════════

def _write_value_expression(value: str, param_names: set) -> str:
    """
    Build a safe Python expression for a (possibly parameterized) write value,
    e.g. 'search {query} now' -> "'search ' + str(query) + ' now'".
    Every literal chunk goes through repr(); every parameter reference was
    already validated as a real identifier in validate_spec().
    """
    parts = re.split(r"(\{\w+\})", value)
    expr_parts = []
    for part in parts:
        m = re.fullmatch(r"\{(\w+)\}", part)
        if m and m.group(1) in param_names:
            expr_parts.append(f"str({m.group(1)})")
        elif part:
            expr_parts.append(repr(part))
    return " + ".join(expr_parts) if expr_parts else repr("")


def _render_nodes(nodes: list, param_names: set, indent: int) -> list:
    """
    One block tree (ai.macro_blocks.parse_program) → Python source
    lines at `indent` levels of 4 spaces.

    Recursive rather than a flat pass with a running indent counter:
    the tree already encodes the nesting, so a repeat's contents are
    simply rendered one level deeper, and an `if colour`'s two branches
    can't accidentally share a body. Every emitted call resolves inside
    ai.actions' own namespace, which is where this source is exec'd
    (see ai/macro_store.py's register_macro).
    """
    pad = "    " * indent
    lines: list = []

    for node in nodes:
        step = node.step
        step_type = node.type

        if step_type == "repeat":
            times = max(0, int(step.get("times", 1)))
            lines.append(f"{pad}for _ in range({times}):")
            lines.extend(_render_nodes(node.body, param_names, indent + 1) or [f"{pad}    pass"])
            continue

        if step_type == "forever":
            cap = max(1, int(step.get("max_loops", 100)))
            # A real `while True` would have no way out short of the
            # mouse-corner fail-safe - the block carries its own cap
            # (see ai/macro_blocks.py) and it's honored here.
            lines.append(f"{pad}for _ in range({cap}):")
            lines.extend(_render_nodes(node.body, param_names, indent + 1) or [f"{pad}    pass"])
            continue

        if step_type == "if_color":
            x, y = int(step.get("x", 0)), int(step.get("y", 0))
            color = str(step.get("color", "#ffffff"))
            tolerance = int(step.get("tolerance", 12))
            lines.append(f"{pad}if color_matches_at({x}, {y}, {color!r}, {tolerance}):")
            lines.extend(_render_nodes(node.body, param_names, indent + 1) or [f"{pad}    pass"])
            if node.else_body:
                lines.append(f"{pad}else:")
                lines.extend(_render_nodes(node.else_body, param_names, indent + 1))
            continue

        if step_type == "repeat_until_color":
            cap = max(1, int(step.get("max_loops", 100)))
            x, y = int(step.get("x", 0)), int(step.get("y", 0))
            color = str(step.get("color", "#00ff00"))
            tolerance = int(step.get("tolerance", 12))
            lines.append(f"{pad}for _ in range({cap}):")
            lines.append(f"{pad}    if color_matches_at({x}, {y}, {color!r}, {tolerance}):")
            lines.append(f"{pad}        break")
            lines.extend(_render_nodes(node.body, param_names, indent + 1) or [f"{pad}    pass"])
            continue

        if step_type == "wait":
            lines.append(f"{pad}time.sleep({max(0.0, float(step.get('seconds', 1.0)))!r})")
            continue

        if step_type == "wait_color_change":
            x, y = int(step.get("x", 0)), int(step.get("y", 0))
            forbidden = str(step.get("forbidden", ""))
            tolerance = int(step.get("tolerance", 12))
            timeout = float(step.get("timeout", 30.0))
            lines.append(
                f"{pad}_wait_for_color_change({x}, {y}, {forbidden!r}, {tolerance}, {timeout!r})"
            )
            continue

        if step_type == "stop":
            lines.append(f"{pad}raise MacroStopped({'stopped by a “stop this macro” block'!r})")
            continue

        if step_type == "click":
            x, y = int(step.get("x", 0)), int(step.get("y", 0))
            lines.append(f"{pad}click_at_target_or_position({step.get('target', '')!r}, {x}, {y})")
        elif step_type == "right_click":
            x, y = int(step.get("x", 0)), int(step.get("y", 0))
            lines.append(
                f"{pad}click_at_target_or_position({step.get('target', '')!r}, {x}, {y}, button=\"right\")"
            )
        elif step_type == "write":
            lines.append(f"{pad}_type_text({_write_value_expression(step.get('value', ''), param_names)})")
        elif step_type == "enter":
            lines.append(f'{pad}keyboard.press_and_release("enter")')
        elif step_type == "hotkey":
            lines.append(f"{pad}keyboard.press_and_release({str(step.get('keys', '')).strip()!r})")
        elif step_type == "new_tab":
            lines.append(f'{pad}pyautogui.hotkey("ctrl", "t")')
        elif step_type == "open_app":
            lines.append(f"{pad}tool_open_application({step.get('app', '')!r})")
        else:
            continue  # unknown/legacy step - skipped rather than emitting broken source

        lines.append(f"{pad}time.sleep(1)")

    return lines


def render_function(spec: dict) -> str:
    """Generate the tool_<name>(...) source from a validated spec dict."""
    from ai.actions import _sanitize_macro_name

    name = _sanitize_macro_name(spec["function_name"])
    parameters = spec.get("parameters", [])
    param_names = {p["name"] for p in parameters}
    # Every parameter MUST be type-annotated: Gemini's automatic function
    # calling introspects the Python signature to build the tool's JSON
    # schema, and fails ("Failed to parse the parameter ... for automatic
    # function calling") on unannotated parameters. All generalizer
    # parameters are free-text values (search terms, names...), so `str`
    # is always the correct annotation here.
    signature = ", ".join(f"{p['name']}: str" for p in parameters)

    doc_lines = [spec["description"].strip()]
    for p in parameters:
        doc_lines.append(f'{p["name"]}: {p.get("description", "")}'.strip())
    docstring = "\n".join(doc_lines)

    import ai.macro_blocks as blocks

    lines = [
        "",
        "",
        f"def tool_{name}({signature}) -> str:",
        f"    {docstring!r}",
        f"    print({'Executing generalized macro: ' + name!r})",
    ]

    # Control blocks compile to real nested Python (a `for` for repeat,
    # an `if`/`else` for a colour test), so the tree - not the flat
    # marker list - is what's walked here. MacroStopped, raised by a
    # "stop" block or a colour watch landing on its excluded colour, is
    # caught at the top level and turned into the same kind of
    # descriptive string every other branch returns.
    body_lines = _render_nodes(blocks.parse_program(spec["steps"]), param_names, indent=2)
    if body_lines:
        lines.append("    try:")
        lines.extend(body_lines)
        lines.append("    except MacroStopped as stop_reason:")
        lines.append(f"        return {'Macro ' + name + ' stopped: '!r} + str(stop_reason)")

    # Every other tool_* function in this codebase - hand-written
    # (ai/actions.py) and compiled (ai/discovery/compiler.py) alike
    # returns a descriptive string; a generalized macro implicitly
    # returning None used to be the one exception. That's fine for
    # MacroRunWorker (app/workers/macro_run_worker.py explicitly treats
    # a None result as ""), which is why running a generalized macro
    # from the Macros page's Run button always worked - but Gemini's
    # automatic function calling expects a real function-response value
    # back from every tool call, and a bare None there is what produced
    # the "hitting a technical error" replies the AI gave when asked to
    # use a generalized macro directly in conversation, even though nothing
    # was actually wrong with what the macro DID.
    lines.append(f"    return {'Executed generalized macro: ' + name!r}")

    return "\n".join(lines) + "\n"


# ══════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════

def generalize_and_rewrite_macro(
    goal: str,
    name: str,
    actions_list: list,
    appended_block: str | None = None,
) -> bool:
    """
    Attempt to generalize a just-saved literal macro into a parameterized
    one, saving it as a SEPARATE macro alongside the literal one (see
    ai.macro_store) and registering it as tool_<function_name> on the
    live ai.actions module - the literal macro is kept, not replaced, so
    both are available afterward. `appended_block` is accepted for
    backward compatibility with older call sites but is no longer used -
    saving is now a structured JSON write, not a text
    search-and-replace.

    Returns True if a generalized version was successfully saved
    alongside the literal macro. Returns False if nothing was added -
    the model wasn't confident, its response was invalid, or anything
    else went wrong - in which case the literal macro is untouched
    either way. This function never raises; every failure path is
    caught and logged.
    """
    if not actions_list:
        return False

    try:
        spec = _call_generalizer(goal, actions_list)
    except Exception as e:
        print(f"[generalizer] Gemini call failed, keeping literal macro {name!r}: {e}")
        return False

    ok, reason = validate_spec(spec)
    if not ok:
        print(f"[generalizer] Not generalizing (kept literal {name!r}): {reason}")
        return False

    # Never trust the model's own x/y for click steps - replace them with
    # the actual recorded coordinates now, before codegen. See
    # _reconcile_click_coordinates's own docstring for why.
    _reconcile_click_coordinates(spec, actions_list)

    try:
        new_source = render_function(spec)
        ast.parse(new_source)  # must be valid Python before it's registered
    except Exception as e:
        print(f"[generalizer] Codegen produced invalid Python, keeping literal {name!r}: {e}")
        return False

    try:
        from ai import macro_store
        import ai.actions as actions_module
        macro_store.save_generalized_spec(spec)

        from ai.actions import _sanitize_macro_name
        new_name = _sanitize_macro_name(spec["function_name"])
        # save_generalized_spec writes under the MODEL's chosen
        # function_name, which is very often different from the literal
        # macro's own name (e.g. "search_soprano_youtube" -> the
        # generalizer naming it "search_youtube_and_play_first") - so
        # they land as two separate JSON files/tools rather than one
        # overwriting the other. That's now deliberate: the literal
        # macro (exact recorded coordinates/text, no parameters) is kept
        # alongside the generalized one (parameterized, more flexible,
        # but a step removed from exactly what was recorded) rather than
        # deleting the literal - the AI, and the user from the Macros
        # page, then has both available and can pick whichever fits.

        macro_store.load_and_register_all(actions_module)
    except Exception as e:
        print(f"[generalizer] Failed to save/register generalized macro, keeping literal {name!r}: {e}")
        return False

    print(f"[generalizer] Generalized {name!r} into tool_{new_name} (kept both - the literal macro wasn't removed)")
    return True
