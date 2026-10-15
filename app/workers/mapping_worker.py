# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/workers/mapping_worker.py
================================
Runs a mapping run for the Mapping page off the GUI thread - same
reasoning as every other worker in this package (AIWorker,
AutonomousWorker, MacroRunWorker): a mapping run clicks through the
target application for as long as it takes to explore it, which would
otherwise freeze the whole window for that entire duration.

Progress while running is NOT delivered through this worker's own
signals - app/mapping_monitor.py's overlay window and
app/mapping_widget.py's page both follow the SAME run directly via
ai/discovery/events.py's pub-sub (on_started/on_progress/on_finished).
This worker reports the outcome: ai.actions.MappingResult, which the
page words for itself (it used to get the text written for the AI, map
outline and all).

The page always maps afresh (force_remap): pressing Start is asking for
a new map. And the AI's action budget for a run (ai/safety.py) isn't
counted: it is there for what the AI does.
"""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

import ai.actions as actions


class MappingWorker(QObject):
    finished = pyqtSignal(object)  # ai.actions.MappingResult
    error = pyqtSignal(str)        # (error message) - only for something unexpected
    start = pyqtSignal(str)        # (app_name)

    def __init__(self):
        super().__init__()
        self.start.connect(self.run)

    @pyqtSlot(str)
    def run(self, app_name: str) -> None:
        try:
            result = actions.run_mapping(app_name, force_remap=True, ask_limiter=False)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))
