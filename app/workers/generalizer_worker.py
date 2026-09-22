# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/workers/generalizer_worker.py
===================================
Backs the Recorder page's manual "Generalize" button. Wraps
ai/macro_generalizer.py's generalize_and_rewrite_macro() in a QThread
since it makes a real (network-bound) Gemini call.
"""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from ai.macro_generalizer import generalize_and_rewrite_macro


class GeneralizerWorker(QObject):
    finished = pyqtSignal(bool)   # True if the macro was generalized, False if kept literal
    error = pyqtSignal(str)
    start = pyqtSignal(str, str, list, object)   # (goal, name, actions_list, appended_block)

    def __init__(self):
        super().__init__()
        self.start.connect(self.run)

    @pyqtSlot(str, str, list, object)
    def run(self, goal: str, name: str, actions_list: list, appended_block) -> None:
        try:
            generalized = generalize_and_rewrite_macro(
                goal=goal, name=name, actions_list=actions_list, appended_block=appended_block,
            )
            self.finished.emit(generalized)
        except Exception as e:
            self.error.emit(str(e))
