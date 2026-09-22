# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_speech.py
======================
ai.main's text-to-speech chunking.

Read aloud used to send the whole reply as one request and play nothing
until every second of audio had been generated, so the wait grew with
the length of the answer. The fix is to synthesize a chunk at a time and
start playing the first one immediately, which makes the size of that
first chunk the thing worth testing.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.main as ai_main


def test_empty_text_produces_no_chunks():
    assert ai_main._split_for_speech("") == []
    assert ai_main._split_for_speech("   \n ") == []


def test_one_short_sentence_stays_one_chunk():
    assert ai_main._split_for_speech("Hello there.") == ["Hello there."]


def test_a_long_reply_is_split():
    chunks = ai_main._split_for_speech("This is a sentence. " * 30)
    assert len(chunks) > 1


def test_no_text_is_lost_in_the_split():
    original = "First one. Second one! Third one? And a fourth."
    chunks = ai_main._split_for_speech(original)
    assert "".join(chunks).replace(" ", "") == original.replace(" ", "")


def test_chunks_stay_near_the_limit():
    chunks = ai_main._split_for_speech("A sentence of some length here. " * 40)
    # One sentence may push a chunk over; nothing should be far over.
    assert all(len(c) <= ai_main._TTS_CHUNK_CHARS + 60 for c in chunks)


def test_a_single_huge_sentence_is_not_cut_mid_clause():
    """A slower start beats speech that stops dead in the middle of a
    thought."""
    giant = "word " * 200 + "."
    assert len(ai_main._split_for_speech(giant)) == 1


def test_the_first_chunk_is_far_smaller_than_the_whole_reply():
    """This is the whole point. Time to first audio tracks the size of
    the first chunk, not the size of the reply."""
    reply = "Here is the answer. " + ("More detail follows. " * 40)
    chunks = ai_main._split_for_speech(reply)
    assert len(chunks) > 3
    assert len(chunks[0]) < len(reply) / 3


def test_splitting_handles_every_sentence_ending():
    chunks = ai_main._split_for_speech("One. Two! Three? Four\n\nFive.")
    joined = " ".join(chunks)
    for word in ("One", "Two", "Three", "Four", "Five"):
        assert word in joined


def test_synthesize_returns_none_rather_than_raising(monkeypatch):
    """A failed chunk must not take the rest of the reply down with it."""
    class _Boom:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                raise ConnectionError("no route to host")

    monkeypatch.setattr(ai_main, "_gemini_client", _Boom)
    assert ai_main._synthesize_speech("anything") is None


def test_synthesize_returns_none_when_no_audio_came_back(monkeypatch):
    class _Part:
        inline_data = None

    class _Content:
        parts = [_Part()]

    class _Candidate:
        content = _Content()

    class _Response:
        candidates = [_Candidate()]

    class _Client:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                return _Response()

    monkeypatch.setattr(ai_main, "_gemini_client", _Client)
    assert ai_main._synthesize_speech("anything") is None


# ══════════════════════════════════════════════════════════════════
# The first chunk has its own, smaller budget
# ══════════════════════════════════════════════════════════════════
# Synthesis is one blocking request per chunk and nothing can play until
# the whole chunk comes back, so time-to-first-audio is set by the size
# of chunk 0 alone. Packing it greedily to the full 240 characters meant
# waiting for roughly fifteen seconds of speech to be generated before
# hearing the first word of it. Everything after chunk 0 is fetched
# while earlier audio plays, so only the first one is worth holding down.

def test_the_first_chunk_uses_the_smaller_budget():
    reply = "Short opener. " + ("A sentence of some length here. " * 20)
    chunks = ai_main._split_for_speech(reply)
    assert len(chunks[0]) <= ai_main._TTS_FIRST_CHUNK_CHARS + 40


def test_later_chunks_use_the_full_budget():
    """Holding every chunk short would mean more requests, more seams and
    worse intonation, for no benefit - nobody waits on chunk 4."""
    reply = "Short opener. " + ("A sentence of some length here. " * 30)
    chunks = ai_main._split_for_speech(reply)
    assert len(chunks) > 3
    biggest_after_first = max(len(c) for c in chunks[1:])
    assert biggest_after_first > ai_main._TTS_FIRST_CHUNK_CHARS


def test_the_first_chunk_is_smaller_than_the_ones_after_it():
    reply = ("One sentence about a topic. " * 25)
    chunks = ai_main._split_for_speech(reply)
    assert len(chunks) >= 3
    assert len(chunks[0]) < len(chunks[1])


def test_first_limit_none_restores_a_single_budget():
    """The escape hatch, and what proves the two budgets are actually
    independent rather than the short one applying everywhere."""
    reply = "One sentence about a topic. " * 25
    single = ai_main._split_for_speech(reply, first_limit=None)
    assert len(single[0]) > ai_main._TTS_FIRST_CHUNK_CHARS


def test_a_short_reply_is_still_one_chunk():
    """The smaller first budget must not split a reply that would
    otherwise have been a single request."""
    assert len(ai_main._split_for_speech("That is done.")) == 1


def test_a_long_opening_sentence_is_still_not_cut():
    """The first chunk's budget is a target, not a hard cut. A sentence
    longer than it is still left whole - the alternative is speech that
    stops dead mid-thought, which is worse than a slow start."""
    giant_opener = "word " * 100 + ". Then a short one."
    chunks = ai_main._split_for_speech(giant_opener)
    assert len(chunks[0]) > ai_main._TTS_FIRST_CHUNK_CHARS
    assert "word word" in chunks[0]


def test_no_text_is_lost_with_two_budgets():
    original = "First one. Second one! Third one? And a fourth that is quite a bit longer than the others."
    chunks = ai_main._split_for_speech(original)
    assert "".join(chunks).replace(" ", "") == original.replace(" ", "")


# ══════════════════════════════════════════════════════════════════
# Utterance generations - two speaks must not clobber each other
# ══════════════════════════════════════════════════════════════════
# This was one shared threading.Event, set by stop_speaking and cleared
# by speak_reply. Two overlapping speaks (a Read aloud during a voice
# reply, or just two Read alouds) meant the second one's clear() silently
# un-stopped the first: press Stop, start another, and the one you
# stopped resumes.

def test_each_utterance_gets_its_own_generation():
    a = ai_main._begin_speech()
    b = ai_main._begin_speech()
    assert b != a


def test_starting_a_new_utterance_supersedes_the_old_one():
    first = ai_main._begin_speech()
    assert not ai_main._speech_superseded(first)
    second = ai_main._begin_speech()
    assert ai_main._speech_superseded(first), "the first should have been cancelled"
    assert not ai_main._speech_superseded(second)


def test_stop_speaking_supersedes_the_current_utterance(monkeypatch):
    monkeypatch.setattr(ai_main, "winsound", None)   # no audio device here
    current = ai_main._begin_speech()
    ai_main.stop_speaking()
    assert ai_main._speech_superseded(current)


def test_stopping_then_starting_does_not_revive_the_stopped_one(monkeypatch):
    """The exact old bug. Under the shared Event, the new speak's
    clear() reset the flag the stopped one was still polling."""
    monkeypatch.setattr(ai_main, "winsound", None)
    stopped = ai_main._begin_speech()
    ai_main.stop_speaking()
    ai_main._begin_speech()                          # a second utterance starts
    assert ai_main._speech_superseded(stopped), "the stopped utterance came back to life"


def test_generations_are_monotonic_under_concurrent_starts():
    """_begin_speech is called from worker threads; two callers must
    never be handed the same number, or each would think the other's
    audio was its own."""
    import threading

    handed_out = []
    lock = threading.Lock()

    def claim():
        got = ai_main._begin_speech()
        with lock:
            handed_out.append(got)

    threads = [threading.Thread(target=claim) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(handed_out)) == len(handed_out), "a generation was issued twice"


def test_only_the_newest_utterance_is_live_after_a_burst():
    generations = [ai_main._begin_speech() for _ in range(5)]
    for old in generations[:-1]:
        assert ai_main._speech_superseded(old)
    assert not ai_main._speech_superseded(generations[-1])
