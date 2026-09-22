# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_log.py
===================
Tests for ai/log.py - the timestamp+level prefix wrapper every ai/*.py
module aliases in as `print` (see that module's docstring), so every
EXISTING print(f"[tag] ...") call site across the codebase gets
consistent, ordered, thread-interleaving-safe console output for free.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.log as log_module


def test_log_prefixes_timestamp_and_level(capsys):
    log_module.log("[demo] hello world")
    out = capsys.readouterr().out
    assert re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3} INFO  \[demo\] hello world\n$", out)


def test_log_warn_uses_warn_level(capsys):
    log_module.log_warn("[demo] careful")
    out = capsys.readouterr().out
    assert " WARN  [demo] careful" in out


def test_log_error_uses_error_level(capsys):
    log_module.log_error("[demo] broken")
    out = capsys.readouterr().out
    assert " ERROR [demo] broken" in out


def test_log_default_message_is_empty_string(capsys):
    # Bare print() calls (blank-line spacing) must still work under the alias.
    log_module.log()
    out = capsys.readouterr().out
    assert re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3} INFO  \n$", out)


def test_ai_modules_alias_print_to_log():
    """
    The whole point of ai/log.py is that every EXISTING print(f"[tag]...")
    call site in these modules gets the timestamp+level prefix with zero
    per-call-site changes - which only works if each module actually
    shadows the builtin. Confirms the aliasing import is really there,
    not just that ai/log.py itself works in isolation.
    """
    import ai.main as ai_main
    import ai.actions as actions
    import ai.macro_store as macro_store
    import ai.macro_generalizer as macro_generalizer

    for module in (ai_main, actions, macro_store, macro_generalizer):
        assert module.print is log_module.log, (
            f"{module.__name__} isn't aliasing print to ai.log.log - "
            f"its existing print(...) call sites won't get formatted"
        )


def test_log_input_data_is_appended_when_provided(capsys):
    log_module.log("[demo] typed text", input_data={"text_value": "youtube"})
    out = capsys.readouterr().out
    assert "[demo] typed text" in out
    assert "input: {'text_value': 'youtube'}" in out


def test_log_without_input_data_has_no_input_suffix(capsys):
    log_module.log("[demo] plain message")
    out = capsys.readouterr().out
    assert "input:" not in out


def test_log_never_emits_ansi_escapes_under_capsys():
    """
    capsys replaces sys.stdout with something that isn't a real tty - _supports_ansi() must recognize that and never emit escape codes
    into captured (or piped, or file-redirected) output. This is what
    keeps test_log_prefixes_timestamp_and_level's exact-match regex
    valid even after color support was added.
    """
    import io
    log_module._ansi_enabled = None  # force re-detection against the current stdout
    fake_stdout = io.StringIO()  # no isatty()==True, unlike a real terminal
    old_stdout = sys.stdout
    sys.stdout = fake_stdout
    try:
        log_module.log("[demo] no color here")
    finally:
        sys.stdout = old_stdout
        log_module._ansi_enabled = None
    out = fake_stdout.getvalue()
    assert "\033[" not in out


def test_supports_ansi_respects_no_color_env(monkeypatch):
    monkeypatch.setenv("KESTREL_NO_COLOR", "1")
    log_module._ansi_enabled = None
    try:
        assert log_module._supports_ansi() is False
    finally:
        log_module._ansi_enabled = None


# ══════════════════════════════════════════════════════════════════
# Sinks - app/console_widget.py's in-app console registers one of these
# instead of hijacking sys.stdout.
# ══════════════════════════════════════════════════════════════════

def test_add_sink_is_notified_on_every_log_call():
    calls = []
    log_module.add_sink(lambda ts, level, message, input_data: calls.append((level, message, input_data)))
    try:
        log_module.log("[demo] hello", level="warn", input_data={"x": 1})
    finally:
        log_module._sinks.clear()

    assert calls == [("warn", "[demo] hello", {"x": 1})]


def test_add_sink_receives_raw_message_not_the_ansi_formatted_one():
    """
    The sink must get the message TEXT, not a copy of whatever ANSI/plain
    string got printed to the real console - a widget rendering its own
    HTML needs the raw pieces, not console escape codes to strip back out.
    """
    calls = []
    log_module.add_sink(lambda ts, level, message, input_data: calls.append(message))
    try:
        log_module.log("[demo] no escape codes here")
    finally:
        log_module._sinks.clear()

    assert calls == ["[demo] no escape codes here"]
    assert "\033[" not in calls[0]


def test_add_sink_is_idempotent():
    calls = []
    sink = lambda ts, level, message, input_data: calls.append(message)
    log_module.add_sink(sink)
    log_module.add_sink(sink)  # registering twice must not double-notify
    try:
        log_module.log("[demo] once")
    finally:
        log_module._sinks.clear()

    assert calls == ["[demo] once"]


def test_remove_sink_stops_notifications():
    calls = []
    sink = lambda ts, level, message, input_data: calls.append(message)
    log_module.add_sink(sink)
    log_module.remove_sink(sink)
    log_module.log("[demo] should not arrive")

    assert calls == []


def test_remove_sink_never_registered_is_a_no_op():
    log_module.remove_sink(lambda *a: None)  # must not raise


def test_a_broken_sink_does_not_break_logging_or_other_sinks(capsys):
    good_calls = []
    log_module.add_sink(lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    log_module.add_sink(lambda ts, level, message, input_data: good_calls.append(message))
    try:
        log_module.log("[demo] still works")
    finally:
        log_module._sinks.clear()

    assert good_calls == ["[demo] still works"]
    assert "[demo] still works" in capsys.readouterr().out


# ══════════════════════════════════════════════════════════════════
# History - app/console_widget.py's backlog replay (opened lazily, so
# without this it always started blank no matter what had already
# logged before it was constructed).
# ══════════════════════════════════════════════════════════════════

def test_get_history_records_log_calls_even_with_no_sinks_registered():
    log_module._history.clear()
    log_module.log("[demo] recorded before any console existed")

    history = log_module.get_history()

    assert any(msg == "[demo] recorded before any console existed" for _, _, msg, _ in history)


def test_get_history_records_log_input_calls():
    log_module._history.clear()
    log_module.log_input("play some music")

    history = log_module.get_history()

    assert any(level == "input" and msg == "play some music" for _, level, msg, _ in history)


def test_get_history_returns_a_copy_not_the_live_buffer():
    log_module._history.clear()
    log_module.log("[demo] one")
    history = log_module.get_history()
    history.append(("fake", "info", "not real", None))

    assert ("fake", "info", "not real", None) not in log_module.get_history()


def test_history_is_capped_at_max_size():
    log_module._history.clear()
    for i in range(log_module._MAX_HISTORY + 50):
        log_module.log(f"[demo] line {i}")

    assert len(log_module.get_history()) == log_module._MAX_HISTORY
    # Oldest entries are the ones dropped - the buffer keeps the MOST RECENT ones.
    newest = log_module.get_history()[-1]
    assert f"line {log_module._MAX_HISTORY + 49}" in newest[2]
