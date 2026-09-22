# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/workers/mapping_worker.py
================================
Runs ai.actions.tool_discover_application_ui(app_name) off the GUI
thread - same reasoning as every other worker in this package
(AIWorker, AutonomousWorker, MacroRunWorker): a mapping run actively
clicks through the target application for as long as it takes to
explore it, which would otherwise freeze the whole window for that
entire duration.

Progress while running is NOT delivered through this worker's own
signals - app/mapping_monitor.py's overlay window and
app/mapping_widget.py's page both follow the SAME run directly via
ai/discovery/events.py's pub-sub (on_started/on_progress/on_finished),
which tool_discover_application_ui already drives. This worker only
needs to report the FINAL outcome (the summary string tool_discover_application_ui
returns, or an error), which is what its own finished/error signals are for.
"""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

import ai.actions as actions


class MappingWorker(QObject):
    finished = pyqtSignal(str)   # (result summary string)
    error = pyqtSignal(str)      # (error message)
    start = pyqtSignal(str)      # (app_name)

    def __init__(self):
        super().__init__()
        self.start.connect(self.run)

    @pyqtSlot(str)
    def run(self, app_name: str) -> None:
        try:
            result = actions.tool_discover_application_ui(app_name)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))
