# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/providers/base.py
=======================
Provider-agnostic types shared by gemini_provider.py, openai_provider.py,
and anthropic_provider.py - the common vocabulary ai/main.py's chat code
talks in, so it never has to import google.genai / openai / anthropic
types directly and doesn't care which provider is actually live.

Why this exists: Gemini's SDK does "automatic function calling" - you
hand client.chats.create() raw Python callables and it introspects
them, builds the schemas, executes the tool loop, and hands back a
finished response, all inside the SDK. OpenAI and Anthropic don't do
any of that: you send a JSON Schema per tool, get back a response that
may contain tool-call requests, execute those yourself, and send the
results back in a second (third, fourth...) request - repeat until the
model stops asking for tools. ChatSession below is what makes that
difference invisible to every ai/main.py call site: all three
providers' send_message() runs its own tool loop internally (Gemini's
inside the SDK, OpenAI/Anthropic's inside openai_provider.py /
anthropic_provider.py) and returns the same ChatResponse either way.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field


# ── Message content parts ────────────────────────────────────────────
# What ai/main.py currently builds as a raw `contenu = [...]` list mixing
# plain strings, a PIL screenshot, and (for voice) a WAV types.Part - kept
# as these small dataclasses instead so each provider module converts
# TO its own SDK's shape rather than ai/main.py needing to know three
# different shapes itself.

@dataclass
class TextPart:
    text: str


@dataclass
class ImagePart:
    # A PIL.Image (what ai/main.py's screenshot capture already produces)
    # or raw bytes - providers that need bytes call to_png_bytes().
    image: "object"

    def to_png_bytes(self) -> bytes:
        if isinstance(self.image, (bytes, bytearray)):
            return bytes(self.image)
        buf = io.BytesIO()
        self.image.save(buf, format="PNG")
        return buf.getvalue()


@dataclass
class AudioPart:
    data: bytes
    mime_type: str = "audio/wav"


ContentPart = "TextPart | ImagePart | AudioPart"


# ── Tool schema (provider-agnostic) ──────────────────────────────────

@dataclass
class ToolParam:
    name: str
    type: str  # one of "string" | "integer" | "number" | "boolean" | "array" | "object"
    description: str = ""
    required: bool = True
    enum: "list[str] | None" = None
    items_type: "str | None" = None  # for type == "array"


@dataclass
class ToolSchema:
    name: str
    description: str
    params: "list[ToolParam]" = field(default_factory=list)


# ── Response ──────────────────────────────────────────────────────────

@dataclass
class ToolCallRecord:
    """One executed tool call, kept only for logging/debugging - no
    call site currently reads these, but having them on the response
    (rather than discarding them inside each provider) means a future
    "show me what it did" UI doesn't need a provider-specific rewrite."""
    name: str
    args: dict
    result: str


@dataclass
class ChatResponse:
    text: str
    tool_calls: "list[ToolCallRecord]" = field(default_factory=list)
    # The model's own extended-reasoning text, when the active provider/
    # model produced any (Settings > "Extended thinking"
    # ai.main.get_extended_thinking_enabled - turned on for Claude's
    # extended thinking, Gemini's thought summaries, or a GPT-5
    # reasoning model's reasoning summary; see each
    # ai/providers/*_provider.py module's own docstring for how it gets
    # there). Empty string for every other provider/config, or a
    # non-reasoning model even with the setting on; callers should
    # treat "" the same as "no thinking happened" rather than a
    # failure.
    thinking: str = ""


# ── Chat session ──────────────────────────────────────────────────────

class ChatSession:
    """
    Common interface every provider module's chat session implements.
    Mirrors the one method ai/main.py actually calls on the existing
    Gemini `chat` object today (`chat.send_message(contenu)`), so
    swapping the provider is a matter of which create_chat_session()
    built `chat`, not a rewrite of every call site.
    """

    def send_message(self, parts: "list[ContentPart]") -> ChatResponse:
        raise NotImplementedError

    def send_message_stream(self, parts: "list[ContentPart]", on_delta, on_thinking_delta=None) -> ChatResponse:
        """
        Real network streaming counterpart to send_message(): identical
        contract (same parts in, same ChatResponse out - tool calls run
        exactly as they do non-streamed), except on_delta(text: str) is
        called as the provider's OWN API streams the reply back, piece
        by piece, rather than the caller waiting for one finished
        response. Default falls back to a single on_delta() call with
        the whole reply - used only if a provider module doesn't
        override this (there currently isn't one that doesn't); real
        providers implement genuine token-by-token streaming here, not
        a client-side simulation of it.

        on_thinking_delta(text: str), if given, is called as the
        model's own extended-reasoning text streams in - BEFORE the
        matching on_delta text for that turn, same ordering Claude.ai
        itself shows (reasoning first, then the answer). Every
        provider module calls it now when Settings > "Extended
        thinking" is on and the active model actually reasons
        (anthropic_provider.py's extended thinking, gemini_provider.py's
        thought summaries, openai_provider.py's reasoning summaries -
        see each module's own docstring); ai/main.py passes it through
        unconditionally without needing to care which provider is
        actually live.
        """
        response = self.send_message(parts)
        if response.text:
            on_delta(response.text)
        return response

    def seed_history(self, turns: "list[tuple[str, str]]") -> None:
        """
        Injects [(role, text), ...] ("user"/"assistant") as prior
        conversation turns WITHOUT making any API call - used by
        ai/providers/__init__.py's switch_provider() to carry a
        conversation's text across a mid-chat provider switch.
        Deliberately separate from send_message(): calling
        send_message() once per past turn would re-run the model on
        every one of them (real cost, real latency, and a replayed
        answer that may not even match what was actually said the
        first time) just to reconstruct context that a plain history
        injection gets for free.
        """
        raise NotImplementedError

    def history_as_text_turns(self) -> "list[tuple[str, str]]":
        """
        Returns [(role, text), ...] ("user"/"assistant") for everything
        said so far, text-only - used when switching providers mid-chat
        (see ai/providers/__init__.py's switch_provider) to reseed a new
        session with a plain-text transcript. Deliberately drops images,
        audio, and tool-call plumbing: none of that is portable across
        providers (a Gemini function-call record means nothing to
        OpenAI's API and vice versa), and re-sending every past
        screenshot into a brand-new session on every model switch would
        be expensive for no benefit - the model only needs to know what
        was already discussed, not re-see it.
        """
        raise NotImplementedError


# ── Curated model registry ("most used models" per provider) ─────────
# Deliberately a short, curated list per provider rather than every
# model id each API currently exposes - this is what populates the
# in-chat model-switcher dropdown (app/chat_widget.py), and a giant
# unfiltered list is worse UX than the handful of models people
# actually reach for. Update as providers ship new flagship models;
# nothing else in this codebase needs to change to add a line here.

@dataclass
class ModelChoice:
    provider: str   # "gemini" | "openai" | "anthropic"
    model_id: str   # exact id/model string the provider's API expects
    label: str      # shown in the dropdown


MODEL_CHOICES: "list[ModelChoice]" = [
    # gemini-3-flash-preview stays first/default: it's the existing
    # default this codebase already used, and per Google's own docs
    # (ai.google.dev/gemini-api/docs/models, checked Sept 2026) it's
    # still the one to use for Computer Use specifically - the other
    # Gemini 3.x Flash releases don't support it yet, which matters
    # here since this whole app IS a computer-use/automation agent.
    ModelChoice("gemini", "models/gemini-3-flash-preview", "Gemini 3 Flash"),
    # NOT "models/gemini-3.1-pro" - that id 404s ("is not found for API
    # version v1beta, or is not supported for generateContent"). Per
    # Google's own docs (ai.google.dev/gemini-api/docs/models/gemini-3.1-pro-preview,
    # checked Sept 2026) the actual model id for this one still carries
    # its "-preview" suffix, same as gemini-3-flash-preview above.
    ModelChoice("gemini", "models/gemini-3.1-pro-preview", "Gemini 3.1 Pro"),
    ModelChoice("gemini", "models/gemini-2.5-flash", "Gemini 2.5 Flash"),

    ModelChoice("openai", "gpt-5.4", "GPT-5.4"),
    ModelChoice("openai", "gpt-5.4-mini", "GPT-5.4 Mini"),
    ModelChoice("openai", "gpt-5.4-nano", "GPT-5.4 Nano"),

    ModelChoice("anthropic", "claude-sonnet-5", "Claude Sonnet 5"),
    ModelChoice("anthropic", "claude-opus-5", "Claude Opus 5"),
    ModelChoice("anthropic", "claude-haiku-4-5-20251001", "Claude Haiku 4.5"),

    # Not a real model id - local model names are whatever the user has
    # pulled into Ollama/LM Studio/llama.cpp/etc, which no curated list
    # could ever enumerate. This single sentinel entry is what lets
    # "Local" show up as one ordinary option in the model-switcher
    # dropdown (app/chat_widget.py) and in AI profiles
    # (ai/profiles.py) like any other choice; ai/providers/__init__.py's
    # create_chat_session() swaps this id out for the actual configured
    # name (ai.main.get_local_model_name()) right before building the
    # session, so nothing downstream of the dropdown/profile ever sends
    # "__local__" to a real server.
    ModelChoice("local", "__local__", "Local model"),
]


def default_model_for(provider: str) -> str:
    for choice in MODEL_CHOICES:
        if choice.provider == provider:
            return choice.model_id
    raise ValueError(f"No known models for provider '{provider}'.")


def merge_consecutive_turns(turns: "list[tuple[str, str]]") -> "list[tuple[str, str]]":
    """
    Collapses consecutive same-role turns into one, joining their text.
    Every provider's history_as_text_turns() runs its raw internal
    message log through this before returning - a single send_message()
    call that went through several rounds of tool calls leaves several
    "assistant" entries in a row once the tool-role messages between
    them are filtered out (see each provider's own history_as_text_turns),
    and Anthropic's Messages API specifically rejects a history that
    doesn't strictly alternate user/assistant - this is what
    switch_provider() (ai/providers/__init__.py) relies on to reseed a
    new session without that failing.
    """
    merged: "list[tuple[str, str]]" = []
    for role, text in turns:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, merged[-1][1] + "\n\n" + text)
        else:
            merged.append((role, text))
    return merged
