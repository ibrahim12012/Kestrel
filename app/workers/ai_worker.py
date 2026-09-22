# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
import ai.main as ai_main
from ai.main import ai, ai_stream, pop_pending_autonomous_goal, pop_pending_screenshot_path

class AIWorker(QObject):

    finished = pyqtSignal(int, str)
    error = pyqtSignal(int, str)
    start = pyqtSignal(int, str)
    autonomous_suggested = pyqtSignal(int, str)   # (chat_id, goal)
    screenshot_captured = pyqtSignal(int, str)    # (chat_id, path) - emitted BEFORE finished
    terminal_output_captured = pyqtSignal(int, str, str, bool)  # (chat_id, command, output, success) - emitted BEFORE finished, once per tool_run_command() call this turn
    file_output_captured = pyqtSignal(int, str, str)  # (chat_id, path, action) - emitted BEFORE finished, once per file tool_generate_file()/tool_merge_files_to_pdf() wrote this turn
    chunk = pyqtSignal(int, str)                  # (chat_id, text_delta) - emitted repeatedly BEFORE finished, only when streaming is on
    thinking_chunk = pyqtSignal(int, str)         # (chat_id, thinking_text_delta) - emitted repeatedly BEFORE finished, only when streaming AND extended thinking are both on, and only for a model (Claude, Gemini, and GPT-5 reasoning models all qualify) that actually produces any

    def __init__(self):
        super().__init__()
        self.start.connect(self.run)

    @pyqtSlot(int, str)
    def run(self, chat_id: int, prompt: str):
        try:
            if ai_main.get_streaming_enabled():
                # emit() from inside this callback is safe across
                # threads the same way finished/error already are
                # this method runs on AIWorker's own QThread, the
                # chat/window slots connected to `chunk`/`thinking_chunk`
                # live on the GUI thread, and Qt auto-queues a
                # cross-thread signal-slot connection.
                response = ai_stream(
                    prompt,
                    lambda delta: self.chunk.emit(chat_id, delta),
                    on_thinking_delta=lambda delta: self.thinking_chunk.emit(chat_id, delta),
                )
            else:
                response = ai(prompt)
            path = pop_pending_screenshot_path()
            if path:
                # Emitted before `finished` so the screenshot bubble lands
                # in the chat ahead of the assistant's text reply, same
                # ordering ChatGPT-style tool screenshots use.
                self.screenshot_captured.emit(chat_id, path)
            for entry in ai_main.pop_pending_terminal_output():
                # Same ordering reasoning as the screenshot above - every
                # command this turn actually ran gets its own terminal
                # block ahead of the assistant's text reply about it, in
                # the order the commands themselves ran.
                self.terminal_output_captured.emit(chat_id, entry["command"], entry["output"], entry["success"])
            for entry in ai_main.pop_pending_file_outputs():
                # Same ordering reasoning again - a card per file the AI
                # wrote this turn, ahead of its text reply about them.
                self.file_output_captured.emit(chat_id, entry["path"], entry["action"])
            self.finished.emit(chat_id, response)
            goal = pop_pending_autonomous_goal()
            if goal:
                self.autonomous_suggested.emit(chat_id, goal)
        except Exception as e:
            self.error.emit(chat_id, str(e))
