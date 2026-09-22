# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_sidebar_search.py
===============================
Tests for the sidebar's chat-history search box (app/sidebar_widget.py's
chatSearchInput).

This used to sit behind a toggle button (btnSearch) up in the nav icon
row, alongside New Chat/Macros/Recorder/Mapping - all tools, so Search
stuck out as the odd one out there, and it used the exact same
magnifying-glass icon as the unrelated Mapping button right next to it.
It's now a small, always-visible field sitting directly above the chat
list it actually filters (see app/ui/sidebar_widget.ui's chatSearchArea),
with no toggle button/state to test at all - just the box itself.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_db(tmp_path, monkeypatch, name="sidebar_search_test.db"):
    import app.chat_database as db
    db_path = str(tmp_path / name)
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    return db


def test_search_input_is_always_visible(qapp, tmp_path, monkeypatch):
    _make_db(tmp_path, monkeypatch)
    from app.sidebar_widget import Sidebar
    sb = Sidebar()
    assert not sb.chatSearchInput.isHidden()
    assert not hasattr(sb, "btnSearch")


def test_search_filters_chat_list_case_insensitively(qapp, tmp_path, monkeypatch):
    db = _make_db(tmp_path, monkeypatch)
    db.create_chat("Weekly grocery run")
    db.create_chat("Quarterly budget review")
    db.create_chat("Grocery list for the party")

    from app.sidebar_widget import Sidebar
    sb = Sidebar()
    sb.chatSearchInput.setText("GROCERY")

    assert sb._search_query == "GROCERY"
    titles = {item._title_btn.text() for item in sb._chat_items.values()}
    assert titles == {"Weekly grocery run", "Grocery list for the party"}


def test_search_with_no_matches_shows_empty_state_not_an_error(qapp, tmp_path, monkeypatch):
    db = _make_db(tmp_path, monkeypatch)
    db.create_chat("Weekly grocery run")

    from app.sidebar_widget import Sidebar
    from PyQt6.QtWidgets import QLabel
    sb = Sidebar()
    sb.chatSearchInput.setText("nonexistent topic")

    assert len(sb._chat_items) == 0
    empty_labels = [
        w for w in sb.historyArea.findChildren(QLabel, "noSearchResultsLabel")
    ]
    assert len(empty_labels) == 1
    assert "nonexistent topic" in empty_labels[0].text()


def test_clearing_search_restores_normal_grouping(qapp, tmp_path, monkeypatch):
    db = _make_db(tmp_path, monkeypatch)
    db.create_chat("Weekly grocery run")
    db.create_chat("Quarterly budget review")

    from app.sidebar_widget import Sidebar
    from PyQt6.QtWidgets import QLabel
    sb = Sidebar()
    sb.chatSearchInput.setText("grocery")
    assert len(sb._chat_items) == 1

    sb.chatSearchInput.setText("")
    assert len(sb._chat_items) == 2
    section_labels = {
        w.text() for w in sb.historyArea.findChildren(QLabel, "sectionLabel")
    }
    assert "RECENT" in section_labels


def test_search_active_chat_stays_checked_after_filtering(qapp, tmp_path, monkeypatch):
    db = _make_db(tmp_path, monkeypatch)
    chat_id = db.create_chat("Weekly grocery run")
    db.create_chat("Quarterly budget review")

    from app.sidebar_widget import Sidebar
    sb = Sidebar()
    sb.set_active_chat(chat_id)
    sb.chatSearchInput.setText("grocery")

    assert sb._chat_items[chat_id]._title_btn.isChecked()
