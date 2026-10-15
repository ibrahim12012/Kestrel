# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_chat_markers.py
============================
The chat's display markers for screenshots and sources
(app/chat_widget.py): who took a screenshot, an autonomous run's screens
in step order, and the sources card's payload surviving a round trip.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

import app.chat_widget as cw  # noqa: E402


def test_a_screenshot_marker_says_whether_the_assistant_took_it():
    mine = cw.SCREENSHOT_MARKER_RE.match("[[SCREENSHOT:C:/Users/me/shot 1.png]]")
    theirs = cw.SCREENSHOT_MARKER_RE.match("[[SCREENSHOT:C:/Users/me/shot 2.png|ai]]")
    assert mine.group(1) == "C:/Users/me/shot 1.png" and mine.group(2) is None
    assert theirs.group(1) == "C:/Users/me/shot 2.png" and theirs.group(2) == "ai"


def test_a_run_marker_carries_its_folder():
    match = cw.RUN_SCREENSHOTS_MARKER_RE.match("[[RUN_SCREENSHOTS:C:/shots/runs/20260927-101500]]")
    assert match.group(1) == "C:/shots/runs/20260927-101500"


def test_a_runs_screens_are_listed_in_step_order(tmp_path):
    for name in ("step_010.png", "step_002.png", "notes.txt", "step_001.png"):
        (tmp_path / name).write_bytes(b"")
    images = cw._run_screenshot_images(str(tmp_path))
    assert [os.path.basename(i.path) for i in images] == ["step_001.png", "step_002.png", "step_010.png"]
    assert "1" in images[0].caption and "3" in images[0].caption


def test_a_missing_run_folder_is_an_empty_strip(tmp_path):
    assert cw._run_screenshot_images(str(tmp_path / "gone")) == []


def test_sources_survive_a_round_trip_and_entries_without_a_url_are_dropped():
    entries = [
        {"url": "https://example.com/a?x=1&y=]]", "title": "Café ]] menu", "kind": "read"},
        {"url": "", "title": "no link"},
        {"url": "https://example.org/", "title": "Found", "kind": "found"},
    ]
    marker = cw.encode_sources_marker(entries)
    match = cw.SOURCES_MARKER_RE.match(marker)
    assert match is not None
    decoded = cw.decode_sources_marker(match.group(1))
    assert decoded == [entries[0], entries[2]]


def test_a_card_that_arrives_while_a_reply_streams_goes_above_it(qapp, tmp_path, monkeypatch):
    """Cards (sources, screenshots, terminal output) are emitted after the
    stream ends but before the reply is saved. Placed below the streaming
    bubble they showed in a different order than after a reload, and they
    took the reply's id and Regenerate button (attach_message_id stamps
    the last row)."""
    import app.chat_database as db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "chats.db"))
    db.init_db()
    chat_id = db.create_chat("t")
    db.add_message(chat_id, "user", "what does example.com say?")

    widget = cw.ChatWidget()
    widget.load_chat(chat_id)
    widget.begin_streaming_response(chat_id)
    reply_row = widget._streaming_row
    card = widget._append_bubble(
        cw.encode_sources_marker([{"url": "https://example.com/", "title": "Example"}]), "assistant")
    widget.finish_streaming_response(chat_id, "It says hello.")
    widget.attach_message_id(chat_id, 4242)

    layout = widget._messages_layout
    assert layout.indexOf(card) < layout.indexOf(reply_row)
    assert widget._last_message_row() is reply_row
    assert reply_row.property("message_id") == 4242
