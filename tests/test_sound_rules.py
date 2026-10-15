# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_sound_rules.py
===========================
Which sound an event makes, and that it makes one: a stopped macro does
not also announce that it finished, the stop shortcut's sound is not
repeated when the run winds down, and an autonomous run's own steps stay
quiet. (ai/sounds.py's own repeat guard is tested in test_sounds.py.)
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.actions as actions
import ai.main as ai_main


@pytest.fixture
def heard(monkeypatch):
    sounds = []
    monkeypatch.setattr(ai_main, "play_notification_sound", lambda kind, wait=False: sounds.append(kind))
    monkeypatch.setattr(ai_main, "_autonomous_run_active", False)
    monkeypatch.setattr(ai_main, "_stop_sound_played_at", 0.0)
    return sounds


def test_a_macro_that_finishes_says_so(heard):
    actions._announce_macro_end(stopped=False, started=time.monotonic())
    assert heard == ["macro_finished"]


def test_a_macro_stopped_by_the_shortcut_makes_no_second_sound(heard, monkeypatch):
    started = time.monotonic()
    monkeypatch.setattr(actions, "emergency_stop", False)
    ai_main.stop_automation_now()
    actions._announce_macro_end(stopped=True, started=started)
    assert heard == ["automation_stopped"]
    monkeypatch.setattr(actions, "emergency_stop", False)


def test_a_macro_stopped_another_way_says_it_stopped(heard):
    actions._announce_macro_end(stopped=True, started=time.monotonic())
    assert heard == ["automation_stopped"]


def test_a_macro_inside_an_autonomous_run_is_quiet(heard, monkeypatch):
    monkeypatch.setattr(ai_main, "_autonomous_run_active", True)
    actions._announce_macro_end(stopped=False, started=time.monotonic())
    actions._announce_macro_end(stopped=True, started=time.monotonic())
    assert heard == []


def test_a_screenshot_makes_a_sound_except_during_a_run(heard, monkeypatch, tmp_path):
    PIL = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(ai_main.ImageGrab, "grab", lambda *a, **k: PIL.new("RGB", (8, 8)))
    monkeypatch.setattr(ai_main, "SCREENSHOTS_DIR", str(tmp_path))
    ai_main.trigger_screenshot(mark_pending=False)
    assert heard == ["screenshot"]
    monkeypatch.setattr(ai_main, "_autonomous_run_active", True)
    ai_main.trigger_screenshot(mark_pending=False)
    assert heard == ["screenshot"]
    ai_main.discard_pending_screenshot()


def test_sounds_can_be_turned_off(monkeypatch, tmp_path):
    played = []
    import ai.sounds as sounds
    monkeypatch.setattr(sounds, "play", lambda kind, wait=False: played.append(kind))
    monkeypatch.setattr(ai_main, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("KESTREL_SOUNDS_ENABLED", "0")
    ai_main.play_notification_sound("screenshot")
    assert played == []
    monkeypatch.setenv("KESTREL_SOUNDS_ENABLED", "1")
    ai_main.play_notification_sound("screenshot")
    assert played == ["screenshot"]
