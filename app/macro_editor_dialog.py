# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/macro_editor_dialog.py
=============================
Manual macro step editor - lets an already-saved macro's steps be
tweaked, reordered, deleted, or have a new one inserted, without having
to re-record the whole thing from scratch. Re-recording used to be the
ONLY fix path once a macro's recorded clicks/targets drifted from the UI
it was learned against (a redesigned page, a moved button, ...), or if
you just wanted to add/remove one step - this is what actually closes
that gap.

Steps render as Scratch-style BLOCKS (see ai/macro_blocks.py for the
vocabulary): a rounded, category-tinted row per step, indented by how
deeply it nests, with each editable value sitting inline as a "pipe" -
Scratch's rounded input slot - that's clicked to change it in place.
That's what turns a recording into something you can actually program:
wait, repeat, loop, branch on what's on screen, and watch a pixel for a
colour change (with a slot for the one colour it must NOT become, which
aborts the macro instead of carrying on).

Deliberately NOT a Scratch canvas: no free-floating scripts on an
infinite surface, no permanent palette panel. It stays a vertical list
- so every bit of existing plumbing (the two on-disk spec shapes,
saving through the same validation) works unchanged - and only borrows
the parts of Scratch that carry their weight at this size: drag to
reorder (a C-block takes its contents with it), colour-by-category,
nesting you can see, and values edited where they're read.

Works on both saved-macro "kind"s (see ai/macro_store.py):
  - "literal" - a plain recorded action sequence, replayed verbatim.
                     Steps are pure (x, y) coordinates / literal text.
  - "generalized" - parameterized, structured steps that reference an
                     on-screen target description and run through
                     vision-based clicking, with {parameter} placeholders
                     in typed text.

Opened from MacrosWidget via a small "Edit" button next to Run/Delete on
each card whose underlying macro is a genuine saved one (see
MacroCard._on_edit / the "editable" flag, which mirrors the existing
"deletable" one).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton,
    QWidget, QMessageBox,
)

from ai import macro_store
from app import theme
from app.block_list import BlockListWidget
from app.i18n import tr


class MacroEditorDialog(QDialog):
    """The main step-list editor - see this module's own docstring."""

    def __init__(self, macro_name: str, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        self._name = macro_name
        self._theme = theme_name
        self._spec = macro_store.load_spec(macro_name) or {}
        self._kind = self._spec.get("kind", "literal")
        self._parameters: list[dict] = list(self._spec.get("parameters", []))

        if self._kind == "generalized":
            raw_steps = self._spec.get("steps", [])
            self._steps = [macro_store.generalized_step_to_editor_step(s) for s in raw_steps]
        else:
            raw_actions = self._spec.get("recorded_actions", [])
            self._steps = [macro_store.literal_action_to_editor_step(a) for a in raw_actions]

        self.setWindowTitle(f"Edit macro - {self._spec.get('name', macro_name)}")
        self.resize(720, 700)
        self.setMinimumSize(520, 460)
        self._build_ui()

    # ── Building the dialog ──────────────────────────────────────────────

    def _build_ui(self):
        """
        Laid out as three stacked sections - a header, the description,
        then the blocks taking whatever's left - rather than a flat
        column of bare "Label:" + widget pairs.

        The blocks are the thing being worked on here, so they get the
        room: everything above them is fixed-height, the strip stretches,
        and the macro's kind sits in the header as a small badge instead
        of a paragraph of explanation above the fold.
        """
        c = theme.colors(self._theme)
        self.setStyleSheet(
            f"QDialog{{background-color:{c['bg_base']};}}"
            f"QLabel{{color:{c['text_primary']};font-family:'Segoe UI';}}"
            f"QTextEdit{{background-color:{c['bg_elevated']};color:{c['text_primary']};"
            f"border:1px solid {c['bg_input']};border-radius:10px;padding:10px 12px;"
            "font-size:13px;}"
            f"QTextEdit:focus{{border-color:{c['accent']};}}"
            f"QListWidget{{background-color:transparent;border:none;}}"
            f"QPushButton#btnAddBlock{{background-color:{c['bg_elevated']};"
            f"border:1px solid {c['bg_hover_strong']};border-radius:8px;"
            f"color:{c['text_primary']};font-size:12px;padding:7px 14px;}}"
            f"QPushButton#btnAddBlock:hover{{background-color:{c['bg_hover']};"
            f"border-color:{c['border_strong']};}}"
            f"QPushButton#btnSaveMacro{{background-color:{c['accent']};color:{c['white']};"
            "border:none;border-radius:8px;font-size:12.5px;font-weight:600;padding:9px 20px;}"
            f"QPushButton#btnSaveMacro:hover{{background-color:{c['accent_hover']};}}"
            f"QPushButton#btnCancelMacro{{background-color:transparent;color:{c['text_muted']};"
            f"border:1px solid {c['bg_hover_strong']};border-radius:8px;font-size:12.5px;"
            "padding:9px 18px;}"
            f"QPushButton#btnCancelMacro:hover{{color:{c['text_primary']};"
            f"border-color:{c['border_strong']};}}"
            f"QWidget#editorCard{{background-color:{c['bg_panel']};"
            f"border:1px solid {c['bg_input']};border-radius:14px;}}"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 20)
        v.setSpacing(14)

        # ── Header: name + what kind of macro this is ────────────────
        header = QHBoxLayout()
        header.setSpacing(10)

        title = QLabel(self._spec.get("name", self._name))
        title.setStyleSheet("font-size:17px;font-weight:700;")
        header.addWidget(title)

        is_generalized = self._kind == "generalized"
        badge = QLabel("Generalized" if is_generalized else "Literal")
        badge.setToolTip(
            "Blocks target described elements on screen and re-find them by "
            "vision, with {parameter} placeholders in typed text."
            if is_generalized else
            "Blocks replay the exact pixel positions that were recorded."
        )
        badge_color = c["accent"] if is_generalized else c["text_muted"]
        badge.setStyleSheet(
            f"color:{badge_color};background-color:{c['bg_elevated']};"
            f"border:1px solid {c['bg_hover_strong']};border-radius:9px;"
            "padding:2px 10px;font-size:10.5px;font-weight:700;"
        )
        header.addWidget(badge)
        header.addStretch()
        v.addLayout(header)

        # ── Description ──────────────────────────────────────────────
        desc_label = QLabel(tr("What this macro does"))
        desc_label.setStyleSheet(f"color:{c['text_muted']};font-size:11.5px;font-weight:600;")
        v.addWidget(desc_label)

        self.desc_edit = QTextEdit(self._spec.get("description", ""))
        self.desc_edit.setFixedHeight(64)
        self.desc_edit.setPlaceholderText(
            tr("e.g. Opens Spotify and starts the Chill Hits playlist.")
        )
        v.addWidget(self.desc_edit)

        # ── Blocks ───────────────────────────────────────────────────
        blocks_label = QLabel(tr("Blocks"))
        blocks_label.setStyleSheet(f"color:{c['text_muted']};font-size:11.5px;font-weight:600;")
        v.addWidget(blocks_label)

        # Carded, so the strip reads as one surface rather than rows
        # floating on the dialog's own background.
        card = QWidget()
        card.setObjectName("editorCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 10)

        # The same block strip the recorder page builds macros with (see
        # app/block_list.py) - one implementation, so a block added
        # while recording and the same block edited afterwards can't
        # drift apart in how they look or what they let you change.
        self._blocks = BlockListWidget(self._theme, self._kind, self)
        # So this macro is not offered as a block inside itself.
        self._blocks.set_editing_macro_name(self._name)
        self._blocks.set_steps(self._steps)
        card_layout.addWidget(self._blocks)
        v.addWidget(card, 1)

        # ── Footer ───────────────────────────────────────────────────
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch()

        btn_cancel = QPushButton(tr("Cancel"))
        btn_cancel.setObjectName("btnCancelMacro")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)
        footer.addWidget(btn_cancel)

        btn_save = QPushButton(tr("Save changes"))
        btn_save.setObjectName("btnSaveMacro")
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._on_save)
        footer.addWidget(btn_save)

        v.addLayout(footer)

    # ── Save ─────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        description = self.desc_edit.toPlainText().strip()

        steps = self._blocks.steps()
        # The block strip's own structural check first, so an unbalanced
        # repeat/if is reported in the editor's own terms rather than
        # surfacing as a storage-layer error.
        structure_error = self._blocks.structure_error()
        if structure_error:
            QMessageBox.warning(self, "Couldn't save changes", structure_error)
            return

        if self._kind == "generalized":
            ok, err = macro_store.update_generalized_steps(
                self._name, description, self._parameters, steps
            )
        else:
            ok, err = macro_store.update_literal_actions(
                self._name, description, steps
            )

        if not ok:
            QMessageBox.warning(self, "Couldn't save changes", err)
            return

        # Reload ai.actions from the just-updated JSON spec and rebuild
        # the AI's tool list - without this the edit would only take
        # effect the next time the app restarts.
        import ai.main as ai_main
        ai_main.refresh_actions()
        self.accept()
