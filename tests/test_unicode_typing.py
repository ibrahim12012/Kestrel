# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_unicode_typing.py
============================
Typing text that is not plain ASCII.

pyautogui.write() cannot type an accented character: it looks each one
up in a table built from ASCII plus named keys, and a character that is
not there types nothing at all, silently. "café" came out "caf".

The old workaround routed `string.punctuation` through the clipboard -
32 ASCII characters, containing no accent, no curly quote, no en dash,
no currency symbol and nothing non-Latin. So every character that
actually broke fell through to the call that drops it.

These cover the routing decision, which is testable anywhere. SendInput
itself is Windows-only and is not exercised here; on this platform
send_char returns False and the fallback is what runs, which is exactly
the path a machine without ctypes would take.
"""

import os
import string
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.actions as actions
from ai import unicode_input

ACCENTED = "éèêëàâäùûüôöîïçñáíóúãõåøæœß"
TYPOGRAPHIC = "‘’“”–—…«»"
SYMBOLS = "€£¥°§±×÷µ¶"


# ── the routing decision ─────────────────────────────────────────────

def test_accented_characters_do_not_go_to_pyautogui_write(mock_pyautogui, monkeypatch):
    """The whole bug. Every one of these used to reach pyautogui.write()
    and vanish without a trace."""
    monkeypatch.setattr(unicode_input, "send_char", lambda ch: False)
    copied = []
    monkeypatch.setattr(actions.pyperclip, "copy", lambda t: copied.append(t))

    actions._type_text(ACCENTED)

    written = [c.args[0] for c in mock_pyautogui.write.call_args_list]
    assert written == [], f"these would have been dropped: {written}"
    assert copied == list(ACCENTED)


def test_typographic_punctuation_is_routed_too(mock_pyautogui, monkeypatch):
    """Curly quotes, dashes and guillemets are not in string.punctuation,
    so the old gate sent them all to write()."""
    monkeypatch.setattr(unicode_input, "send_char", lambda ch: False)
    copied = []
    monkeypatch.setattr(actions.pyperclip, "copy", lambda t: copied.append(t))

    actions._type_text(TYPOGRAPHIC)

    assert [c.args[0] for c in mock_pyautogui.write.call_args_list] == []
    assert copied == list(TYPOGRAPHIC)


def test_symbols_are_routed_too(mock_pyautogui, monkeypatch):
    monkeypatch.setattr(unicode_input, "send_char", lambda ch: False)
    copied = []
    monkeypatch.setattr(actions.pyperclip, "copy", lambda t: copied.append(t))

    actions._type_text(SYMBOLS)

    assert [c.args[0] for c in mock_pyautogui.write.call_args_list] == []


def test_plain_ascii_still_takes_the_cheap_path(mock_pyautogui, monkeypatch):
    """The clipboard clobbers whatever the user had copied, so it is for
    characters that need it - not for every character."""
    monkeypatch.setattr(unicode_input, "send_char", lambda ch: False)
    copied = []
    monkeypatch.setattr(actions.pyperclip, "copy", lambda t: copied.append(t))

    actions._type_text("hello")

    assert [c.args[0] for c in mock_pyautogui.write.call_args_list] == list("hello")
    assert copied == []


def test_ascii_punctuation_keeps_its_clipboard_route(mock_pyautogui, monkeypatch):
    """The original behaviour, which was right as far as it went."""
    monkeypatch.setattr(unicode_input, "send_char", lambda ch: False)
    copied = []
    monkeypatch.setattr(actions.pyperclip, "copy", lambda t: copied.append(t))

    actions._type_text("a!b")

    assert copied == ["!"]


def test_mixed_text_routes_each_character_correctly(mock_pyautogui, monkeypatch):
    monkeypatch.setattr(unicode_input, "send_char", lambda ch: False)
    copied = []
    monkeypatch.setattr(actions.pyperclip, "copy", lambda t: copied.append(t))

    actions._type_text("Crème brûlée!")

    assert copied == ["è", "û", "é", "!"]
    assert "".join(c.args[0] for c in mock_pyautogui.write.call_args_list) == "Crme brle"


def test_sendinput_is_preferred_when_available(mock_pyautogui, monkeypatch):
    """When SendInput works, nothing touches the clipboard at all - the
    user's copied content is left alone."""
    sent = []
    monkeypatch.setattr(unicode_input, "send_char", lambda ch: sent.append(ch) or True)
    copied = []
    monkeypatch.setattr(actions.pyperclip, "copy", lambda t: copied.append(t))

    actions._type_text("café!")

    assert sent == list("café!")
    assert copied == []
    assert mock_pyautogui.write.call_args_list == []


def test_a_partial_sendinput_failure_falls_back_per_character(mock_pyautogui, monkeypatch):
    """SendInput can be refused for one character (a higher-integrity
    window took focus). That character falls back; the rest do not."""
    monkeypatch.setattr(unicode_input, "send_char", lambda ch: ch != "é")
    copied = []
    monkeypatch.setattr(actions.pyperclip, "copy", lambda t: copied.append(t))

    actions._type_text("café")

    assert copied == ["é"]


# ── the helper itself ────────────────────────────────────────────────

def test_utf16_units_for_a_basic_character():
    assert unicode_input._utf16_units("A") == [0x0041]


def test_utf16_units_for_an_accented_character():
    assert unicode_input._utf16_units("é") == [0x00E9]


def test_utf16_units_for_a_surrogate_pair():
    """Anything above U+FFFF is two code units and needs two events.
    Sending only the first produces a lone surrogate, not the emoji."""
    units = unicode_input._utf16_units("\U0001F600")
    assert len(units) == 2
    assert 0xD800 <= units[0] <= 0xDBFF
    assert 0xDC00 <= units[1] <= 0xDFFF


def test_send_text_is_a_noop_for_empty_input():
    assert unicode_input.send_text("") is True


def test_send_char_reports_failure_rather_than_raising(monkeypatch):
    """A typing helper that throws mid-macro leaves the target
    application half-filled, which is worse than a missing character."""
    monkeypatch.setattr(unicode_input, "_prepared", True)
    monkeypatch.setattr(unicode_input, "_send_input", None)
    assert unicode_input.send_char("é") is False
    assert unicode_input.send_text("café") is False


def test_the_old_gate_would_have_missed_all_of_these():
    """Documents the original defect: string.punctuation is ASCII-only,
    so none of the characters that actually broke were covered by it."""
    for ch in ACCENTED + TYPOGRAPHIC + SYMBOLS:
        assert ch not in string.punctuation
