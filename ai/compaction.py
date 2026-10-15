# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/compaction.py
==================
Keeping a long conversation inside the model's context, the way Claude
does it: when the history gets too big, the older part is replaced by a
summary written by the model, and the conversation carries on from
"summary + the most recent messages".

Why it matters here in particular: every message resends the whole
history, and this app's history is heavy. A screenshot is well over a
thousand tokens and a crawled web page can be tens of thousands. Without
this, a long chat got slower and more expensive with every message and
then failed outright once it passed the model's limit.

When it happens:

  - before a message is sent, if the history plus the new message is
    estimated at more than COMPACT_AT of the budget for the provider
    (context_budget); the budget can be set with KESTREL_CONTEXT_BUDGET
  - when the provider rejects a request for being too long
    (is_context_overflow), after which the request is retried once
  - when asked, by sending /compact as a message

What is kept verbatim: the last KEEP_RECENT_EXCHANGES exchanges. What
the summary keeps: what the user wants, decisions and preferences,
facts, names, paths and numbers that came up, what was done on the
computer and how it went, and what was about to happen next.

Images and tool plumbing do not survive into the rebuilt session (the
same text-only carry-over a provider switch does, see
ai.providers.switch_provider). That is most of the saving: the model
does not need to see an old screenshot again, it needs to know what it
showed, which the conversation already says.

The summary is stored with the chat (app/chat_database.py,
chat_summaries), so reopening the chat later, or restarting the app,
starts from the summary instead of redoing it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ai.log import log as print
from ai.log import log_debug

# Rough tokens per character for English-like text. Deliberately a
# little pessimistic (real tokenizers average nearer 1/4): compacting a
# message early costs one summary call, compacting it late costs a
# failed request.
CHARS_PER_TOKEN = 3.6
# What one image costs, whatever the provider (they all land between
# roughly 1,100 and 1,600 tokens for a full-screen screenshot).
IMAGE_TOKENS = 1500

# Compact once the estimate passes this share of the budget.
COMPACT_AT = 0.85
KEEP_RECENT_EXCHANGES = 4
# The newest messages are kept whole only up to this share of the budget;
# a single enormous pasted message is summarised like the rest.
RECENT_SHARE = 0.3

# Budgets in tokens. Well under each provider's hard limit: the point is
# to stay fast and affordable, not to use the window up. Local models
# often run with a small context (Ollama defaults to a few thousand
# tokens), so theirs is small.
DEFAULT_BUDGETS = {
    "gemini": 200_000,
    "openai": 200_000,
    "anthropic": 150_000,
    "local": 6_000,
}

COMPACT_COMMANDS = ("/compact", "/compress", "/summarize")

SUMMARY_TURN_PREFIX = "[Summary of the earlier part of this conversation"
SUMMARY_ACK = "Understood. I have the earlier part of our conversation from that summary and will continue from there."

SUMMARY_INSTRUCTIONS = """You are compacting a long conversation between a user and {assistant}, a desktop assistant that can see and control the user's Windows computer, so that the conversation can continue without the earlier messages.

Write a summary that keeps everything needed to carry on:
- what the user wants overall, and why, in their terms
- decisions made, preferences and instructions the user gave
- facts that came up: names, numbers, dates, file paths, URLs, commands, settings, code
- what was done on the computer, and how it turned out
- anything unresolved, and what was about to happen next

Leave out greetings, repetition, and anything later corrected or replaced. Write in the language the conversation is in, as short plain paragraphs and lists, under {words} words. Only include what was actually said or done; do not add advice or conclusions of your own."""


@dataclass
class CompactionResult:
    summary: str
    summarized_turns: int      # how many earlier turns went into the summary
    kept_turns: int            # how many recent turns were kept verbatim
    tokens_before: int
    tokens_after: int
    kept_user_turns: int = 0   # of kept_turns, how many were the user's
    requested: bool = False    # asked for with /compact, rather than needed to fit


def is_compact_command(text: str) -> bool:
    return (text or "").strip().lower() in COMPACT_COMMANDS


def context_budget(provider: str) -> int:
    raw = os.getenv("KESTREL_CONTEXT_BUDGET", "").strip().replace("_", "")
    if raw.isdigit() and int(raw) >= 2000:
        return int(raw)
    return DEFAULT_BUDGETS.get(provider, 150_000)


# ── Estimating ─────────────────────────────────────────────────────────

# Base64 has line breaks at most, never spaces; any real text this long
# has spaces in it.
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=\r\n]+$")


def _walk(obj, depth: int = 0) -> "tuple[int, int]":
    """(characters of text, number of images) in a history structure:
    provider SDK objects, dicts, lists, dataclasses."""
    if obj is None or depth > 14:
        return 0, 0
    if isinstance(obj, str):
        if obj.startswith("data:image"):
            return 0, 1
        if len(obj) > 4000 and _BASE64_RE.match(obj[:4000]):
            return 0, 1    # a raw base64 payload: an image, not text
        return len(obj), 0
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return 0, 1
    if isinstance(obj, (int, float, bool)):
        return 0, 0
    if type(obj).__name__ in ("Image", "PngImageFile", "JpegImageFile") or hasattr(obj, "getpixel"):
        return 0, 1
    if isinstance(obj, dict):
        chars = images = 0
        for key, value in obj.items():
            c, i = _walk(value, depth + 1)
            chars += c + (len(key) if isinstance(key, str) else 0)
            images += i
        return chars, images
    if isinstance(obj, (list, tuple, set)):
        chars = images = 0
        for item in obj:
            c, i = _walk(item, depth + 1)
            chars += c
            images += i
        return chars, images
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return _walk(dump(exclude_none=True), depth + 1)
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return _walk({k: v for k, v in vars(obj).items() if not k.startswith("_")}, depth + 1)
    return 0, 0


def _history_payload(session):
    """Whatever a provider session keeps its history in."""
    chat = getattr(session, "_chat", None)
    if chat is not None and hasattr(chat, "get_history"):
        try:
            return chat.get_history()
        except Exception:
            pass
    for attr in ("_messages", "_items"):
        if hasattr(session, attr):
            return getattr(session, attr)
    try:
        return session.history_as_text_turns()
    except Exception:
        return []


def estimate_tokens(obj) -> int:
    chars, images = _walk(obj)
    return int(chars / CHARS_PER_TOKEN) + images * IMAGE_TOKENS


def estimate_session_tokens(session) -> int:
    if session is None:
        return 0
    return estimate_tokens(_history_payload(session))


def is_context_overflow(error: BaseException) -> bool:
    """Whether a provider error means "the request is too long"."""
    text = f"{type(error).__name__}: {error}".lower()
    needles = (
        "context length", "context_length", "context window", "maximum context",
        "too many tokens", "prompt is too long", "input is too long", "token limit",
        "exceeds the maximum", "reduce the length", "request too large", "string too long",
        "input token count", "exceeds the context",
    )
    return any(n in text for n in needles)


# ── Summarising ─────────────────────────────────────────────────────────

def _transcript(turns: "list[tuple[str, str]]", assistant: str) -> str:
    names = {"user": "User", "assistant": assistant}
    return "\n\n".join(f"{names.get(role, role)}: {text}" for role, text in turns)


def _chunks(turns: "list[tuple[str, str]]", max_chars: int) -> "list[list[tuple[str, str]]]":
    chunks, current, size = [], [], 0
    for role, text in turns:
        if len(text) > max_chars:
            text = text[: max_chars // 2] + "\n[...]\n" + text[-max_chars // 4:]
        if current and size + len(text) > max_chars:
            chunks.append(current)
            current, size = [], 0
        current.append((role, text))
        size += len(text)
    if current:
        chunks.append(current)
    return chunks


def _one_shot(provider: str, model: str, system: str, text: str, max_tokens: int = 2000) -> str:
    """One request with no history and no tools, on the conversation's own
    provider and model (the user's key, the user's choice of model)."""
    import ai.providers as providers

    if provider == "gemini":
        from google.genai import types
        client = providers._make_gemini_client()
        result = client.models.generate_content(
            model=model,
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=0.2, max_output_tokens=max_tokens,
            ),
        )
        return (result.text or "").strip()
    if provider == "openai":
        client = providers._make_openai_client(None)
        result = client.responses.create(model=model, instructions=system, input=text, max_output_tokens=max_tokens)
        return (getattr(result, "output_text", "") or "").strip()
    if provider == "anthropic":
        client = providers._make_anthropic_client(None)
        result = client.messages.create(
            model=model, system=system, max_tokens=max_tokens,
            messages=[{"role": "user", "content": text}],
        )
        return "".join(getattr(b, "text", "") for b in result.content).strip()
    if provider == "local":
        import ai.main as ai_main
        client = providers._make_local_client()
        result = client.chat.completions.create(
            model=ai_main.get_local_model_name(),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        return (result.choices[0].message.content or "").strip()
    raise ValueError(f"Unknown provider {provider!r}")


def summarize(provider: str, model: str, turns: "list[tuple[str, str]]", previous_summary: str = "",
              assistant: str = "Assistant", budget: int = 150_000) -> str:
    """
    A summary of `turns`, folding in `previous_summary` when there is one
    (compacting a conversation that was already compacted). Done in
    pieces when the turns alone would not fit comfortably in one request.
    """
    words = 350 if budget < 20_000 else 700
    system = SUMMARY_INSTRUCTIONS.format(assistant=assistant, words=words)
    # A request's worth: half the budget, leaving the rest for the answer
    # and for the estimate being off.
    max_chars = int(budget * 0.5 * CHARS_PER_TOKEN)
    summary = (previous_summary or "").strip()
    for chunk in _chunks(turns, max_chars) or [[]]:
        parts = []
        if summary:
            parts.append(f"Summary of the conversation before this point:\n{summary}")
        if chunk:
            parts.append("Conversation to summarise:\n\n" + _transcript(chunk, assistant))
        parts.append("Write the updated summary now.")
        summary = _one_shot(provider, model, system, "\n\n".join(parts)) or summary
    return summary.strip()


def summary_turns(summary: str) -> "list[tuple[str, str]]":
    """How a summary goes into a rebuilt session: as the opening user turn,
    acknowledged, so the history still alternates user/assistant (which
    Anthropic requires) and the model reads it as context, not a request."""
    if not summary:
        return []
    return [
        ("user", f"{SUMMARY_TURN_PREFIX}, written when it got long]\n\n{summary}"),
        ("assistant", SUMMARY_ACK),
    ]


def split_recent(turns: "list[tuple[str, str]]", budget: int,
                 keep_exchanges: int = KEEP_RECENT_EXCHANGES) -> "tuple[list, list]":
    """(older, recent): the newest `keep_exchanges` user turns and what
    follows them are kept, within RECENT_SHARE of the budget; recent
    always starts with a user turn."""
    recent_start = len(turns)
    users_seen = 0
    for i in range(len(turns) - 1, -1, -1):
        if turns[i][0] == "user":
            users_seen += 1
            if users_seen > keep_exchanges:
                break
            recent_start = i
    limit = budget * RECENT_SHARE
    # Drop whole exchanges from the front of `recent` while it is too big.
    while recent_start < len(turns) and estimate_tokens(turns[recent_start:]) > limit:
        next_user = next((j for j in range(recent_start + 1, len(turns)) if turns[j][0] == "user"), None)
        if next_user is None:
            break
        recent_start = next_user
    return turns[:recent_start], turns[recent_start:]


def compact_session(session, provider: str, model: str, tools: list, system_instruction: str,
                    previous_summary: str = "", assistant: str = "Assistant", force: bool = False,
                    drop_trailing_user: bool = False):
    """
    Returns (new_session, CompactionResult) or (session, None) when there
    was nothing to compact. The session is only replaced once the summary
    exists: a summary call that fails leaves the conversation as it was.
    """
    import ai.providers as providers
    from ai.providers.base import merge_consecutive_turns

    budget = context_budget(provider)
    before = estimate_session_tokens(session)
    turns = session.history_as_text_turns() if session is not None else []
    if drop_trailing_user:
        # A request the provider refused as too long can still have been
        # added to the history (OpenAI, Anthropic and local sessions add
        # the message before sending it). It is about to be sent again.
        while turns and turns[-1][0] == "user":
            turns.pop()
    # A summary already at the top of the history is folded back in, not
    # summarised as if the user had said it.
    if turns and turns[0][0] == "user" and turns[0][1].startswith(SUMMARY_TURN_PREFIX):
        previous_summary = turns[0][1].split("\n\n", 1)[-1] if not previous_summary else previous_summary
        turns = turns[2:] if len(turns) > 1 and turns[1][0] == "assistant" else turns[1:]

    older, recent = split_recent(turns, budget)
    if not older and not force:
        return session, None
    if not older and not previous_summary:
        # Nothing old enough to summarise: at most, shed images and tool
        # plumbing by rebuilding from the text alone.
        older, recent = [], turns

    summary = previous_summary
    if older:
        summary = summarize(provider, model, older, previous_summary, assistant=assistant, budget=budget)
        if not summary:
            raise RuntimeError("the model returned an empty summary")

    new_session = providers.create_chat_session(provider, model, tools, system_instruction)
    seeded = merge_consecutive_turns(summary_turns(summary) + list(recent))
    if seeded:
        new_session.seed_history(seeded)
    after = estimate_session_tokens(new_session)
    print(f"[compaction] {len(older)} earlier turn(s) summarised, {len(recent)} kept: "
          f"~{before:,} -> ~{after:,} tokens")
    return new_session, CompactionResult(
        summary=summary, summarized_turns=len(older), kept_turns=len(recent),
        tokens_before=before, tokens_after=after,
        kept_user_turns=sum(1 for role, _ in recent if role == "user"),
    )


def should_compact(session, provider: str, incoming_tokens: int = 0) -> bool:
    budget = context_budget(provider)
    estimate = estimate_session_tokens(session) + incoming_tokens
    if estimate >= budget * COMPACT_AT:
        log_debug(f"[compaction] ~{estimate:,} tokens against a budget of {budget:,}: compacting")
        return True
    return False
