# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_sounds.py
======================
ai/sounds.py: the sound files are there and sane, and one event played
twice in quick succession is heard once. Playback itself is replaced;
nothing here makes a noise.
"""

import os
import struct
import sys
import threading
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.sounds as sounds


@pytest.fixture
def player(monkeypatch):
    """sounds.play with a fake audio device; returns the kinds played."""
    played = []
    finished = threading.Event()

    def fake_play_clip(pcm, rate, channels, sample_width):
        played.append((len(pcm), rate))
        finished.set()

    monkeypatch.setattr(sounds, "is_available", lambda: True)
    monkeypatch.setattr(sounds, "_play_clip", fake_play_clip)
    monkeypatch.setattr(sounds, "_last_played", {})
    return played


@pytest.mark.parametrize("kind", sounds.KINDS)
def test_every_sound_is_a_short_clean_wav(kind):
    path = os.path.join(sounds.SOUND_DIR, f"{kind}.wav")
    with wave.open(path, "rb") as w:
        channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        data = w.readframes(frames)
    assert channels in (1, 2) and width == 2 and rate in (44100, 48000)
    duration = frames / rate
    assert 0.05 < duration < 2.5, f"{kind} is {duration:.2f}s"
    samples = struct.unpack(f"<{len(data) // 2}h", data)
    peak = max(abs(s) for s in samples)
    assert 1000 < peak < 32767, f"{kind}: silent or clipping (peak {peak})"
    # Starts and ends at silence: no click at either end.
    edge = channels * 4
    assert max(abs(s) for s in samples[:edge]) < peak * 0.02
    assert max(abs(s) for s in samples[-edge:]) < peak * 0.02


def test_a_sound_plays(player):
    assert sounds.play("screenshot", wait=True)
    assert len(player) == 1


def test_the_same_sound_twice_in_quick_succession_plays_once(player):
    assert sounds.play("screenshot", wait=True)
    assert not sounds.play("screenshot", wait=True)
    assert len(player) == 1


def test_different_sounds_do_not_block_each_other(player):
    assert sounds.play("confirm_accept", wait=True)
    assert sounds.play("confirm_refuse", wait=True)
    assert len(player) == 2


def test_the_same_sound_plays_again_once_the_window_has_passed(player, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(sounds.time, "monotonic", lambda: clock[0])
    assert sounds.play("macro_finished", wait=True)
    clock[0] += sounds.REPEAT_WINDOW + 0.01
    assert sounds.play("macro_finished", wait=True)
    assert len(player) == 2


def test_played_since_reports_what_played(player, monkeypatch):
    clock = [50.0]
    monkeypatch.setattr(sounds.time, "monotonic", lambda: clock[0])
    sounds.play("automation_stopped", wait=True)
    assert sounds.played_since("automation_stopped", 49.0)
    assert not sounds.played_since("automation_stopped", 51.0)
    assert not sounds.played_since("human_handoff", 0.0)


def test_unknown_sounds_and_other_platforms_play_nothing(player, monkeypatch):
    assert not sounds.play("fanfare")
    monkeypatch.setattr(sounds, "is_available", lambda: False)
    assert not sounds.play("screenshot")
    assert player == []


def test_a_broken_device_does_not_stop_later_sounds(player, monkeypatch):
    calls = []

    def flaky(pcm, rate, channels, width):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("no device")

    monkeypatch.setattr(sounds, "_play_clip", flaky)
    sounds.play("listen_start", wait=True)
    sounds.play("listen_end", wait=True)
    assert len(calls) == 2
