# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/providers/schema.py
=========================
Turns a plain Python function (one of ai/actions.py's tool_* functions,
or the vision tool in ai/main.py) into a provider-agnostic ToolSchema -
the one place that knows how to read a function's signature/docstring,
so openai_provider.py and anthropic_provider.py both build their very
different JSON-schema shapes FROM THE SAME extraction instead of each
re-parsing docstrings independently (and drifting from each other, or
from what Gemini's own automatic function calling infers from the
exact same functions).

Gemini needs none of this - google-genai's automatic function calling
does its own equivalent introspection internally when you hand it raw
callables. This module exists so OpenAI and Anthropic, which both
require an explicit schema up front, describe each tool the same way
Gemini already infers it from the same source of truth: the function
itself. No tool needs a second, hand-maintained schema anywhere.
"""

from __future__ import annotations

import inspect
import re
import typing

from ai.providers.base import ToolParam, ToolSchema

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

# Matches a docstring line like "reason: one short sentence describing..."
# or (inside a Google-style "Args:" block) "  target: what to click...".
# Continuation lines (further-indented, no "word:" prefix) get folded
# into the same param's description until a blank line or the next
# "word:" line - see _extract_param_docs below.
_PARAM_LINE = re.compile(r"^\s*(\w+)\s*:\s*(.+)$")


def _python_type_to_json_type(annotation) -> tuple[str, "str | None", "list[str] | None"]:
    """Returns (json_type, items_type_if_array, enum_values_if_literal)."""
    if annotation is inspect.Parameter.empty:
        return "string", None, None  # untyped param - safest default, still usable

    origin = typing.get_origin(annotation)

    # `X | None` / `Optional[X]` - unwrap to X; required-ness is handled
    # separately (from the function's own default value), this is only
    # about the underlying type of the value itself.
    if origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _python_type_to_json_type(args[0])
        return "string", None, None  # a real multi-type union - no clean JSON-schema mapping, fall back

    if origin is typing.Literal:
        values = [str(v) for v in typing.get_args(annotation)]
        return "string", None, values

    if origin in (list, typing.List):
        item_args = typing.get_args(annotation)
        item_type = _python_type_to_json_type(item_args[0])[0] if item_args else "string"
        return "array", item_type, None

    if annotation in _TYPE_MAP:
        return _TYPE_MAP[annotation], None, None

    return "string", None, None  # unknown/custom type - degrade to string rather than fail


def _extract_param_docs(docstring: str, param_names: "set[str]") -> dict:
    """
    Best-effort per-parameter description extraction. Not all tool
    docstrings in this codebase document params this way (several are
    pure prose - see ai/actions.py's tool_open_application for one) -
    those simply get an empty per-param description, which is fine:
    the full docstring still becomes the tool's overall description
    (see build_tool_schema below), so the model isn't missing context,
    just a per-field label.
    """
    docs: dict[str, str] = {}
    current: "str | None" = None
    for line in docstring.splitlines():
        stripped = line.strip()
        if not stripped:
            current = None
            continue
        m = _PARAM_LINE.match(line)
        if m and m.group(1) in param_names:
            current = m.group(1)
            docs[current] = m.group(2).strip()
        elif current and stripped:
            docs[current] += " " + stripped
    return docs


def build_tool_schema(func) -> ToolSchema:
    """
    Introspects a single tool_* function (or any plain function with
    type-hinted params and a docstring) into a ToolSchema. Skips `self`
    and any leading underscore-prefixed param by convention, matching
    what would already be a broken tool for Gemini's automatic
    function calling too (that convention isn't new here).
    """
    sig = inspect.signature(func)
    doc = inspect.getdoc(func) or ""
    param_names = {n for n in sig.parameters if not n.startswith("_") and n != "self"}
    param_docs = _extract_param_docs(doc, param_names)
    # Explicit per-param enums, declared as a plain attribute on the
    # function instead of a Literal[...] annotation - see
    # ai/actions.py's tool_generate_file.__enum_params__ and the long
    # comment above it: a subscripted generic in a tool signature makes
    # Gemini's automatic function calling raise "Subscripted generics
    # cannot be used with class and instance checks" on every call,
    # before the function's body ever runs. This attribute is how a
    # tool keeps a real enum in the OpenAI/Anthropic JSON Schema
    # without carrying that annotation.
    explicit_enums = getattr(func, "__enum_params__", None) or {}

    params = []
    for name, p in sig.parameters.items():
        if name.startswith("_") or name == "self":
            continue
        json_type, items_type, enum_values = _python_type_to_json_type(p.annotation)
        if name in explicit_enums:
            enum_values = [str(v) for v in explicit_enums[name]]
        required = p.default is inspect.Parameter.empty
        params.append(ToolParam(
            name=name,
            type=json_type,
            description=param_docs.get(name, ""),
            required=required,
            enum=enum_values,
            items_type=items_type,
        ))

    return ToolSchema(
        name=func.__name__,
        # Full docstring, not just the first line - several tools (e.g.
        # tool_run_command's allow-list rules, tool_request_human_handoff's
        # "call this ONLY for..." guidance) put load-bearing behavior
        # instructions past the first sentence. Truncating here would
        # silently make those providers behave differently from Gemini,
        # which sees the same full docstring via automatic function
        # calling.
        description=doc.strip() or f"Call {func.__name__}.",
        params=params,
    )


def build_tool_schemas(functions: "list") -> "list[ToolSchema]":
    return [build_tool_schema(f) for f in functions]


# ── Per-provider JSON shape conversion ────────────────────────────────
# Both providers want "the same information" (name, description,
# per-param JSON Schema, required list) but wrapped differently - kept
# here rather than duplicated inside each provider module.

def _params_to_json_schema(params: "list[ToolParam]") -> dict:
    properties = {}
    required = []
    for p in params:
        prop: dict = {"type": p.type}
        if p.description:
            prop["description"] = p.description
        if p.enum:
            prop["enum"] = p.enum
        if p.type == "array":
            prop["items"] = {"type": p.items_type or "string"}
        properties[p.name] = prop
        if p.required:
            required.append(p.name)
    return {"type": "object", "properties": properties, "required": required}


def to_openai_tool(schema: ToolSchema) -> dict:
    """OpenAI Chat Completions tool shape (nested under "function") - kept only for reference/back-compat; openai_provider.py itself now
    talks to the Responses API and uses to_openai_responses_tool below
    instead."""
    return {
        "type": "function",
        "function": {
            "name": schema.name,
            "description": schema.description,
            "parameters": _params_to_json_schema(schema.params),
        },
    }


def to_openai_responses_tool(schema: ToolSchema) -> dict:
    """
    OpenAI Responses API tool shape - flat, NOT nested under a
    "function" key the way Chat Completions' tool defs are (see
    to_openai_tool above). Same name/description/parameters otherwise;
    this is purely a wire-format difference between OpenAI's two APIs,
    not a difference in what either one can express.
    """
    return {
        "type": "function",
        "name": schema.name,
        "description": schema.description,
        "parameters": _params_to_json_schema(schema.params),
    }


def to_anthropic_tool(schema: ToolSchema) -> dict:
    """Anthropic Messages API tool shape."""
    return {
        "name": schema.name,
        "description": schema.description,
        "input_schema": _params_to_json_schema(schema.params),
    }
