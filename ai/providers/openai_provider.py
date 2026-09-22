# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/providers/openai_provider.py
==================================
Talks to OpenAI's Responses API (client.responses.*) - NOT the older
Chat Completions API (client.chat.completions.*) this module used
before extended thinking support existed. That switch is the only way
to get a GPT-5 reasoning model's own reasoning back at all: Chat
Completions never exposes it, full stop, no matter the model -
`reasoning.summary` is OpenAI's own documented, supported way to get a
plain-language summary of it, and it only exists on the Responses API
(OpenAI platform docs, "Reasoning models" guide). Everything else about
this module's shape carries over unchanged from the Chat Completions
version: same manual tool loop (Responses API has no automatic function
calling either), same fail-safe "a tool that raises becomes a normal-
looking failure result, not a crash" behavior (see ai/actions.py's
emergency_stop comment) - just in the Responses API's own request/
response vocabulary:
  - `instructions` (a plain string) replaces a `{"role": "system", ...}`
    message - there's no system/developer message in `input` here.
  - `input` is a flat list of typed ITEMS, not chat "messages" - a
    reply's own text lives in a "message" item, a tool call is its own
    separate "function_call" item (not a `tool_calls` list hanging off
    one message), and a tool's result goes back as its own
    "function_call_output" item, keyed by `call_id` - not a
    `{"role": "tool", "tool_call_id": ...}` message.
  - a turn is finished when the response's `.output` contains no
    "function_call" items, rather than `message.tool_calls` being empty.

Continuing a multi-turn tool loop works by resending the FULL previous
`.output` list (reasoning items included) back as new `input` items,
immediately followed by this app's own `function_call_output` items -
the same pattern anthropic_provider.py uses for its own thinking
blocks, and for the same reason: a reasoning item is expected back
alongside the function_call It reasoned its way into, on models that
produce one.

CAVEAT (please read if GPT's "Thinking…" section never appears): the
exact event-type strings the Responses API's streaming mode uses for a
reasoning summary's own delta events are the one part of this module
not pinned down by long-stable, thoroughly-documented API surface the
way tool-calling and plain text streaming are - see
_REASONING_SUMMARY_DELTA_EVENT_TYPES below. Text streaming, tool calls,
and non-streamed reasoning summaries (send_message, not
send_message_stream) do NOT depend on that constant at all and are on
solid ground regardless.
"""

from __future__ import annotations

import base64
import json

from ai.providers.base import ChatResponse, ChatSession, ImagePart, TextPart, ToolCallRecord
from ai.providers.schema import build_tool_schema, to_openai_responses_tool

MAX_TOOL_ITERATIONS = 25  # a runaway "call a tool, get an error, call it again forever" loop stops here

# response.output_text.delta is long-stable, thoroughly-documented
# Responses API streaming surface (the plain "here's the next chunk of
# visible text" event) - high confidence.
_TEXT_DELTA_EVENT_TYPE = "response.output_text.delta"

# The equivalent event for a reasoning SUMMARY's own streamed text is
# newer, less universally exercised surface - this app hedges by
# accepting either name OpenAI has used for it rather than betting on
# one. If a future SDK version renames it again, streamed thinking
# quietly stops arriving (falls through to nothing, same as thinking
# being off) rather than crashing the whole reply - see the
# defensive `getattr` usage in send_message_stream below.
_REASONING_SUMMARY_DELTA_EVENT_TYPES = (
    "response.reasoning_summary_text.delta",
    "response.reasoning_summary.delta",
)


def _reasoning_kwarg(enable_thinking: bool) -> dict:
    """Empty dict when extended thinking is off - a request with no
    `reasoning` param at all is byte-identical to every request this
    module sent before this feature existed, which also matters
    because a non-reasoning model (e.g. a "-nano"/"-mini" variant that
    doesn't support reasoning at all) would reject a `reasoning` param
    outright. "auto" is OpenAI's own value for "let the model decide
    how much of a summary to produce" - about as close as this app
    gets to matching Anthropic's fixed budget_tokens or Gemini's
    thinking_budget=-1 with an API that takes neither."""
    if not enable_thinking:
        return {}
    return {"reasoning": {"effort": "medium", "summary": "auto"}}


def _output_text(response_or_output_item) -> str:
    """Joins every output_text part's text out of either a full
    Response object's `.output` (message items only - function_call
    and reasoning items have no `.content` to read here, so they're
    skipped rather than erroring) or the SDK's own `.output_text`
    convenience property, whichever is available. Kept as one place so
    both send_message() and send_message_stream()'s final-response
    handling agree on how a reply's visible text is extracted.
    Deliberately checks `isinstance(..., str)` rather than just "is not
    None" - a Response-shaped object that doesn't actually set this
    attribute should fall through to scanning `.output` instead of
    treating some other non-string placeholder as if it were real text."""
    text = getattr(response_or_output_item, "output_text", None)
    if isinstance(text, str):
        return text
    chunks = []
    for item in getattr(response_or_output_item, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            piece = getattr(part, "text", None)
            if piece:
                chunks.append(piece)
    return "".join(chunks)


def _reasoning_summary_text(output_items) -> str:
    """Joins every "reasoning" item's summary text out of a Response's
    `.output` list - a reasoning item with no summary parts at all
    (e.g. extended thinking off, or a model/tier that only returns
    encrypted reasoning content with no plain-language summary)
    contributes nothing rather than erroring."""
    chunks = []
    for item in output_items or []:
        if getattr(item, "type", None) != "reasoning":
            continue
        for part in getattr(item, "summary", None) or []:
            piece = getattr(part, "text", None)
            if piece:
                chunks.append(piece)
    return "".join(chunks)


def _function_calls(output_items):
    """Every "function_call" item in a Response's `.output` list, in
    order - Responses API puts each tool call in its own top-level
    output item (unlike Chat Completions, which nests a list of them
    under one assistant message)."""
    return [item for item in (output_items or []) if getattr(item, "type", None) == "function_call"]


class OpenAIChatSession(ChatSession):
    def __init__(self, client, model: str, tool_functions: list, system_instruction: str, enable_thinking: bool = False):
        self._client = client  # an openai.OpenAI instance
        self._model = model
        self._instructions = system_instruction
        self._tool_functions = {f.__name__: f for f in tool_functions}
        self._tool_defs = [to_openai_responses_tool(build_tool_schema(f)) for f in tool_functions]
        self._enable_thinking = enable_thinking
        # Responses API history: a flat list of typed ITEMS (see module
        # docstring) - NOT chat "messages". No system/developer item is
        # seeded here; `instructions` is passed fresh on every single
        # call instead (see send_message), the same way Anthropic's
        # separate top-level `system` param works, rather than
        # reserving a first-message slot the way Chat Completions did.
        self._items: list = []

    def _parts_to_content(self, parts: list):
        content = []
        for part in parts:
            if isinstance(part, TextPart):
                content.append({"type": "input_text", "text": part.text})
            elif isinstance(part, ImagePart):
                b64 = base64.b64encode(part.to_png_bytes()).decode("ascii")
                content.append({"type": "input_image", "image_url": f"data:image/png;base64,{b64}"})
            else:
                # Audio input isn't supported by the plain
                # Responses-API models in this codebase's curated list
                # (base.py's MODEL_CHOICES) - voice mode stays on
                # Gemini specifically (see ai/main.py's voice_chat,
                # left untouched by this abstraction) rather than
                # silently dropping the audio here.
                raise TypeError(
                    "OpenAIChatSession received an unsupported content part "
                    f"({type(part).__name__}) - voice input isn't wired through "
                    "the OpenAI/Anthropic path yet, only Gemini's."
                )
        return content

    def _execute_tool_call(self, name: str, raw_arguments: str) -> str:
        func = self._tool_functions.get(name)
        if func is None:
            return f"Error: no such tool '{name}'."
        try:
            args = json.loads(raw_arguments) if raw_arguments else {}
        except json.JSONDecodeError as e:
            return f"Error: couldn't parse arguments for '{name}': {e}"
        try:
            result = func(**args)
        except Exception as e:
            # Same shape Gemini's automatic function calling already
            # gives the model on a tool exception - see module docstring.
            return f"Error: tool '{name}' failed: {e}"
        return "" if result is None else str(result)

    def seed_history(self, turns: "list[tuple[str, str]]") -> None:
        for role, text in turns:
            self._items.append({"role": role, "content": text})

    def send_message(self, parts: list) -> ChatResponse:
        self._items.append({"role": "user", "content": self._parts_to_content(parts)})

        tool_call_records: list[ToolCallRecord] = []
        for _ in range(MAX_TOOL_ITERATIONS):
            response = self._client.responses.create(
                model=self._model,
                instructions=self._instructions,
                input=self._items,
                tools=self._tool_defs or [],
                **_reasoning_kwarg(self._enable_thinking),
            )
            # The FULL output list - reasoning items included - goes
            # back into history verbatim, same as
            # anthropic_provider.py's `response.content`: a later
            # function_call_output item in this same loop is expected
            # to follow the reasoning/function_call item it answers,
            # on models that produce one (see module docstring).
            self._items.extend(response.output)
            thinking_text = _reasoning_summary_text(response.output) if self._enable_thinking else ""

            calls = _function_calls(response.output)
            if not calls:
                return ChatResponse(text=_output_text(response), tool_calls=tool_call_records, thinking=thinking_text)

            for call in calls:
                result_text = self._execute_tool_call(call.name, call.arguments)
                tool_call_records.append(ToolCallRecord(
                    name=call.name,
                    args=_safe_json_loads(call.arguments),
                    result=result_text,
                ))
                self._items.append({
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": result_text,
                })

        return ChatResponse(
            text="(Stopped after a long chain of tool calls without a final answer - try rephrasing.)",
            tool_calls=tool_call_records,
        )

    def send_message_stream(self, parts: list, on_delta, on_thinking_delta=None) -> ChatResponse:
        """
        Same manual tool loop as send_message(), but each leg's own
        outgoing text - and, when extended thinking is on, its
        reasoning summary text - streams in as OpenAI's API generates
        it (client.responses.stream(...), real network streaming, not
        simulated). Iterates the stream's raw events (rather than a
        text-only convenience helper) so a reasoning-summary delta
        event can be routed to on_thinking_delta separately from
        on_delta - see _REASONING_SUMMARY_DELTA_EVENT_TYPES above for
        the one part of this that's a best-effort guess rather than a
        sure thing. stream.get_final_response() hands back the exact
        same Response shape send_message() already works with (.output
        fully parsed, function_call items included), so nothing below
        this line needs to know streaming even happened.
        """
        self._items.append({"role": "user", "content": self._parts_to_content(parts)})

        tool_call_records: list[ToolCallRecord] = []
        for _ in range(MAX_TOOL_ITERATIONS):
            with self._client.responses.stream(
                model=self._model,
                instructions=self._instructions,
                input=self._items,
                tools=self._tool_defs or [],
                **_reasoning_kwarg(self._enable_thinking),
            ) as stream:
                for event in stream:
                    event_type = getattr(event, "type", None)
                    if event_type == _TEXT_DELTA_EVENT_TYPE:
                        on_delta(event.delta)
                    elif (
                        self._enable_thinking
                        and on_thinking_delta is not None
                        and event_type in _REASONING_SUMMARY_DELTA_EVENT_TYPES
                    ):
                        on_thinking_delta(event.delta)
                response = stream.get_final_response()

            self._items.extend(response.output)
            thinking_text = _reasoning_summary_text(response.output) if self._enable_thinking else ""

            calls = _function_calls(response.output)
            if not calls:
                return ChatResponse(text=_output_text(response), tool_calls=tool_call_records, thinking=thinking_text)

            for call in calls:
                result_text = self._execute_tool_call(call.name, call.arguments)
                tool_call_records.append(ToolCallRecord(
                    name=call.name,
                    args=_safe_json_loads(call.arguments),
                    result=result_text,
                ))
                self._items.append({
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": result_text,
                })

        return ChatResponse(
            text="(Stopped after a long chain of tool calls without a final answer - try rephrasing.)",
            tool_calls=tool_call_records,
        )

    def history_as_text_turns(self) -> "list[tuple[str, str]]":
        from ai.providers.base import merge_consecutive_turns
        turns = []
        for m in self._items:
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
            if role not in ("user", "assistant"):
                continue
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text_chunks = []
                for c in content:
                    if isinstance(c, dict):
                        piece = c.get("text", "")
                    else:
                        piece = getattr(c, "text", "") or ""
                    if piece:
                        text_chunks.append(piece)
                text = " ".join(text_chunks)
            else:
                text = ""
            text = text.strip()
            if text:
                turns.append((role, text))
        return merge_consecutive_turns(turns)


def _safe_json_loads(raw: str) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def create_chat_session(client, model: str, tool_functions: list, system_instruction: str) -> OpenAIChatSession:
    # Read lazily - see anthropic_provider.py's create_chat_session for
    # why (avoids a circular import with ai.main, which imports this
    # package). Settings > "Extended thinking" takes effect on the very
    # next message, on whichever provider/model is active at the time.
    import ai.main as ai_main
    enable_thinking = ai_main.get_extended_thinking_enabled()
    return OpenAIChatSession(client, model, tool_functions, system_instruction, enable_thinking=enable_thinking)
