# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/workers/wakeword_worker.py
================================
Background wake word listener, behind the Settings page toggle.

Every AI profile's name is its own wake word. Hearing one switches to
that profile before the rest of the phrase is captured as a voice turn;
the longest matching name wins. See match_profile_for_phrase and
MainWindow._on_wake_word_detected.

Runs on speech_recognition's own background thread, and transcribes with
Google's free Web Speech API, the same engine ai.main.listen_mic uses.

Two things worth knowing:
  - It needs internet, is subject to free-tier rate limits, and a
    misheard phrase just does not trigger rather than raising.
  - While the assistant is speaking this doubles as the "say stop"
    listener (set_speaking_mode). With no echo cancellation the mic can
    in principle hear the assistant's own voice and mishear "stop" in it.
"""

import threading

import ai.main as ai_main
import ai.profiles as profiles
from ai.log import log
from PyQt6.QtCore import QObject, pyqtSignal
from ai.log import log_debug

# Recognized as substrings (case-insensitive) of the transcribed phrase,
# not exact matches - so "j'accepte", "vas-y j'accepte", "je refuse ça"
# all count.
#
# More than one word each, because "accept" and "refuse" were the only
# two accepted and almost nobody says either one at a confirmation
# prompt. People say yes, no, go ahead, cancel, stop. A voice
# confirmation that only answers to a word you would not think to use is
# a voice confirmation that does not work, which is exactly how it was
# reported.
#
# "ok" is deliberately absent: it appears inside "okay" fine, but also
# inside far too much ordinary speech to be safe as a substring.
CONFIRM_ACCEPT_STEMS = (
    "accept",      # accept, accepte, acceptée
    "yes",
    "yeah",
    "yep",
    "okay",
    "go ahead",
    "do it",
    "confirm",     # confirm, confirme
    "oui",
    "vas-y",
    "d'accord",
)
CONFIRM_REFUSE_STEMS = (
    "refus",       # refuse, refusé, refusée
    "no",          # also matches "nope"; see classify_confirmation for ordering
    "nope",
    "cancel",
    "don't",
    "do not",
    "never mind",
    "nevermind",
    "non",
    "annul",       # annule, annuler
)

# Kept as the old single-stem names for anything still importing them.
CONFIRM_ACCEPT_STEM = CONFIRM_ACCEPT_STEMS[0]
CONFIRM_REFUSE_STEM = CONFIRM_REFUSE_STEMS[0]

# Same substring-match approach for interrupting the assistant mid-reply:
# "stop" (used as-is in French too) and the "arrêt" stem (arrête/arrêtes/arrêter).
STOP_WORD_STEMS = ("stop", "arrêt")

# How long adjust_for_ambient_noise reads the mic before listening
# starts. It is a hard wall-clock floor - the call reads the stream for
# exactly this long and cannot return early - so it is a direct tax on
# how soon voice mode becomes usable. It was 0.5s.
#
# Shortening it is safe because it is no longer the only thing setting
# the threshold: _start_worker turns on dynamic_energy_threshold, so the
# recogniser keeps adjusting as it listens. This reading just needs to
# land in the right neighbourhood for the first utterance, and a sixth
# of a second of room tone does that. Nothing on the resume path
# calibrates at all and it has always worked.
_CALIBRATION_SECONDS = 0.15


def match_wake_word(lowered_text: str) -> "profiles.Profile | None":
    """
    Returns whichever profile's name was said, or None if no profile's
    name appears in the phrase at all. Replaces the old boolean
    matches_wake_word(...) -> bool - callers need to know WHICH profile
    matched, not just that one did, since that's what gets activated.
    """
    return profiles.match_profile_for_phrase(lowered_text)


def matches_stop_word(lowered_text: str) -> bool:
    return any(stem in lowered_text for stem in STOP_WORD_STEMS)


def classify_confirmation(lowered_text: str) -> "bool | None":
    """
    True = accept, False = refuse, None = neither.

    Refusal is checked FIRST, and that ordering is load-bearing. "no" is
    a substring of nothing dangerous, but "don't do it" contains "do it",
    and "no, cancel" contains neither problem while "yes" appears inside
    nothing at all. Checking refusal first means any phrase carrying both
    a refusal and an acceptance stem resolves as a refusal - which is the
    safe way round for a prompt whose yes starts an automation that
    drives the machine.
    """
    if any(stem in lowered_text for stem in CONFIRM_REFUSE_STEMS):
        return False
    if any(stem in lowered_text for stem in CONFIRM_ACCEPT_STEMS):
        return True
    return None


class WakeWordWorker(QObject):
    # Emits the id of whichever profile's name was heard - MainWindow
    # calls ai.profiles.activate_profile(profile_id) with it before
    # starting the voice turn (see _on_wake_word_detected). A single-
    # profile install still gets exactly one signal firing for exactly
    # one name, same observable behavior as the old parameterless
    # version of this signal.
    wake_word_detected = pyqtSignal(str)
    confirmation_word_detected = pyqtSignal(bool)  # True = accept, False = refuse
    stop_word_detected = pyqtSignal()
    error = pyqtSignal(str)
    ready = pyqtSignal()  # setup finished successfully and is now actually listening

    def __init__(self):
        super().__init__()
        self._recognizer = None
        self._mic = None
        self._stop_listening = None
        self._pending_stop = None
        self._starting = False
        # Which generation the in-flight start belongs to. A start that
        # stop() has already invalidated must not block a new one.
        self._starting_generation = -1
        self._generation = 0
        self._confirmation_mode = False
        self._speaking_mode = False

    @property
    def is_active(self) -> bool:
        return self._stop_listening is not None

    def set_confirmation_mode(self, active: bool) -> None:
        """
        While a voice-originated autonomous-mode suggestion is awaiting the
        user's answer, listening switches from the wake word to
        just "accept" / "refuse" - so the user can answer by voice without
        repeating the wake word first. MainWindow turns this on right
        before showing the confirmation card (only for suggestions that
        themselves came from voice mode) and back off as soon as it's
        answered, by whichever means.
        """
        self._confirmation_mode = active

    def set_speaking_mode(self, active: bool) -> None:
        """
        While the assistant is reading a reply aloud, listening switches from
        the wake word to just "stop"/"arrête" - lets the user
        interrupt by voice instead of only by clicking the mic button.
        MainWindow turns this on right as playback starts and back off
        once it finishes (see _on_voice_speaking_started/_finished).
        Requires the wake word listener to already be running (enabled in
        Settings) - if it isn't active, voice-triggered stop just isn't
        available and the manual click-to-stop is still the way to do it.
        """
        self._speaking_mode = active

    def start(self) -> bool:
        """
        Kick off background listening. Returns True immediately once
        setup has started (or is already running/done) - the actual
        mic/recognizer setup now happens on a plain background thread,
        not here, which is what used to make "enabling the wake word"
        feel like it took several real seconds: this ran entirely
        synchronously on whatever thread called it (always the GUI
        thread - app launch or the Settings toggle), and
        sr.Microphone()'s constructor triggers PyAudio/PortAudio
        enumerating every audio device on the system, which - ESPECIALLY
        the first time in a process - can genuinely take a real, variable
        amount of time on top of the explicit 0.5s ambient-noise
        calibration right after it. Nothing here makes that underlying
        setup itself faster (that delay lives inside PyAudio, not this
        code) - this just stops it from freezing the UI while it happens.

        Only returns False for the immediate, synchronous "clearly
        unavailable" check (SpeechRecognition/PyAudio not installed) - a
        failure discovered ON the background thread (e.g. no
        microphone present) arrives later via the `error` signal
        instead, since by definition this has already returned True by
        the time that's known. Success arrives via `ready`.
        """
        if self.is_active:
            return True
        # "Already starting" only counts if that start is still wanted.
        # Turning the wake word off and straight back on used to land
        # inside the two seconds a start takes (device enumeration plus
        # ambient calibration): stop() bumped the generation, start()
        # saw _starting and returned True without spawning anything, and
        # the in-flight thread then tore down the listener it had just
        # built. Nothing was left listening and the status sat on
        # "Starting..." until the app was restarted.
        if self._starting and self._starting_generation == self._generation:
            return True
        if ai_main.sr is None:
            self.error.emit(
                "Voice is unavailable: install 'SpeechRecognition' and 'PyAudio'."
            )
            return False
        # A fresh listening session starts with both latches down. Either
        # one stuck on makes a running listener silently ignore the wake
        # word, which looks identical to it not running at all.
        self._confirmation_mode = False
        self._speaking_mode = False
        self._starting = True
        my_generation = self._generation
        self._starting_generation = my_generation
        threading.Thread(target=self._start_worker, args=(my_generation,), daemon=True).start()
        return True

    def _start_worker(self, my_generation: int) -> None:
        """
        Runs on a plain background thread - see start()'s own docstring
        for why.

        The cold start used to take around two seconds before the mic
        would accept anything, spent in three places:

          1. `sr.Microphone()` instantiates PyAudio, which loads the
             PortAudio DLL and enumerates every host API and device, to
             resolve the default input. The first one in a process also
             pays `import pyaudio`. ai_main.prewarm_audio() now does
             this during startup, so by the time anyone toggles voice on
             the DLL is loaded and the device list is warm.
          2. `adjust_for_ambient_noise(duration=0.5)` is a literal half
             second of wall clock - it reads the stream for exactly that
             long and cannot return early. Now _CALIBRATION_SECONDS, and
             see that constant for why shortening it is safe here.
          3. Opening and closing the stream for calibration, then
             reopening it inside listen_in_background.

        _resume_worker was already the evidence that most of this is
        optional: it skips calibration entirely and nobody noticed.
        """
        try:
            recognizer = ai_main.sr.Recognizer()
            # Let the recogniser retune its own threshold as it listens
            # rather than relying on one upfront reading. This is what
            # makes a short calibration safe: a half second of silence
            # measured once was never going to stay right for the rest
            # of the session anyway - someone turns on a fan, or starts
            # listening in a quiet room and then the room fills up.
            recognizer.dynamic_energy_threshold = True
            mic = ai_main.sr.Microphone()
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=_CALIBRATION_SECONDS)
            stop_listening = recognizer.listen_in_background(mic, self._on_audio, phrase_time_limit=5)
        except Exception as e:
            self._starting = False
            self.error.emit(f"Couldn't start the wake word: {e}")
            return
        if my_generation != self._generation:
            # stop() was called while this setup was still in flight
            # discard the result instead of "reviving" a listener the
            # person just turned off. Not leaked: this fresh mic/
            # recognizer were never stored anywhere else, and
            # stop_listening(wait_for_stop=False) here tears down the
            # listen_in_background thread this call just started, the
            # same way pause() tears down any other one.
            #
            # Only clear the flag if no NEWER start has taken it over.
            # A doomed thread resetting it unconditionally would report
            # "not starting" while a fresh attempt was still in flight.
            if self._starting_generation == my_generation:
                self._starting = False
            stop_listening(wait_for_stop=False)
            return
        self._recognizer = recognizer
        self._mic = mic
        self._stop_listening = stop_listening
        self._starting = False
        self.ready.emit()

    def pause(self) -> None:
        """
        Stop listening without discarding the mic/recognizer setup - used
        while a manual voice turn (mic button or the wake word itself) is
        using the microphone, since two listeners can't share it at once.

        Deliberately non-blocking (wait_for_stop=False) - this is called
        from the GUI thread (MainWindow._on_mic_requested), and actually
        waiting for speech_recognition's background thread to finish can
        take a real, noticeable moment, which would freeze the UI right
        here. That means "paused" and "actually stopped/released" are NOT
        the same instant - see wait_until_paused() below, which is how
        the gap between them gets closed safely instead.
        """
        if self._stop_listening is not None:
            self._pending_stop = self._stop_listening
            self._pending_stop(wait_for_stop=False)
            self._stop_listening = None

    def wait_until_paused(self, timeout: float = 3.0) -> None:
        """
        Blocks the CALLING thread until the most recently paused listener
        thread has actually finished releasing its microphone - meant to
        be called from a background QThread (VoiceWorker's own, right
        before it opens its own microphone), never the GUI thread, since
        this can genuinely block for a moment.

        This is what actually closes the gap pause() leaves open: pause()
        only signals a stop and returns immediately so it's safe to call
        from the GUI thread, but that means a listener started right after
        pause() returns can still race the old one's actual teardown -
        which is exactly what crashed with speech_recognition's own
        "This audio source is already inside a context manager" assertion
        (the old listener thread hadn't actually exited its `with source:`
        block yet). Calling the SAME stop-callable again with
        wait_for_stop=True is safe even if pause() already called it once
        with wait_for_stop=False - the underlying thread.join() this uses
        is idempotent, returning immediately if the thread already exited.
        A no-op if nothing was ever paused (or this was already awaited).
        """
        pending, self._pending_stop = self._pending_stop, None
        if pending is not None:
            try:
                pending(wait_for_stop=True)
            except Exception as e:
                log_debug(f"[wakeword_worker] wait_until_paused ignored: {e}")

    def resume(self) -> None:
        if self.is_active or self._starting or self._recognizer is None:
            return
        self._starting = True
        my_generation = self._generation
        threading.Thread(target=self._resume_worker, args=(my_generation,), daemon=True).start()

    def _resume_worker(self, my_generation: int) -> None:
        """
        Runs on a plain background thread - same reasoning as
        _start_worker: constructing a fresh sr.Microphone() (see resume()'s
        own comment for why it must be fresh, not self._mic) triggers the
        same PyAudio device-enumeration cost start() has, and resume() is
        called from MainWindow's GUI thread (_reset_after_voice_capture,
        right after every voice interaction) - leaving it synchronous
        would freeze the UI there too, on top of the app-launch case
        start() already covers.
        """
        recognizer = self._recognizer
        if recognizer is None:
            self._starting = False
            return
        try:
            mic = ai_main.sr.Microphone()
            stop_listening = recognizer.listen_in_background(mic, self._on_audio, phrase_time_limit=5)
        except Exception as e:
            self._starting = False
            self.error.emit(f"Couldn't resume listening for the wake word: {e}")
            return
        if my_generation != self._generation:
            # See _start_worker's own comment on the identical check
            # same "stuck on Starting… forever" bug applies here too.
            self._starting = False
            stop_listening(wait_for_stop=False)  # stop() fired mid-setup
            return
        self._mic = mic
        self._stop_listening = stop_listening
        self._starting = False
        self.ready.emit()

    def stop(self) -> None:
        self._generation += 1  # invalidate any in-flight _start_worker/_resume_worker (see their own comments)
        self.pause()
        self._recognizer = None
        self._mic = None
        # Both latches come down with the listener. Left set, they would
        # survive into the next session and make it deaf from the start.
        self._confirmation_mode = False
        self._speaking_mode = False

    def _on_audio(self, recognizer, audio) -> None:
        # Runs on speech_recognition's own background thread, never the
        # GUI thread - signal emission across threads is safe, PyQt
        # queues delivery back to the GUI thread automatically.
        try:
            text = recognizer.recognize_google(audio, language="en-US")
        except Exception as e:
            # Was a bare `return` with zero logging - every failed
            # transcription (no network, quota exceeded, genuinely
            # unclear audio) looked IDENTICAL from the outside to the
            # wake word listener simply never having heard anything at
            # all, making "it's not working" impossible to diagnose
            # without this. Not every one of these means something's
            # wrong (plenty are just background noise that never
            # resembled speech), so this stays a plain log line, not a
            # user-facing error - the wake word status label already
            # covers genuine startup failures (see start()/_on_wake_word_error).
            log(f"[wakeword] Transcription attempt failed (still listening): {e}")
            return
        lowered = text.lower()
        log(f"[wakeword] Heard: {lowered!r}")

        if self._confirmation_mode:
            result = classify_confirmation(lowered)
            if result is True:
                ai_main.play_notification_sound("confirm_accept")
                self.confirmation_word_detected.emit(True)
            elif result is False:
                ai_main.play_notification_sound("confirm_refuse")
                self.confirmation_word_detected.emit(False)
            return  # not listening for the wake word while a confirmation is pending

        if self._speaking_mode:
            if matches_stop_word(lowered):
                self.stop_word_detected.emit()
            return  # not listening for the wake word (or anything else) while the assistant is talking

        matched_profile = match_wake_word(lowered)
        if matched_profile is not None:
            ai_main.play_notification_sound("wake_word")
            self.wake_word_detected.emit(matched_profile.id)
