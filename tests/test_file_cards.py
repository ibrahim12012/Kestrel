# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_file_cards.py
=========================
The "Show in folder" card the chat shows when the AI writes a file
(tool_generate_file / tool_merge_files_to_pdf):

  ai.actions  -> ai.main.queue_file_output()      (queueing)
  ai.main     -> pop_pending_file_outputs()       (draining)
  app.chat_widget encode/decode_file_marker       (persistence format)
  app.file_reveal.reveal_in_explorer              (opening Explorer)
  app.chat_widget._make_file_row                  (the card + button)
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import ai.actions as actions
import ai.main as ai_main
import ai.file_generation as file_generation


@pytest.fixture(autouse=True)
def _clean_queue_and_redirect_output(tmp_path, monkeypatch):
    """Empty the shared pending-file queue around every test and keep
    generated files out of the real Documents\\Kestrel Files folder."""
    ai_main.pop_pending_file_outputs()
    out_dir = tmp_path / "Kestrel Files"
    out_dir.mkdir()
    monkeypatch.setattr(file_generation, "generated_files_dir", lambda: str(out_dir))
    monkeypatch.setattr(actions.os, "startfile", lambda p: None, raising=False)
    yield out_dir
    ai_main.pop_pending_file_outputs()


# ── queue ────────────────────────────────────────────────────────────

def test_pop_returns_entries_in_order_and_clears():
    ai_main.queue_file_output("C:/a.txt", "created")
    ai_main.queue_file_output("C:/b.pdf", "merged")

    assert ai_main.pop_pending_file_outputs() == [
        {"path": "C:/a.txt", "action": "created"},
        {"path": "C:/b.pdf", "action": "merged"},
    ]
    assert ai_main.pop_pending_file_outputs() == []


# ── tools queue a card ───────────────────────────────────────────────

def test_generate_file_queues_a_card_for_the_saved_path(_clean_queue_and_redirect_output):
    result = actions.tool_generate_file("notes", "txt", "hello")

    entries = ai_main.pop_pending_file_outputs()
    assert len(entries) == 1
    assert entries[0]["action"] == "created"
    assert entries[0]["path"] == str(_clean_queue_and_redirect_output / "notes.txt")
    assert entries[0]["path"] in result


def test_generate_file_queues_even_if_auto_open_fails(monkeypatch):
    def boom(_p):
        raise OSError("no associated app")
    monkeypatch.setattr(actions.os, "startfile", boom, raising=False)

    actions.tool_generate_file("notes", "txt", "hello")

    assert len(ai_main.pop_pending_file_outputs()) == 1


def test_generate_file_queues_nothing_on_failure():
    actions.tool_generate_file("notes", "txt", "   ")   # empty content -> rejected
    actions.tool_generate_file("", "txt", "hello")      # no filename -> rejected

    assert ai_main.pop_pending_file_outputs() == []


def test_merge_files_queues_a_merged_card(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("merge me", encoding="utf-8")

    actions.tool_merge_files_to_pdf([str(src)], "combined")

    entries = ai_main.pop_pending_file_outputs()
    assert len(entries) == 1
    assert entries[0]["action"] == "merged"
    assert entries[0]["path"].endswith("combined.pdf")


def test_a_broken_queue_never_fails_the_tool(monkeypatch):
    """A UI nicety erroring must not turn a successful save into a reported failure."""
    def boom(*_a, **_k):
        raise RuntimeError("queue exploded")
    monkeypatch.setattr(ai_main, "queue_file_output", boom)

    result = actions.tool_generate_file("notes", "txt", "hello")

    assert result.startswith("File saved to")


# ── marker round-trip ────────────────────────────────────────────────

def test_file_marker_round_trips_awkward_paths():
    pytest.importorskip("PyQt6")
    import app.chat_widget as cw

    path = r"C:\Users\Anaïs\Documents\Kestrel Files\rapport [final]]v2.docx"
    marker = cw.encode_file_marker(path, "created")

    m = cw.FILE_MARKER_RE.match(marker)
    assert m is not None
    assert cw.decode_file_marker(m.group(1)) == {"path": path, "action": "created"}


# ── reveal_in_explorer ───────────────────────────────────────────────

def test_reveal_selects_the_file_on_windows(tmp_path, monkeypatch):
    import app.file_reveal as fr
    f = tmp_path / "My Report.docx"
    f.write_text("x", encoding="utf-8")

    calls = []
    monkeypatch.setattr(fr.sys, "platform", "win32")
    monkeypatch.setattr(fr.subprocess, "Popen", lambda cmd, *a, **k: calls.append(cmd))

    assert fr.reveal_in_explorer(str(f)) is True
    assert calls == [f'explorer /select,"{os.path.normpath(str(f))}"']


def test_reveal_opens_just_the_folder_when_the_file_is_gone(tmp_path, monkeypatch):
    import app.file_reveal as fr
    opened = []
    monkeypatch.setattr(fr.sys, "platform", "win32")
    monkeypatch.setattr(fr.os, "startfile", lambda p: opened.append(p), raising=False)

    assert fr.reveal_in_explorer(str(tmp_path / "deleted.docx")) is True
    assert opened == [str(tmp_path)]


def test_reveal_returns_false_when_nothing_exists(tmp_path):
    import app.file_reveal as fr

    assert fr.reveal_in_explorer(str(tmp_path / "nope" / "ghost.docx")) is False
    assert fr.reveal_in_explorer("") is False


# ── the card itself ──────────────────────────────────────────────────

def test_file_card_shows_name_and_button_reveals_the_path(qapp, monkeypatch):
    import app.chat_widget as cw
    from PyQt6.QtWidgets import QLabel, QPushButton

    revealed = []
    monkeypatch.setattr(cw, "reveal_in_explorer", lambda p: revealed.append(p) or True)

    path = r"C:\Users\me\Documents\Kestrel Files\report.docx"
    row = cw._make_file_row(path, "created", "dark")

    assert row.findChild(QLabel, "fileCardName").text() == os.path.basename(path)
    btn = row.findChild(QPushButton, "fileRevealBtn")
    assert btn.text() == "Show in folder"

    btn.click()

    assert revealed == [path]
    assert btn.isEnabled()


def test_file_card_button_reports_a_missing_folder(qapp, monkeypatch):
    import app.chat_widget as cw
    from PyQt6.QtWidgets import QPushButton

    monkeypatch.setattr(cw, "reveal_in_explorer", lambda p: False)
    row = cw._make_file_row("C:/gone/report.docx", "created", "dark")
    btn = row.findChild(QPushButton, "fileRevealBtn")

    btn.click()

    assert btn.text() == "Folder not found"
    assert not btn.isEnabled()


def test_append_bubble_renders_a_card_for_a_file_marker(qapp, tmp_path, monkeypatch):
    import app.chat_database as db
    import app.chat_widget as cw
    from PyQt6.QtWidgets import QPushButton

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "chats.db"))
    db.init_db()
    widget = cw.ChatWidget()

    row = widget._append_bubble(cw.encode_file_marker("C:/x/report.docx", "merged"), "assistant")

    assert row.findChild(QPushButton, "fileRevealBtn") is not None


def test_append_bubble_falls_back_to_text_on_a_corrupt_file_marker(qapp, tmp_path, monkeypatch):
    import app.chat_database as db
    import app.chat_widget as cw
    from PyQt6.QtWidgets import QPushButton

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "chats.db"))
    db.init_db()
    widget = cw.ChatWidget()

    row = widget._append_bubble("[[FILE:bm90IGpzb24=]]", "assistant")   # base64 of "not json"

    assert row.findChild(QPushButton, "fileRevealBtn") is None
