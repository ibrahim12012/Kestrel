# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/providers/gemini_provider.py
==================================
Wraps google-genai's Chat object (built with automatic function calling
- see ai/main.py's existing _ensure_initialized for how `tools` gets
passed straight through as raw Python callables) behind the common
ChatSession interface. This is intentionally the THINNEST of the three
provider modules: Gemini already does everything the interface asks
for internally (schema generation from the same tool_* functions, the
tool-execution loop, multi-turn history) - there's no manual loop to
write here, unlike openai_provider.py / anthropic_provider.py.

Extended thinking (Settings > "Extended thinking" - see
ai.main.get_extended_thinking_enabled): Gemini 2.5/3.x models already
think internally by default; what this toggles is THOUGHT SUMMARIES -
ThinkingConfig(include_thoughts=True) makes the model hand back a
plain-language summary of that reasoning as its own kind of Part
(`part.thought is True`), alongside its normal answer Part(s) in the
very same response/chunk. Every model in base.MODEL_CHOICES for this
provider is a 2.5/3.x model, so no per-model capability check is needed
here the way Anthropic's provider doesn't need one either - Kestrel
simply never lists an older Gemini model that lacks this. When the
setting is off, this session is built with no ThinkingConfig at all
(byte-identical request to every build before this feature existed).
"""

from __future__ import annotations

from PIL import Image as PILImage

from ai.providers.base import ChatResponse, ChatSession, ImagePart, TextPart, AudioPart


class GeminiChatSession(ChatSession):
    def __init__(self, client, model: str, tools: list, system_instruction: str, raw_chat=None, enable_thinking: bool = False):
        # Stored (not just `raw_chat` itself) because seed_history()
        # below has to rebuild the underlying Chat object - google-genai
        # only accepts initial history at client.chats.create() time,
        # there's no "insert into an existing Chat's history" call.
        self._client = client
        self._model = model
        self._tools = tools
        self._system_instruction = system_instruction
        self._enable_thinking = enable_thinking
        self._chat = raw_chat or self._build_chat(history=[])

    def _build_chat(self, history: list):
        config = {
            "tools": self._tools,
            "system_instruction": str(self._system_instruction),
            "tool_config": {
                "function_calling_config": {"mode": "AUTO"}
            },
        }
        if self._enable_thinking:
            # thinking_budget=-1 asks the model to size its own
            # reasoning ("dynamic" budget, Google's documented value
            # for "let the model decide") rather than this app guessing
            # a fixed token count the way anthropic_provider.py has to
            # (Anthropic's API has no dynamic option, only an explicit
            # budget_tokens). include_thoughts=True is the part that
            # actually turns that internal reasoning into a visible
            # `part.thought` Part in the response - without it, 2.5/3.x
            # models still think just as much, they just never hand any
            # of it back.
            config["thinking_config"] = {"thinking_budget": -1, "include_thoughts": True}
        return self._client.chats.create(
            model=self._model,
            history=history,
            config=config,
        )

    def seed_history(self, turns: "list[tuple[str, str]]") -> None:
        from google.genai import types
        history = [
            types.Content(role=("model" if role == "assistant" else "user"), parts=[types.Part(text=text)])
            for role, text in turns
        ]
        self._chat = self._build_chat(history=history)

    def _build_contenu(self, parts: "list") -> list:
        from google.genai import types

        contenu = []
        for part in parts:
            if isinstance(part, TextPart):
                contenu.append(part.text)
            elif isinstance(part, ImagePart):
                # google-genai accepts a PIL Image directly - pass it
                # through unchanged when it already is one (the normal
                # case: ai/main.py's screenshot capture is PIL), only
                # converting raw bytes explicitly.
                if isinstance(part.image, PILImage.Image):
                    contenu.append(part.image)
                else:
                    contenu.append(types.Part.from_bytes(data=part.to_png_bytes(), mime_type="image/png"))
            elif isinstance(part, AudioPart):
                contenu.append(types.Part.from_bytes(data=part.data, mime_type=part.mime_type))
            else:
                raise TypeError(f"Unknown content part type: {type(part)}")
        return contenu

    @staticmethod
    def _split_thought_and_answer(candidate) -> "tuple[str, str]":
        """
        Splits one candidate's parts into (thought_text, answer_text) -
        `part.thought is True` marks a thought-summary Part, anything
        else with `.text` is normal answer text. Deliberately tolerant
        of a candidate that doesn't look like this (missing
        .content/.parts, or a Part with no .thought attribute at all) -
        older google-genai responses and safety-blocked candidates
        shouldn't crash a request just because there's nothing to
        split; they just contribute no thought text.
        """
        thought_chunks: list[str] = []
        answer_chunks: list[str] = []
        content = getattr(candidate, "content", None)
        for part in (getattr(content, "parts", None) or []):
            text = getattr(part, "text", None)
            if not text:
                continue
            if getattr(part, "thought", False):
                thought_chunks.append(text)
            else:
                answer_chunks.append(text)
        return "".join(thought_chunks), "".join(answer_chunks)

    def send_message(self, parts: "list") -> ChatResponse:
        response = self._chat.send_message(self._build_contenu(parts))
        if not self._enable_thinking:
            # Unchanged from before this feature existed.
            return ChatResponse(text=response.text or "")
        thinking_text = ""
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            thinking_text, _ = self._split_thought_and_answer(candidates[0])
        return ChatResponse(text=response.text or "", thinking=thinking_text)

    def send_message_stream(self, parts: "list", on_delta, on_thinking_delta=None) -> ChatResponse:
        """
        Same content-part conversion as send_message(), but iterates
        chat.send_message_stream() - google-genai's own documented way
        to combine automatic function calling with real streaming
        (Google recommends AFC specifically through
        Chat.send_message_stream, not a lower-level
        generate_content_stream call). The SDK still executes any tool
        calls internally mid-stream exactly as send_message() does;
        only the assistant's own outgoing text arrives incrementally
        instead of all at once.

        With extended thinking off, this is exactly the pre-existing
        behavior: `chunk.text` straight to on_delta. With it on, each
        chunk's own candidate is split into thought vs. answer text
        (see _split_thought_and_answer) - thought text goes to
        on_thinking_delta as it streams in, same "reasoning first, then
        the answer" ordering Anthropic's provider (and claude.ai
        itself) already show.
        """
        contenu = self._build_contenu(parts)
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        for chunk in self._chat.send_message_stream(contenu):
            if not self._enable_thinking:
                piece = getattr(chunk, "text", "") or ""
                if piece:
                    text_parts.append(piece)
                    on_delta(piece)
                continue
            candidates = getattr(chunk, "candidates", None) or []
            if not candidates:
                piece = getattr(chunk, "text", "") or ""
                if piece:
                    text_parts.append(piece)
                    on_delta(piece)
                continue
            thought_piece, answer_piece = self._split_thought_and_answer(candidates[0])
            if thought_piece:
                thinking_parts.append(thought_piece)
                if on_thinking_delta is not None:
                    on_thinking_delta(thought_piece)
            if answer_piece:
                text_parts.append(answer_piece)
                on_delta(answer_piece)
        return ChatResponse(text="".join(text_parts), thinking="".join(thinking_parts))

    def history_as_text_turns(self) -> "list[tuple[str, str]]":
        from ai.providers.base import merge_consecutive_turns
        turns = []
        for content in self._chat.get_history():
            role = "assistant" if content.role == "model" else "user"
            text = "".join(
                getattr(p, "text", "") or ""
                for p in (content.parts or [])
                if not getattr(p, "thought", False)
            ).strip()
            if text:
                turns.append((role, text))
        return merge_consecutive_turns(turns)


def create_chat_session(client, model: str, tools: list, system_instruction: str) -> GeminiChatSession:
    """Builds a fresh GeminiChatSession - same client.chats.create(...)
    call ai/main.py's _ensure_initialized used to make directly, now
    reachable from the provider registry too, without ai/main.py
    needing an if/elif on provider name at every call site that
    (re)builds `chat`. Reads Settings > "Extended thinking" lazily - see
    anthropic_provider.py's create_chat_session for why (avoids a
    circular import with ai.main, which imports this package)."""
    import ai.main as ai_main
    enable_thinking = ai_main.get_extended_thinking_enabled()
    return GeminiChatSession(client, model, tools, system_instruction, enable_thinking=enable_thinking)
