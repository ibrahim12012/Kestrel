# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_ai_worker_compaction.py
====================================
AIWorker._on_compacted: a compaction is kept with the chat (so reopening
it starts from the summary), and the chat is told, except after /compact,
whose reply already says it.
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

import app.chat_database as db  # noqa: E402
from ai.compaction import CompactionResult  # noqa: E402
from app.workers.ai_worker import AIWorker  # noqa: E402


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "chats.db"))
    db.init_db()
    chat_id = db.create_chat("t")
    ids = []
    for n in range(1, 7):
        ids.append(db.add_message(chat_id, "user", f"u{n}"))
        ids.append(db.add_message(chat_id, "assistant", f"a{n}"))
    db.add_message(chat_id, "user", "being answered")
    return chat_id, ids


def _worker():
    emitted = []
    return types.SimpleNamespace(
        history_before_id=None,
        compacted=types.SimpleNamespace(emit=lambda *args: emitted.append(args)),
        emitted=emitted,
    )


def _result(requested=False):
    return CompactionResult(summary="the gist", summarized_turns=4, kept_turns=8,
                            tokens_before=9000, tokens_after=2000, kept_user_turns=4,
                            requested=requested)


def test_a_compaction_is_kept_with_the_chat_and_announced(chat):
    chat_id, ids = chat
    worker = _worker()
    AIWorker._on_compacted(worker, chat_id, _result())
    stored = db.get_chat_summary(chat_id)
    assert stored["summary"] == "the gist"
    assert stored["upto_message_id"] == ids[3]          # u1 a1 u2 a2 summarised; u3 onward kept
    assert worker.emitted == [(chat_id, 4, 9000, 2000)]


def test_after_compact_the_reply_is_the_announcement(chat):
    chat_id, _ = chat
    worker = _worker()
    AIWorker._on_compacted(worker, chat_id, _result(requested=True))
    assert db.get_chat_summary(chat_id) is not None
    assert worker.emitted == []
