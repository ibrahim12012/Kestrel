# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/workers/autonomous_worker.py
==================================
Runs ai/main.py's run_autonomous_mode() in a background thread, after the
user has confirmed a goal the AI proposed via the AUTONOMOUS_SUGGEST
protocol (see ai/instructions.txt Section 11).
"""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from ai.main import run_autonomous_mode, get_max_automation_steps


class AutonomousWorker(QObject):
    finished = pyqtSignal(int, dict)   # (chat_id, outcome) - see ai.main._describe_outcome
    error = pyqtSignal(int, str)
    start = pyqtSignal(int, str)  # (chat_id, goal)
    human_handoff_needed = pyqtSignal(int, str)  # (chat_id, reason) - run is paused, waiting on a human
    network_retry = pyqtSignal(int, int, int)    # (chat_id, attempt, wait_seconds) - reconnecting after a network error

    def __init__(self):
        super().__init__()
        self.start.connect(self.run)

    @pyqtSlot(int, str)
    def run(self, chat_id: int, goal: str) -> None:
        try:
            outcome = run_autonomous_mode(
                goal,
                get_max_automation_steps(),
                on_human_handoff=lambda reason: self.human_handoff_needed.emit(chat_id, reason),
                on_network_retry=lambda attempt, wait_seconds: self.network_retry.emit(chat_id, attempt, wait_seconds),
            )
            # The run's own account of what happened, rather than the UI
            # assuming success. A dict, not a bare string, so a richer
            # report can be added without changing the signal again.
            self.finished.emit(chat_id, outcome or {})
        except Exception as e:
            self.error.emit(chat_id, str(e))
