# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_chat_context.py
============================
What the model is given of a saved chat (app/chat_context.py), and the
two tables behind it: images sent with a message (message_attachments)
and a compacted chat's summary (chat_summaries).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.chat_database as db
from app import chat_context


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "chats.db"))
    db.init_db()


def _chat(*messages):
    """A chat with (role, text) messages; returns (chat_id, [ids])."""
    chat_id = db.create_chat("t")
    return chat_id, [db.add_message(chat_id, role, text) for role, text in messages]


# ── what counts as conversation ────────────────────────────────────────

@pytest.mark.parametrize("role,text", [
    ("assistant", "[[SCREENSHOT:C:/shots/a.png|ai]]"),
    ("assistant", "[[RUN_SCREENSHOTS:C:/shots/runs/1]]"),
    ("assistant", "[[SYSTEM:compress|Conversation compacted]]"),
    ("assistant", "[[SOURCES:W10=]]"),
    ("assistant", "[[TERMINAL:e30=]]"),
    ("assistant", "[[FILE:e30=]]"),
    ("user", "/compact"),
    ("user", "   "),
    ("system", "hello"),
])
def test_display_rows_are_not_conversation(role, text):
    assert not chat_context.is_conversation_text(role, text)


@pytest.mark.parametrize("role,text", [
    ("user", "what is on my screen?"),
    ("assistant", "A browser with [[two]] tabs."),
    ("user", "/compact is a command, right?"),
])
def test_things_people_said_are_conversation(role, text):
    assert chat_context.is_conversation_text(role, text)


# ── model_history ──────────────────────────────────────────────────────

def test_history_is_the_thread_minus_markers_and_the_message_being_answered():
    chat_id, _ = _chat(
        ("user", "take a screenshot"),
        ("assistant", "[[SCREENSHOT:C:/a.png|ai]]"),
        ("assistant", "Here it is."),
        ("user", "thanks, now what?"),
    )
    summary, turns = chat_context.model_history(chat_id)
    assert summary == ""
    assert turns == [("user", "take a screenshot"), ("assistant", "Here it is.")]


def test_a_regenerate_cuts_at_the_message_being_re_asked():
    chat_id, ids = _chat(("user", "one"), ("assistant", "1"), ("user", "two"), ("assistant", "2"))
    _, turns = chat_context.model_history(chat_id, before_message_id=ids[2])
    assert turns == [("user", "one"), ("assistant", "1")]


def test_a_thread_that_opens_with_a_note_starts_at_the_first_question():
    chat_id, _ = _chat(("assistant", "Welcome back."), ("user", "hi"), ("assistant", "hello"), ("user", "next"))
    _, turns = chat_context.model_history(chat_id)
    assert turns[0] == ("user", "hi")


def test_a_summary_stands_in_for_what_it_covers():
    chat_id, ids = _chat(("user", "u1"), ("assistant", "a1"), ("user", "u2"), ("assistant", "a2"), ("user", "u3"))
    db.set_chat_summary(chat_id, "the gist of u1 and a1", ids[1])
    summary, turns = chat_context.model_history(chat_id)
    assert summary == "the gist of u1 and a1"
    assert turns == [("user", "u2"), ("assistant", "a2")]


def test_a_summary_of_an_edited_away_branch_is_dropped():
    chat_id, ids = _chat(("user", "u1"), ("assistant", "a1"), ("user", "u2"), ("assistant", "a2"))
    db.set_chat_summary(chat_id, "about the old branch", ids[3])
    db.branch_message(chat_id, ids[0], "u1, edited")
    summary, turns = chat_context.model_history(chat_id)
    assert summary == ""
    assert db.get_chat_summary(chat_id) is None
    assert turns == []                      # the edited message is the one being answered


def test_regenerating_an_older_reply_ignores_but_keeps_a_newer_summary():
    chat_id, ids = _chat(("user", "u1"), ("assistant", "a1"), ("user", "u2"), ("assistant", "a2"))
    db.set_chat_summary(chat_id, "covers up to a2", ids[3])
    summary, turns = chat_context.model_history(chat_id, before_message_id=ids[2])
    assert summary == ""
    assert turns == [("user", "u1"), ("assistant", "a1")]
    assert db.get_chat_summary(chat_id) is not None


# ── summary_upto_message_id ────────────────────────────────────────────

def test_the_summary_covers_everything_before_the_kept_exchanges():
    chat_id, ids = _chat(
        ("user", "u1"), ("assistant", "a1"), ("user", "u2"), ("assistant", "a2"),
        ("user", "u3"), ("assistant", "a3"), ("user", "being answered"),
    )
    assert chat_context.summary_upto_message_id(chat_id, kept_user_turns=2) == ids[1]
    assert chat_context.summary_upto_message_id(chat_id, kept_user_turns=0) == ids[5]
    assert chat_context.summary_upto_message_id(chat_id, kept_user_turns=3) is None
    assert chat_context.summary_upto_message_id(chat_id, kept_user_turns=9) is None


def test_markers_do_not_count_as_kept_messages():
    chat_id, ids = _chat(
        ("user", "u1"), ("assistant", "a1"),
        ("user", "u2"), ("assistant", "[[SCREENSHOT:C:/a.png]]"), ("assistant", "a2"),
    )
    # History is u1 a1 u2 a2 (the marker is display only); keeping one
    # exchange keeps u2 onward, so the summary ends at a1.
    assert chat_context.summary_upto_message_id(chat_id, kept_user_turns=1) == ids[1]


# ── attachments ─────────────────────────────────────────────────────────

def test_attachments_come_back_in_the_order_they_were_sent():
    chat_id, ids = _chat(("user", "look at these"))
    db.add_attachments(ids[0], ["C:/b.png", "", "C:/a.png"])
    assert db.get_attachments(ids[0]) == ["C:/b.png", "C:/a.png"]
    assert db.get_attachments(ids[0] + 999) == []


def test_attachments_go_when_their_chat_goes():
    chat_id, ids = _chat(("user", "look"))
    db.add_attachments(ids[0], ["C:/a.png"])
    db.delete_chat(chat_id)
    assert db.get_attachments(ids[0]) == []


# ── summaries ───────────────────────────────────────────────────────────

def test_a_chat_has_one_summary_replaced_on_each_compaction():
    chat_id, ids = _chat(("user", "u1"), ("assistant", "a1"), ("user", "u2"))
    assert db.get_chat_summary(chat_id) is None
    db.set_chat_summary(chat_id, "first", ids[0])
    db.set_chat_summary(chat_id, "second", ids[1])
    stored = db.get_chat_summary(chat_id)
    assert (stored["summary"], stored["upto_message_id"]) == ("second", ids[1])
    db.clear_chat_summary(chat_id)
    assert db.get_chat_summary(chat_id) is None


def test_a_summary_goes_when_its_chat_goes():
    chat_id, ids = _chat(("user", "u1"))
    db.set_chat_summary(chat_id, "gist", ids[0])
    db.delete_chat(chat_id)
    assert db.get_chat_summary(chat_id) is None
