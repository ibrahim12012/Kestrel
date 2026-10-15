# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/chat_context.py
=====================
What the model is given of a saved chat: the bridge between the chat
database (what is shown) and ai.main's conversation (what the model
reads).

The model used to have one conversation for the whole app session:
switching chats in the sidebar kept talking to the same history, and
reopening a chat after a restart gave the model none of it. Now each
chat is its own conversation (ai.main.use_chat), rebuilt from here when
it is not already loaded.

Not everything in a chat is conversation. Screenshot bubbles, terminal
blocks, file cards, run screenshot strips and status pills are stored as
[[MARKER:...]] rows for display; none of them is something either side
said, so they are left out. A "/compact" request and its status reply
are left out too.

When a chat has been compacted (ai/compaction.py), its stored summary
stands in for every message up to the one it covers.
"""

from __future__ import annotations

import re

import app.chat_database as db
from ai import compaction

# A whole message that is one of the display markers (see chat_widget.py).
_MARKER_ONLY_RE = re.compile(r"^\[\[(SCREENSHOT|RUN_SCREENSHOTS|SYSTEM|TERMINAL|FILE|SOURCES|COMPACTED)[:\]].*\]\]$", re.DOTALL)


def is_conversation_text(role: str, content: str) -> bool:
    text = (content or "").strip()
    if not text or role not in ("user", "assistant"):
        return False
    if _MARKER_ONLY_RE.match(text):
        return False
    if role == "user" and compaction.is_compact_command(text):
        return False
    return True


def _history_rows(chat_id: int, before_message_id: "int | None" = None) -> list:
    """
    The chat's active thread as sqlite rows, minus the message being
    answered right now: by default the last message if it is the user's
    (a normal send, or an edit, which ends the thread with the new
    message); with `before_message_id`, everything from that message on
    (a regenerate, which re-asks an earlier message while the old reply
    is still the active one).
    """
    rows = list(db.get_messages(chat_id))
    if before_message_id is not None:
        ids = [r["id"] for r in rows]
        if before_message_id in ids:
            rows = rows[: ids.index(before_message_id)]
    elif rows and rows[-1]["role"] == "user":
        rows = rows[:-1]
    return rows


def model_history(chat_id: int, before_message_id: "int | None" = None) -> "tuple[str, list[tuple[str, str]]]":
    """
    (summary, turns) to rebuild the model's conversation for `chat_id`:
    the stored summary if the chat was compacted, and the (role, text)
    turns after it. A summary whose last covered message is no longer on
    the chat's active thread (it was edited away, or the branch switched)
    no longer describes this conversation and is dropped.
    """
    rows = _history_rows(chat_id, before_message_id)
    summary = ""
    stored = db.get_chat_summary(chat_id)
    if stored is not None:
        ids = [r["id"] for r in rows]
        upto = stored["upto_message_id"]
        if upto in ids:
            summary = stored["summary"]
            rows = rows[ids.index(upto) + 1:]
        elif upto not in {r["id"] for r in db.get_messages(chat_id)}:
            # It covers messages that are not on this thread at all any
            # more. (Otherwise it covers messages past the cut, as when
            # the reply being regenerated is older than the summary;
            # then it just isn't used this time.)
            db.clear_chat_summary(chat_id)
    turns = [(r["role"], r["content"].strip()) for r in rows if is_conversation_text(r["role"], r["content"])]
    # A thread that opens with the assistant (a note before the first
    # question) cannot be sent as-is to a provider that wants the user
    # first; that turn is context the summary or the next turns carry.
    while turns and turns[0][0] != "user" and not summary:
        turns.pop(0)
    return summary, turns


def summary_upto_message_id(chat_id: int, kept_user_turns: int, before_message_id: "int | None" = None) -> "int | None":
    """
    The id of the last message a new summary covers: the message just
    before the kept part, which starts at the `kept_user_turns`-th last
    user message of the history. None when there is nothing before it.
    """
    rows = [r for r in _history_rows(chat_id, before_message_id)
            if is_conversation_text(r["role"], r["content"])]
    if not rows:
        return None
    if kept_user_turns <= 0:
        return rows[-1]["id"]
    seen = 0
    for i in range(len(rows) - 1, -1, -1):
        if rows[i]["role"] == "user":
            seen += 1
            if seen == kept_user_turns:
                return rows[i - 1]["id"] if i > 0 else None
    return None
