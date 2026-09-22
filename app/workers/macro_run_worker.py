# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/workers/macro_run_worker.py
=================================
Runs one macro (a tool_* function from ai/actions.py, selected from the
Macros page) in a background thread - same reasoning as every other
worker in this package (AIWorker, AutonomousWorker, GeneralizerWorker):
the function being called can click things, type text, and sleep for
real seconds between steps (see ai.actions.play_actions/_type_text, and
any macro generalized by ai/macro_generalizer.py), none of which can run
on the GUI thread without freezing the entire window for the macro's
whole duration. It used to run straight on the GUI thread (inside
MainWindow._on_macro_run) - this worker is that fix.
"""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

import ai.actions as actions


class MacroRunWorker(QObject):
    finished = pyqtSignal(str, str)   # (macro_id, result - "" if the function returned None)
    error = pyqtSignal(str, str)      # (macro_id, error message)
    start = pyqtSignal(str, dict)     # (macro_id, params)

    def __init__(self):
        super().__init__()
        self.start.connect(self.run)

    @pyqtSlot(str, dict)
    def run(self, macro_id: str, params: dict) -> None:
        # Local import, not top-of-file: _coerce_macro_params still lives
        # in app/main_window.py (tests/test_main_window_helpers.py
        # imports it from there directly) - main_window.py imports THIS
        # module at module load time, so a top-level import here of
        # app.main_window would be circular. By the time this slot
        # actually runs (after the GUI is up and a macro's been
        # clicked), app.main_window is already fully loaded, so a
        # function-local import is safe - same trick ai/actions.py
        # already uses for its own imports of ai.main.
        from app.main_window import _coerce_macro_params

        func = getattr(actions, macro_id, None)
        if func is None:
            self.error.emit(macro_id, f"Macro introuvable : {macro_id}")
            return
        if not callable(func):
            self.error.emit(macro_id, f"{macro_id} is not a function")
            return

        try:
            coerced = _coerce_macro_params(func, params)
            result = func(**coerced)
            self.finished.emit(macro_id, "" if result is None else str(result))
        except Exception as e:
            self.error.emit(macro_id, str(e))
