# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_chat_widget_console_input.py
==========================================
Regression test: sending a chat message must notify ai.log's sinks (so
the in-app console - app/console_widget.py - shows it as an input line).
Also guards against the stray, un-formatted debug print()
("Message sended") this replaced from ever creeping back in.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture
def chat_widget(qapp, tmp_path, monkeypatch):
    import app.chat_database as db
    db_path = str(tmp_path / "test_console_input_chats.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    chat_id = db.create_chat("Test chat")

    from app.chat_widget import ChatWidget
    widget = ChatWidget()
    widget.load_chat(chat_id)
    return widget, chat_id


def test_send_message_notifies_log_input_sink(chat_widget, monkeypatch):
    import ai.main as ai_main
    widget, chat_id = chat_widget

    # _send_message goes on to call ai_main.ia()/queue an AIWorker
    # irrelevant to this test and not something we want actually
    # running, so stub the one thing it needs to get past the guard
    # clauses without touching the network.
    monkeypatch.setattr(widget, "_start_ai_worker", lambda *a, **k: None, raising=False)

    captured = []
    import ai.log as log_module
    log_module.add_sink(lambda ts, level, message, input_data: captured.append((level, message)))
    try:
        widget.chatInput.setPlainText("search lo-fi music on youtube")
        widget._send_message()
    finally:
        log_module._sinks.clear()

    assert ("input", "search lo-fi music on youtube") in captured


def test_send_message_no_longer_prints_the_stray_debug_line(chat_widget, capsys):
    widget, chat_id = chat_widget
    widget.chatInput.setPlainText("hello")
    widget._send_message()

    assert "Message sended" not in capsys.readouterr().out
