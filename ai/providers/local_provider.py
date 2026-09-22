# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/providers/local_provider.py
=================================
Talks to a model running on the user's own machine (or LAN) - Ollama,
LM Studio, llama.cpp's `server`, vLLM, text-generation-webui, etc. -
through whichever one of those an "OpenAI-compatible" `/v1` HTTP
endpoint (every one of the servers above ships one) so this app never
has to speak five different local-runtime protocols. Concretely: the
same `openai` package already required for openai_provider.py, just
pointed at a `base_url` on localhost/the LAN instead of api.openai.com,
via ai/providers/__init__.py's _make_local_client().

Deliberately built on the older Chat Completions API
(client.chat.completions.create), NOT the Responses API
openai_provider.py switched to for GPT-5 reasoning support - Chat
Completions is the format every local runtime above actually
implements; the newer Responses API is real OpenAI-only surface, and a
local server that claimed to support it would be the exception, not
the rule. That does mean no extended-reasoning summaries here (Settings
> "Extended thinking" simply has no effect when the active provider is
"local" - ChatResponse.thinking stays "" always, same as any other
provider/config that doesn't produce one; see base.py's ChatResponse
docstring), but tool calling, vision input, and real token-by-token
streaming are all still Chat Completions' own well-supported surface
and work the same way they do against OpenAI itself.

No API key is required by default - most local servers don't check for
one at all - but a value saved in Settings for the "Local" row (see
ai.main's generalized per-provider key storage) is still sent as a
Bearer token, for the LAN/reverse-proxy case where the user's own
server does enforce one.
"""

from __future__ import annotations

import json

from ai.providers.base import ChatResponse, ChatSession, ImagePart, TextPart, ToolCallRecord
from ai.providers.schema import build_tool_schema, to_openai_tool

MAX_TOOL_ITERATIONS = 25  # same runaway-loop guard as openai_provider.py / anthropic_provider.py


def _safe_json_loads(raw: str) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


class LocalChatSession(ChatSession):
    def __init__(self, client, model: str, tool_functions: list, system_instruction: str):
        self._client = client  # an openai.OpenAI instance, base_url pointed at the local server
        self._model = model
        self._instructions = system_instruction
        self._tool_functions = {f.__name__: f for f in tool_functions}
        self._tool_defs = [to_openai_tool(build_tool_schema(f)) for f in tool_functions]
        # Chat Completions history: plain {"role": ..., "content": ...}
        # messages - no separate system-message slot is seeded here;
        # the system message is built fresh from self._instructions on
        # every call instead (see _messages_for_request), matching how
        # openai_provider.py/anthropic_provider.py keep their own
        # instructions/system param out of the replayed history.
        self._messages: list = []

    def _parts_to_content(self, parts: list):
        """Chat Completions content: a string is fine for text-only
        turns, but a list of typed parts is what's needed the moment an
        image is involved - the same `image_url` shape OpenAI's own
        vision models use, which Ollama, LM Studio, and llama.cpp's
        server all also accept for a vision-capable local model."""
        if all(isinstance(p, TextPart) for p in parts):
            return "\n".join(p.text for p in parts)
        content = []
        for part in parts:
            if isinstance(part, TextPart):
                content.append({"type": "text", "text": part.text})
            elif isinstance(part, ImagePart):
                import base64
                b64 = base64.b64encode(part.to_png_bytes()).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
            else:
                # Voice input stays on Gemini specifically (see
                # openai_provider.py's identical guard) - no local
                # runtime in this module's scope takes raw audio
                # through the chat-completions endpoint.
                raise TypeError(
                    "LocalChatSession received an unsupported content part "
                    f"({type(part).__name__}) - voice input isn't wired through "
                    "the local-model path, only Gemini's."
                )
        return content

    def _messages_for_request(self) -> list:
        if not self._instructions:
            return list(self._messages)
        return [{"role": "system", "content": self._instructions}] + self._messages

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
            # Same fail-safe shape every other provider module gives the
            # model on a tool exception - see ai/actions.py's
            # emergency_stop comment.
            return f"Error: tool '{name}' failed: {e}"
        return "" if result is None else str(result)

    def seed_history(self, turns: "list[tuple[str, str]]") -> None:
        for role, text in turns:
            self._messages.append({"role": role, "content": text})

    def send_message(self, parts: list) -> ChatResponse:
        self._messages.append({"role": "user", "content": self._parts_to_content(parts)})

        tool_call_records: list[ToolCallRecord] = []
        for _ in range(MAX_TOOL_ITERATIONS):
            response = self._client.chat.completions.create(
                model=self._model,
                messages=self._messages_for_request(),
                tools=self._tool_defs or None,
            )
            message = response.choices[0].message
            calls = list(message.tool_calls or [])

            self._messages.append({
                "role": "assistant",
                "content": message.content or "",
                **({"tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in calls
                ]} if calls else {}),
            })

            if not calls:
                return ChatResponse(text=message.content or "", tool_calls=tool_call_records)

            for call in calls:
                result_text = self._execute_tool_call(call.function.name, call.function.arguments)
                tool_call_records.append(ToolCallRecord(
                    name=call.function.name,
                    args=_safe_json_loads(call.function.arguments),
                    result=result_text,
                ))
                self._messages.append({"role": "tool", "tool_call_id": call.id, "content": result_text})

        return ChatResponse(
            text="(Stopped after a long chain of tool calls without a final answer - try rephrasing.)",
            tool_calls=tool_call_records,
        )

    def send_message_stream(self, parts: list, on_delta, on_thinking_delta=None) -> ChatResponse:
        """
        Real token-by-token streaming (stream=True), same tool loop as
        send_message() otherwise. Tool-call deltas arrive fragmented
        across many chunks, keyed by `index` (OpenAI's own documented
        Chat Completions streaming shape, which every local runtime
        here mirrors) - accumulated into whole calls before executing
        any of them, since a tool needs its full arguments JSON, not a
        half-arrived fragment. `on_thinking_delta` is accepted for
        interface parity with every other provider's
        send_message_stream but never called - Chat Completions has no
        reasoning-summary concept (see module docstring).
        """
        self._messages.append({"role": "user", "content": self._parts_to_content(parts)})

        tool_call_records: list[ToolCallRecord] = []
        for _ in range(MAX_TOOL_ITERATIONS):
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=self._messages_for_request(),
                tools=self._tool_defs or None,
                stream=True,
            )

            text_chunks: list[str] = []
            calls_by_index: dict[int, dict] = {}
            for chunk in stream:
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    text_chunks.append(delta.content)
                    on_delta(delta.content)
                for tc in (getattr(delta, "tool_calls", None) or []):
                    slot = calls_by_index.setdefault(tc.index, {"id": None, "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function is not None:
                        if tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function.arguments:
                            slot["arguments"] += tc.function.arguments

            text = "".join(text_chunks)
            ordered_calls = [calls_by_index[i] for i in sorted(calls_by_index)]

            self._messages.append({
                "role": "assistant",
                "content": text,
                **({"tool_calls": [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in ordered_calls
                ]} if ordered_calls else {}),
            })

            if not ordered_calls:
                return ChatResponse(text=text, tool_calls=tool_call_records)

            for call in ordered_calls:
                result_text = self._execute_tool_call(call["name"], call["arguments"])
                tool_call_records.append(ToolCallRecord(
                    name=call["name"],
                    args=_safe_json_loads(call["arguments"]),
                    result=result_text,
                ))
                self._messages.append({"role": "tool", "tool_call_id": call["id"], "content": result_text})

        return ChatResponse(
            text="(Stopped after a long chain of tool calls without a final answer - try rephrasing.)",
            tool_calls=tool_call_records,
        )

    def history_as_text_turns(self) -> "list[tuple[str, str]]":
        from ai.providers.base import merge_consecutive_turns
        turns = []
        for m in self._messages:
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue
            content = m.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("text"))
            else:
                text = ""
            text = text.strip()
            if text:
                turns.append((role, text))
        return merge_consecutive_turns(turns)


def create_chat_session(client, model: str, tool_functions: list, system_instruction: str) -> LocalChatSession:
    return LocalChatSession(client, model, tool_functions, system_instruction)
