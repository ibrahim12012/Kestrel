# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/providers/anthropic_provider.py
=====================================
Same reasoning as openai_provider.py's module docstring - Anthropic's
Messages API also has no automatic function calling, so this runs the
same kind of manual tool loop, just in Anthropic's own request/response
shape (system prompt is a separate top-level param, not a message;
tool results go back as "tool_result" content blocks inside a user
message, not their own "tool" role; a turn is finished when
stop_reason != "tool_use" rather than "no tool_calls on the message").

Extended thinking (Settings > "Extended thinking" - see
ai.main.get_extended_thinking_enabled): when on, every request in this
session's tool loop asks Claude for a `thinking` block per Anthropic's
Messages API (thinking={"type": "enabled", "budget_tokens": ...}),
same feature/toggle claude.ai itself exposes. The thinking text is
never treated as the final answer - ChatResponse.text stays exactly
the visible reply - it's surfaced separately (ChatResponse.thinking,
and live via on_thinking_delta while streaming) purely so
app/chat_widget.py can render it the way claude.ai does: a collapsible
"Thought for Ns" section shown above the reply, not mixed into it.
Assistant turns are re-appended to self._messages with their raw
content blocks (thinking included) exactly as Anthropic returned them -
required so a later tool-result turn in the same loop still has the
matching thinking block in front of it, which the API expects.
"""

from __future__ import annotations

import base64

from ai.providers.base import ChatResponse, ChatSession, ImagePart, TextPart, ToolCallRecord
from ai.providers.schema import build_tool_schema, to_anthropic_tool

MAX_TOOL_ITERATIONS = 25
# Anthropic's Messages API requires an explicit max_tokens (no provider
# default) - 4096 is comfortably above anything this app's tool
# responses or chat replies need, without being so large a runaway
# response burns an outsized amount of the plan's usage budget.
MAX_TOKENS = 4096

# How much of max_tokens extended thinking is allowed to spend on its
# own reasoning before it has to move on to the visible answer. Well
# under Claude's minimum practical ceiling, generous enough for
# genuine multi-step reasoning; added ON TOP of MAX_TOKENS below (the
# API requires max_tokens > budget_tokens) rather than carved out of
# it, so turning thinking on never shrinks the room left for the reply
# itself.
THINKING_BUDGET_TOKENS = 8000


def _extract_thinking_text(content_blocks) -> str:
    """Joins every `thinking`-type block's text out of a Messages API
    response's content list - redacted_thinking blocks (encrypted,
    Anthropic-side safety flagged) are skipped since there's no
    plaintext in them to show."""
    return "".join(
        getattr(b, "thinking", None) or ""
        for b in content_blocks
        if getattr(b, "type", None) == "thinking"
    )


class AnthropicChatSession(ChatSession):
    def __init__(self, client, model: str, tool_functions: list, system_instruction: str, enable_thinking: bool = False):
        self._client = client  # an anthropic.Anthropic instance
        self._model = model
        self._system = system_instruction
        self._tool_functions = {f.__name__: f for f in tool_functions}
        self._tool_defs = [to_anthropic_tool(build_tool_schema(f)) for f in tool_functions]
        self._messages: list = []
        self._enable_thinking = enable_thinking

    def _request_kwargs(self) -> dict:
        """Extra kwargs merged into every messages.create()/stream()
        call in this session - just the `thinking` param and its
        necessarily-larger max_tokens when extended thinking is on,
        kept in one place since send_message() and
        send_message_stream() both need it identically."""
        if not self._enable_thinking:
            return {"max_tokens": MAX_TOKENS}
        return {
            "max_tokens": MAX_TOKENS + THINKING_BUDGET_TOKENS,
            "thinking": {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS},
        }

    def _parts_to_content(self, parts: list):
        content = []
        for part in parts:
            if isinstance(part, TextPart):
                content.append({"type": "text", "text": part.text})
            elif isinstance(part, ImagePart):
                b64 = base64.b64encode(part.to_png_bytes()).decode("ascii")
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64},
                })
            else:
                raise TypeError(
                    "AnthropicChatSession received an unsupported content part "
                    f"({type(part).__name__}) - voice input isn't wired through "
                    "the OpenAI/Anthropic path yet, only Gemini's."
                )
        return content

    def _execute_tool_call(self, name: str, args: dict) -> str:
        func = self._tool_functions.get(name)
        if func is None:
            return f"Error: no such tool '{name}'."
        try:
            result = func(**args)
        except Exception as e:
            # Same "tell the model it failed, let it try something else"
            # shape Gemini's automatic function calling already gives
            # see openai_provider.py's module docstring for why this
            # matters for ai/actions.py's emergency_stop flag specifically.
            return f"Error: tool '{name}' failed: {e}"
        return "" if result is None else str(result)

    def seed_history(self, turns: "list[tuple[str, str]]") -> None:
        for role, text in turns:
            self._messages.append({"role": role, "content": [{"type": "text", "text": text}]})

    def send_message(self, parts: list) -> ChatResponse:
        self._messages.append({"role": "user", "content": self._parts_to_content(parts)})

        tool_call_records: list[ToolCallRecord] = []
        thinking_chunks: list[str] = []
        for _ in range(MAX_TOOL_ITERATIONS):
            response = self._client.messages.create(
                model=self._model,
                system=self._system,
                messages=self._messages,
                tools=self._tool_defs or [],
                **self._request_kwargs(),
            )
            self._messages.append({"role": "assistant", "content": response.content})
            thinking_chunks.append(_extract_thinking_text(response.content))

            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
                return ChatResponse(text=text, tool_calls=tool_call_records, thinking="".join(thinking_chunks))

            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                result_text = self._execute_tool_call(block.name, block.input or {})
                tool_call_records.append(ToolCallRecord(name=block.name, args=block.input or {}, result=result_text))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })
            self._messages.append({"role": "user", "content": tool_results})

        return ChatResponse(
            text="(Stopped after a long chain of tool calls without a final answer - try rephrasing.)",
            tool_calls=tool_call_records,
            thinking="".join(thinking_chunks),
        )

    def send_message_stream(self, parts: list, on_delta, on_thinking_delta=None) -> ChatResponse:
        """
        Same manual tool loop as send_message(), but each leg's text
        streams to on_delta as Anthropic's API generates it - real
        network streaming via client.messages.stream(), not simulated.
        stream.get_final_message() hands back the exact same Message
        shape send_message() already works with (.content / .stop_reason,
        tool_use blocks fully parsed) per Anthropic's own streaming docs,
        so nothing below this line needs to know streaming even happened.

        When extended thinking is on, iterates the stream's raw events
        (rather than the text_stream convenience helper, which only
        surfaces text_delta) so `thinking_delta` events can be routed to
        on_thinking_delta as they arrive - live, and strictly before the
        text for that same leg, same ordering Claude.ai itself shows.
        """
        self._messages.append({"role": "user", "content": self._parts_to_content(parts)})

        tool_call_records: list[ToolCallRecord] = []
        thinking_chunks: list[str] = []
        for _ in range(MAX_TOOL_ITERATIONS):
            with self._client.messages.stream(
                model=self._model,
                system=self._system,
                messages=self._messages,
                tools=self._tool_defs or [],
                **self._request_kwargs(),
            ) as stream:
                for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    delta = event.delta
                    if delta.type == "text_delta":
                        on_delta(delta.text)
                    elif delta.type == "thinking_delta" and on_thinking_delta is not None:
                        on_thinking_delta(delta.thinking)
                response = stream.get_final_message()

            self._messages.append({"role": "assistant", "content": response.content})
            thinking_chunks.append(_extract_thinking_text(response.content))

            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
                return ChatResponse(text=text, tool_calls=tool_call_records, thinking="".join(thinking_chunks))

            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                result_text = self._execute_tool_call(block.name, block.input or {})
                tool_call_records.append(ToolCallRecord(name=block.name, args=block.input or {}, result=result_text))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })
            self._messages.append({"role": "user", "content": tool_results})

        return ChatResponse(
            text="(Stopped after a long chain of tool calls without a final answer - try rephrasing.)",
            tool_calls=tool_call_records,
            thinking="".join(thinking_chunks),
        )

    def history_as_text_turns(self) -> "list[tuple[str, str]]":
        from ai.providers.base import merge_consecutive_turns
        turns = []
        for m in self._messages:
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue
            content = m.get("content")
            texts = []
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block["text"])
                    elif getattr(block, "type", None) == "text":
                        texts.append(block.text)
            text = " ".join(texts).strip()
            if text:
                turns.append((role, text))
        return merge_consecutive_turns(turns)


def create_chat_session(client, model: str, tool_functions: list, system_instruction: str) -> AnthropicChatSession:
    # Read lazily (not passed in from ai/providers/__init__.py's
    # call site) for the same reason ai/model_client.py's make_client()
    # lazily imports ai.main: ai.main imports this package at module
    # load time, so importing it back at THIS module's own top level
    # would be a circular import. Settings > "Extended thinking" toggles
    # ai.main.get_extended_thinking_enabled() at runtime; reading it here
    # (once, per session build - i.e. on every model/provider switch)
    # means a toggle takes effect on the very next message without
    # needing to thread a new parameter through switch_provider() too.
    import ai.main as ai_main
    enable_thinking = ai_main.get_extended_thinking_enabled()
    return AnthropicChatSession(client, model, tool_functions, system_instruction, enable_thinking=enable_thinking)
