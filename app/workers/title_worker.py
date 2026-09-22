# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/workers/title_worker.py
=============================
Names a conversation from its opening message, off the UI thread.

The chat gets the truncated-message title the instant it is sent, so
there is never a blank row in the sidebar; this replaces it a moment
later with one that describes what was actually asked. A network call on
the GUI thread would freeze the window at exactly the worst moment,
which is why it is a worker and not an inline call.

Failure is not reported anywhere: ai.main.suggest_chat_title already
falls back to the truncated title, so the worst case is the title
staying what it already was.
"""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class TitleWorker(QObject):
    # (chat_id, title)
    finished = pyqtSignal(int, str)
    start = pyqtSignal(int, str)

    def __init__(self):
        super().__init__()
        self.start.connect(self.run)

    @pyqtSlot(int, str)
    def run(self, chat_id: int, message: str) -> None:
        import ai.main as ai_main
        try:
            title = ai_main.suggest_chat_title(message)
        except Exception as e:
            from ai.log import log
            log(f"[title] Could not name chat {chat_id}: {e}", level="warn")
            return
        if title:
            self.finished.emit(chat_id, title)
