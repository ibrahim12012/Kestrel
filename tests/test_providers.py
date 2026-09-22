# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_providers.py
==========================
Unit tests for ai/providers/ - all against mocked openai/anthropic
clients (no live API calls, no cost, no network). Covers the two things
most likely to silently break: the manual tool-calling loop each
provider has to run itself (Gemini doesn't need this - automatic
function calling is the SDK's job, not this codebase's), and turn
extraction/merging for switch_provider()'s mid-chat provider switch.

Note: ai/actions.py itself can't be imported outside Windows (win32gui/
AppOpener/etc. at module level) - these tests use small standalone
functions with the same tool_* shape (typed params + docstring)
instead. ai/providers/schema.py's own extraction logic was separately
verified against the REAL ai/actions.py functions via AST-based stub
generation during development (not checked in as a test - it needs the
actual source file's functions, which change; the point was validating
the extractor against real docstrings once, not asserting on their
exact current wording forever).
"""

from unittest.mock import MagicMock, patch

import pytest

from ai.providers.base import TextPart, merge_consecutive_turns
from ai.providers.openai_provider import OpenAIChatSession
from ai.providers.anthropic_provider import AnthropicChatSession
from ai.providers.local_provider import LocalChatSession
from ai.providers.schema import build_tool_schema, to_openai_tool, to_openai_responses_tool, to_anthropic_tool


def _ok_tool(x: str) -> str:
    """A tool that works. x: anything."""
    return f"ok:{x}"


def _failing_tool(x: str) -> str:
    """A tool that always fails. x: anything."""
    raise ValueError("boom")


# ── schema.py ──────────────────────────────────────────────────────

def test_schema_infers_required_vs_optional_from_defaults():
    def f(a: str, b: str | None = None) -> str:
        """Does a thing."""
        return ""
    schema = build_tool_schema(f)
    by_name = {p.name: p for p in schema.params}
    assert by_name["a"].required is True
    assert by_name["b"].required is False


def test_schema_extracts_inline_param_docs():
    def f(reason: str) -> str:
        """
        Does a thing.
        reason: one short sentence explaining why.
        """
        return ""
    schema = build_tool_schema(f)
    assert "one short sentence" in schema.params[0].description


def test_schema_full_docstring_becomes_description_not_just_first_line():
    def f() -> str:
        """First line.

        Second paragraph with load-bearing instructions that must not
        be dropped.
        """
        return ""
    schema = build_tool_schema(f)
    assert "load-bearing instructions" in schema.description


def test_openai_and_anthropic_tool_shapes_are_valid():
    schema = build_tool_schema(_ok_tool)
    oa = to_openai_tool(schema)
    oa_responses = to_openai_responses_tool(schema)
    an = to_anthropic_tool(schema)
    assert oa["type"] == "function"
    assert oa["function"]["name"] == "_ok_tool"
    assert "x" in oa["function"]["parameters"]["properties"]
    # Responses API shape is flat - no nested "function" key.
    assert oa_responses["type"] == "function"
    assert oa_responses["name"] == "_ok_tool"
    assert "x" in oa_responses["parameters"]["properties"]
    assert an["name"] == "_ok_tool"
    assert "x" in an["input_schema"]["properties"]


# ── merge_consecutive_turns ──────────────────────────────────────────

def test_merge_consecutive_turns_collapses_same_role():
    turns = [("user", "a"), ("assistant", "b"), ("assistant", "c"), ("user", "d")]
    merged = merge_consecutive_turns(turns)
    assert merged == [("user", "a"), ("assistant", "b\n\nc"), ("user", "d")]


def test_merge_consecutive_turns_noop_when_already_alternating():
    turns = [("user", "a"), ("assistant", "b")]
    assert merge_consecutive_turns(turns) == turns


# ── OpenAI provider: tool loop (Responses API) ────────────────────────
# client.responses.create(...) returns a Response whose `.output` is a
# flat list of typed ITEMS (message / function_call / reasoning) - not
# chat "messages" with a tool_calls list hanging off one of them. These
# helpers build exactly that shape rather than reusing Chat
# Completions' message/choice shape.

def _openai_message_item(text):
    part = MagicMock(type="output_text", text=text)
    item = MagicMock(type="message", role="assistant", content=[part])
    return item


def _openai_function_call_item(call_id, name, arguments, item_id="fc_1"):
    item = MagicMock(type="function_call", call_id=call_id, arguments=arguments, id=item_id)
    item.name = name  # set separately - `name` is a reserved MagicMock kwarg
    return item


def _openai_reasoning_item(summary_text, item_id="rs_1"):
    item = MagicMock(type="reasoning", id=item_id)
    item.summary = [MagicMock(type="summary_text", text=summary_text)] if summary_text else []
    return item


def _openai_response(output, output_text=None):
    resp = MagicMock(output=output)
    if output_text is not None:
        resp.output_text = output_text
    else:
        # Force _output_text() to fall back to scanning .output instead
        # of treating a MagicMock auto-vivified attribute as real text
        # (see openai_provider.py's own isinstance(..., str) guard).
        del resp.output_text
    return resp


def test_openai_single_tool_call_then_final_answer():
    client = MagicMock()
    call = _openai_function_call_item("call_1", "_ok_tool", '{"x": "hello"}')
    resp1 = _openai_response([call])
    resp2 = _openai_response([_openai_message_item("Done: ok:hello")], output_text="Done: ok:hello")
    client.responses.create.side_effect = [resp1, resp2]

    session = OpenAIChatSession(client, "gpt-5.4", [_ok_tool], "system")
    result = session.send_message([TextPart("go")])

    assert result.text == "Done: ok:hello"
    assert result.tool_calls[0].result == "ok:hello"
    # The tool's result went back as a function_call_output item keyed
    # by call_id - not a {"role": "tool", "tool_call_id": ...} message.
    _, kwargs = client.responses.create.call_args
    fco = [i for i in kwargs["input"] if isinstance(i, dict) and i.get("type") == "function_call_output"]
    assert fco == [{"type": "function_call_output", "call_id": "call_1", "output": "ok:hello"}]


def test_openai_failing_tool_does_not_crash_the_loop():
    client = MagicMock()
    call = _openai_function_call_item("call_1", "_failing_tool", '{"x": "hello"}')
    resp1 = _openai_response([call])
    resp2 = _openai_response([_openai_message_item("I tried and it failed.")], output_text="I tried and it failed.")
    client.responses.create.side_effect = [resp1, resp2]

    session = OpenAIChatSession(client, "gpt-5.4", [_failing_tool], "system")
    result = session.send_message([TextPart("go")])

    assert "Error: tool '_failing_tool' failed: boom" in result.tool_calls[0].result
    assert result.text == "I tried and it failed."


def test_openai_malformed_tool_arguments_handled_gracefully():
    client = MagicMock()
    call = _openai_function_call_item("call_1", "_ok_tool", "{not valid json")
    resp1 = _openai_response([call])
    resp2 = _openai_response([_openai_message_item("ok")], output_text="ok")
    client.responses.create.side_effect = [resp1, resp2]

    session = OpenAIChatSession(client, "gpt-5.4", [_ok_tool], "system")
    result = session.send_message([TextPart("go")])
    assert "couldn't parse arguments" in result.tool_calls[0].result


def test_openai_history_round_trips_through_seed_history():
    client = MagicMock()
    resp = _openai_response([_openai_message_item("hi there")], output_text="hi there")
    client.responses.create.side_effect = [resp]

    session = OpenAIChatSession(client, "gpt-5.4", [], "system")
    session.send_message([TextPart("hello")])
    turns = session.history_as_text_turns()
    assert turns == [("user", "hello"), ("assistant", "hi there")]

    session2 = OpenAIChatSession(client, "gpt-5.4", [], "system")
    session2.seed_history(turns)
    assert session2._items == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_openai_output_text_falls_back_to_scanning_output_when_convenience_property_missing():
    """Some Response-shaped test doubles (and, in principle, a future
    SDK edge case) won't have the `.output_text` convenience property
    at all - _output_text() must still assemble the same string from
    the raw message/output_text parts."""
    from ai.providers.openai_provider import _output_text
    resp = _openai_response([_openai_message_item("hi "), _openai_message_item("there")])
    assert _output_text(resp) == "hi there"


# ── OpenAI provider: extended thinking (reasoning summaries) ──────────
# Settings > "Extended thinking" (ai.main.get_extended_thinking_enabled)
# - see openai_provider.py's module docstring for why this required
# switching from Chat Completions to the Responses API in the first
# place (Chat Completions never exposes any of a reasoning model's own
# reasoning, full stop).

def test_openai_thinking_off_by_default_sends_no_reasoning_param():
    client = MagicMock()
    client.responses.create.return_value = _openai_response(
        [_openai_message_item("hi")], output_text="hi"
    )
    session = OpenAIChatSession(client, "gpt-5.4", [], "system")
    session.send_message([TextPart("hello")])
    _, kwargs = client.responses.create.call_args
    assert "reasoning" not in kwargs


def test_openai_thinking_on_sends_reasoning_param_and_captures_summary_text():
    client = MagicMock()
    reasoning_item = _openai_reasoning_item("Let me work through this.")
    client.responses.create.return_value = _openai_response(
        [reasoning_item, _openai_message_item("42")], output_text="42"
    )
    session = OpenAIChatSession(client, "gpt-5.4", [], "system", enable_thinking=True)
    result = session.send_message([TextPart("what is 6*7?")])

    _, kwargs = client.responses.create.call_args
    assert kwargs["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert result.thinking == "Let me work through this."
    assert result.text == "42"


def test_openai_reasoning_item_with_no_summary_contributes_no_text():
    """A reasoning item with an empty/absent summary (thinking off, or
    a tier that only returns encrypted reasoning with no plain-language
    summary) must not crash - just contribute nothing."""
    client = MagicMock()
    reasoning_item = _openai_reasoning_item(None)
    client.responses.create.return_value = _openai_response(
        [reasoning_item, _openai_message_item("answer")], output_text="answer"
    )
    session = OpenAIChatSession(client, "gpt-5.4", [], "system", enable_thinking=True)
    result = session.send_message([TextPart("go")])
    assert result.thinking == ""
    assert result.text == "answer"


# ── Anthropic provider: tool loop ─────────────────────────────────────

def _anthropic_tool_block(tool_id, name, input_):
    b = MagicMock(type="tool_use", id=tool_id, input=input_)
    b.name = name  # MagicMock(name=...) is special-cased by the constructor; must assign post-hoc
    return b


def test_anthropic_single_tool_call_then_final_answer():
    client = MagicMock()
    tb = _anthropic_tool_block("t1", "_ok_tool", {"x": "hello"})
    resp1 = MagicMock(stop_reason="tool_use", content=[tb])
    text_block = MagicMock(type="text", text="Done: ok:hello")
    resp2 = MagicMock(stop_reason="end_turn", content=[text_block])
    client.messages.create.side_effect = [resp1, resp2]

    session = AnthropicChatSession(client, "claude-sonnet-5", [_ok_tool], "system")
    result = session.send_message([TextPart("go")])

    assert result.text == "Done: ok:hello"
    assert result.tool_calls[0].result == "ok:hello"


def test_anthropic_failing_tool_does_not_crash_the_loop():
    client = MagicMock()
    tb = _anthropic_tool_block("t1", "_failing_tool", {"x": "hello"})
    resp1 = MagicMock(stop_reason="tool_use", content=[tb])
    text_block = MagicMock(type="text", text="It failed.")
    resp2 = MagicMock(stop_reason="end_turn", content=[text_block])
    client.messages.create.side_effect = [resp1, resp2]

    session = AnthropicChatSession(client, "claude-sonnet-5", [_failing_tool], "system")
    result = session.send_message([TextPart("go")])
    assert "Error: tool '_failing_tool' failed: boom" in result.tool_calls[0].result


def test_anthropic_multi_round_tool_calls_stay_alternating_after_merge():
    client = MagicMock()
    tb1 = _anthropic_tool_block("t1", "_ok_tool", {"x": "a"})
    resp1 = MagicMock(stop_reason="tool_use", content=[tb1])
    tb2 = _anthropic_tool_block("t2", "_ok_tool", {"x": "b"})
    resp2 = MagicMock(stop_reason="tool_use", content=[tb2])
    text_block = MagicMock(type="text", text="All done.")
    resp3 = MagicMock(stop_reason="end_turn", content=[text_block])
    client.messages.create.side_effect = [resp1, resp2, resp3]

    session = AnthropicChatSession(client, "claude-sonnet-5", [_ok_tool], "system")
    session.send_message([TextPart("go")])

    turns = session.history_as_text_turns()
    roles = [r for r, _ in turns]
    assert roles == ["user", "assistant"]  # the two tool-round assistant entries collapsed into one


def test_anthropic_history_round_trips_through_seed_history():
    client = MagicMock()
    text_block = MagicMock(type="text", text="hi there")
    resp = MagicMock(stop_reason="end_turn", content=[text_block])
    client.messages.create.side_effect = [resp]

    session = AnthropicChatSession(client, "claude-sonnet-5", [], "system")
    session.send_message([TextPart("hello")])
    turns = session.history_as_text_turns()
    assert turns == [("user", "hello"), ("assistant", "hi there")]

    session2 = AnthropicChatSession(client, "claude-sonnet-5", [], "system")
    session2.seed_history(turns)
    assert session2._messages[0]["role"] == "user"  # Anthropic requires starting with "user"


# ── Anthropic provider: REAL streaming (send_message_stream) ──────────

def _text_delta_event(text):
    return MagicMock(type="content_block_delta", delta=MagicMock(type="text_delta", text=text))


def _thinking_delta_event(text):
    """A `thinking_delta` content_block_delta event - the raw streaming
    event send_message_stream now reads (see that method's own
    docstring for why it iterates the stream directly instead of the
    text_stream convenience helper, which only ever surfaces text)."""
    return MagicMock(type="content_block_delta", delta=MagicMock(type="thinking_delta", thinking=text))


def _anthropic_stream_cm(events, final_message):
    """Fakes `with client.messages.stream(...) as stream: for event in
    stream: ...` - the stream itself iterates as a sequence of raw
    events (content_block_delta, etc.), plus stream.get_final_message()
    (the fully-parsed accumulated Message, same shape
    client.messages.create() already returns)."""
    stream_obj = MagicMock()
    stream_obj.__iter__ = MagicMock(return_value=iter(events))
    stream_obj.get_final_message.return_value = final_message
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=stream_obj)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def test_anthropic_stream_runs_tool_loop_and_streams_only_final_leg_text():
    tb = _anthropic_tool_block("t1", "_ok_tool", {"x": "hello"})
    leg1_final = MagicMock(stop_reason="tool_use", content=[tb])
    text_block = MagicMock(type="text", text="Done: ok:hello")
    leg2_final = MagicMock(stop_reason="end_turn", content=[text_block])

    client = MagicMock()
    client.messages.stream.side_effect = [
        _anthropic_stream_cm([], leg1_final),  # leg 1: tool call, no visible text
        _anthropic_stream_cm(
            [_text_delta_event("Done: "), _text_delta_event("ok:hello")], leg2_final
        ),  # leg 2: streamed final answer
    ]

    session = AnthropicChatSession(client, "claude-sonnet-5", [_ok_tool], "system")
    received = []
    result = session.send_message_stream([TextPart("go")], received.append)

    assert result.text == "Done: ok:hello"
    assert "".join(received) == "Done: ok:hello"
    assert result.tool_calls[0].name == "_ok_tool"
    assert result.tool_calls[0].result == "ok:hello"


def test_anthropic_stream_with_no_tool_use_streams_text_and_returns_final_message():
    text_block = MagicMock(type="text", text="hi there")
    final = MagicMock(stop_reason="end_turn", content=[text_block])
    client = MagicMock()
    client.messages.stream.return_value = _anthropic_stream_cm(
        [_text_delta_event("hi "), _text_delta_event("there")], final
    )

    session = AnthropicChatSession(client, "claude-sonnet-5", [], "system")
    received = []
    result = session.send_message_stream([TextPart("hello")], received.append)

    assert result.text == "hi there"
    assert "".join(received) == "hi there"


# ── Anthropic provider: extended thinking ──────────────────────────────
# Settings > "Extended thinking" (ai.main.get_extended_thinking_enabled)
# - see anthropic_provider.py's module docstring for the feature itself.

def test_anthropic_thinking_off_by_default_uses_plain_max_tokens():
    """Without enable_thinking, the `thinking` param is never sent and
    max_tokens stays the original constant - existing installs that
    never touch the new setting see byte-identical requests to before."""
    from ai.providers.anthropic_provider import MAX_TOKENS

    client = MagicMock()
    text_block = MagicMock(type="text", text="hi")
    client.messages.create.return_value = MagicMock(stop_reason="end_turn", content=[text_block])

    session = AnthropicChatSession(client, "claude-sonnet-5", [], "system")
    session.send_message([TextPart("hello")])

    _, kwargs = client.messages.create.call_args
    assert "thinking" not in kwargs
    assert kwargs["max_tokens"] == MAX_TOKENS


def test_anthropic_thinking_on_sends_thinking_param_and_bigger_max_tokens():
    from ai.providers.anthropic_provider import MAX_TOKENS, THINKING_BUDGET_TOKENS

    client = MagicMock()
    text_block = MagicMock(type="text", text="hi")
    client.messages.create.return_value = MagicMock(stop_reason="end_turn", content=[text_block])

    session = AnthropicChatSession(client, "claude-sonnet-5", [], "system", enable_thinking=True)
    session.send_message([TextPart("hello")])

    _, kwargs = client.messages.create.call_args
    assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS}
    assert kwargs["max_tokens"] == MAX_TOKENS + THINKING_BUDGET_TOKENS


def test_anthropic_send_message_captures_thinking_text():
    thinking_block = MagicMock(type="thinking", thinking="step one. step two.")
    text_block = MagicMock(type="text", text="answer")
    resp = MagicMock(stop_reason="end_turn", content=[thinking_block, text_block])
    client = MagicMock()
    client.messages.create.side_effect = [resp]

    session = AnthropicChatSession(client, "claude-sonnet-5", [], "system", enable_thinking=True)
    result = session.send_message([TextPart("go")])

    assert result.thinking == "step one. step two."
    assert result.text == "answer"


def test_anthropic_redacted_thinking_blocks_contribute_no_text():
    """redacted_thinking blocks are encrypted (Anthropic-side safety
    flagged) - there's no plaintext in them, so they must be skipped
    rather than crash on a missing `.thinking` attribute."""
    redacted = MagicMock(type="redacted_thinking")
    del redacted.thinking  # MagicMock would otherwise auto-vivify a truthy attribute
    text_block = MagicMock(type="text", text="answer")
    resp = MagicMock(stop_reason="end_turn", content=[redacted, text_block])
    client = MagicMock()
    client.messages.create.side_effect = [resp]

    session = AnthropicChatSession(client, "claude-sonnet-5", [], "system", enable_thinking=True)
    result = session.send_message([TextPart("go")])

    assert result.thinking == ""
    assert result.text == "answer"


def test_anthropic_stream_routes_thinking_deltas_separately_from_text():
    thinking_block = MagicMock(type="thinking", thinking="Let me think...")
    text_block = MagicMock(type="text", text="42")
    final = MagicMock(stop_reason="end_turn", content=[thinking_block, text_block])
    client = MagicMock()
    client.messages.stream.return_value = _anthropic_stream_cm(
        [_thinking_delta_event("Let me "), _thinking_delta_event("think..."), _text_delta_event("42")],
        final,
    )

    session = AnthropicChatSession(client, "claude-sonnet-5", [], "system", enable_thinking=True)
    received_text = []
    received_thinking = []
    result = session.send_message_stream(
        [TextPart("what is 6*7?")], received_text.append, received_thinking.append
    )

    assert "".join(received_thinking) == "Let me think..."
    assert "".join(received_text) == "42"
    assert result.text == "42"
    assert result.thinking == "Let me think..."


def test_anthropic_stream_thinking_deltas_ignored_when_no_callback_given():
    """on_thinking_delta is optional (default None) - a caller that
    doesn't pass one (every provider except Anthropic's own call sites
    that care) must not crash when a thinking_delta event arrives."""
    thinking_block = MagicMock(type="thinking", thinking="reasoning")
    text_block = MagicMock(type="text", text="answer")
    final = MagicMock(stop_reason="end_turn", content=[thinking_block, text_block])
    client = MagicMock()
    client.messages.stream.return_value = _anthropic_stream_cm(
        [_thinking_delta_event("reasoning"), _text_delta_event("answer")], final
    )

    session = AnthropicChatSession(client, "claude-sonnet-5", [], "system", enable_thinking=True)
    received = []
    result = session.send_message_stream([TextPart("go")], received.append)

    assert "".join(received) == "answer"
    assert result.text == "answer"


# ── Gemini provider: REAL streaming (send_message_stream) ─────────────
# Gemini's automatic-function-calling SDK runs the whole tool loop
# internally - chat.send_message_stream() here is faked as a plain
# iterable of chunks with a `.text` delta each, same as
# google-genai's own contract, since there's no manual loop in this
# module to exercise (see gemini_provider.py's module docstring).

def test_gemini_stream_forwards_each_chunks_text_and_returns_the_joined_result():
    from ai.providers.gemini_provider import GeminiChatSession

    def make_text_chunk(text):
        chunk = MagicMock()
        chunk.text = text
        return chunk

    raw_chat = MagicMock()
    raw_chat.send_message_stream.return_value = [
        make_text_chunk("Hello "),
        make_text_chunk("from Gemini!"),
    ]
    session = GeminiChatSession(MagicMock(), "models/gemini-3-flash-preview", [], "system", raw_chat=raw_chat)

    received = []
    result = session.send_message_stream([TextPart("hi")], received.append)

    assert result.text == "Hello from Gemini!"
    assert "".join(received) == "Hello from Gemini!"


# ── Gemini provider: extended thinking (thought summaries) ────────────
# Settings > "Extended thinking" (ai.main.get_extended_thinking_enabled)
# - see gemini_provider.py's module docstring: this toggles
# ThinkingConfig(include_thoughts=True), which makes `part.thought is
# True` parts show up alongside normal answer parts in the very same
# response/chunk.

def _gemini_part(text, thought=False):
    p = MagicMock()
    p.text = text
    p.thought = thought
    return p


def _gemini_candidate_chunk(parts):
    content = MagicMock()
    content.parts = parts
    candidate = MagicMock()
    candidate.content = content
    chunk = MagicMock()
    chunk.candidates = [candidate]
    return chunk


def test_gemini_thinking_off_by_default_builds_chat_with_no_thinking_config():
    from ai.providers.gemini_provider import GeminiChatSession

    client = MagicMock()
    GeminiChatSession(client, "models/gemini-3-flash-preview", [], "system")
    _, kwargs = client.chats.create.call_args
    assert "thinking_config" not in kwargs["config"]


def test_gemini_thinking_on_builds_chat_with_dynamic_thinking_config():
    from ai.providers.gemini_provider import GeminiChatSession

    client = MagicMock()
    GeminiChatSession(client, "models/gemini-3-flash-preview", [], "system", enable_thinking=True)
    _, kwargs = client.chats.create.call_args
    assert kwargs["config"]["thinking_config"] == {"thinking_budget": -1, "include_thoughts": True}


def test_gemini_send_message_captures_thought_text_when_thinking_on():
    from ai.providers.gemini_provider import GeminiChatSession

    response = MagicMock()
    response.text = "The answer is 42."
    candidate = MagicMock()
    content = MagicMock()
    content.parts = [_gemini_part("thinking it through...", thought=True), _gemini_part("The answer is 42.")]
    candidate.content = content
    response.candidates = [candidate]
    raw_chat = MagicMock()
    raw_chat.send_message.return_value = response

    session = GeminiChatSession(
        MagicMock(), "models/gemini-3-flash-preview", [], "system", raw_chat=raw_chat, enable_thinking=True
    )
    result = session.send_message([TextPart("go")])

    assert result.thinking == "thinking it through..."
    assert result.text == "The answer is 42."


def test_gemini_stream_routes_thought_parts_separately_from_answer_parts():
    from ai.providers.gemini_provider import GeminiChatSession

    raw_chat = MagicMock()
    raw_chat.send_message_stream.return_value = [
        _gemini_candidate_chunk([_gemini_part("Let me reason...", thought=True)]),
        _gemini_candidate_chunk([_gemini_part("more reasoning", thought=True)]),
        _gemini_candidate_chunk([_gemini_part("The answer is 42.")]),
    ]
    session = GeminiChatSession(
        MagicMock(), "models/gemini-3-flash-preview", [], "system", raw_chat=raw_chat, enable_thinking=True
    )

    received_text, received_thinking = [], []
    result = session.send_message_stream(
        [TextPart("what is 6*7?")], received_text.append, received_thinking.append
    )

    assert "".join(received_thinking) == "Let me reason...more reasoning"
    assert "".join(received_text) == "The answer is 42."
    assert result.thinking == "Let me reason...more reasoning"
    assert result.text == "The answer is 42."


def test_gemini_stream_thinking_off_ignores_candidates_and_uses_plain_chunk_text():
    """With thinking off, streaming must take the exact pre-existing
    code path (plain `chunk.text`) - never touch `.candidates` at all,
    since a chunk without that attribute pre-configured (the shape
    every pre-thinking-feature test/caller already uses) must keep
    working unchanged."""
    from ai.providers.gemini_provider import GeminiChatSession

    def make_text_chunk(text):
        chunk = MagicMock()
        chunk.text = text
        return chunk

    raw_chat = MagicMock()
    raw_chat.send_message_stream.return_value = [make_text_chunk("hi "), make_text_chunk("there")]
    session = GeminiChatSession(MagicMock(), "models/gemini-3-flash-preview", [], "system", raw_chat=raw_chat)

    received = []
    result = session.send_message_stream([TextPart("hi")], received.append)
    assert result.text == "hi there"
    assert "".join(received) == "hi there"


# ── MAX_TOOL_ITERATIONS safety cap ────────────────────────────────────

def test_openai_stops_after_max_tool_iterations_instead_of_looping_forever():
    client = MagicMock()
    call = _openai_function_call_item("call_1", "_ok_tool", '{"x": "a"}')
    # Every response asks for another tool call - never a final answer.
    client.responses.create.return_value = _openai_response([call])

    session = OpenAIChatSession(client, "gpt-5.4", [_ok_tool], "system")
    result = session.send_message([TextPart("go")])
    assert "Stopped after a long chain of tool calls" in result.text


# ── OpenAI provider: REAL streaming (send_message_stream) ─────────────
# The Responses API streams a flat sequence of typed EVENTS (not
# per-message deltas) - a plain text chunk is its own
# "response.output_text.delta" event, and (when extended thinking is
# on) a reasoning summary chunk is its own reasoning-summary delta
# event (see openai_provider.py's _REASONING_SUMMARY_DELTA_EVENT_TYPES
# for why two names are accepted). Unlike function-call ARGUMENTS,
# which Chat Completions streamed split across many chunks,
# function_call items in the Responses API arrive complete once
# stream.get_final_response() is called - this module never has to
# reassemble one field by field the way it used to.

def _openai_event(event_type, delta=None):
    evt = MagicMock(type=event_type)
    if delta is not None:
        evt.delta = delta
    return evt


def _openai_stream_cm(events, final_response):
    """Fakes `with client.responses.stream(...) as stream: for event in
    stream: ...` plus stream.get_final_response() (the fully-parsed
    Response, same shape client.responses.create() already returns)."""
    stream_obj = MagicMock()
    stream_obj.__iter__ = MagicMock(return_value=iter(events))
    stream_obj.get_final_response.return_value = final_response
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=stream_obj)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def test_openai_stream_runs_tool_loop_and_streams_only_final_leg_text():
    call = _openai_function_call_item("call_1", "_ok_tool", '{"x": "hello"}')
    leg1_final = _openai_response([call])
    leg2_final = _openai_response([_openai_message_item("Done: ok:hello")], output_text="Done: ok:hello")

    client = MagicMock()
    client.responses.stream.side_effect = [
        _openai_stream_cm([], leg1_final),  # leg 1: tool call, no visible text
        _openai_stream_cm(
            [
                _openai_event("response.output_text.delta", delta="Done: "),
                _openai_event("response.output_text.delta", delta="ok:hello"),
            ],
            leg2_final,
        ),
    ]

    session = OpenAIChatSession(client, "gpt-5.4", [_ok_tool], "system")
    received = []
    result = session.send_message_stream([TextPart("go")], received.append)

    assert result.text == "Done: ok:hello"
    assert "".join(received) == "Done: ok:hello"
    assert result.tool_calls[0].name == "_ok_tool"
    assert result.tool_calls[0].result == "ok:hello"


def test_openai_stream_falls_back_like_send_message_when_no_tools_are_called():
    final = _openai_response([_openai_message_item("Hi there!")], output_text="Hi there!")
    client = MagicMock()
    client.responses.stream.return_value = _openai_stream_cm(
        [
            _openai_event("response.output_text.delta", delta="Hi "),
            _openai_event("response.output_text.delta", delta="there!"),
        ],
        final,
    )
    session = OpenAIChatSession(client, "gpt-5.4", [], "system")
    received = []
    result = session.send_message_stream([TextPart("hello")], received.append)
    assert result.text == "Hi there!"
    assert "".join(received) == "Hi there!"


def test_openai_stream_routes_reasoning_summary_deltas_separately_from_text():
    reasoning_item = _openai_reasoning_item("Let me think...")
    final = _openai_response([reasoning_item, _openai_message_item("42")], output_text="42")
    client = MagicMock()
    client.responses.stream.return_value = _openai_stream_cm(
        [
            _openai_event("response.reasoning_summary_text.delta", delta="Let me "),
            _openai_event("response.reasoning_summary_text.delta", delta="think..."),
            _openai_event("response.output_text.delta", delta="42"),
        ],
        final,
    )

    session = OpenAIChatSession(client, "gpt-5.4", [], "system", enable_thinking=True)
    received_text = []
    received_thinking = []
    result = session.send_message_stream(
        [TextPart("what is 6*7?")], received_text.append, received_thinking.append
    )

    assert "".join(received_thinking) == "Let me think..."
    assert "".join(received_text) == "42"
    assert result.thinking == "Let me think..."
    assert result.text == "42"


def test_openai_stream_reasoning_deltas_ignored_when_thinking_off():
    """Same reasoning-summary delta events arriving on a session that
    never turned thinking on (enable_thinking defaults False) must be
    silently ignored, not routed to on_thinking_delta."""
    final = _openai_response([_openai_message_item("answer")], output_text="answer")
    client = MagicMock()
    client.responses.stream.return_value = _openai_stream_cm(
        [
            _openai_event("response.reasoning_summary_text.delta", delta="should not appear"),
            _openai_event("response.output_text.delta", delta="answer"),
        ],
        final,
    )
    session = OpenAIChatSession(client, "gpt-5.4", [], "system")
    received_text = []
    received_thinking = []
    result = session.send_message_stream([TextPart("go")], received_text.append, received_thinking.append)
    assert received_thinking == []
    assert "".join(received_text) == "answer"
    assert result.text == "answer"


# ── ai/main.py integration: the active-provider plumbing ─────────────
# These exercise ai.main.ai()/_ensure_initialized()/set_active_provider_and_model
# against a MOCKED ai.providers.create_chat_session - no real provider
# client, no network - verifying the wiring itself: that ai() actually
# calls through the factory with the active provider/model, that a
# mid-chat switch calls seed_history() (not send_message(), which would
# fire a real, billed call per past turn - see
# ai.providers.switch_provider's own docstring for why that would be a
# bug), and that .env writes from these tests don't leak into the repo.

@pytest.fixture(autouse=True)
def _isolate_env_file(tmp_path, monkeypatch):
    """Every one of these tests writes to ai.main.ENV_PATH via
    _write_env_value() (set_active_provider_and_model, save_api_key_for)
    - redirect that to a throwaway tmp_path file so tests never touch
    this repo's real .env (see the session notes on why that matters:
    an earlier manual test run left a real-looking .env, with what
    could be real Auth0/API-key-shaped values, sitting in the repo
    before this fixture existed)."""
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "ENV_PATH", str(tmp_path / "test.env"))
    yield


@pytest.fixture
def _reset_ai_main_state():
    import ai.main as ai_main
    ai_main._initialized = False
    ai_main.chat = None
    ai_main._gemini_client = None
    yield
    ai_main._initialized = False
    ai_main.chat = None
    ai_main._gemini_client = None


def test_ai_uses_the_active_provider_and_converts_input_to_a_text_part(_reset_ai_main_state):
    import ai.main as ai_main
    from ai.providers.base import TextPart

    fake_response = MagicMock(text="Hello from the mocked model!")
    fake_session = MagicMock()
    fake_session.send_message.return_value = fake_response

    with patch("ai.providers.create_chat_session", return_value=fake_session) as mock_create, \
         patch("ai.model_client.make_client", return_value=MagicMock()):
        result = ai_main.ai(input_text="hi there")

    assert result == "Hello from the mocked model!"
    assert mock_create.call_args[0][0] == "gemini"  # default active provider
    sent_parts = fake_session.send_message.call_args[0][0]
    assert isinstance(sent_parts[0], TextPart)
    assert sent_parts[0].text == "hi there"


def test_switching_provider_mid_chat_seeds_history_instead_of_replaying_it(_reset_ai_main_state):
    import ai.main as ai_main

    fake_gemini_session = MagicMock()
    fake_gemini_session.send_message.return_value = MagicMock(text="Gemini says hi")
    fake_gemini_session.history_as_text_turns.return_value = [("user", "hi there"), ("assistant", "Gemini says hi")]
    fake_claude_session = MagicMock()

    def fake_create(provider, model=None, tool_functions=None, system_instruction="", api_key_override=None):
        return fake_gemini_session if provider == "gemini" else fake_claude_session

    with patch("ai.providers.create_chat_session", side_effect=fake_create), \
         patch("ai.model_client.make_client", return_value=MagicMock()):
        ai_main.ai(input_text="hi there")
        assert ai_main.chat is fake_gemini_session

        ai_main.set_active_provider_and_model("anthropic", "claude-sonnet-5")

    assert ai_main.get_active_provider() == "anthropic"
    assert ai_main.get_active_model() == "claude-sonnet-5"
    assert ai_main.chat is fake_claude_session
    # The critical assertion: history moves via seed_history (no API
    # call), never via repeated send_message calls (which would each be
    # a real, billed request - see switch_provider()'s own docstring).
    fake_claude_session.seed_history.assert_called_once_with(
        [("user", "hi there"), ("assistant", "Gemini says hi")]
    )
    fake_claude_session.send_message.assert_not_called()


def test_ai_returns_a_friendly_message_when_the_active_provider_has_no_chat_session(_reset_ai_main_state):
    import ai.main as ai_main
    import ai.providers as providers

    with patch("ai.providers.create_chat_session", side_effect=providers.ProviderClientError("No OpenAI API key is configured.")), \
         patch("ai.model_client.make_client", return_value=MagicMock()):
        ai_main.set_active_provider_and_model("openai", "gpt-5.4")
        result = ai_main.ai(input_text="hi there")

    assert "openai" in result.lower() or "No OpenAI API key" in result


# ── ai/main.py integration: ai_stream() ───────────────────────────────
# Same wiring as ai() above, but through the streaming entry point
# verifies it calls send_message_stream (not send_message), forwards
# provider deltas to the caller's on_delta as they arrive, and still
# returns the same final text ai() would.

def test_ai_stream_calls_send_message_stream_and_forwards_deltas(_reset_ai_main_state):
    import ai.main as ai_main
    from ai.providers.base import ChatResponse

    def fake_send_message_stream(parts, on_delta):
        on_delta("Hello ")
        on_delta("from the mocked model!")
        return ChatResponse(text="Hello from the mocked model!")

    fake_session = MagicMock()
    fake_session.send_message_stream.side_effect = fake_send_message_stream

    received = []
    with patch("ai.providers.create_chat_session", return_value=fake_session), \
         patch("ai.model_client.make_client", return_value=MagicMock()):
        result = ai_main.ai_stream(input_text="hi there", on_delta=received.append)

    assert result == "Hello from the mocked model!"
    assert "".join(received) == "Hello from the mocked model!"
    fake_session.send_message.assert_not_called()  # streaming path only, never the non-streaming one


def test_ai_stream_never_forwards_a_raw_control_tag_to_on_delta(_reset_ai_main_state):
    """
    Integration-level version of test_ai_main.py's _StreamMarkerFilter
    unit tests: feeds ai_stream() a reply whose autonomous-suggest tag
    is split across several send_message_stream deltas, and checks the
    tag never reaches on_delta even though it DOES end up correctly
    parsed out for pop_pending_autonomous_goal() afterward.
    """
    import ai.main as ai_main
    from ai.providers.base import ChatResponse

    def fake_send_message_stream(parts, on_delta):
        for piece in ["Sure, ", "opening Chrome now.", "\n\n[[AUTONOMOUS", "_SUGGEST: open chrome]]"]:
            on_delta(piece)
        return ChatResponse(text="Sure, opening Chrome now.\n\n[[AUTONOMOUS_SUGGEST: open chrome]]")

    fake_session = MagicMock()
    fake_session.send_message_stream.side_effect = fake_send_message_stream

    received = []
    with patch("ai.providers.create_chat_session", return_value=fake_session), \
         patch("ai.model_client.make_client", return_value=MagicMock()):
        result = ai_main.ai_stream(input_text="open chrome", on_delta=received.append)

    assert "AUTONOMOUS_SUGGEST" not in "".join(received)
    assert result == "Sure, opening Chrome now."
    assert ai_main.pop_pending_autonomous_goal() == "open chrome"


def test_ai_stream_attaches_queued_chat_images_as_image_parts(_reset_ai_main_state):
    import ai.main as ai_main
    from ai.providers.base import ChatResponse, ImagePart

    fake_session = MagicMock()

    def fake_send_message_stream(parts, on_delta):
        on_delta("I see it.")
        return ChatResponse(text="I see it.")
    fake_session.send_message_stream.side_effect = fake_send_message_stream

    fake_image = object()  # stand-in for a PIL Image - never actually rendered/opened in this test
    ai_main.queue_chat_images([fake_image])

    with patch("ai.providers.create_chat_session", return_value=fake_session), \
         patch("ai.model_client.make_client", return_value=MagicMock()):
        ai_main.ai_stream(input_text="what's in this?", on_delta=lambda _: None)

    sent_parts = fake_session.send_message_stream.call_args[0][0]
    image_parts = [p for p in sent_parts if isinstance(p, ImagePart)]
    assert len(image_parts) == 1
    assert image_parts[0].image is fake_image
    # Queue is consumed, not left to leak into the NEXT unrelated message.
    assert ai_main._pop_pending_chat_images() == []


# ── Local provider: tool loop (Chat Completions shape) ─────────────────
# client.chat.completions.create(...) returns a response whose
# `.choices[0].message` carries a `.tool_calls` list hanging off ONE
# assistant message - the classic Chat Completions shape every local
# runtime (Ollama, LM Studio, llama.cpp's server, vLLM) mirrors, unlike
# the Responses API's flat typed-item list used above for OpenAI.

def _local_message(content=None, tool_calls=None):
    return MagicMock(content=content, tool_calls=tool_calls)


def _local_tool_call(call_id, name, arguments):
    call = MagicMock(id=call_id)
    call.function = MagicMock(arguments=arguments)
    call.function.name = name  # set separately - `name` is a reserved MagicMock kwarg
    return call


def _local_response(message):
    return MagicMock(choices=[MagicMock(message=message)])


def test_local_single_tool_call_then_final_answer():
    client = MagicMock()
    call = _local_tool_call("call_1", "_ok_tool", '{"x": "hello"}')
    resp1 = _local_response(_local_message(tool_calls=[call]))
    resp2 = _local_response(_local_message(content="Done: ok:hello"))
    client.chat.completions.create.side_effect = [resp1, resp2]

    session = LocalChatSession(client, "llama3.2", [_ok_tool], "system")
    result = session.send_message([TextPart("go")])

    assert result.text == "Done: ok:hello"
    assert result.tool_calls[0].result == "ok:hello"
    # The tool's result went back as a {"role": "tool", ...} message
    # keyed by tool_call_id - the Chat Completions shape, not the
    # Responses API's function_call_output item.
    _, kwargs = client.chat.completions.create.call_args
    tool_messages = [m for m in kwargs["messages"] if m.get("role") == "tool"]
    assert tool_messages == [{"role": "tool", "tool_call_id": "call_1", "content": "ok:hello"}]


def test_local_failing_tool_does_not_crash_the_loop():
    client = MagicMock()
    call = _local_tool_call("call_1", "_failing_tool", '{"x": "hello"}')
    resp1 = _local_response(_local_message(tool_calls=[call]))
    resp2 = _local_response(_local_message(content="I tried and it failed."))
    client.chat.completions.create.side_effect = [resp1, resp2]

    session = LocalChatSession(client, "llama3.2", [_failing_tool], "system")
    result = session.send_message([TextPart("go")])

    assert "Error: tool '_failing_tool' failed: boom" in result.tool_calls[0].result
    assert result.text == "I tried and it failed."


def test_local_malformed_tool_arguments_handled_gracefully():
    client = MagicMock()
    call = _local_tool_call("call_1", "_ok_tool", "{not valid json")
    resp1 = _local_response(_local_message(tool_calls=[call]))
    resp2 = _local_response(_local_message(content="ok"))
    client.chat.completions.create.side_effect = [resp1, resp2]

    session = LocalChatSession(client, "llama3.2", [_ok_tool], "system")
    result = session.send_message([TextPart("go")])
    assert "couldn't parse arguments" in result.tool_calls[0].result


def test_local_history_round_trips_through_seed_history():
    client = MagicMock()
    client.chat.completions.create.return_value = _local_response(_local_message(content="hi there"))

    session = LocalChatSession(client, "llama3.2", [], "system")
    session.send_message([TextPart("hello")])
    turns = session.history_as_text_turns()
    assert turns == [("user", "hello"), ("assistant", "hi there")]

    session2 = LocalChatSession(client, "llama3.2", [], "system")
    session2.seed_history(turns)
    assert session2._messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_local_system_instruction_sent_as_a_system_message_not_replayed_in_history():
    """Mirrors openai_provider.py/anthropic_provider.py keeping their
    own system-prompt param out of the replayed conversation - the
    system message here is rebuilt fresh from self._instructions on
    every request rather than living in self._messages."""
    client = MagicMock()
    client.chat.completions.create.return_value = _local_response(_local_message(content="hi"))

    session = LocalChatSession(client, "llama3.2", [], "You are a pirate.")
    session.send_message([TextPart("hello")])

    _, kwargs = client.chat.completions.create.call_args
    assert kwargs["messages"][0] == {"role": "system", "content": "You are a pirate."}
    assert all(m.get("role") != "system" for m in session._messages)


def test_local_send_message_stream_accumulates_tool_call_deltas_across_chunks():
    """A local server's streamed tool-call arguments arrive fragmented
    across many chunks, keyed by `index` - must be fully assembled
    before the tool is ever actually called."""
    client = MagicMock()

    def _chunk(content=None, tool_call_deltas=None):
        delta = MagicMock(content=content, tool_calls=tool_call_deltas)
        return MagicMock(choices=[MagicMock(delta=delta)])

    def _tc_delta(index, call_id=None, name=None, arguments=None):
        tc = MagicMock(index=index, id=call_id)
        tc.function = MagicMock(arguments=arguments)
        tc.function.name = name
        return tc

    first_stream = [
        _chunk(tool_call_deltas=[_tc_delta(0, call_id="call_1", name="_ok_tool", arguments="")]),
        _chunk(tool_call_deltas=[_tc_delta(0, arguments='{"x": ')]),
        _chunk(tool_call_deltas=[_tc_delta(0, arguments='"hello"}')]),
    ]
    second_stream = [_chunk(content="Done: "), _chunk(content="ok:hello")]
    client.chat.completions.create.side_effect = [iter(first_stream), iter(second_stream)]

    session = LocalChatSession(client, "llama3.2", [_ok_tool], "system")
    deltas = []
    result = session.send_message_stream([TextPart("go")], on_delta=deltas.append)

    assert deltas == ["Done: ", "ok:hello"]
    assert result.text == "Done: ok:hello"
    assert result.tool_calls[0].name == "_ok_tool"
    assert result.tool_calls[0].result == "ok:hello"


def test_local_provider_never_produces_thinking_text():
    """Chat Completions has no reasoning-summary concept at all (see
    local_provider.py's module docstring) - thinking must always come
    back empty, regardless of Settings > "Extended thinking"."""
    client = MagicMock()
    client.chat.completions.create.return_value = _local_response(_local_message(content="42"))
    session = LocalChatSession(client, "llama3.2", [], "system")
    result = session.send_message([TextPart("what is 6*7?")])
    assert result.thinking == ""


# ── ai/providers/__init__.py: local-provider wiring ────────────────────

def test_make_local_client_uses_configured_base_url():
    """A local client points at whatever endpoint the user configured."""
    import ai.providers as providers
    import ai.main as ai_main

    with patch.object(ai_main, "get_local_base_url", return_value="http://localhost:11434/v1"), \
         patch.object(ai_main, "get_api_key_for", return_value=""):
        client = providers._make_local_client()

    assert str(client.base_url).rstrip("/") == "http://localhost:11434/v1"


def test_make_local_client_requires_a_base_url():
    import ai.providers as providers
    import ai.main as ai_main

    with patch.object(ai_main, "get_local_base_url", return_value=""):
        with pytest.raises(providers.ProviderClientError, match="URL"):
            providers._make_local_client()


def test_create_chat_session_local_substitutes_sentinel_model_for_configured_name():
    """base.MODEL_CHOICES' "local" entry is the "__local__" sentinel
    (see that module's own comment) - create_chat_session() must swap
    it for the real configured local model name before ever building
    the session, never send the sentinel itself to a real server."""
    import ai.providers as providers
    import ai.main as ai_main

    with patch.object(providers, "_make_local_client", return_value=MagicMock()) as mock_make_client, \
         patch.object(ai_main, "get_local_model_name", return_value="qwen2.5-coder:7b"), \
         patch("ai.providers.local_provider.create_chat_session") as mock_create:
        providers.create_chat_session("local", "__local__", [], "system")

    mock_create.assert_called_once()
    args, _ = mock_create.call_args
    assert args[1] == "qwen2.5-coder:7b"
    mock_make_client.assert_called_once()


def test_create_chat_session_local_requires_a_configured_model_name():
    import ai.providers as providers
    import ai.main as ai_main

    with patch.object(providers, "_make_local_client", return_value=MagicMock()), \
         patch.object(ai_main, "get_local_model_name", return_value=""):
        with pytest.raises(providers.ProviderClientError, match="model"):
            providers.create_chat_session("local", "__local__", [], "system")


# ── No subscripted generics in tool signatures ────────────────────────
# Regression guard for the bug that made tool_generate_file fail on
# EVERY call, for every format, with "Subscripted generics cannot be
# used with class and instance checks" - see the long comment under
# ai/actions.py's tool_generate_file. Gemini's automatic function
# calling validates the model's returned arguments against each
# parameter's annotation with isinstance(), and Python raises that
# TypeError for isinstance(value, Literal[...]) / list[str] / etc.
# The annotation never reaches the function body, so no amount of
# input-side defensiveness inside the tool could have caught it.

def test_explicit_enum_params_reach_the_schema_without_a_literal_annotation():
    def _tool(fmt: str, other: str) -> str:
        """Does a thing.
        fmt: which kind.
        """
        return ""
    _tool.__enum_params__ = {"fmt": ["txt", "pdf"]}

    schema = build_tool_schema(_tool)
    by_name = {p.name: p for p in schema.params}
    assert by_name["fmt"].enum == ["txt", "pdf"]
    assert by_name["fmt"].type == "string"          # plain str annotation, no Literal
    assert by_name["other"].enum is None            # untouched
    # And it survives the conversion into each provider's own shape.
    assert to_openai_tool(schema)["function"]["parameters"]["properties"]["fmt"]["enum"] == ["txt", "pdf"]
    assert to_anthropic_tool(schema)["input_schema"]["properties"]["fmt"]["enum"] == ["txt", "pdf"]


def test_isinstance_against_a_subscripted_generic_is_what_broke_tool_calls():
    """Pins the actual failure mode, so the reason the rule exists
    stays legible: this is the exact exception google-genai's argument
    validation raised on every tool_generate_file call."""
    import typing
    with pytest.raises(TypeError, match="Subscripted generics cannot be used"):
        isinstance("txt", typing.Literal["txt", "md"])
