# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tools/make_sounds.py
======================
Generates the app's sound effects, assets/sounds/*.wav.

The sounds are synthesized rather than recorded, so they have no
licence attached and can be changed by editing a line here. Only needed
after changing a sound; the WAV files it writes are committed. Needs
numpy (a development tool, not an app dependency):

    python tools/make_sounds.py

Every sound is built from the same few ingredients, which is what makes
them sound like one set:

  - notes from one key (C major), in consonant steps: a rising pair or
    arpeggio means "done / yes", a falling pair means "stopped / no"
  - a mallet timbre (a struck bar: a fundamental plus a few quickly
    fading overtones) for short cues, a glass timbre for the longer
    "finished" chimes, a bell for the one that has to get your attention
  - a soft attack, so nothing starts with a click, and an exponential
    decay, so nothing stops abruptly
  - a little room reverb, which is most of the difference between a
    tone and a sound

The old sounds were winsound.Beep: a square-edged tone at a fixed
frequency, which is the PC speaker's beep and sounds like one.
"""

from __future__ import annotations

import os
import wave

import numpy as np

RATE = 44100
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "assets", "sounds")

# Equal-tempered pitches used below (Hz).
NOTE = {
    "D4": 293.66, "G4": 392.00, "A4": 440.00, "B4": 493.88,
    "C5": 523.25, "D5": 587.33, "E5": 659.26, "G5": 783.99, "A5": 880.00, "B5": 987.77,
    "C6": 1046.50, "D6": 1174.66, "E6": 1318.51, "G6": 1567.98,
}

rng = np.random.default_rng(20260927)   # fixed seed: the same file every run


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(round(seconds * RATE))) / RATE


def _attack(n: int, seconds: float) -> np.ndarray:
    """Raised-cosine fade-in, so a note starts without a click."""
    env = np.ones(n)
    a = min(n, max(1, int(seconds * RATE)))
    env[:a] = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, a))
    return env


def _tail_fade(x: np.ndarray, seconds: float = 0.012) -> np.ndarray:
    n = min(len(x), int(seconds * RATE))
    if n > 1:
        x[-n:] *= 0.5 + 0.5 * np.cos(np.linspace(0.0, np.pi, n))
    return x


def partials(freq: float, seconds: float, ratios, amps, taus, attack: float = 0.004) -> np.ndarray:
    """A note as a sum of decaying sine partials."""
    t = _t(seconds)
    out = np.zeros_like(t)
    for ratio, amp, tau in zip(ratios, amps, taus):
        f = freq * ratio
        if f >= RATE / 2.2:
            continue
        phase = rng.uniform(0, 2 * np.pi)
        out += amp * np.sin(2 * np.pi * f * t + phase) * np.exp(-t / tau)
    out *= _attack(len(t), attack)
    # Every note ends at exactly zero. Cut off while still ringing, even
    # quietly, a note ends in a click that is louder than the note's tail.
    return _tail_fade(out, min(0.06, seconds * 0.3))


def mallet(freq: float, seconds: float = 0.35, tau: float = 0.11) -> np.ndarray:
    """A struck bar: warm fundamental, a bright overtone that dies fast."""
    body = partials(freq, seconds, (1.0, 2.0, 3.93, 9.2), (1.0, 0.10, 0.16, 0.035),
                    (tau, tau * 0.5, tau * 0.22, tau * 0.08), attack=0.0025)
    # The soft "tock" of the mallet itself: a few milliseconds of filtered noise.
    n = int(0.006 * RATE)
    tick = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.0012 * RATE))
    tick = np.convolve(tick, np.ones(6) / 6, mode="same") * 0.05
    body[:n] += tick
    return body


def glass(freq: float, seconds: float = 0.9, tau: float = 0.28) -> np.ndarray:
    """A clean, slightly shimmering chime for the longer 'finished' cues."""
    note = partials(freq, seconds, (1.0, 2.0, 3.0, 4.16), (1.0, 0.28, 0.08, 0.035),
                    (tau, tau * 0.55, tau * 0.3, tau * 0.18), attack=0.005)
    # Two voices a few cents apart give the gentle beating of real glass.
    shimmer = partials(freq * 1.0035, seconds, (1.0,), (0.22,), (tau * 0.9,), attack=0.008)
    return note + shimmer


def bell(freq: float, seconds: float = 1.3, tau: float = 0.42) -> np.ndarray:
    """A small bell: inharmonic partials, the classic hum-prime-tierce set."""
    return partials(freq, seconds, (0.5, 1.0, 1.19, 1.5, 2.0, 2.74),
                    (0.12, 1.0, 0.22, 0.14, 0.2, 0.07),
                    (tau * 1.3, tau, tau * 0.8, tau * 0.6, tau * 0.45, tau * 0.25), attack=0.003)


def place(parts, total: float) -> np.ndarray:
    """Mix (start_seconds, samples, gain) events into one buffer."""
    out = np.zeros(int(total * RATE))
    for start, samples, gain in parts:
        i = int(start * RATE)
        seg = samples[: max(0, len(out) - i)]
        out[i:i + len(seg)] += gain * seg
    return out


def _comb(x: np.ndarray, delay: int, feedback: float, damp: float) -> np.ndarray:
    """Feedback comb with a one-pole low-pass in the loop (Freeverb style)."""
    y = np.zeros(len(x) + delay)
    lp = 0.0
    buf = np.concatenate([x, np.zeros(delay)])
    for i in range(len(buf)):
        prev = y[i - delay] if i >= delay else 0.0
        lp = prev * (1 - damp) + lp * damp
        y[i] = buf[i] + feedback * lp
    return y[: len(x)]


def _allpass(x: np.ndarray, delay: int, gain: float = 0.5) -> np.ndarray:
    y = np.zeros_like(x)
    for i in range(len(x)):
        xd = x[i - delay] if i >= delay else 0.0
        yd = y[i - delay] if i >= delay else 0.0
        y[i] = -gain * x[i] + xd + gain * yd
    return y


def room(x: np.ndarray, wet: float = 0.14, size: float = 0.72) -> np.ndarray:
    """A small, dark room. Enough to take the 'test tone' edge off."""
    combs = (1116, 1188, 1277, 1356)
    y = sum(_comb(x, d, size, 0.3) for d in combs) / len(combs)
    for d in (556, 441):
        y = _allpass(y, d)
    return x * (1 - wet) + y * wet


def finish(x: np.ndarray, peak_db: float) -> np.ndarray:
    x = x - np.mean(x)
    x = x * _attack(len(x), 0.001)   # the first sample is exactly zero: no click
    x = _tail_fade(x)
    peak = np.max(np.abs(x)) or 1.0
    return x / peak * (10 ** (peak_db / 20))


def shutter() -> np.ndarray:
    """A camera shutter: two short clicks, the second lower, plus a faint tick."""
    def click(center_hz: float, decay: float, length: float = 0.03) -> np.ndarray:
        n = int(length * RATE)
        noise = rng.standard_normal(n)
        # Band-limit with an FFT mask: a click is a narrow band of noise.
        spec = np.fft.rfft(noise)
        freqs = np.fft.rfftfreq(n, 1 / RATE)
        spec *= np.exp(-0.5 * ((freqs - center_hz) / (center_hz * 0.45)) ** 2)
        shaped = np.fft.irfft(spec, n)
        env = np.exp(-np.arange(n) / (decay * RATE)) * _attack(n, 0.0008)
        return shaped / (np.max(np.abs(shaped)) or 1) * env

    tick = partials(2640.0, 0.05, (1.0,), (0.25,), (0.006,), attack=0.0006)
    return place([
        (0.000, click(3400.0, 0.0045), 1.0),
        (0.002, tick, 1.0),
        (0.068, click(1900.0, 0.0065), 0.75),
    ], 0.16)


def build() -> dict:
    n = NOTE
    return {
        # The global screenshot shortcut. Short and quiet: it confirms a
        # capture, it doesn't announce one.
        "screenshot": finish(room(shutter(), wet=0.1), -13.0),

        # Mic opened / closed. Two quick mallet notes, up for "listening",
        # down for "got it". Quiet, because they happen every voice turn.
        "listen_start": finish(room(place([
            (0.000, mallet(n["G5"], 0.3, 0.07), 1.0),
            (0.055, mallet(n["D6"], 0.3, 0.08), 0.9),
        ], 0.42)), -15.0),
        "listen_end": finish(room(place([
            (0.000, mallet(n["D6"], 0.3, 0.07), 0.9),
            (0.055, mallet(n["G5"], 0.3, 0.08), 1.0),
        ], 0.42)), -16.0),

        # The wake word heard: a quick three-note rise, a touch brighter
        # than listen_start so it reads as "you called?".
        "wake_word": finish(room(place([
            (0.000, mallet(n["G5"], 0.3, 0.07), 0.9),
            (0.045, mallet(n["B5"], 0.3, 0.07), 0.9),
            (0.090, glass(n["D6"], 0.5, 0.12), 0.8),
        ], 0.6)), -12.0),

        # A spoken yes / no to a confirmation card.
        "confirm_accept": finish(room(place([
            (0.000, mallet(n["C6"], 0.3, 0.08), 1.0),
            (0.075, glass(n["G6"], 0.45, 0.11), 0.7),
        ], 0.55)), -12.0),
        "confirm_refuse": finish(room(place([
            (0.000, mallet(n["E5"], 0.3, 0.08), 1.0),
            (0.085, mallet(n["C5"], 0.35, 0.10), 1.0),
        ], 0.5)), -14.0),

        # A macro played to the end: a rising fifth.
        "macro_finished": finish(room(place([
            (0.000, glass(n["A5"], 0.6, 0.16), 1.0),
            (0.095, glass(n["E6"], 0.7, 0.2), 0.75),
        ], 0.85), wet=0.16), -11.0),

        # Autonomous mode reached its goal: a major arpeggio.
        "automation_finished": finish(room(place([
            (0.000, glass(n["C5"], 0.9, 0.22), 0.9),
            (0.075, glass(n["E5"], 0.9, 0.22), 0.85),
            (0.150, glass(n["G5"], 0.9, 0.24), 0.8),
            (0.225, glass(n["C6"], 1.0, 0.3), 0.75),
        ], 1.35), wet=0.18), -9.0),

        # Stopped, by the user or a limit. Low and short: information,
        # not an alarm.
        "automation_stopped": finish(room(place([
            (0.000, mallet(n["G4"], 0.4, 0.12), 1.0),
            (0.095, mallet(n["D4"], 0.5, 0.16), 1.0),
        ], 0.65)), -11.0),

        # A person is needed (CAPTCHA, MFA): the one sound that should be
        # noticed from across the room.
        "human_handoff": finish(room(place([
            (0.000, bell(n["A5"], 1.2, 0.38), 1.0),
            (0.200, bell(n["E5"], 1.3, 0.45), 0.9),
        ], 1.6), wet=0.18), -7.0),
    }


def write_wav(path: str, samples: np.ndarray) -> None:
    pcm = np.clip(np.round(samples * 32767), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, samples in build().items():
        path = os.path.join(OUT_DIR, f"{name}.wav")
        write_wav(path, samples)
        print(f"{name:20s} {len(samples) / RATE:5.2f} s  -> {os.path.relpath(path)}")


if __name__ == "__main__":
    main()
