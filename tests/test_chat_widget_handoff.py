# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_chat_widget_handoff.py
===================================
Regression test for the human-handoff "Continue" card surviving a chat
reload.

Bug: load_chat() rebuilds the message list purely from what's in the
database (app/chat_database.py), and the interactive Continue card was
only ever a live, in-memory widget - the DB only ever got a plain-text
note (see MainWindow._on_human_handoff_needed). So switching away from
the chat and back (or any other reload) while an autonomous run was
paused waiting on a human silently destroyed the only way to resume it:
the note stayed, visible as inert history text, but its button was gone.

Fix: ChatWidget now tracks which chat (if any) has an unresolved handoff
independently of the message widgets (_pending_handoff_chat_id /
_pending_handoff_reason, set by show_human_handoff /
note_pending_human_handoff, cleared by clear_pending_human_handoff), and
load_chat() re-attaches a working card whenever it detects one for the
chat being loaded.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture
def chat_widget(qapp, tmp_path, monkeypatch):
    import app.chat_database as db
    db_path = str(tmp_path / "test_handoff_chats.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    chat_id = db.create_chat("Test chat")

    from app.chat_widget import ChatWidget
    from PyQt6.QtWidgets import QPushButton
    widget = ChatWidget()
    widget.load_chat(chat_id)
    return widget, chat_id, QPushButton


def test_handoff_card_survives_reload(chat_widget):
    widget, chat_id, QPushButton = chat_widget

    widget.show_human_handoff(chat_id, "A CAPTCHA is on screen")
    assert widget._pending_handoff_row is not None
    assert widget._pending_handoff_row.findChild(QPushButton, "handoffContinue") is not None

    # Reload the same chat, standing in for the user switching away and
    # back (or any other trigger for load_chat) while still paused.
    widget.load_chat(chat_id)

    assert widget._pending_handoff_row is not None, "Continue card was lost after reload"
    btn = widget._pending_handoff_row.findChild(QPushButton, "handoffContinue")
    assert btn is not None, "Continue button was lost after reload"

    received = []
    widget.human_handoff_response.connect(lambda cid: received.append(cid))
    btn.click()

    assert received == [chat_id]
    assert widget._pending_handoff_chat_id is None
    assert widget._pending_handoff_reason is None


def test_note_pending_human_handoff_is_picked_up_on_first_load(chat_widget):
    """
    Covers the "handoff happened on a chat that wasn't even open yet"
    case (MainWindow._on_human_handoff_needed calls this unconditionally,
    before deciding whether to also build the live card).
    """
    widget, chat_id, QPushButton = chat_widget

    widget.note_pending_human_handoff(chat_id, "MFA code requested")
    assert widget._pending_handoff_row is None  # no live card yet

    widget.load_chat(chat_id)

    assert widget._pending_handoff_row is not None
    assert widget._pending_handoff_row.findChild(QPushButton, "handoffContinue") is not None


def test_clear_pending_human_handoff_prevents_stale_reattach(chat_widget):
    widget, chat_id, QPushButton = chat_widget

    widget.show_human_handoff(chat_id, "A CAPTCHA is on screen")
    widget.clear_pending_human_handoff(chat_id)

    widget.load_chat(chat_id)

    assert widget._pending_handoff_row is None, (
        "reloading after the run finished/errored should not resurrect a stale card"
    )
