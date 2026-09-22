# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_chat_regenerate.py
================================
Tests for "Regenerate" on an assistant reply - app/chat_widget.py's
Regenerate button + app/main_window.py's _on_regenerate_requested flow.
Branching-model coverage (db.add_message with an explicit parent_id
creating a sibling) lives in tests/test_chat_database.py; these tests
cover the Qt wiring on top of it: button enablement, the click ->
signal -> AIWorker -> DB -> reload path, and the error path.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore import QObject, pyqtSignal


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    import app.chat_database as db
    path = str(tmp_path / "regenerate_test.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    return db


def _find_row_by_message_id(chat, message_id):
    for i in range(chat._messages_layout.count()):
        item = chat._messages_layout.itemAt(i)
        widget = item.widget() if item is not None else None
        if widget is not None and widget.property("message_id") == message_id:
            return widget
    return None


# ══════════════════════════════════════════════════════════════════
# ChatWidget: button enablement
# ══════════════════════════════════════════════════════════════════

def test_regenerate_enabled_only_on_the_last_assistant_message(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    import app.chat_database as db

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    db.add_message(chat_id, "user", "first question")
    old_reply_id = db.add_message(chat_id, "assistant", "first answer")
    db.add_message(chat_id, "user", "second question")
    last_reply_id = db.add_message(chat_id, "assistant", "second answer")
    chat.load_chat(chat_id)

    old_row = _find_row_by_message_id(chat, old_reply_id)
    last_row = _find_row_by_message_id(chat, last_reply_id)

    assert not old_row.findChild(QPushButton, "msgRegenerateBtn").isEnabled()
    assert last_row.findChild(QPushButton, "msgRegenerateBtn").isEnabled()


def test_regenerate_button_not_offered_on_user_messages(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    import app.chat_database as db

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    user_id = db.add_message(chat_id, "user", "only message so far")
    chat.load_chat(chat_id)

    row = _find_row_by_message_id(chat, user_id)
    assert row.findChild(QPushButton, "msgRegenerateBtn") is None


def test_clicking_regenerate_emits_signal_with_chat_and_message_id(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    import app.chat_database as db

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    db.add_message(chat_id, "user", "question")
    reply_id = db.add_message(chat_id, "assistant", "answer")
    chat.load_chat(chat_id)

    emitted = []
    chat.regenerate_requested.connect(lambda cid, mid: emitted.append((cid, mid)))

    row = _find_row_by_message_id(chat, reply_id)
    row.findChild(QPushButton, "msgRegenerateBtn").click()

    assert emitted == [(chat_id, reply_id)]


def test_attach_message_id_enables_new_reply_and_disables_previous(qapp, fresh_db):
    """
    Simulates two successive live replies in the same session (no
    load_chat reload in between, same as a real back-to-back
    conversation) - attach_message_id is the only thing that ever
    flips a live-streamed reply's Regenerate button on, and it must
    also turn the PREVIOUS reply's off since it's no longer the leaf.
    """
    from app.chat_widget import ChatWidget
    import app.chat_database as db

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    chat._current_chat_id = chat_id

    db.add_message(chat_id, "user", "first question")
    first_reply_id = db.add_message(chat_id, "assistant", "first answer")
    row1 = chat.reveal_response(chat_id, "first answer")
    chat.attach_message_id(chat_id, first_reply_id)

    btn1 = _find_row_by_message_id(chat, first_reply_id).findChild(QPushButton, "msgRegenerateBtn")
    assert btn1.isEnabled()

    db.add_message(chat_id, "user", "second question")
    second_reply_id = db.add_message(chat_id, "assistant", "second answer")
    chat.reveal_response(chat_id, "second answer")
    chat.attach_message_id(chat_id, second_reply_id)

    btn2 = _find_row_by_message_id(chat, second_reply_id).findChild(QPushButton, "msgRegenerateBtn")
    assert not btn1.isEnabled()
    assert btn2.isEnabled()


def test_attach_message_id_is_a_noop_for_a_backgrounded_chat(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    import app.chat_database as db

    chat = ChatWidget()
    other_chat_id = db.create_chat("Other chat")
    chat._current_chat_id = other_chat_id  # a DIFFERENT chat is showing

    background_chat_id = db.create_chat("Background chat")
    reply_id = db.add_message(background_chat_id, "assistant", "reply")

    # Should not raise, and should not touch the currently-shown chat's rows.
    chat.attach_message_id(background_chat_id, reply_id)


# ══════════════════════════════════════════════════════════════════
# MainWindow: the full regenerate request/response flow
# ══════════════════════════════════════════════════════════════════

class _FakeAIWorker(QObject):
    finished = pyqtSignal(int, str)
    error = pyqtSignal(int, str)
    start = pyqtSignal(int, str)
    autonomous_suggested = pyqtSignal(int, str)
    screenshot_captured = pyqtSignal(int, str)

    response_text = "regenerated answer"
    should_error = False
    seen_prompts = []

    def __init__(self):
        super().__init__()
        self.start.connect(self._run)

    def _run(self, chat_id, prompt):
        type(self).seen_prompts.append(prompt)
        if type(self).should_error:
            self.error.emit(chat_id, "simulated provider error")
        else:
            self.finished.emit(chat_id, type(self).response_text)


def _pump_until(qapp, predicate, timeout=2.0):
    import time
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        qapp.processEvents()


def test_regenerate_sends_the_parent_users_message_as_the_prompt(qapp, fresh_db, monkeypatch):
    import app.main_window as main_window_module
    from app.main_window import MainWindow
    import app.chat_database as db

    _FakeAIWorker.seen_prompts = []
    _FakeAIWorker.should_error = False
    _FakeAIWorker.response_text = "regenerated answer"
    monkeypatch.setattr(main_window_module, "AIWorker", _FakeAIWorker)

    window = MainWindow()
    chat_id = db.create_chat("Test chat")
    window.chat._current_chat_id = chat_id
    db.add_message(chat_id, "user", "what's the weather")
    old_reply_id = db.add_message(chat_id, "assistant", "first answer")

    window._on_regenerate_requested(chat_id, old_reply_id)
    _pump_until(qapp, lambda: _FakeAIWorker.seen_prompts)

    assert _FakeAIWorker.seen_prompts == ["what's the weather"]


def test_regenerate_success_creates_sibling_and_switches_active_branch(qapp, fresh_db, monkeypatch):
    import app.main_window as main_window_module
    from app.main_window import MainWindow
    import app.chat_database as db

    _FakeAIWorker.seen_prompts = []
    _FakeAIWorker.should_error = False
    _FakeAIWorker.response_text = "regenerated answer"
    monkeypatch.setattr(main_window_module, "AIWorker", _FakeAIWorker)

    window = MainWindow()
    chat_id = db.create_chat("Test chat")
    window.chat._current_chat_id = chat_id
    user_id = db.add_message(chat_id, "user", "question")
    old_reply_id = db.add_message(chat_id, "assistant", "first answer")
    window.chat.load_chat(chat_id)

    window._on_regenerate_requested(chat_id, old_reply_id)
    _pump_until(qapp, lambda: len(db.list_siblings(old_reply_id)) > 1)

    active = db.get_messages(chat_id)
    assert [m["content"] for m in active] == ["question", "regenerated answer"]

    # Old reply survives as a switchable-back-to branch.
    siblings = db.list_siblings(old_reply_id)
    assert {s["content"] for s in siblings} == {"first answer", "regenerated answer"}


def test_regenerate_error_does_not_touch_the_branch_tree(qapp, fresh_db, monkeypatch):
    import app.main_window as main_window_module
    from app.main_window import MainWindow
    import app.chat_database as db

    _FakeAIWorker.seen_prompts = []
    _FakeAIWorker.should_error = True
    monkeypatch.setattr(main_window_module, "AIWorker", _FakeAIWorker)

    window = MainWindow()
    chat_id = db.create_chat("Test chat")
    window.chat._current_chat_id = chat_id
    db.add_message(chat_id, "user", "question")
    old_reply_id = db.add_message(chat_id, "assistant", "first answer")
    window.chat.load_chat(chat_id)

    window._on_regenerate_requested(chat_id, old_reply_id)
    _pump_until(qapp, lambda: _FakeAIWorker.seen_prompts)
    # Give the error signal's queued delivery a moment to land too.
    _pump_until(qapp, lambda: len(db.get_messages(chat_id)) > 2)

    # No sibling was ever created - the old reply is still the only version.
    assert len(db.list_siblings(old_reply_id)) == 1
    active = db.get_messages(chat_id)
    assert active[1]["content"] == "first answer"
    assert "AI error" in active[-1]["content"]
