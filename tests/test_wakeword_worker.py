# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_wakeword_worker.py
================================
Tests for app/workers/wakeword_worker.py's matching logic - pure
functions (match_wake_word/matches_stop_word/classify_confirmation), so
these run without a real microphone or speech_recognition audio object.

match_wake_word routes across every configured AI profile
(ai.profiles.list_profiles()) rather than a single fixed name - each
profile's own name is checked as a case-insensitive substring of the
phrase, and the LONGEST matching name wins if more than one profile's
name happens to appear (see ai.profiles.match_profile_for_phrase, which
this delegates to directly). A single-profile install (the common case,
and what every install starts as before ever creating a second profile)
behaves exactly like the old single-assistant-name version: one name,
one match.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.main as ai_main
import ai.profiles as profiles
from app.workers.wakeword_worker import (
    WakeWordWorker, classify_confirmation, match_wake_word, matches_stop_word,
)


def _profile(name: str, provider: str = "gemini", model_id: str = "models/gemini-3-flash-preview") -> profiles.Profile:
    return profiles.Profile(id=name.lower(), name=name, provider=provider, model_id=model_id, created_at=time.time())


# ══════════════════════════════════════════════════════════════════
# match_wake_word - routes across every configured profile
# ══════════════════════════════════════════════════════════════════

def test_match_wake_word_with_full_word(monkeypatch):
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Kestrel")])
    matched = match_wake_word("kestrel, ouvre le navigateur")
    assert matched is not None and matched.name == "Kestrel"


def test_match_wake_word_is_case_insensitive_given_already_lowered_input(monkeypatch):
    # _on_audio always lower()s before calling this - sanity check the
    # match itself doesn't accidentally require exact case anyway.
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Kestrel")])
    assert match_wake_word("kestrel") is not None


def test_match_wake_word_none_for_unrelated_phrase(monkeypatch):
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Kestrel")])
    assert match_wake_word("quelle heure est-il") is None


def test_match_wake_word_none_for_empty_string(monkeypatch):
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Kestrel")])
    assert match_wake_word("") is None


def test_match_wake_word_follows_a_renamed_profile(monkeypatch):
    """
    Renaming a profile changes its own `name` field - which IS what
    gets matched, no separate wake-word string to keep in sync.
    """
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Jarvis")])
    matched = match_wake_word("jarvis, ouvre le navigateur")
    assert matched is not None and matched.name == "Jarvis"
    assert match_wake_word("kestrel, ouvre le navigateur") is None


def test_match_wake_word_routes_to_the_right_profile_among_several(monkeypatch):
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Aria"), _profile("Nova")])
    assert match_wake_word("hey nova, what's the weather").name == "Nova"
    assert match_wake_word("aria, open the browser").name == "Aria"
    assert match_wake_word("what time is it") is None


def test_match_wake_word_prefers_the_longer_matching_name(monkeypatch):
    # "Ari" is itself a substring of "Aria" - a phrase containing "aria"
    # would otherwise ambiguously match both profiles; the more specific
    # (longer) name should win rather than whichever happens to be
    # first in the list.
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Ari"), _profile("Aria")])
    assert match_wake_word("aria, open the browser").name == "Aria"


# ══════════════════════════════════════════════════════════════════
# matches_stop_word
# ══════════════════════════════════════════════════════════════════

def test_matches_stop_word_english():
    assert matches_stop_word("stop") is True


def test_matches_stop_word_french_stem():
    assert matches_stop_word("arrête-toi") is True
    assert matches_stop_word("arrêtez") is True


def test_matches_stop_word_false_for_unrelated_phrase():
    assert matches_stop_word("continue") is False


# ══════════════════════════════════════════════════════════════════
# classify_confirmation
# ══════════════════════════════════════════════════════════════════

def test_classify_confirmation_accept():
    assert classify_confirmation("j'accepte") is True
    assert classify_confirmation("vas-y j'accepte") is True


def test_classify_confirmation_refuse():
    assert classify_confirmation("je refuse ça") is False


def test_classify_confirmation_neither():
    assert classify_confirmation("je ne sais pas") is None


# ── Toggling off and straight back on ────────────────────────────────────

def _stub_sr(monkeypatch, on_listen=None, slow=False):
    """A speech_recognition stand-in whose setup can be made slow, so the
    off/on race has a window to happen in."""
    import time as _time

    class _Source:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Recognizer:
        def adjust_for_ambient_noise(self, source, duration=0.5):
            if slow:
                _time.sleep(0.3)

        def listen_in_background(self, mic, callback, phrase_time_limit=None):
            if on_listen is not None:
                on_listen()
            return lambda wait_for_stop=True: None

    class _SR:
        Recognizer = _Recognizer
        Microphone = _Source

    monkeypatch.setattr(ai_main, "sr", _SR)


def test_toggling_off_and_on_still_starts_listening(monkeypatch):
    """
    The reported bug: ticking the wake word box did nothing.

    stop() bumps the generation, but start() used to see _starting still
    True and return success without spawning anything, while the
    in-flight thread tore down the listener it had just built. Nothing
    ended up listening and the status sat on "Starting...".
    """
    import time as _time
    started = []
    _stub_sr(monkeypatch, on_listen=lambda: started.append(True), slow=True)

    worker = WakeWordWorker()
    assert worker.start() is True
    worker.stop()                      # lands while setup is still running
    assert worker.start() is True      # must spawn a NEW attempt

    for _ in range(40):
        if len(started) >= 2:
            break
        _time.sleep(0.05)
    assert len(started) >= 2, "the second start never actually began listening"
    worker.stop()


def test_stop_clears_the_latches_that_make_a_listener_deaf(monkeypatch):
    """_speaking_mode and _confirmation_mode both short-circuit matching.
    Either one surviving a stop/start is a listener that runs and hears
    nothing, which looks exactly like one that never started."""
    _stub_sr(monkeypatch)
    worker = WakeWordWorker()
    worker.set_speaking_mode(True)
    worker.set_confirmation_mode(True)
    worker.stop()
    assert worker._speaking_mode is False
    assert worker._confirmation_mode is False


def test_start_clears_the_latches_too(monkeypatch):
    _stub_sr(monkeypatch)
    worker = WakeWordWorker()
    worker.set_speaking_mode(True)
    worker.start()
    assert worker._speaking_mode is False
    worker.stop()


# ══════════════════════════════════════════════════════════════════
# match_wake_word - phonetic variants
# ══════════════════════════════════════════════════════════════════
# The transcript is whatever Google's speech API decided the audio said,
# and it writes "Clio" for Cleo often enough that a name-only substring
# match reads, from the user's side, as a wake word that silently does
# not work. ai.assistant_names.ASR_ALIASES lists the spellings that
# should also count - only ones that are not themselves ordinary words,
# so this widens what counts as the name being SAID without widening
# what counts as an accidental wake.

def test_a_known_mistranscription_still_wakes(monkeypatch):
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Cleo")])
    matched = match_wake_word("clio open the browser")
    assert matched is not None and matched.name == "Cleo"


def test_the_name_itself_still_wins_normally(monkeypatch):
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Cleo")])
    assert match_wake_word("cleo open the browser") is not None


def test_an_unrelated_phrase_does_not_wake_through_an_alias(monkeypatch):
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Nadia")])
    assert match_wake_word("what time is the meeting") is None


def test_a_user_typed_name_with_no_aliases_matches_on_itself(monkeypatch):
    """Most names are typed by the person, not picked from the pool, and
    must go through the same path without special-casing."""
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Bartholomew")])
    assert match_wake_word("bartholomew stop") is not None
    assert match_wake_word("something else entirely") is None


def test_longest_profile_name_still_wins_when_one_matched_via_alias(monkeypatch):
    """The tie-break ranks by the PROFILE NAME's length, not by the
    length of whichever variant matched, so a longer name still beats a
    shorter one that only matched through a long alias."""
    monkeypatch.setattr(
        profiles, "list_profiles",
        lambda: [_profile("Cleo"), _profile("Peregrine")],
    )
    matched = match_wake_word("peregrine and clio are both here")
    assert matched is not None and matched.name == "Peregrine"


def test_a_blank_profile_name_never_matches(monkeypatch):
    """An empty name would be a substring of every transcript, so a
    profile saved with a blank name would wake on any speech at all -
    including silence transcribed as an empty string."""
    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("")])
    assert match_wake_word("anything at all") is None
    assert match_wake_word("") is None


# ══════════════════════════════════════════════════════════════════
# classify_confirmation - the words people actually say
# ══════════════════════════════════════════════════════════════════
# Only "accept" and "refus" were recognised, and almost nobody says
# either one at a yes/no prompt. The card could be answered by voice in
# theory and not in practice, which is how "oral commands don't work"
# gets reported.

def test_plain_yes_and_no_are_understood():
    assert classify_confirmation("yes") is True
    assert classify_confirmation("no") is False


def test_the_original_words_still_work():
    assert classify_confirmation("accept") is True
    assert classify_confirmation("je refuse") is False


def test_natural_acceptances():
    for phrase in ("yeah go ahead", "okay", "yep do it", "oui", "vas-y", "confirm that"):
        assert classify_confirmation(phrase) is True, phrase


def test_natural_refusals():
    for phrase in ("nope", "cancel that", "never mind", "non", "annule", "do not"):
        assert classify_confirmation(phrase) is False, phrase


def test_an_unrelated_phrase_is_neither():
    assert classify_confirmation("what time is the meeting") is None
    assert classify_confirmation("") is None


def test_a_refusal_containing_an_acceptance_stem_refuses():
    """"don't do it" contains "do it". Refusal is checked first, so a
    phrase carrying both resolves as a refusal - the safe direction for
    a prompt whose yes starts an automation that drives the machine."""
    assert classify_confirmation("don't do it") is False
    assert classify_confirmation("no, cancel") is False
    assert classify_confirmation("do not accept") is False


def test_stop_words_are_unaffected():
    """The stop vocabulary is separate and must not have absorbed any of
    the confirmation words."""
    assert matches_stop_word("stop") is True
    assert matches_stop_word("yes") is False
    assert matches_stop_word("cancel") is False
