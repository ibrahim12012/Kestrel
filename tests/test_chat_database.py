# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_chat_database.py
============================
Tests for app/chat_database.py.

Every test gets its own throwaway SQLite file (via the db_path fixture),
so tests never touch the real data/chats.db and can run in parallel.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app.chat_database as db  # noqa: E402


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point the module at a fresh temp DB file for the duration of one test."""
    path = str(tmp_path / "test_chats.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    return path


# ══════════════════════════════════════════════════════════════════
# Schema / init
# ══════════════════════════════════════════════════════════════════

def test_init_db_creates_expected_tables(db_path):
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0] for row in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "chats" in tables
    assert "messages" in tables


def test_init_db_is_idempotent(db_path):
    # Calling it again shouldn't raise (CREATE TABLE IF NOT EXISTS + caught ALTER TABLE)
    db.init_db()
    db.init_db()


# ══════════════════════════════════════════════════════════════════
# Chats CRUD
# ══════════════════════════════════════════════════════════════════

def test_create_chat_returns_incrementing_ids(db_path):
    id1 = db.create_chat("First")
    id2 = db.create_chat("Second")
    assert id2 == id1 + 1


def test_create_chat_defaults_title(db_path):
    chat_id = db.create_chat()
    chats = {c["id"]: c["title"] for c in db.get_all_chats()}
    assert chats[chat_id] == "New chat"


def test_get_all_chats_orders_pinned_first_then_by_recency(db_path):
    old_chat = db.create_chat("Old")
    new_chat = db.create_chat("New")
    db.set_chat_pinned(old_chat, True)

    ordered_ids = [c["id"] for c in db.get_all_chats()]

    assert ordered_ids[0] == old_chat   # pinned, comes first despite being older
    assert new_chat in ordered_ids


def test_update_chat_title(db_path):
    chat_id = db.create_chat("Original")
    db.update_chat_title(chat_id, "Renamed")

    titles = {c["id"]: c["title"] for c in db.get_all_chats()}
    assert titles[chat_id] == "Renamed"


def test_delete_chat_removes_it(db_path):
    chat_id = db.create_chat("To delete")
    db.delete_chat(chat_id)

    remaining_ids = {c["id"] for c in db.get_all_chats()}
    assert chat_id not in remaining_ids


def test_delete_chat_cascades_to_messages(db_path):
    chat_id = db.create_chat("Chat with messages")
    db.add_message(chat_id, "user", "hello")

    db.delete_chat(chat_id)

    # Foreign key ON DELETE CASCADE should have removed the message too
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        remaining = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE chat_id=?", (chat_id,)
        ).fetchone()[0]
    assert remaining == 0


def test_set_chat_pinned_toggle(db_path):
    chat_id = db.create_chat()

    db.set_chat_pinned(chat_id, True)
    assert db.get_all_chats()[0]["pinned"] == 1

    db.set_chat_pinned(chat_id, False)
    assert db.get_all_chats()[0]["pinned"] == 0


def test_ai_working_state_defaults_false_and_can_be_toggled(db_path):
    chat_id = db.create_chat()
    assert db.is_ai_working(chat_id) is False

    db.set_ai_working(chat_id, True)
    assert db.is_ai_working(chat_id) is True

    db.set_ai_working(chat_id, False)
    assert db.is_ai_working(chat_id) is False


def test_is_ai_working_for_nonexistent_chat_returns_false(db_path):
    assert db.is_ai_working(chat_id=999999) is False


def test_init_db_clears_stale_ai_working_flags(db_path):
    """
    Regression test: if the app is closed/crashes while a request is in
    flight, is_ai_working=1 used to persist forever in the DB - nothing
    could ever set it back to False, since the worker that would have done
    so no longer exists. Every future load of that chat showed a
    permanently-stuck typing indicator (the "trois points" bug). init_db()
    now clears any leftover True flags on startup, since nothing can
    legitimately still be "in progress" the moment the app just started.
    """
    chat_id = db.create_chat()
    db.set_ai_working(chat_id, True)
    assert db.is_ai_working(chat_id) is True  # sanity check before "restart"

    db.init_db()  # simulates the app restarting

    assert db.is_ai_working(chat_id) is False


# ══════════════════════════════════════════════════════════════════
# Messages
# ══════════════════════════════════════════════════════════════════

def test_add_message_and_get_messages_roundtrip(db_path):
    chat_id = db.create_chat()

    db.add_message(chat_id, "user", "Hello")
    db.add_message(chat_id, "assistant", "Hi there")

    messages = db.get_messages(chat_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert [m["content"] for m in messages] == ["Hello", "Hi there"]


def test_add_message_updates_chat_updated_at(db_path):
    chat_id = db.create_chat()
    before = [c["updated_at"] for c in db.get_all_chats() if c["id"] == chat_id][0]

    db.add_message(chat_id, "user", "hello")

    after = [c["updated_at"] for c in db.get_all_chats() if c["id"] == chat_id][0]
    assert after >= before


def test_get_message_count(db_path):
    chat_id = db.create_chat()
    assert db.get_message_count(chat_id) == 0

    db.add_message(chat_id, "user", "one")
    db.add_message(chat_id, "assistant", "two")

    assert db.get_message_count(chat_id) == 2


def test_messages_returned_in_chronological_order(db_path):
    chat_id = db.create_chat()
    for i in range(5):
        db.add_message(chat_id, "user", f"message {i}")

    contents = [m["content"] for m in db.get_messages(chat_id)]
    assert contents == [f"message {i}" for i in range(5)]


# ══════════════════════════════════════════════════════════════════
# get_message (single-row lookup - used by the regenerate flow)
# ══════════════════════════════════════════════════════════════════

def test_get_message_returns_the_row(db_path):
    chat_id = db.create_chat()
    message_id = db.add_message(chat_id, "user", "hello there")

    msg = db.get_message(message_id)
    assert msg["id"] == message_id
    assert msg["chat_id"] == chat_id
    assert msg["role"] == "user"
    assert msg["content"] == "hello there"


def test_get_message_returns_none_for_unknown_id(db_path):
    assert db.get_message(999999) is None


def test_get_message_exposes_parent_id(db_path):
    chat_id = db.create_chat()
    user_id = db.add_message(chat_id, "user", "question")
    reply_id = db.add_message(chat_id, "assistant", "answer")

    assert db.get_message(user_id)["parent_id"] is None
    assert db.get_message(reply_id)["parent_id"] == user_id


# ══════════════════════════════════════════════════════════════════
# Regenerating an assistant reply (add_message with an explicit
# parent_id, creating a sibling branch of an existing reply - the
# same mechanism app/main_window.py's _on_regenerate_requested uses)
# ══════════════════════════════════════════════════════════════════

def test_regenerating_a_reply_creates_a_sibling_under_the_same_parent(db_path):
    chat_id = db.create_chat()
    user_id = db.add_message(chat_id, "user", "question")
    old_reply_id = db.add_message(chat_id, "assistant", "first answer")

    new_reply_id = db.add_message(chat_id, "assistant", "second answer", parent_id=user_id)

    siblings = db.list_siblings(old_reply_id)
    sibling_ids = {s["id"] for s in siblings}
    assert sibling_ids == {old_reply_id, new_reply_id}


def test_regenerating_a_reply_makes_the_new_one_active(db_path):
    chat_id = db.create_chat()
    user_id = db.add_message(chat_id, "user", "question")
    db.add_message(chat_id, "assistant", "first answer")

    new_reply_id = db.add_message(chat_id, "assistant", "second answer", parent_id=user_id)

    active = db.get_messages(chat_id)
    assert [m["content"] for m in active] == ["question", "second answer"]
    assert active[-1]["id"] == new_reply_id


def test_can_switch_back_to_the_old_reply_after_regenerating(db_path):
    chat_id = db.create_chat()
    user_id = db.add_message(chat_id, "user", "question")
    old_reply_id = db.add_message(chat_id, "assistant", "first answer")
    db.add_message(chat_id, "assistant", "second answer", parent_id=user_id)

    db.switch_branch(chat_id, old_reply_id)

    active = db.get_messages(chat_id)
    assert [m["content"] for m in active] == ["question", "first answer"]


def test_regenerating_does_not_touch_the_original_users_message(db_path):
    chat_id = db.create_chat()
    user_id = db.add_message(chat_id, "user", "question")
    db.add_message(chat_id, "assistant", "first answer")

    db.add_message(chat_id, "assistant", "second answer", parent_id=user_id)

    assert db.list_siblings(user_id) == [db.get_message(user_id)]


# ══════════════════════════════════════════════════════════════════
# Connection handling (regression test for the connection-leak fix)
# ══════════════════════════════════════════════════════════════════

def test_db_connection_closes_after_use(db_path):
    with db.db_connection() as conn:
        conn.execute("SELECT 1")

    # A closed sqlite3.Connection raises ProgrammingError on further use
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_db_connection_closes_even_on_exception(db_path):
    conn_ref = {}
    try:
        with db.db_connection() as conn:
            conn_ref["conn"] = conn
            raise ValueError("boom")
    except ValueError:
        pass

    with pytest.raises(sqlite3.ProgrammingError):
        conn_ref["conn"].execute("SELECT 1")


def test_many_sequential_calls_dont_leak_connections(db_path):
    """
    Regression test: with the old `with get_connection() as conn:` pattern,
    every one of these calls leaked an open sqlite3 connection/file handle.
    This just exercises a lot of calls in a row - it would previously build
    up hundreds of unclosed connections, though the failure mode (too many
    open files) is more of a long-running-process problem than something
    that reliably fails a single test run.
    """
    chat_id = db.create_chat()
    for i in range(200):
        db.add_message(chat_id, "user", f"msg {i}")
        db.is_ai_working(chat_id)
        db.get_message_count(chat_id)

    assert db.get_message_count(chat_id) == 200
