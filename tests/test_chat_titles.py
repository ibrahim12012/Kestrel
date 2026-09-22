# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_chat_titles.py
===========================
Conversation titles: ai.main.suggest_chat_title and the cleanup around
it. A model asked for a title returns a title most of the time and
something else the rest of the time, so the parsing and the fallbacks
are what these cover.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.main as ai_main


class _FakeResponse:
    def __init__(self, text):
        self.text = text


def _model_returning(monkeypatch, text, record=None):
    """Point suggest_chat_title at a model that answers `text`."""
    class _Models:
        def generate_content(self, **kwargs):
            if record is not None:
                record.append(kwargs)
            if isinstance(text, Exception):
                raise text
            return _FakeResponse(text)

    class _Client:
        models = _Models()

    import ai.model_client as model_client
    monkeypatch.setattr(model_client, "make_client", lambda *a, **k: _Client())


# -- the fallback, which is what everything degrades to ----------------

def test_fallback_keeps_a_short_message_whole():
    assert ai_main.fallback_chat_title("Fix my Excel macro") == "Fix my Excel macro"


def test_fallback_truncates_a_long_message():
    long_message = "Please help me understand why my spreadsheet keeps rounding numbers"
    title = ai_main.fallback_chat_title(long_message)
    assert len(title) <= ai_main.TITLE_MAX_CHARS + 1
    assert title.endswith("…")


def test_fallback_collapses_whitespace():
    assert ai_main.fallback_chat_title("  hello   there \n friend ") == "hello there friend"


def test_fallback_on_an_empty_message():
    assert ai_main.fallback_chat_title("") == "New chat"
    assert ai_main.fallback_chat_title("   \n ") == "New chat"


# -- gibberish ---------------------------------------------------------

def test_a_keyboard_mash_is_recognised():
    for text in ("aazeeeeeeeeeeeee", "asdfgh", "aaaa", "zzzzzzz"):
        assert ai_main.looks_like_gibberish(text), text


def test_real_words_are_not_gibberish():
    for text in ("hello", "kubernetes", "bonjour", "caf\u00e9",
                 "how do I deploy this", "fix my macro"):
        assert not ai_main.looks_like_gibberish(text), text


def test_a_mash_never_becomes_the_title():
    """The bug this fixes: typing "aazeeeeeeeeeeeee" left a sidebar row
    reading exactly that, forever."""
    assert ai_main.fallback_chat_title("aazeeeeeeeeeeeee") == "New chat"


def test_a_single_real_word_survives_the_fallback():
    """The fallback runs when the model is unreachable. Throwing away a
    real message then would be worse than the problem."""
    assert ai_main.fallback_chat_title("kubernetes") == "kubernetes"
    assert ai_main.fallback_chat_title("hello") == "hello"


def test_a_single_token_always_reaches_the_model(monkeypatch):
    """However short. A word count cannot tell a mash from a label, and
    that is exactly the case that needs the model most."""
    calls = []
    _model_returning(monkeypatch, "Testing output", record=calls)
    assert ai_main.suggest_chat_title("aazeeeeeeeeeeeee") == "Testing output"
    assert calls, "a single token skipped the model"


def test_a_short_real_phrase_still_skips_the_model(monkeypatch):
    calls = []
    _model_returning(monkeypatch, "Something Else", record=calls)
    assert ai_main.suggest_chat_title("fix my macro") == "fix my macro"
    assert calls == []


# -- the model path ----------------------------------------------------

def test_uses_the_models_title(monkeypatch):
    _model_returning(monkeypatch, "Excel rounding part numbers")
    title = ai_main.suggest_chat_title(
        "How do I get Excel to stop rounding my part numbers when I paste them in"
    )
    assert title == "Excel rounding part numbers"


def test_a_short_multi_word_message_never_reaches_the_model(monkeypatch):
    calls = []
    _model_returning(monkeypatch, "Something Else Entirely", record=calls)
    assert ai_main.suggest_chat_title("Fix my macro") == "Fix my macro"
    assert calls == [], "a message already short enough is its own title"


def test_a_network_failure_falls_back(monkeypatch):
    _model_returning(monkeypatch, ConnectionError("no route to host"))
    message = "Walk me through setting up a recurring backup of my project folder"
    assert ai_main.suggest_chat_title(message) == ai_main.fallback_chat_title(message)


def test_an_empty_answer_falls_back(monkeypatch):
    _model_returning(monkeypatch, "")
    message = "Walk me through setting up a recurring backup of my project folder"
    assert ai_main.suggest_chat_title(message) == ai_main.fallback_chat_title(message)


def test_a_none_answer_falls_back(monkeypatch):
    _model_returning(monkeypatch, None)
    message = "Walk me through setting up a recurring backup of my project folder"
    assert ai_main.suggest_chat_title(message) == ai_main.fallback_chat_title(message)


# -- cleanup of what a model actually sends back -----------------------

def test_quotes_are_stripped():
    assert ai_main._clean_title('"Excel rounding"') == "Excel rounding"
    assert ai_main._clean_title("'Excel rounding'") == "Excel rounding"
    assert ai_main._clean_title("“Excel rounding”") == "Excel rounding"
    assert ai_main._clean_title("«Excel rounding»") == "Excel rounding"


def test_a_title_prefix_is_stripped():
    assert ai_main._clean_title("Title: Excel rounding") == "Excel rounding"


def test_a_trailing_period_is_stripped():
    assert ai_main._clean_title("Excel rounding.") == "Excel rounding"


def test_a_multi_line_answer_is_refused():
    assert ai_main._clean_title("Here you go:\nExcel rounding") == ""


def test_a_whole_paragraph_is_refused():
    assert ai_main._clean_title("Sure! " + "word " * 60) == ""


def test_a_long_but_plausible_title_is_cut_not_refused():
    over = "A" * (ai_main.TITLE_MAX_CHARS + 5)
    cleaned = ai_main._clean_title(over)
    assert cleaned and len(cleaned) <= ai_main.TITLE_MAX_CHARS


def test_a_refused_title_means_the_fallback_is_used(monkeypatch):
    _model_returning(monkeypatch, "Certainly! Here is a title for you:\nMy Title")
    message = "Walk me through setting up a recurring backup of my project folder"
    assert ai_main.suggest_chat_title(message) == ai_main.fallback_chat_title(message)


def test_the_prompt_carries_the_message(monkeypatch):
    calls = []
    _model_returning(monkeypatch, "Short title", record=calls)
    ai_main.suggest_chat_title("Walk me through setting up a recurring backup please")
    assert calls, "the model should have been called"
    assert "recurring backup" in calls[0]["contents"]


def test_a_very_long_message_is_truncated_before_being_sent(monkeypatch):
    calls = []
    _model_returning(monkeypatch, "Short title", record=calls)
    ai_main.suggest_chat_title("word " * 2000)
    # A pasted log should not be billed as a title prompt.
    assert len(calls[0]["contents"]) < 3000
