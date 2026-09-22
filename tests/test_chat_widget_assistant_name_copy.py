# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_chat_widget_assistant_name_copy.py
================================================
Regression coverage for the chat input's placeholder text.

History: it used to be a hardcoded "Ask Kestrel…" (the product name),
which was wrong for anyone who'd renamed their assistant. That was
first fixed by making it dynamic ("Ask <assistant name>…"), but a
follow-up request asked for it to name the assistant NOT AT ALL - a plain, generic hint instead, same reasoning as the mic tooltip
still using the name but a static input field reading oddly with one
baked in. It should now read "Ask anything…" unconditionally,
regardless of the active profile's name or any rename, and never
contain "Kestrel" or any other assistant name.
"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture
def chat_widget(qapp, tmp_path, monkeypatch):
    import app.chat_database as db
    db_path = str(tmp_path / "test_copy_chats.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    chat_id = db.create_chat("Test chat")

    import ai.main as ai_main
    import ai.profiles as profiles
    monkeypatch.setattr(ai_main, "ENV_PATH", str(tmp_path / ".env"))
    original_env_var = os.environ.pop("KESTREL_ASSISTANT_NAME", None)
    monkeypatch.setattr(profiles, "_PROFILES_PATH", str(tmp_path / "profiles.json"))

    from app.chat_widget import ChatWidget
    widget = ChatWidget()
    widget.load_chat(chat_id)

    yield widget, chat_id, ai_main, profiles

    if original_env_var is None:
        os.environ.pop("KESTREL_ASSISTANT_NAME", None)
    else:
        os.environ["KESTREL_ASSISTANT_NAME"] = original_env_var


def test_placeholder_is_generic_on_construction(chat_widget):
    widget, chat_id, ai_main, profiles = chat_widget
    assert widget.chatInput.placeholderText() == "Ask anything…"


def test_placeholder_stays_generic_after_a_rename(chat_widget):
    widget, chat_id, ai_main, profiles = chat_widget
    ai_main.set_assistant_name("Nova")
    widget._refresh_assistant_name_copy()
    assert widget.chatInput.placeholderText() == "Ask anything…"
    assert "Nova" not in widget.chatInput.placeholderText()


def test_placeholder_stays_generic_on_profile_switch(chat_widget):
    widget, chat_id, ai_main, profiles = chat_widget
    other = profiles.create_profile(name="Juno", provider="gemini")
    widget._on_profile_picker_changed(other.id)
    assert widget.chatInput.placeholderText() == "Ask anything…"
    assert "Juno" not in widget.chatInput.placeholderText()
