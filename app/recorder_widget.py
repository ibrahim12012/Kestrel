# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/recorder_widget.py
========================
"Recorder" page - lets the user record clicks/keystrokes with global
hotkeys and save them as a reusable macro, without any AI step.

What was recorded shows up as a strip of Scratch-style BLOCKS
(app/block_list.py, vocabulary in ai/macro_blocks.py): every captured
action is a block whose values are edited inline, and the palette adds
the control and sensing ones around them - wait, repeat, forever,
branch on a colour that's on screen, or watch a pixel until it changes
(and abort if it changes to the colour it was told to treat as a
failure).

That's here, on the recording page, rather than only in the after-the-
fact macro editor, because this is where a macro is actually built: you
record the clicks, then wrap them in the logic that makes them worth
replaying, and save the whole thing once.

Reachable from two places (by design): a dedicated "Recorder" button in
the sidebar, and a "Record" button in the Macros page toolbar - both
just navigate to this same page.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QSizePolicy, QMessageBox, QInputDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread

from app.workers.recorder_worker import RecorderController
from app.workers.generalizer_worker import GeneralizerWorker
import ai.actions as actions
import ai.main as ai_main
import ai.macro_store as macro_store
import app.theme as theme
import app.icons as icons
from app.block_list import BlockListWidget
from app.undo_bar import UndoBar
from app.i18n import tr


def _current_recorder_shortcuts() -> dict[str, str]:
    """Reads the CURRENTLY configured (possibly user-changed in Settings) recorder trigger keys, upper-cased for display - same source of truth RecorderController itself reads from (see app/workers/recorder_worker.py's _current_triggers)."""
    return {
        sid: ai_main.get_shortcut(sid).upper()
        for sid in ai_main.RECORDER_SHORTCUT_IDS
    }


def _shortcuts_subtitle() -> str:
    """Long-form description shown under the page title - was hardcoded to the F6-F10 defaults, so it silently went stale the moment a user changed a recorder shortcut in Settings.

    Reworded to drop the "without going through the AI" phrasing per
    request - this page's copy shouldn't reference the assistant (by
    name or generically) at all, it's describing the recorder itself.
    """
    k = _current_recorder_shortcuts()
    return (
        "Records a sequence of clicks and text for exact, repeatable playback.\n"
        f"{k['recorder_right_click']} = right-click  ·  {k['recorder_click']} = left-click at the mouse position  ·  "
        f"{k['recorder_toggle_typing']} = toggle text mode ({k['recorder_toggle_typing']}/Esc to stop typing)  ·  "
        f"{k['recorder_enter']} = Enter key  ·  {k['recorder_stop']} = stop recording."
    )


def _shortcuts_status_line() -> str:
    """Short form shown in the status label while recording - same staleness problem as _shortcuts_subtitle, same fix."""
    k = _current_recorder_shortcuts()
    return f"Recording - {k['recorder_click']} / {k['recorder_toggle_typing']} / {k['recorder_enter']} / {k['recorder_stop']}."


def _build_style(c: dict) -> str:
    return f"""
QWidget#RecorderWidget {{ background-color: {c['bg_base']}; }}
QLabel#pageTitle {{
    color: {c['text_primary']};
    font-family: "Syne";
    font-size: 16px;
    font-weight: 700;
    background: transparent;
}}
QLabel#subtitle {{
    color: {c['text_muted']};
    font-family: "Segoe UI";
    font-size: 12px;
    background: transparent;
}}
QLabel#statusLabel {{
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    background: transparent;
    padding: 4px 0;
}}
QListWidget#blockList {{
    background-color: {c['bg_panel']};
    border: 1px solid {c['bg_input']};
    border-radius: 8px;
    color: {c['text_secondary']};
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 6px;
}}
QScrollBar:vertical {{
    background: transparent;
    border: none;
    width: 8px;
    margin: 2px 0px 2px 0px;
}}
QScrollBar::handle:vertical {{
    background: {c['bg_hover_strong']};
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
QScrollBar:horizontal {{
    background: transparent;
    border: none;
    height: 8px;
    margin: 0px 2px 0px 2px;
}}
QScrollBar::handle:horizontal {{
    background: {c['bg_hover_strong']};
    border-radius: 4px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {c['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QPushButton#btnAddBlock {{
    background-color: {c['bg_elevated']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 6px;
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 6px 12px;
}}
QPushButton#btnAddBlock:hover {{
    background-color: {c['bg_hover']};
    border-color: {c['border_strong']};
}}
QLineEdit#nameInput {{
    background-color: {c['bg_elevated']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 6px;
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 8px 10px;
}}
QTextEdit#descriptionInput {{
    background-color: {c['bg_elevated']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 6px;
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 8px 10px;
}}
QLabel#descriptionHint {{
    color: {c['text_faint']};
    font-family: "Segoe UI";
    font-size: 11px;
    background: transparent;
}}
QWidget#actionRow {{
    background-color: transparent;
}}
QLabel#actionRowLabel {{
    color: {c['text_secondary']};
    font-family: "Segoe UI";
    font-size: 12px;
    background: transparent;
}}
QPushButton#actionRowEdit, QPushButton#actionRowDelete {{
    background-color: transparent;
    border: none;
    border-radius: 4px;
    padding: 3px;
}}
QPushButton#actionRowEdit:hover {{ background-color: {c['bg_hover_strong']}; }}
QPushButton#actionRowDelete:hover {{ background-color: rgba({c['danger_rgb']},0.15); }}
QPushButton#actionRowEdit:disabled {{ background-color: transparent; }}
QPushButton#btnToggle {{
    background-color: {c['accent']};
    color: white;
    border: none;
    border-radius: 7px;
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 600;
    padding: 10px 18px;
}}
QPushButton#btnToggle:hover {{ background-color: {c['accent_hover']}; }}
QPushButton#btnToggle[recording="true"] {{
    background-color: rgba({c['danger_rgb']},0.15);
    color: {c['danger']};
    border: 1px solid rgba({c['danger_rgb']},0.35);
}}
QPushButton#btnSave, QPushButton#btnClear {{
    background-color: {c['bg_elevated']};
    color: {c['text_secondary']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 7px;
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 8px 14px;
}}
QPushButton#btnSave:hover {{ background-color: {c['bg_hover']}; color: {c['text_primary']}; }}
QPushButton#btnSave:disabled {{ color: {c['text_disabled']}; border-color: {c['bg_elevated']}; }}
QPushButton#btnClear:hover {{ background-color: rgba({c['danger_rgb']},0.15); color: {c['danger']}; }}
QPushButton#btnGeneralize {{
    background-color: transparent;
    color: {c['accent_soft']};
    border: 1px solid rgba({c['accent_rgb']},0.4);
    border-radius: 7px;
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 8px 14px;
}}
QPushButton#btnGeneralize:hover {{ background-color: rgba({c['accent_rgb']},0.12); }}
QPushButton#btnGeneralize:disabled {{ color: {c['text_disabled']}; border-color: {c['bg_hover']}; }}
"""


class RecorderWidget(QWidget):
    macro_saved = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("RecorderWidget")
        self._theme = ai_main.get_theme()
        self.setStyleSheet(_build_style(theme.colors(self._theme)))

        self._controller = RecorderController()
        # What Clear removed, until something else is recorded. Backs
        # the undo bar Clear raises.
        self._cleared_backup: list = []
        # Screen geometry captured when recording starts, saved with the
        # macro so a replay on a different monitor can warn instead of
        # misclicking (ai.actions.screen_metrics).
        self._recording_screen: dict = {}
        self._controller.action_recorded.connect(self._on_action_recorded)
        self._controller.typing_started.connect(self._on_typing_started)
        self._controller.typing_stopped.connect(self._on_typing_stopped)
        self._controller.stopped.connect(self._on_stopped)

        self._build_ui()
        self._undo_bar = UndoBar(self, self._theme)

    def set_theme(self, theme_name: str) -> None:
        self._theme = theme_name
        self.setStyleSheet(_build_style(theme.colors(theme_name)))
        self._undo_bar.set_theme(theme_name)
        self._blocks.set_theme(theme_name)

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel(tr("Recorder"))
        title.setObjectName("pageTitle")
        root.addWidget(title)

        subtitle = QLabel(_shortcuts_subtitle())
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)
        self._subtitle_label = subtitle

        self._status_label = QLabel(tr("Ready to record."))
        self._status_label.setObjectName("statusLabel")
        root.addWidget(self._status_label)

        # The same Scratch-style block strip the macro editor uses
        # (app/block_list.py) - this is where a macro is actually built,
        # so the control blocks belong here, around the clicks as
        # they're captured, rather than only being reachable after it's
        # saved. Recorded actions come in as blue "action" blocks; the
        # control and sensing blocks are added by hand from the palette
        # below the list.
        self._blocks = BlockListWidget(self._theme, kind="literal", parent=self)
        self._blocks.changed.connect(self._on_blocks_changed)
        root.addWidget(self._blocks, 1)

        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)

        self._btn_toggle = QPushButton(tr("Start recording"))
        self._btn_toggle.setObjectName("btnToggle")
        self._btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_toggle.clicked.connect(self._on_toggle_clicked)
        controls_row.addWidget(self._btn_toggle)

        self._btn_clear = QPushButton(tr("Clear"))
        self._btn_clear.setObjectName("btnClear")
        self._btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear.clicked.connect(self._on_clear_clicked)
        controls_row.addWidget(self._btn_clear)

        controls_row.addStretch()
        root.addLayout(controls_row)

        description_hint = QLabel(
            tr("Description - explains what this macro does and when it should be used.")
        )
        description_hint.setObjectName("descriptionHint")
        description_hint.setWordWrap(True)
        root.addWidget(description_hint)

        self._description_input = QTextEdit()
        self._description_input.setObjectName("descriptionInput")
        self._description_input.setPlaceholderText(
            tr("e.g. Opens Spotify and starts playing the Chill Hits playlist.")
        )
        self._description_input.setFixedHeight(56)
        root.addWidget(self._description_input)

        save_row = QHBoxLayout()
        save_row.setSpacing(8)

        self._name_input = QLineEdit()
        self._name_input.setObjectName("nameInput")
        self._name_input.setPlaceholderText(tr("Macro name (e.g. open_youtube_lofi)"))
        self._name_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        save_row.addWidget(self._name_input, 1)

        self._btn_save = QPushButton(tr("Save macro"))
        self._btn_save.setObjectName("btnSave")
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._on_save_clicked)
        save_row.addWidget(self._btn_save)

        root.addLayout(save_row)

        generalize_row = QHBoxLayout()
        generalize_row.addStretch()
        self._btn_generalize = QPushButton(tr("Generalize this macro"))
        self._btn_generalize.setObjectName("btnGeneralize")
        self._btn_generalize.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_generalize.setEnabled(False)
        self._btn_generalize.setToolTip(
            tr("Turns this fixed macro into a reusable, parameterized version "
            "(e.g. « search {term} on YouTube » instead of locked to one term).")
        )
        self._btn_generalize.clicked.connect(self._on_generalize_clicked)
        generalize_row.addWidget(self._btn_generalize)
        root.addLayout(generalize_row)

    # ── Actions list (editable/deletable rows) ───────────────────────

    def _refresh_actions_list(self) -> None:
        """
        Fully rebuilds the block strip from ai.actions.actions_list (the
        single source of truth - same list RecorderController appends to
        live and tool_name_action reads from on save) rather than
        maintaining a second, separately-tracked copy in the UI.

        That list now holds two shapes at once: the recorder's own
        tuples for what was captured, and block dicts for anything added
        from the palette (see ai/actions.py's play_actions, which
        replays both). literal_action_to_editor_step normalizes them
        into the one shape the block strip draws.
        """
        self._blocks.set_steps(
            [macro_store.literal_action_to_editor_step(a) for a in actions.actions_list]
        )

    def _on_blocks_changed(self) -> None:
        """The block strip edited something - write it straight back to
        ai.actions.actions_list, which stays the single source of truth
        that saving, generalizing and clearing all read from. Mutated in
        place rather than rebound, since ai.actions and the recorder
        controller both hold a reference to that same list object."""
        actions.actions_list[:] = [
            macro_store.editor_step_to_literal_action(s) for s in self._blocks.steps()
        ]
        has_actions = bool(actions.actions_list)
        self._btn_save.setEnabled(not self._controller.is_recording and has_actions)
        if not has_actions:
            self._status_label.setText(tr("Ready to record."))

    # ── Controller callbacks ─────────────────────────────────────────

    def _on_action_recorded(self, description: str) -> None:
        self._refresh_actions_list()
        self._blocks.scroll_to_bottom()

    def _on_typing_started(self) -> None:
        toggle_key = _current_recorder_shortcuts()["recorder_toggle_typing"]
        self._status_label.setText(f"Text mode - type, then {toggle_key} or Esc to confirm.")

    def _on_typing_stopped(self, text: str) -> None:
        self._status_label.setText(_shortcuts_status_line())

    def _on_stopped(self) -> None:
        self._btn_toggle.setText(tr("Start recording"))
        self._btn_toggle.setProperty("recording", "false")
        self._btn_toggle.style().unpolish(self._btn_toggle)
        self._btn_toggle.style().polish(self._btn_toggle)

        has_actions = bool(actions.actions_list)
        self._btn_save.setEnabled(has_actions)
        self._status_label.setText(
            f"Recording finished - {len(actions.actions_list)} action(s). "
            "Give the macro a name to save it."
            if has_actions else
            "Recording stopped - no actions captured."
        )

    # ── User actions ─────────────────────────────────────────────────

    def refresh_shortcuts_description(self) -> None:
        """
        Re-reads the current shortcuts and updates the subtitle text.
        Was previously only refreshed at construction time and again
        right as a NEW recording starts (_on_toggle_clicked below) - so
        rebinding a shortcut in Settings and then just navigating back
        to this page without starting a fresh recording still showed the
        stale description until the app was relaunched. Called by
        MainWindow._navigate() on every visit to this page (cheap - just
        rebuilds one short string, no I/O), and safe to call while a
        recording is in progress: it only touches the subtitle label,
        never actions_list/the controller.
        """
        self._subtitle_label.setText(_shortcuts_subtitle())

    def _on_toggle_clicked(self) -> None:
        if self._controller.is_recording:
            self._controller.stop()
            return

        # Recording never wipes what is already here. New actions are
        # appended, so stopping to go and work out the rest of a macro
        # costs nothing. Clear is how you empty it on purpose, and that
        # is undoable. See RecorderController.start.
        #
        # The name and description are left alone for the same reason:
        # having typed them once, resuming a recording should not make
        # you type them again.
        self._btn_save.setEnabled(False)
        self._btn_generalize.setEnabled(False)
        self._subtitle_label.setText(_shortcuts_subtitle())
        # The screen a macro is recorded on decides whether its clicks
        # mean anything on replay - see ai.actions.screen_metrics.
        self._recording_screen = actions.screen_metrics()
        self._controller.start()

        self._btn_toggle.setText(tr("Stop recording"))
        self._btn_toggle.setProperty("recording", "true")
        self._btn_toggle.style().unpolish(self._btn_toggle)
        self._btn_toggle.style().polish(self._btn_toggle)
        self._status_label.setText(_shortcuts_status_line())

    def _undo_clear(self) -> None:
        """Puts back what Clear removed. Recording something is real
        work; one misplaced click should not be able to end it."""
        if not self._cleared_backup:
            return
        actions.actions_list[:] = list(self._cleared_backup)
        self._cleared_backup = []
        self._refresh_actions_list()
        self._btn_save.setEnabled(not self._controller.is_recording and bool(actions.actions_list))
        self._status_label.setText(f"Restored {len(actions.actions_list)} action(s).")

    def _on_clear_clicked(self) -> None:
        if self._controller.is_recording:
            self._controller.stop()
        self._cleared_backup = list(actions.actions_list)
        if self._cleared_backup:
            self._undo_bar.offer(
                f"Cleared {len(self._cleared_backup)} action(s).", self._undo_clear
            )
        actions.actions_list.clear()
        self._refresh_actions_list()
        self._name_input.clear()
        self._description_input.clear()
        self._btn_save.setEnabled(False)
        self._btn_generalize.setEnabled(False)
        self._status_label.setText(tr("Ready to record."))

    def _on_save_clicked(self) -> None:
        if not actions.actions_list:
            return

        # Control blocks have to balance before this is saved - an
        # unclosed repeat would otherwise only misbehave later, at
        # replay time, far from the place it was built.
        structure_error = self._blocks.structure_error()
        if structure_error:
            QMessageBox.warning(self, "Can't save this macro yet", structure_error)
            return

        name = self._name_input.text().strip()
        if not name:
            name, ok = QInputDialog.getText(
                self, "Macro name", "Give this macro a name (snake_case):"
            )
            if not ok or not name.strip():
                return
            name = name.strip()

        description = self._description_input.toPlainText().strip()

        try:
            actions.tool_name_action(name, description=description or None)
            # Stamped onto the saved spec so a replay elsewhere can warn
            # rather than misclick. Written after the save because
            # tool_name_action owns the spec's creation.
            macro_store.attach_screen_metrics(
                name, self._recording_screen or actions.screen_metrics()
            )
            ai_main.refresh_actions()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Couldn't save the macro: {e}")
            return

        self._refresh_actions_list()
        self._name_input.clear()
        self._description_input.clear()
        self._btn_save.setEnabled(False)
        self._btn_generalize.setEnabled(bool(actions.last_saved_actions))
        self._status_label.setText(tr("Macro saved."))
        self.macro_saved.emit()

    def _on_generalize_clicked(self) -> None:
        name = actions.last_saved_name
        recorded = actions.last_saved_actions
        block = actions.last_appended_block
        if not name or not recorded:
            return

        goal, ok = QInputDialog.getText(
            self, "Generalize the macro",
            "In one sentence, what does this macro do? (helps the AI generalize it well)\n"
            "E.g.: « search for a track on YouTube and play the first result »",
            text=name.replace("_", " "),
        )
        if not ok or not goal.strip():
            return

        self._btn_generalize.setEnabled(False)
        self._btn_generalize.setText(tr("Generalizing…"))

        self._gen_thread = QThread()
        self._gen_worker = GeneralizerWorker()
        self._gen_worker.moveToThread(self._gen_thread)
        self._gen_thread.started.connect(
            lambda: self._gen_worker.start.emit(goal.strip(), name, list(recorded), block)
        )
        self._gen_worker.finished.connect(self._on_generalize_finished)
        self._gen_worker.error.connect(self._on_generalize_error)
        self._gen_worker.finished.connect(self._gen_thread.quit)
        self._gen_worker.finished.connect(self._gen_worker.deleteLater)
        self._gen_worker.error.connect(self._gen_thread.quit)
        self._gen_worker.error.connect(self._gen_worker.deleteLater)
        self._gen_thread.finished.connect(self._gen_thread.deleteLater)
        # Keep a strong reference alive until the thread actually finishes,
        # independent of self._gen_thread itself (which a second call here
        # would otherwise overwrite before this one is done, dropping the
        # only Python reference to a still-running QThread and crashing
        # with "QThread: Destroyed while thread '' is still running" - see
        # MainWindow._track_thread for the same fix applied there).
        if not hasattr(self, "_active_threads"):
            self._active_threads: list = []
        self._active_threads.append(self._gen_thread)
        self._gen_thread.finished.connect(
            lambda t=self._gen_thread: self._active_threads.remove(t) if t in self._active_threads else None
        )
        self._gen_thread.start()

    def _on_generalize_finished(self, generalized: bool) -> None:
        self._btn_generalize.setText(tr("Generalize this macro"))
        self._btn_generalize.setEnabled(False)
        ai_main.refresh_actions()
        self._status_label.setText(
            "Macro generalized (reusable with different parameters)"
            if generalized else
            "Not generic enough to transform - the recorded version stays as-is."
        )
        self.macro_saved.emit()

    def _on_generalize_error(self, error: str) -> None:
        self._btn_generalize.setText(tr("Generalize this macro"))
        self._btn_generalize.setEnabled(True)
        QMessageBox.warning(
            self, "Error",
            f"Generalization failed, but the saved macro is untouched: {error}",
        )

    # ── Lifecycle ────────────────────────────────────────────────────

    def hideEvent(self, event) -> None:
        """Stop listening for global hotkeys as soon as the user navigates away - we don't want F7/F8/F9/F10 hijacked app-wide while this page isn't visible."""
        if self._controller.is_recording:
            self._controller.stop()
        super().hideEvent(event)
