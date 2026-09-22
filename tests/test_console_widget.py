# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_console_widget.py
==============================
Tests for app/console_widget.py - the in-app console that mirrors
ai.log.log() output. _classify/_line_html/_input_html are pure functions
(no Qt needed) so most of this is plain unit testing; the widget-level
tests use qapp to confirm the ai.log sink is wired correctly and cleaned
up on close.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.log as log_module
import ai.main as ai_main
from app.console_widget import ConsoleWidget, _classify, _input_html, _line_html


# ══════════════════════════════════════════════════════════════════
# _classify - pure logic
# ══════════════════════════════════════════════════════════════════

def test_classify_bracketed_message_is_tool():
    assert _classify("[tool_name_action] Saved macro: tool_search_lofi_youtube") == "tool"


def test_classify_macro_tag_is_tool():
    assert _classify("[Macro] Run: tool_example") == "tool"


def test_classify_bare_assistant_reply_is_reply():
    # The ASSISTANT's name, which is deliberately not the app's (see
    # branding.py) - _classify keys off whatever the assistant is called.
    name = ai_main.get_assistant_name()
    assert _classify(f"{name}: Reddit is a network of community-run boards.") == "reply"


def test_classify_bracketed_assistant_reply_is_still_reply_not_tool():
    """
    ai/main.py's autonomous-mode reply print is "[autonomous] <name>:
    ...". A reply is a reply regardless of which internal loop produced
    it - the bracket here is incidental provenance, not the semantic tag.
    """
    name = ai_main.get_assistant_name()
    assert _classify(f"[autonomous] {name}: done searching.") == "reply"


def test_classify_waiting_is_idle():
    assert _classify("Waiting...") == "idle"


def test_classify_waiting_with_surrounding_whitespace_is_idle():
    assert _classify("  Waiting...  ") == "idle"


def test_classify_unbracketed_plain_message_is_plain():
    assert _classify("Clicking 'address bar'") == "plain"


def test_classify_empty_message_is_plain():
    assert _classify("") == "plain"


# ══════════════════════════════════════════════════════════════════
# HTML rendering
# ══════════════════════════════════════════════════════════════════

def test_line_html_matches_expected_dom_shape_for_tool():
    out = _line_html("11:55:53.180", "info", "[tool_name_action] Saved macro: tool_search_lofi_youtube")
    assert 'class="console-line"' in out
    assert 'class="log-stamp"' in out
    assert "11:55:53.180" in out
    assert 'class="tag-tool"' in out
    assert "[tool_name_action] Saved macro: tool_search_lofi_youtube" in out


def test_line_html_reply_gets_reply_class():
    out = _line_html("09:12:03.640", "info", f"{ai_main.get_assistant_name()}: a reply.")
    assert 'class="tag-reply"' in out


def test_line_html_idle_gets_idle_class():
    out = _line_html("11:55:44.780", "info", "Waiting...")
    assert 'class="tag-idle"' in out


def test_line_html_plain_gets_plain_class():
    out = _line_html("11:55:19.850", "info", "Clicking 'address bar'")
    assert 'class="tag-plain"' in out


def test_line_html_escapes_html_special_characters():
    out = _line_html("00:00:00.000", "info", "[tool] a < b && c > d")
    assert "<b>" not in out  # would corrupt the DOM if unescaped
    assert "&lt;" in out
    assert "&gt;" in out


def test_input_html_matches_expected_dom_shape():
    out = _input_html("search lo-fi music on youtube")
    assert 'class="console-line input-line"' in out
    assert 'class="arrow"' in out
    assert "search lo-fi music on youtube" in out
    # No log-stamp at all for input lines - they're never timestamped per spec.
    assert "log-stamp" not in out


def test_input_html_escapes_html_special_characters():
    out = _input_html("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# ══════════════════════════════════════════════════════════════════
# Widget wiring
# ══════════════════════════════════════════════════════════════════

def test_console_widget_registers_a_sink_on_construction(qapp):
    before = list(log_module._sinks)
    widget = ConsoleWidget()
    try:
        assert len(log_module._sinks) == len(before) + 1
    finally:
        widget._unregister_sink()


def test_console_widget_removes_its_sink_on_close(qapp):
    widget = ConsoleWidget()
    before_close = list(log_module._sinks)
    widget._unregister_sink()
    assert len(log_module._sinks) == len(before_close) - 1


def test_console_widget_appends_a_line_when_log_is_called(qapp):
    widget = ConsoleWidget()
    try:
        log_module.log("[demo] hello from a test")
        assert "hello from a test" in widget._view.toPlainText()
    finally:
        widget._unregister_sink()


def test_console_widget_log_input_appends_without_a_timestamp(qapp):
    widget = ConsoleWidget()
    try:
        widget.log_input("search lo-fi music on youtube")
        assert "search lo-fi music on youtube" in widget._view.toPlainText()
    finally:
        widget._unregister_sink()


def test_console_widget_replays_history_recorded_before_construction(qapp):
    """
    Regression test for "the console at startup is blank" - the widget
    is only built lazily, the first time someone opens it from the tray
    menu, so anything logged before that (app startup, tool calls, an
    entire autonomous run) had already happened and was lost - a sink
    only receives calls made AFTER add_sink() registers it, and this
    widget didn't exist yet to register one.
    """
    log_module._history.clear()
    log_module.log("[startup] this happened before the console was ever opened")
    log_module.log_input("an earlier message, also before the console existed")

    widget = ConsoleWidget()
    try:
        text = widget._view.toPlainText()
        assert "this happened before the console was ever opened" in text
        assert "an earlier message, also before the console existed" in text
    finally:
        widget._unregister_sink()
