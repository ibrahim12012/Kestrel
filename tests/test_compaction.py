# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_compaction.py
==========================
ai/compaction.py (estimating, splitting, summarising, rebuilding a
session) and the parts of ai/main.py that use it: one conversation per
chat (use_chat / forget_chat), compacting before a send, "/compact", and
the retry after a provider says the request is too long.

No provider is called: sessions are fakes and the summary call
(compaction._one_shot) is replaced.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.main as ai_main
import ai.providers as providers
from ai import compaction


class _Response:
    def __init__(self, text):
        self.text = text


class FakeSession:
    """Enough of a ChatSession: text turns in, text turns out."""

    def __init__(self, turns=None):
        self.turns = list(turns or [])
        self.sent = []
        self.fail_next = None

    def seed_history(self, turns):
        self.turns = list(turns)

    def history_as_text_turns(self):
        return list(self.turns)

    def send_message(self, parts):
        self.sent.append(parts)
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        text = " ".join(getattr(p, "text", "") for p in parts)
        self.turns += [("user", text), ("assistant", "ok")]
        return _Response("ok")


def _conversation(exchanges: int, size: int = 20) -> list:
    turns = []
    for n in range(1, exchanges + 1):
        turns.append(("user", f"question {n} " + "q" * size))
        turns.append(("assistant", f"answer {n} " + "a" * size))
    return turns


@pytest.fixture
def summaries(monkeypatch):
    """Replaces the summary call; records what it was asked."""
    calls = []

    def fake_one_shot(provider, model, system, text, max_tokens=2000):
        calls.append({"provider": provider, "model": model, "system": system, "text": text})
        return f"SUMMARY {len(calls)}"

    monkeypatch.setattr(compaction, "_one_shot", fake_one_shot)
    monkeypatch.setattr(providers, "create_chat_session", lambda *a, **k: FakeSession())
    monkeypatch.delenv("KESTREL_CONTEXT_BUDGET", raising=False)
    return calls


# ── small pieces ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["/compact", "  /Compact ", "/compress", "/summarize"])
def test_compact_commands_are_recognised(text):
    assert compaction.is_compact_command(text)


@pytest.mark.parametrize("text", ["", "compact", "/compact this please", "please /compact"])
def test_other_text_is_not_a_compact_command(text):
    assert not compaction.is_compact_command(text)


def test_the_budget_follows_the_provider_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("KESTREL_CONTEXT_BUDGET", raising=False)
    assert compaction.context_budget("local") == compaction.DEFAULT_BUDGETS["local"]
    assert compaction.context_budget("somebody-new") == 150_000
    monkeypatch.setenv("KESTREL_CONTEXT_BUDGET", "50_000")
    assert compaction.context_budget("gemini") == 50_000
    monkeypatch.setenv("KESTREL_CONTEXT_BUDGET", "12")      # too small to be meant
    assert compaction.context_budget("gemini") == compaction.DEFAULT_BUDGETS["gemini"]


def test_text_is_estimated_by_length():
    assert compaction.estimate_tokens("x" * 360) == 100


def test_images_are_counted_as_images_not_as_their_bytes():
    history = [
        {"role": "user", "content": [{"type": "text", "text": "look"},
                                     {"type": "image", "data": "data:image/png;base64,AAAA"}]},
        {"role": "user", "content": [b"\x89PNG" * 5000]},
        {"inline": "A" * 20000},                       # a raw base64 payload
    ]
    tokens = compaction.estimate_tokens(history)
    assert tokens >= 3 * compaction.IMAGE_TOKENS
    assert tokens < 3 * compaction.IMAGE_TOKENS + 100


def test_sdk_objects_are_walked_through_model_dump():
    class Part:
        def model_dump(self, exclude_none=True):
            return {"text": "y" * 720}
    assert compaction.estimate_tokens([Part()]) >= 200


def test_a_session_is_estimated_from_its_history():
    session = FakeSession(_conversation(3, size=360))
    assert compaction.estimate_session_tokens(session) > 3 * 2 * 100
    assert compaction.estimate_session_tokens(None) == 0


@pytest.mark.parametrize("message", [
    "prompt is too long: 212000 tokens > 200000 maximum",
    "This model's maximum context length is 128000 tokens.",
    "The input token count (1100000) exceeds the maximum number of tokens allowed",
    "Request too large for gpt-4o",
])
def test_too_long_errors_are_recognised(message):
    assert compaction.is_context_overflow(RuntimeError(message))


@pytest.mark.parametrize("message", ["rate limit reached", "invalid api key", "connection reset"])
def test_other_errors_are_not_mistaken_for_too_long(message):
    assert not compaction.is_context_overflow(RuntimeError(message))


def test_split_keeps_the_last_exchanges_and_starts_on_a_user_turn():
    turns = _conversation(6)
    older, recent = compaction.split_recent(turns, budget=150_000, keep_exchanges=4)
    assert older == turns[:4]
    assert recent == turns[4:]
    assert recent[0][0] == "user"


def test_split_sheds_whole_exchanges_when_the_recent_part_is_too_big():
    turns = _conversation(4, size=4000)            # ~2,200 tokens an exchange
    older, recent = compaction.split_recent(turns, budget=10_000, keep_exchanges=4)
    assert recent and recent[0][0] == "user"
    assert compaction.estimate_tokens(recent) <= 10_000 * compaction.RECENT_SHARE
    assert older + recent == turns


def test_split_never_drops_the_last_exchange_however_big():
    turns = [("user", "x" * 100_000), ("assistant", "ok")]
    older, recent = compaction.split_recent(turns, budget=10_000)
    assert older == [] and recent == turns


def test_summary_turns_open_with_the_summary_and_an_acknowledgement():
    assert compaction.summary_turns("") == []
    (role1, text1), (role2, text2) = compaction.summary_turns("the gist")
    assert role1 == "user" and text1.startswith(compaction.SUMMARY_TURN_PREFIX) and text1.endswith("the gist")
    assert role2 == "assistant" and text2 == compaction.SUMMARY_ACK


def test_chunks_split_by_size_and_trim_a_huge_turn():
    chunks = compaction._chunks([("user", "a" * 60), ("assistant", "b" * 60), ("user", "c" * 500)], 100)
    assert len(chunks) == 3
    assert "[...]" in chunks[2][0][1] and len(chunks[2][0][1]) < 500


def test_a_long_history_is_summarised_in_pieces_each_building_on_the_last(summaries):
    turns = _conversation(3, size=3000)
    summary = compaction.summarize("anthropic", "m", turns, budget=4000)   # ~7,200 characters a request
    assert len(summaries) > 1
    assert summary == f"SUMMARY {len(summaries)}"
    assert "SUMMARY 1" in summaries[1]["text"]
    assert "{assistant}" not in summaries[0]["system"]


# ── compact_session ─────────────────────────────────────────────────────

def test_a_short_conversation_is_left_alone_unless_forced(summaries):
    session = FakeSession(_conversation(2))
    same, result = compaction.compact_session(session, "anthropic", "m", [], "sys")
    assert same is session and result is None
    assert summaries == []


def test_compacting_summarises_the_older_part_and_keeps_the_rest_verbatim(summaries):
    turns = _conversation(6, size=400)
    session = FakeSession(turns)
    new_session, result = compaction.compact_session(session, "anthropic", "m", [], "sys", force=True)
    assert new_session is not session
    assert result.summarized_turns == 4 and result.kept_turns == 8 and result.kept_user_turns == 4
    assert result.summary == "SUMMARY 1"
    seeded = new_session.turns
    assert seeded[0][1].startswith(compaction.SUMMARY_TURN_PREFIX) and seeded[0][1].endswith("SUMMARY 1")
    assert seeded[1] == ("assistant", compaction.SUMMARY_ACK)
    assert seeded[2:] == turns[4:]
    assert "question 1" in summaries[0]["text"] and "question 5" not in summaries[0]["text"]
    assert result.tokens_after < result.tokens_before


def test_an_earlier_summary_is_folded_in_not_summarised_as_something_the_user_said(summaries):
    first = FakeSession(_conversation(6, size=400))
    second, _ = compaction.compact_session(first, "anthropic", "m", [], "sys", force=True)
    second.turns += _conversation(6, size=400)
    compaction.compact_session(second, "anthropic", "m", [], "sys", force=True)
    asked = summaries[1]["text"]
    assert "Summary of the conversation before this point:\nSUMMARY 1" in asked
    assert compaction.SUMMARY_TURN_PREFIX not in asked
    assert compaction.SUMMARY_ACK not in asked


def test_a_refused_request_left_in_the_history_is_not_kept(summaries):
    turns = _conversation(6) + [("user", "the message that was too long")]
    new_session, _ = compaction.compact_session(FakeSession(turns), "anthropic", "m", [], "sys",
                                                force=True, drop_trailing_user=True)
    assert new_session.turns[-1][0] == "assistant"
    assert all("too long" not in text for _, text in new_session.turns)


def test_an_empty_summary_leaves_the_conversation_as_it_was(summaries, monkeypatch):
    monkeypatch.setattr(compaction, "_one_shot", lambda *a, **k: "")
    session = FakeSession(_conversation(6))
    with pytest.raises(RuntimeError):
        compaction.compact_session(session, "anthropic", "m", [], "sys", force=True)
    assert session.turns == _conversation(6)


def test_should_compact_compares_against_the_budget(monkeypatch):
    monkeypatch.setenv("KESTREL_CONTEXT_BUDGET", "10000")
    small = FakeSession(_conversation(2))
    assert not compaction.should_compact(small, "anthropic")
    assert compaction.should_compact(small, "anthropic", incoming_tokens=9000)


# ── ai.main: one conversation per chat ─────────────────────────────────

@pytest.fixture
def main_state(monkeypatch, summaries):
    """ai.main with fake sessions, no instructions file, no provider."""
    created = []

    def make_session(*args, **kwargs):
        session = FakeSession()
        created.append(session)
        return session

    monkeypatch.setattr(providers, "create_chat_session", make_session)
    monkeypatch.setattr(ai_main, "_initialized", True)
    monkeypatch.setattr(ai_main, "_tools_stale", False)
    monkeypatch.setattr(ai_main, "chat", None)
    monkeypatch.setattr(ai_main, "tools", [])
    monkeypatch.setattr(ai_main, "screenshot", None)
    monkeypatch.setattr(ai_main, "_bound_chat_id", None)
    monkeypatch.setattr(ai_main, "_stashed_sessions", {})
    monkeypatch.setattr(ai_main, "load_instructions", lambda: "instructions")
    monkeypatch.setattr(ai_main, "_render_instructions", lambda raw: raw)
    monkeypatch.setattr(ai_main, "get_active_provider", lambda: "anthropic")
    monkeypatch.setattr(ai_main, "get_active_model", lambda: "model-a")
    monkeypatch.setattr(ai_main, "get_extended_thinking_enabled", lambda: False)
    monkeypatch.setattr(ai_main, "get_assistant_name", lambda: "Kestrel")
    return created


def _history(summary="", turns=()):
    return lambda: (summary, list(turns))


def test_each_chat_gets_its_own_conversation(main_state):
    ai_main.use_chat(1, _history(turns=[("user", "in chat one"), ("assistant", "yes")]))
    one = ai_main.chat
    ai_main.use_chat(2, _history(turns=[("user", "in chat two"), ("assistant", "yes")]))
    two = ai_main.chat
    assert one is not two
    assert one.turns[0] == ("user", "in chat one")
    assert two.turns[0] == ("user", "in chat two")


def test_going_back_to_a_chat_takes_its_live_session_back(main_state):
    ai_main.use_chat(1, _history())
    one = ai_main.chat
    ai_main.use_chat(2, _history())

    def must_not_load():
        raise AssertionError("chat 1 was held; it should not be rebuilt")

    ai_main.use_chat(1, must_not_load)
    assert ai_main.chat is one


def test_staying_in_the_same_chat_changes_nothing(main_state):
    ai_main.use_chat(1, _history())
    one = ai_main.chat
    ai_main.use_chat(1, _history(turns=[("user", "different")]))
    assert ai_main.chat is one


def test_a_saved_summary_opens_the_rebuilt_conversation(main_state):
    ai_main.use_chat(7, _history("what happened before", [("user", "and now?")]))
    turns = ai_main.chat.turns
    assert turns[0][1].endswith("what happened before")
    assert turns[1] == ("assistant", compaction.SUMMARY_ACK)
    assert turns[2] == ("user", "and now?")


def test_only_a_few_conversations_are_held(main_state):
    for chat_id in range(1, ai_main._MAX_STASHED_SESSIONS + 3):
        ai_main.use_chat(chat_id, _history())
    assert len(ai_main._stashed_sessions) == ai_main._MAX_STASHED_SESSIONS
    assert 1 not in ai_main._stashed_sessions


def test_a_held_conversation_moves_to_a_new_model_text_and_all(main_state, monkeypatch):
    ai_main.use_chat(1, _history(turns=[("user", "remember 42"), ("assistant", "ok")]))
    one = ai_main.chat
    ai_main.use_chat(2, _history())
    monkeypatch.setattr(ai_main, "get_active_model", lambda: "model-b")
    ai_main.use_chat(1, _history())
    assert ai_main.chat is not one
    assert ai_main.chat.turns == [("user", "remember 42"), ("assistant", "ok")]


def test_forgetting_a_chat_rebuilds_it_from_what_was_saved(main_state):
    ai_main.use_chat(1, _history(turns=[("user", "old"), ("assistant", "old")]))
    ai_main.forget_chat(1)
    ai_main.use_chat(1, _history(turns=[("user", "edited"), ("assistant", "new")]))
    assert ai_main.chat.turns[0] == ("user", "edited")


def test_a_rebuilt_tool_list_keeps_the_conversation(main_state, monkeypatch):
    ai_main.use_chat(1, _history(turns=[("user", "my name is Sam"), ("assistant", "hi Sam")]))
    monkeypatch.setattr(ai_main, "_tools_stale", True)
    ai_main._ensure_chat_session_fresh()
    assert ai_main.chat.turns == [("user", "my name is Sam"), ("assistant", "hi Sam")]


def test_compact_command_summarises_and_replies_with_a_note(main_state):
    ai_main.use_chat(1, _history(turns=_conversation(6)))
    seen = []
    reply = ai_main.ai("/compact", on_compacted=seen.append)
    assert reply.startswith("[[SYSTEM:compress|Conversation compacted: 4 earlier messages")
    assert len(seen) == 1 and seen[0].summary == "SUMMARY 1"
    assert ai_main.chat.turns[0][1].endswith("SUMMARY 1")


def test_compact_command_on_a_short_chat_says_there_is_nothing_to_do(main_state):
    ai_main.use_chat(1, _history(turns=_conversation(1)))
    reply = ai_main.ai("/compact")
    assert "Nothing to compact" in reply


def test_compact_command_result_is_marked_as_asked_for(main_state):
    ai_main.use_chat(1, _history(turns=_conversation(6)))
    seen = []
    ai_main.ai("/compact", on_compacted=seen.append)
    assert seen[0].requested is True


def test_automatic_compaction_is_not_marked_as_asked_for(main_state, monkeypatch):
    monkeypatch.setenv("KESTREL_CONTEXT_BUDGET", "4000")
    ai_main.use_chat(1, _history(turns=_conversation(6, size=2000)))
    seen = []
    ai_main.ai("next", on_compacted=seen.append)
    assert seen and seen[0].requested is False


def test_long_plain_text_is_not_mistaken_for_an_image():
    assert compaction.estimate_tokens("word " * 2000) == int(10_000 / compaction.CHARS_PER_TOKEN)


def test_a_message_that_would_pass_the_budget_compacts_first(main_state, monkeypatch):
    monkeypatch.setenv("KESTREL_CONTEXT_BUDGET", "4000")
    ai_main.use_chat(1, _history(turns=_conversation(6, size=2000)))
    seen = []
    assert ai_main.ai("next question", on_compacted=seen.append) == "ok"
    assert len(seen) == 1
    assert ai_main.chat.turns[0][1].startswith(compaction.SUMMARY_TURN_PREFIX)
    assert ai_main.chat.turns[-2] == ("user", "next question")


def test_too_long_for_the_provider_compacts_and_retries_once(main_state):
    ai_main.use_chat(1, _history(turns=_conversation(6)))
    first = ai_main.chat
    first.fail_next = RuntimeError("prompt is too long: 250000 tokens > 200000 maximum")
    seen = []
    assert ai_main.ai("hello", on_compacted=seen.append) == "ok"
    assert len(seen) == 1
    assert ai_main.chat is not first
    assert len(first.sent) == 1 and len(ai_main.chat.sent) == 1


def test_other_errors_are_not_retried(main_state):
    ai_main.use_chat(1, _history(turns=_conversation(6)))
    ai_main.chat.fail_next = RuntimeError("invalid api key")
    reply = ai_main.ai("hello")
    assert reply == "Error: invalid api key"
