# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_voice_worker_cancel.py
====================================
Tests for app/workers/voice_worker.py's cancellable listening - the fix
for "once on voicemode u can't stop it": the mic button used to be
DISABLED entirely while listening, because the old implementation
(ai.main.listen_mic's recognizer.listen()) is a single blocking call
with no way to interrupt it once started. VoiceWorker now uses
recognizer.listen_in_background() instead - polling a cancel Event
rather than blocking uninterruptibly - the same technique
app/workers/wakeword_worker.py already relies on for its own stop().

Uses small fake speech_recognition-shaped objects (FakeRecognizer /
FakeMicrophone) rather than a real microphone - same "mock the
underlying library boundary" approach tests/conftest.py already uses
for pyautogui/keyboard.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.workers.voice_worker as voice_worker_module
from app.workers.voice_worker import VoiceWorker


class FakeAudio:
    pass


class FakeMicrophone:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


class FakeRecognizer:
    """
    capture_immediately=True simulates a phrase being heard right away
    (calls the listen_in_background callback on a short-lived thread,
    like the real background listener would); False simulates silence - the callback is never called, so the polling loop only ends via
    cancellation or the silence timeout.
    """

    def __init__(self, capture_immediately=False, transcript="hello world", raise_on_recognize=None):
        self.pause_threshold = None
        self._capture_immediately = capture_immediately
        self._transcript = transcript
        self._raise_on_recognize = raise_on_recognize
        self.stopped = False

    def adjust_for_ambient_noise(self, source, duration=0.5):
        pass

    def listen_in_background(self, mic, callback, phrase_time_limit=None):
        if self._capture_immediately:
            threading.Thread(target=lambda: callback(self, FakeAudio())).start()

        def _stop(wait_for_stop=True):
            self.stopped = True

        return _stop

    def recognize_google(self, audio, language=None):
        if self._raise_on_recognize:
            raise self._raise_on_recognize
        return self._transcript


class FakeSR:
    Microphone = FakeMicrophone

    def __init__(self, recognizer):
        self._recognizer = recognizer

    def Recognizer(self):
        return self._recognizer


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    # Real defaults (7s timeout, 100ms poll) would make every test in
    # this file slow - these tests care about the LOGIC, not real timing.
    monkeypatch.setattr(voice_worker_module, "SILENCE_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(voice_worker_module, "POLL_INTERVAL_SECONDS", 0.02)


def test_run_emits_transcript_ready_when_phrase_captured(monkeypatch):
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "sr", FakeSR(FakeRecognizer(capture_immediately=True, transcript="ouvre youtube")))
    monkeypatch.setattr(ai_main, "winsound", None)

    worker = VoiceWorker()
    captured = []
    worker.transcript_ready.connect(lambda chat_id, text: captured.append((chat_id, text)))
    worker.run(1)

    assert captured == [(1, "ouvre youtube")]


def test_run_emits_no_speech_detected_when_nothing_captured_within_timeout(monkeypatch):
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "sr", FakeSR(FakeRecognizer(capture_immediately=False)))
    monkeypatch.setattr(ai_main, "winsound", None)

    worker = VoiceWorker()
    captured = []
    worker.no_speech_detected.connect(lambda chat_id: captured.append(chat_id))
    worker.run(1)

    assert captured == [1]


def test_request_cancel_stops_listening_before_the_silence_timeout(monkeypatch):
    """
    THE regression test for "once on voicemode u can't stop it". The
    timeout is set deliberately long (5s) here - if cancellation didn't
    actually work, run() would only return after that full 5s wait,
    which the elapsed-time assertion below would catch.
    """
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "sr", FakeSR(FakeRecognizer(capture_immediately=False)))
    monkeypatch.setattr(ai_main, "winsound", None)
    monkeypatch.setattr(voice_worker_module, "SILENCE_TIMEOUT_SECONDS", 5.0)

    worker = VoiceWorker()
    cancelled = []
    worker.cancelled.connect(lambda chat_id: cancelled.append(chat_id))

    def _cancel_soon():
        time.sleep(0.1)
        worker.request_cancel()

    canceller = threading.Thread(target=_cancel_soon)
    canceller.start()

    start = time.monotonic()
    worker.run(1)
    elapsed = time.monotonic() - start
    canceller.join()

    assert cancelled == [1]
    assert elapsed < 1.0, f"took {elapsed:.2f}s - cancellation didn't actually interrupt listening"


def test_cancelled_run_does_not_emit_any_other_outcome_signal(monkeypatch):
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "sr", FakeSR(FakeRecognizer(capture_immediately=False)))
    monkeypatch.setattr(ai_main, "winsound", None)
    monkeypatch.setattr(voice_worker_module, "SILENCE_TIMEOUT_SECONDS", 5.0)

    worker = VoiceWorker()
    all_signals = []
    worker.cancelled.connect(lambda chat_id: all_signals.append("cancelled"))
    worker.no_speech_detected.connect(lambda chat_id: all_signals.append("no_speech"))
    worker.transcript_ready.connect(lambda *a: all_signals.append("transcript"))
    worker.transcription_failed.connect(lambda chat_id: all_signals.append("failed"))

    threading.Thread(target=lambda: (time.sleep(0.1), worker.request_cancel())).start()
    worker.run(1)
    time.sleep(0.1)  # let any stray extra emit land, if the bug regresses

    assert all_signals == ["cancelled"]


def test_run_emits_transcription_failed_when_recognize_raises(monkeypatch):
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "sr", FakeSR(FakeRecognizer(capture_immediately=True, raise_on_recognize=RuntimeError("no network"))))
    monkeypatch.setattr(ai_main, "winsound", None)

    worker = VoiceWorker()
    captured = []
    worker.transcription_failed.connect(lambda chat_id: captured.append(chat_id))
    worker.run(1)

    assert captured == [1]


def test_run_emits_unavailable_when_sr_is_none(monkeypatch):
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "sr", None)

    worker = VoiceWorker()
    captured = []
    worker.unavailable.connect(lambda chat_id, reason: captured.append((chat_id, reason)))
    worker.run(1)

    assert len(captured) == 1 and captured[0][0] == 1


def test_stray_cancel_before_run_does_not_leak_into_the_next_run(monkeypatch):
    """request_cancel() called with nothing listening must not immediately cancel the NEXT run() - run() clears the event at its own start."""
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "sr", FakeSR(FakeRecognizer(capture_immediately=True, transcript="hello")))
    monkeypatch.setattr(ai_main, "winsound", None)

    worker = VoiceWorker()
    worker.request_cancel()   # stray call - nothing is listening yet

    captured = []
    worker.transcript_ready.connect(lambda chat_id, text: captured.append(("transcript", chat_id, text)))
    worker.cancelled.connect(lambda chat_id: captured.append(("cancelled", chat_id)))
    worker.run(1)

    assert captured == [("transcript", 1, "hello")]


def test_stop_listening_is_called_even_when_cancelled():
    """The background listener thread must always be told to stop - leaving it running would leak a live microphone stream."""
    import ai.main as ai_main
    import unittest.mock as mock
    recognizer = FakeRecognizer(capture_immediately=False)
    with mock.patch.object(ai_main, "sr", FakeSR(recognizer)), \
         mock.patch.object(ai_main, "winsound", None), \
         mock.patch.object(voice_worker_module, "SILENCE_TIMEOUT_SECONDS", 5.0), \
         mock.patch.object(voice_worker_module, "POLL_INTERVAL_SECONDS", 0.02):
        worker = VoiceWorker()
        threading.Thread(target=lambda: (time.sleep(0.1), worker.request_cancel())).start()
        worker.run(1)

    assert recognizer.stopped is True
