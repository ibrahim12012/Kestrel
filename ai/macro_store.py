# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/macro_store.py
===================
Macros on disk. Each one is a single JSON file, its "spec", under
MACROS_DIR, in one of two shapes: a literal recording replayed verbatim,
or a generalized version with parameters interpreted at call time.

A real Python function is synthesized from the spec at load time,
because Gemini's automatic function calling needs an introspectable
callable rather than a JSON blob. That generated source is exec'd into
ai.actions's namespace (register_macro), never written to a file, so
deleting or replacing a macro is a file operation plus a reload.
"""

from __future__ import annotations

import json
import os
import time

import branding
from paths import USER_DATA_PATH
from ai.log import log as print

MACROS_DIR = os.path.join(USER_DATA_PATH, "macros")

# Provenance, stored on every spec - see save_literal_macro.
CREATED_BY_USER = "user"
CREATED_BY_AI = "ai"


def created_by(spec: "dict | None") -> str:
    """
    Who made the macro a spec describes.

    Specs saved before this field existed don't carry it, so it's
    inferred from the kind rather than defaulting everything to "ai"
    the way the Macros page used to: a "literal" macro is a recording
    (the person performed it), a "generalized" one was authored by the
    generalizer from that recording.
    """
    if not spec:
        return CREATED_BY_USER
    value = str(spec.get("created_by", "")).strip().lower()
    if value in (CREATED_BY_USER, CREATED_BY_AI):
        return value
    return CREATED_BY_AI if spec.get("kind") == "generalized" else CREATED_BY_USER


def _ensure_dir() -> None:
    os.makedirs(MACROS_DIR, exist_ok=True)


def _path_for(name: str) -> str:
    return os.path.join(MACROS_DIR, f"{name}.json")


def macro_exists(name: str) -> bool:
    return os.path.isfile(_path_for(name))


def list_macro_names() -> list[str]:
    _ensure_dir()
    return sorted(
        fn[:-5] for fn in os.listdir(MACROS_DIR) if fn.endswith(".json")
    )


def save_literal_macro(name: str, recorded_actions: list, description: str | None = None) -> str:
    """
    Save a plain recorded macro (no generalization/parameters) - played
    back verbatim via play_actions(). Returns the path written.

    `description` becomes this macro's actual Python docstring once
    synthesized into a callable (see _render_literal_function below) -
    which is exactly what Gemini's automatic function calling reads to
    decide whether/when this macro is the right tool to call. A vague
    or missing description genuinely makes the AI worse at using it, not
    just cosmetically incomplete - falls back to a generic auto-generated
    one (better than nothing, but no substitute for the user's own words
    on what the macro is actually for) when none is given.
    """
    spec = {
        "kind": "literal",
        "name": name,
        # Who made this. A literal macro is a RECORDING - the person sat
        # there and performed it - so it's theirs, even when the AI was
        # the one that called tool_name_action to save it at the end of
        # the recording. Only the generalizer authors a macro itself
        # (see save_generalized_spec), and that's the only thing the
        # Macros page should be badging as AI-made.
        "created_by": CREATED_BY_USER,
        "description": description.strip() if description and description.strip() else f"Auto-generated macro: {name.replace('_', ' ')}",
        "parameters": [],
        "recorded_actions": recorded_actions,
    }
    return _write_spec(name, spec)


def save_generalized_spec(spec: dict) -> str:
    """
    Save an already-validated generalized macro spec (see
    ai.macro_generalizer.validate_spec) - parameterized, with structured
    steps interpreted at call time rather than replayed verbatim.
    Returns the path written. Overwrites any existing macro of the same
    (sanitized) name - recording a new macro under an existing name is
    how a broken/outdated one gets replaced.
    """
    from ai.actions import _sanitize_macro_name
    name = _sanitize_macro_name(spec["function_name"])
    record = {
        "kind": "generalized",
        "name": name,
        "created_by": CREATED_BY_AI,
        "description": spec["description"],
        "parameters": spec.get("parameters", []),
        "steps": spec["steps"],
    }
    return _write_spec(name, record)


def _write_spec(name: str, spec: dict) -> str:
    _ensure_dir()
    path = _path_for(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)
    return path


def attach_screen_metrics(name: str, metrics: dict) -> bool:
    """
    Stamp the screen a macro was recorded on onto its saved spec, so a
    later replay can warn when the current screen is a different size
    or scaling (see ai.actions.screen_mismatch). Returns False if there
    was nothing to stamp or no such macro.
    """
    if not metrics:
        return False
    from ai.actions import _sanitize_macro_name
    name = _sanitize_macro_name(name)
    spec = load_spec(name)
    if spec is None:
        return False
    spec["screen"] = dict(metrics)
    try:
        _write_spec(name, spec)
    except OSError as e:
        print(f"[macro_store] Could not stamp screen metrics on {name}: {e}")
        return False
    return True


def screen_of(spec: "dict | None") -> dict:
    """The screen a macro was recorded on, or {} for older macros saved
    before that was tracked."""
    if not isinstance(spec, dict):
        return {}
    screen = spec.get("screen")
    return screen if isinstance(screen, dict) else {}


def load_spec(name: str) -> dict | None:
    path = _path_for(name)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[macro_store] Could not read {path}: {e}")
        return None


def delete_macro(name: str) -> bool:
    """Removes the macro's JSON file. Returns True if a file was actually deleted."""
    path = _path_for(name)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def restore_macro(spec: dict) -> str:
    """
    Write a spec back after a delete, so deleting a macro is undoable
    rather than needing it re-recorded. The caller holds the spec it read
    before deleting; this just puts it back where it was.
    """
    return _write_spec(spec["name"], spec)


# ── Manual step editing (app/macro_editor_dialog.py) ────────────────────────
# Re-recording used to be the ONLY way to fix a macro whose steps had
# drifted from the UI it was learned against, or to tweak/reorder/insert
# a step - a real ceiling on how useful a learned macro stays over time.
# The functions below are the storage-layer half of manual editing: they
# convert between the two saved macro "kind"s' own on-disk shapes and a
# single, editor-friendly step dict ({"type": ..., <type-specific
# fields>}), and write an edited spec back - through the SAME validation
# a freshly-generalized macro goes through, so a hand-edited spec has to
# clear the identical bar (declared parameters, required per-step
# fields, no undeclared {placeholder} in a write step, ...) rather than
# a second, looser set of rules for the manual path.

_LITERAL_STEP_TYPES = {"click", "right_click", "write", "enter", "new_tab", "open_app", "hotkey"}


def literal_action_to_editor_step(action) -> dict:
    """
    One recorded_actions entry (a plain list/tuple once round-tripped
    through JSON - see _describe_action in ai/macro_generalizer.py,
    which this mirrors) → one editor-friendly step dict. The raw shapes
    are genuinely irregular (some marker-first like ("enter", n), some
    marker-last like (text, "write") or (app_name, "open")) because
    that's simply what the recorder has always appended - this is the
    one place that irregularity gets translated into a single
    consistent shape for the editor UI to work with.
    """
    # A block added in the editor (wait / repeat / if colour / colour
    # watch / end …) is already stored in exactly the editor's own
    # shape - see ai/macro_blocks.py, and play_actions' docstring for
    # why new blocks are dicts in the same list rather than positional
    # tuples like the recorded ones.
    if isinstance(action, dict):
        return dict(action)
    if len(action) == 3 and action[0] in ("click", "right_click"):
        return {"type": action[0], "x": action[1], "y": action[2]}
    if len(action) == 2 and action[1] == "write":
        return {"type": "write", "value": action[0]}
    if len(action) == 2 and action[0] == "enter":
        return {"type": "enter", "_extra": action[1]}
    if len(action) == 2 and action[0] == "hotkey":
        return {"type": "hotkey", "keys": action[1]}
    if len(action) == 2 and action[0] == "new_page":
        return {"type": "new_tab", "_extra": action[1]}
    if len(action) == 2 and action[1] == "open":
        return {"type": "open_app", "app": action[0]}
    return {"type": "unknown", "_raw": action}


def editor_step_to_literal_action(step: dict) -> list:
    """The inverse of literal_action_to_editor_step - turns one edited/
    inserted step dict back into the exact (irregular) shape
    play_actions() replays. `_extra` (enter/new_tab's own second field,
    whatever it actually means at replay time) is carried through
    unchanged for an edited step, and defaults to 1 for a brand new one
    inserted through the editor rather than recorded live."""
    t = step["type"]
    # Non-recorded blocks round-trip as themselves (see this function's
    # counterpart above) - there is no legacy tuple shape for "repeat 3
    # times", and inventing one would only be a second encoding to keep
    # in sync with ai/macro_blocks.py.
    import ai.macro_blocks as blocks
    if t in blocks.ALL_TYPES and t not in blocks.RECORDED_TYPES:
        return dict(step)
    if t in ("click", "right_click"):
        return [t, int(step["x"]), int(step["y"])]
    if t == "write":
        return [step.get("value", ""), "write"]
    if t == "enter":
        return ["enter", step.get("_extra", 1)]
    if t == "hotkey":
        return ["hotkey", step.get("keys", "")]
    if t == "new_tab":
        return ["new_page", step.get("_extra", 1)]
    if t == "open_app":
        return [step.get("app", ""), "open"]
    if t == "unknown":
        return step.get("_raw", ["enter", 1])
    raise ValueError(f"Unknown literal step type: {t!r}")


def generalized_step_to_editor_step(step: dict) -> dict:
    """A generalized macro's own step dicts (see ai/macro_generalizer.py's
    _VALID_STEP_TYPES) are already close to the editor's shape - this
    only exists so the editor UI can treat both macro kinds identically
    (same dict-in, dict-out shape) rather than branching on kind
    everywhere it touches a step."""
    return dict(step)


def editor_step_to_generalized_step(step: dict) -> dict:
    """Inverse of generalized_step_to_editor_step - drops any UI-only
    keys (none today, but keeps this symmetric with the literal side
    above and future-proof against the editor growing one) before the
    step goes back through validate_spec."""
    return {k: v for k, v in step.items() if not k.startswith("_")}


def update_literal_actions(name: str, description: str, editor_steps: list) -> tuple[bool, str]:
    """
    Overwrites an existing LITERAL macro's description/recorded_actions
    after manual editing. Returns (ok, error_message) - ok is False (spec
    untouched) if the macro doesn't exist, isn't actually a literal
    macro, ends up with zero steps, or a step dict is malformed.
    """
    spec = load_spec(name)
    if spec is None or spec.get("kind") != "literal":
        return False, f"No literal macro named {name!r} was found."
    if not editor_steps:
        return False, "A macro needs at least one step."
    # Control blocks have to balance before this is written: a literal
    # macro doesn't go through validate_spec (that's the generalized
    # path), so without this check a repeat with no end would be saved
    # happily and only misbehave at replay time.
    import ai.macro_blocks as blocks
    ok, structure_error = blocks.validate_structure(editor_steps)
    if not ok:
        return False, structure_error
    try:
        recorded_actions = [editor_step_to_literal_action(s) for s in editor_steps]
    except (KeyError, ValueError) as e:
        return False, f"Couldn't save that step: {e}"

    updated = dict(spec)
    updated["description"] = description.strip() or spec.get("description", "")
    updated["recorded_actions"] = recorded_actions
    _write_spec(name, updated)
    return True, ""


def update_generalized_steps(
    name: str, description: str, parameters: list, editor_steps: list
) -> tuple[bool, str]:
    """
    Overwrites an existing GENERALIZED macro's description/parameters/
    steps after manual editing - validated through the exact same
    validate_spec() that guards an AI-generalized macro before it's ever
    saved in the first place (see ai/macro_generalizer.py), so a
    hand-edited spec is held to the identical bar rather than a second,
    looser set of manual-editing-only rules. Returns (ok, error_message);
    on failure the on-disk spec is left untouched.
    """
    from ai.macro_generalizer import validate_spec

    spec = load_spec(name)
    if spec is None or spec.get("kind") != "generalized":
        return False, f"No generalized macro named {name!r} was found."

    steps = [editor_step_to_generalized_step(s) for s in editor_steps]
    # validate_spec (written for the AI-generalization path) expects a
    # "function_name" key rather than storage's own "name", and a
    # "confident": True flag - the AI generalizer sets that itself as
    # part of its own response and validate_spec treats its ABSENCE as
    # an automatic rejection (it's how validate_spec tells a low-
    # confidence AI response apart from a well-formed one); a person
    # editing by hand through this dialog has already implicitly
    # confirmed it by clicking Save, so it's set here explicitly.
    # Everything else lines up directly with what's already on disk.
    candidate = {
        "confident": True,
        "function_name": name,
        "description": description.strip() or spec.get("description", ""),
        "parameters": parameters,
        "steps": steps,
    }
    ok, reason = validate_spec(candidate)
    if not ok:
        return False, reason

    updated = dict(spec)
    updated["description"] = candidate["description"]
    updated["parameters"] = parameters
    updated["steps"] = steps
    _write_spec(name, updated)
    return True, ""


def resolve_macro_name(query: str) -> str | None:
    """
    Fuzzy-matches a (possibly imprecise, possibly 'tool_'-prefixed) name
    against the saved macros, the same way tool_close_application matches
    a running process name - so "the youtube one" typed slightly off, or
    "tool_open_youtube_lofi" with the prefix still attached, both resolve.
    Returns the exact stored name, or None if nothing matches closely.
    """
    import difflib
    from ai.actions import _sanitize_macro_name

    candidates = list_macro_names()
    if not candidates:
        return None

    query = _sanitize_macro_name(query.removeprefix("tool_"))
    if query in candidates:
        return query

    matches = difflib.get_close_matches(query, candidates, n=1, cutoff=0.5)
    return matches[0] if matches else None


# ══════════════════════════════════════════════════════════════════
# Synthesizing real Python callables from stored specs
# ══════════════════════════════════════════════════════════════════

def _render_literal_function(spec: dict) -> str:
    name = spec["name"]
    return (
        "\n\n"
        f"def tool_{name}():\n"
        f"    {spec['description']!r}\n"
        f"    print({'Executing macro: ' + name!r})\n"
        f"    play_actions({spec['recorded_actions']!r})\n"
    )


def register_macro(actions_module, spec: dict) -> bool:
    """
    Compiles the given macro spec into a real tool_<name> function and
    execs it into actions_module's own namespace (ai.actions.__dict__),
    so get_all_tools() picks it up exactly like any hand-written tool_*
    function - no changes needed on the Gemini/tool-registration side.
    Returns True on success. Never raises; a bad spec is logged and
    skipped rather than crashing startup.
    """
    try:
        if spec.get("kind") == "generalized":
            from ai.macro_generalizer import render_function
            # render_function expects function_name; stored specs use name.
            render_spec = dict(spec)
            render_spec["function_name"] = spec["name"]
            source = render_function(render_spec)
        else:
            source = _render_literal_function(spec)

        import ast
        ast.parse(source)  # must be valid Python before it ever runs
        exec(compile(source, f"<macro:{spec['name']}>", "exec"), actions_module.__dict__)
        return True
    except Exception as e:
        print(f"[macro_store] Failed to register macro {spec.get('name')!r}: {e}")
        return False


def load_and_register_all(actions_module) -> int:
    """
    Reads every saved macro's JSON spec and registers it as a real
    tool_<name> function on actions_module. Called after every module
    (re)load - see ai.main._ensure_initialized / refresh_actions - so
    macros survive a reload the same way hand-written tool_* functions
    always did. Returns how many were successfully registered.

    Also removes any macro function left over from a PREVIOUS call whose
    JSON record no longer exists (i.e. was deleted via tool_delete_macro
    or replaced under a different sanitized name) - importlib.reload()
    only re-runs the module's own source file, it doesn't clear
    attributes that source never mentions, so a dynamically exec'd macro
    function would otherwise keep lingering (and stay callable) in the
    module's namespace even after its JSON file is gone. This is what
    makes deletion actually take effect instead of just deleting the file
    on disk while the function silently keeps working until restart.
    """
    global _currently_registered
    current_names = set(list_macro_names())

    stale = _currently_registered - current_names
    for name in stale:
        attr = f"tool_{name}"
        if hasattr(actions_module, attr):
            try:
                delattr(actions_module, attr)
            except AttributeError:
                pass

    count = 0
    for name in current_names:
        spec = load_spec(name)
        if spec and register_macro(actions_module, spec):
            count += 1

    _currently_registered = current_names
    if count:
        print(f"[macro_store] {count} saved macro(s) registered.")
    if stale:
        print(f"[macro_store] Removed {len(stale)} stale macro function(s): {', '.join(sorted(stale))}")
    return count


def unregister_macro(actions_module, name: str) -> bool:
    """
    Take one macro's tool function out of the live namespace. Returns
    True if there was one to remove.

    The incremental counterpart to load_and_register_all, for the case
    where exactly one macro changed and the other N-1 did not. Deleting
    a macro used to go through load_and_register_all, which re-read,
    re-parsed and re-exec'd every surviving macro from disk to achieve
    the removal of one attribute - O(N) file reads and compiles to do
    O(1) of work, on the GUI thread, with the window frozen for it.

    Keeps _currently_registered in step so a later full reload still
    knows what is registered.
    """
    global _currently_registered

    attr = f"tool_{name}"
    removed = False
    if hasattr(actions_module, attr):
        try:
            delattr(actions_module, attr)
            removed = True
        except AttributeError:
            pass

    _currently_registered = _currently_registered - {name}
    return removed


# Names (without the tool_ prefix) most recently registered by
# load_and_register_all - used to detect and clean up macros that were
# deleted/replaced since the last call. Process-lifetime state, not
# persisted (nothing to persist: the JSON files under MACROS_DIR are
# themselves the durable record).
_currently_registered: set[str] = set()


# ── Export / import / share ───────────────────────────────────────────
# A macro is already a self-contained JSON spec, so moving one between
# installs is a file copy with a wrapper around it. The wrapper exists
# for three reasons: to carry more than one macro in a single file, to
# state which app version wrote it (so a future format change has
# something to check), and to give the file a recognisable shape so an
# arbitrary .json dropped into Import fails with a sentence rather than
# a traceback.
#
# Deliberately NOT a new storage format: the "macros" list holds the
# exact spec dicts that live in MACROS_DIR, unchanged, so an export can
# be read by anything that already understands a macro and importing is
# just writing them back.

EXPORT_FORMAT = branding.EXPORT_FORMAT
EXPORT_VERSION = 1
EXPORT_SUFFIX = branding.EXPORT_SUFFIX
# Ids written under a previous name, still accepted on import.
_ACCEPTED_FORMATS = (EXPORT_FORMAT, *branding.LEGACY_EXPORT_FORMATS)


class MacroTransferError(Exception):
    """Raised for an export/import that can't proceed, with a message
    written to be shown to the user as-is."""


def export_macros(names: "list[str]", path: str) -> int:
    """
    Writes the named macros to `path` as one portable file. Returns how
    many were actually written - a name with no spec on disk is skipped
    rather than failing the whole export, since the usual cause is a
    macro deleted between picking it and confirming.
    """
    specs = []
    for name in names:
        spec = load_spec(name)
        if spec is None:
            print(f"[macro_store] Skipping {name!r} in export - no spec on disk.")
            continue
        specs.append(spec)

    if not specs:
        raise MacroTransferError("None of those macros could be read, so there was nothing to export.")

    payload = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "macros": specs,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except OSError as e:
        raise MacroTransferError(f"Couldn't write {path}: {e}") from e
    return len(specs)


def read_export(path: str) -> "list[dict]":
    """
    The macro specs inside an export file, validated enough to fail
    with a sentence instead of a traceback.

    Also accepts a BARE macro spec (a single .json straight out of
    MACROS_DIR), because that's what someone who found the folder will
    inevitably try to import, and refusing it would be pedantry - the
    contents are the same either way.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise MacroTransferError(f"Couldn't read {os.path.basename(path)}: {e}") from e

    if isinstance(data, dict) and data.get("format") in _ACCEPTED_FORMATS:
        macros = data.get("macros")
        if not isinstance(macros, list) or not macros:
            raise MacroTransferError("That file is a macro export, but it contains no macros.")
        specs = [m for m in macros if isinstance(m, dict) and m.get("name")]
        if not specs:
            raise MacroTransferError("That export's macros are malformed - none of them have a name.")
        return specs

    if isinstance(data, dict) and data.get("name") and data.get("kind"):
        return [data]  # a single spec file

    raise MacroTransferError(
        f"{os.path.basename(path)} isn't a {branding.APP_NAME} macro file. Exports end in {EXPORT_SUFFIX}."
    )


def import_macros(specs: "list[dict]", overwrite: bool = False) -> "tuple[list[str], list[str]]":
    """
    Writes imported specs into MACROS_DIR. Returns (imported, skipped)
    names.

    Won't silently replace an existing macro: a name that's already
    taken is reported back as skipped unless `overwrite` says otherwise,
    since the alternative is someone losing a macro they built by
    importing a file that happened to share its name.
    """
    from ai.actions import _sanitize_macro_name

    imported, skipped = [], []
    for spec in specs:
        name = _sanitize_macro_name(str(spec.get("name", "")))
        if not name:
            continue
        if macro_exists(name) and not overwrite:
            skipped.append(name)
            continue
        record = dict(spec)
        record["name"] = name
        # Imported macros are somebody else's work, and the badge on the
        # Macros page should say what it is rather than claiming the
        # importer recorded it.
        record.setdefault("created_by", created_by(spec))
        _write_spec(name, record)
        imported.append(name)
    return imported, skipped
