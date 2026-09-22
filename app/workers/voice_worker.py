# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/workers/voice_worker.py
============================
Backs the microphone button in the chat input bar.

Job is deliberately narrow: record from the mic and transcribe it - that's
it. The transcript is written into the chat's text input and sent through
the exact same path a typed message takes (ChatWidget._send_message →
MainWindow.process_user_request → AIWorker), instead of this worker also
calling the AI itself with raw audio bytes the way it used to.

Why the change: sending raw audio straight to Gemini and separately
running a local transcript just for display meant two different
transcriptions of the same recording could end up saying different
things - what showed up in the chat wasn't reliably what the AI actually
responded to. Routing everything through one transcript removes that
mismatch entirely: what's shown IS what gets sent, always. It also means
voice-originated turns automatically get the same macro handling,
autonomous-mode confirmation protocol, and history bookkeeping as typed
ones, instead of a separate parallel code path for each.

Speaking the reply back is handled separately - see
app/workers/speak_worker.py, triggered by MainWindow once the normal text
pipeline's response comes back.

Runs in a QThread (recording blocks on the microphone for a few seconds,
which would otherwise freeze the UI).

Cancellable listening
-----------------------
Unlike ai.main.listen_mic() (still used as-is by the CLI voice loop,
run_voice_assistant()), this worker does NOT use recognizer.listen() - a
single call that blocks until speech is heard or its own internal
timeout fires, with no way to interrupt it early once started. That's
what used to make clicking the mic button while "listening" a no-op (see
ChatWidget._on_mic_clicked's old comment): there was nothing to cancel
into. Instead, this uses recognizer.listen_in_background() - the same
technique app/workers/wakeword_worker.py already relies on for its own
clean stop() - polling a cancel Event every 100ms instead of blocking
indefinitely, so request_cancel() (wired to a click on the now-enabled
mic button during listening) can actually interrupt an in-progress
recording rather than forcing the user to wait out the full silence
timeout.
"""

import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

import ai.main as ai_main

# How long to wait for the user to START speaking before giving up as
# silence - matches ai.main.listen_mic()'s own timeout=7, kept the
# same here so cancellable listening doesn't feel more trigger-happy
# about "nothing was said" than the CLI path.
SILENCE_TIMEOUT_SECONDS = 7.0
PHRASE_TIME_LIMIT_SECONDS = 25
POLL_INTERVAL_SECONDS = 0.1


class VoiceWorker(QObject):
    listening_started = pyqtSignal(int)
    transcript_ready = pyqtSignal(int, str)       # (chat_id, transcript) - ready to send
    no_speech_detected = pyqtSignal(int)          # silence/timeout - nothing was said
    transcription_failed = pyqtSignal(int)        # something was said but couldn't be transcribed
    unavailable = pyqtSignal(int, str)            # (chat_id, reason) - mic/deps missing
    cancelled = pyqtSignal(int)                   # (chat_id) - request_cancel() was called mid-listening
    start = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.start.connect(self.run)
        self._cancel_event = threading.Event()
        # Set by MainWindow right after construction (see _on_mic_requested)
        # - not passed into __init__ since this fires on the GUI thread
        # before this object is moved to its own QThread. Optional: stays
        # None if voice mode is ever used somewhere without a wake-word
        # listener wired up, in which case there's nothing to wait for.
        self.wakeword_worker = None

    def request_cancel(self) -> None:
        """
        Aborts an in-progress recording early - called from the GUI
        thread (ChatWidget's mic button, now clickable during
        "listening" instead of disabled) when the user clicks the mic a
        second time to back out. Safe to call even if nothing is
        currently listening; the polling loop below just won't be
        running to notice, and the next run() clears the event before
        it does anything with it.
        """
        self._cancel_event.set()

    @pyqtSlot(int)
    def run(self, chat_id: int) -> None:
        self._cancel_event.clear()

        if ai_main.sr is None:
            self.unavailable.emit(
                chat_id,
                "Voice assistant unavailable: install 'SpeechRecognition' and 'PyAudio'.",
            )
            return

        try:
            # Bug fix: this used to emit listening_started() right here,
            # before _listen_cancellable() had done ANY of its setup
            # mic construction, then an explicit 0.5s ambient-noise
            # calibration, both of which run before listen_in_background()
            # is actually capturing audio. The mic button flips to its red
            # "Listening" state the instant this signal fires, so the
            # person saw "Listening" and started talking right away
            # losing however much of the start of the phrase got said
            # during that setup window (reported as "I said 'give me 5
            # ways to call a lion', it only got 'to call a lion'").
            # Passing a callback that _listen_cancellable() only invokes
            # once listen_in_background() has actually started means the
            # UI's "Listening" cue (and this signal) now line up with
            # when the microphone is genuinely capturing.
            audio, was_cancelled = self._listen_cancellable(
                on_listening=lambda: self.listening_started.emit(chat_id)
            )
        except Exception as e:
            self.unavailable.emit(chat_id, str(e))
            return

        if was_cancelled:
            self.cancelled.emit(chat_id)
            return

        if audio is None:
            self.no_speech_detected.emit(chat_id)
            return

        transcript = None
        try:
            transcript = ai_main.sr.Recognizer().recognize_google(audio, language="en-US")
        except Exception as e:
            print(f"[voice] Local transcription unavailable (showing no transcript): {e}")

        if not transcript or not transcript.strip():
            # Something was recorded but the transcription came back empty
            # (no network, quota, unclear speech, ...) - rather than
            # silently falling back to sending the raw audio anyway (the
            # old behavior, and the actual source of the "inconsistent"
            # transcript reports), be upfront about it so the user can
            # just try again or type instead.
            self.transcription_failed.emit(chat_id)
            return

        self.transcript_ready.emit(chat_id, transcript.strip())

    def _listen_cancellable(self, on_listening=None):
        """
        Records one utterance, polling self._cancel_event instead of
        blocking uninterruptibly on recognizer.listen() the way
        ai.main.listen_mic() does. Returns (audio_or_None, was_cancelled).

        `on_listening`, if given, is called right after the microphone
        is actually capturing (listen_in_background has started) - NOT
        at the top of this function, before mic construction and the
        0.5s ambient-noise calibration below have even run. See run()'s
        own comment for why that distinction matters.
        """
        sr = ai_main.sr
        recognizer = sr.Recognizer()
        recognizer.pause_threshold = 1.2
        # If the wake-word listener was just paused to make room for this
        # recording (see MainWindow._on_mic_requested), its background
        # thread may not have actually finished releasing the microphone
        # yet - pause() itself is deliberately non-blocking so it's safe
        # to call from the GUI thread. Waiting for it HERE, on this
        # worker's own background thread (never the GUI thread - this can
        # genuinely block for a moment), is what actually closes that gap
        # before opening a second microphone below. Skipping this is what
        # used to crash with speech_recognition's own "This audio source
        # is already inside a context manager" assertion.
        if self.wakeword_worker is not None:
            self.wakeword_worker.wait_until_paused()

        mic = sr.Microphone()

        captured = {"audio": None}
        done_event = threading.Event()

        def _on_phrase(recognizer, audio):
            captured["audio"] = audio
            done_event.set()

        # IMPORTANT: `with mic as source:` here wraps ONLY the ambient-
        # noise calibration and is fully exited before listen_in_background
        # is called below - do not nest listen_in_background (or anything
        # else that touches `mic`) inside this block. listen_in_background
        # spawns its OWN background thread which does its own `with source:`
        # on this same `mic` object; if that block is still open (as it
        # was in an earlier version of this function, wrapping the entire
        # listen_in_background call + wait loop), the background thread's
        # entry immediately fails speech_recognition's own
        # `assert self.stream is None` - because THIS thread already set
        # it, and hadn't exited yet. That crashed in the background
        # thread on every single call, silently: no exception surfaced
        # here, done_event never got set (the crashed thread never ran
        # the callback), so this just quietly fell through to the
        # silence-timeout path below and reported "no speech detected"
        # even though the person was talking.
        with mic as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)

        print("\n Listening...")
        if ai_main.winsound is not None:
            ai_main.winsound.Beep(800, 150)

        stop_listening = recognizer.listen_in_background(
            mic, _on_phrase, phrase_time_limit=PHRASE_TIME_LIMIT_SECONDS
        )
        if on_listening is not None:
            on_listening()
        try:
            start = time.monotonic()
            while not done_event.is_set() and not self._cancel_event.is_set():
                if time.monotonic() - start > SILENCE_TIMEOUT_SECONDS:
                    break  # nothing said within the window - same outcome listen_mic's WaitTimeoutError gives
                time.sleep(POLL_INTERVAL_SECONDS)
        finally:
            stop_listening(wait_for_stop=False)

        if self._cancel_event.is_set():
            return None, True

        if captured["audio"] is not None and ai_main.winsound is not None:
            ai_main.winsound.Beep(600, 150)

        return captured["audio"], False
