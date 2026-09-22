# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/workers/speak_worker.py
=============================
Plays a text reply aloud via ai.main.speak_reply(), on a background
thread so TTS generation + playback (both blocking calls) don't freeze
the UI. Used only for voice-originated chat turns (see MainWindow's mic
button flow, _speak_response) - a typed message never triggers this.

Deliberately separate from VoiceWorker (which only records/transcribes
now, see voice_worker.py): recording and speaking never happen at the
same time and don't share any mic-in-use bookkeeping, so two small
single-purpose workers are simpler to reason about than one worker doing
both - and it's what lets speaking start only once the actual AI reply
(from the normal text pipeline) is ready, rather than needing VoiceWorker
itself to make the AI call.

Stopping mid-reply is NOT handled here - ai.main.stop_speaking() sets a
flag that speak_reply() itself polls while audio is playing (see its
docstring); this worker doesn't need to know about that at all, it just
calls speak_reply() and reports when it returns.
"""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from ai.main import speak_reply


class SpeakWorker(QObject):
    speaking_started = pyqtSignal(int)   # (chat_id) - about to start TTS generation/playback
    speaking_finished = pyqtSignal(int)  # (chat_id) - always fires: done, interrupted, or failed
    start = pyqtSignal(int, str)         # (chat_id, text)

    def __init__(self):
        super().__init__()
        self.start.connect(self.run)

    @pyqtSlot(int, str)
    def run(self, chat_id: int, text: str) -> None:
        self.speaking_started.emit(chat_id)
        try:
            if text:
                speak_reply(text)
        except Exception as e:
            print(f"[voice] TTS playback failed: {e}")
        finally:
            # Unconditional: whatever happened above, the mic (and the
            # wake-word listener's normal "Kestrel" mode) must come back
            # see MainWindow._on_voice_speaking_finished.
            self.speaking_finished.emit(chat_id)
