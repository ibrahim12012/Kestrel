# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/block_list.py
====================
The Scratch-style block strip, as a reusable widget - the editing
surface for a macro's steps, used in both places a macro's logic gets
built:

  - app/recorder_widget.py   while/after recording, so the control
                              blocks go around the clicks that were
                              just captured, before it's ever saved.
  - app/macro_editor_dialog.py  on an already-saved macro.

That sharing is the point of it being its own module rather than
something the editor dialog owns: the recorder is where a macro is
actually built, so a block editor that only existed after saving would
be the wrong way round.

See ai/macro_blocks.py for the vocabulary itself (what blocks exist,
what slots they carry, how nesting is stored). This module only draws
it: a rounded, category-tinted row per step, indented by depth, with
each editable value inline as a "pipe" - Scratch's rounded input slot
- that's clicked to change in place.

Deliberately NOT a Scratch canvas: no free-floating scripts on an
infinite surface, no permanent palette panel. It stays a vertical list
- dragged to reorder, with a C-block carrying its contents along - which
is what keeps it compact enough to sit inside an existing page
without taking it over.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QAction, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QDialog, QDialogButtonBox, QFormLayout, QSpinBox,
    QMessageBox, QInputDialog, QMenu, QColorDialog, QAbstractItemView,
)

from ai import macro_blocks as blocks
from app import theme, icons
from app.undo_bar import UndoBar
from app.i18n import tr
from ai.log import log_debug


# ── Block palette colours ─────────────────────────────────────────────
# Scratch's own colour language, one accent per category (see
# ai/macro_blocks.py's CATEGORY_*). Fixed hex rather than theme colours:
# the point of block colours is that a category is recognisable at a
# glance from its hue alone, which it stops being if the hues shift
# with the app's light/dark chrome. Readability across both themes is
# handled by tinting the row background instead (see _block_tint).
CATEGORY_COLORS = {
    blocks.CATEGORY_ACTION: "#4c8dff",   # blue - clicking, typing, opening
    blocks.CATEGORY_CONTROL: "#ffab19",  # amber - wait/repeat/forever/if/stop
    blocks.CATEGORY_SENSING: "#22c1c3",  # teal - looking at the screen
}
_INDENT_PX = 18  # per nesting level


def _readable_on(hex_color: str) -> str:
    """Black or white, whichever actually reads on `hex_color` - a
    colour swatch chip shows its own hex on top of itself, and a
    picked colour can be anything from near-black to near-white."""
    color = QColor(hex_color)
    # Rec. 601 luma: cheap, and good enough for a two-way choice.
    luma = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue())
    return "#101010" if luma > 150 else "#f5f5f5"


def _block_tint(hex_color: str, alpha: float) -> str:
    """The block's own accent at low opacity, as an rgba() string - a
    tinted row reads as "this block is a Control block" against either
    theme's background, where the solid accent would only work against
    one of them."""
    color = QColor(hex_color)
    return f"rgba({color.red()},{color.green()},{color.blue()},{alpha:.2f})"


def _category_swatch(category: str, size: int = 10) -> QIcon:
    """
    A small rounded square in a block category's own colour, used as the
    icon on each palette entry.

    Drawn rather than shipped as an asset: there is one per category,
    the colours already live in CATEGORY_COLORS, and a painted square
    stays crisp at any display scaling where three tiny PNGs would not.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(CATEGORY_COLORS.get(category, "#888888")))
    painter.drawRoundedRect(0, 0, size, size, 3, 3)
    painter.end()
    return QIcon(pixmap)


def _add_block_style(c: dict) -> str:
    """
    The Add block button.

    It carried no stylesheet at all, so it rendered as a stock Fusion
    push button - a grey slab with a hard 2px border and square-ish
    corners, sitting directly under a list of rounded, coloured blocks.
    It was the only control on the page that did not look like it
    belonged to the app.

    Now it reads as the primary action it is: accent-tinted rather than
    grey, matching the blocks' 8px radius, with the three interactive
    states distinct enough to feel like a button rather than a label -
    the old one lifted only on hover and did nothing at all on press.
    """
    return f"""
QPushButton#btnAddBlock {{
    background-color: rgba({c['accent_rgb']},0.12);
    color: {c['accent_soft']};
    border: 1px solid rgba({c['accent_rgb']},0.28);
    border-radius: 8px;
    padding: 7px 14px 7px 11px;
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    min-height: 18px;
    text-align: center;
}}
QPushButton#btnAddBlock:hover {{
    background-color: rgba({c['accent_rgb']},0.20);
    border-color: rgba({c['accent_rgb']},0.45);
    color: {c['text_primary']};
}}
QPushButton#btnAddBlock:pressed {{
    /* Sinks rather than just darkening: the press needs to register as
       a press without the button moving enough to shift the row. */
    background-color: rgba({c['accent_rgb']},0.32);
    border-color: {c['accent']};
    padding-top: 8px;
    padding-bottom: 6px;
}}
QPushButton#btnAddBlock:focus {{
    /* Keyboard focus is its own state, not borrowed from hover - a
       focus ring that looks identical to hover tells a keyboard user
       nothing about where they are. */
    border-color: {c['accent']};
    background-color: rgba({c['accent_rgb']},0.18);
    outline: none;
}}
QPushButton#btnAddBlock:disabled {{
    background-color: transparent;
    border-color: {c['border']};
    color: {c['text_disabled']};
}}
"""


def _list_style(c: dict) -> str:
    """
    The block strip's own stylesheet.

    The scrollbar rules are here rather than inherited from the page,
    because this widget sets a stylesheet of its own and its host pages
    (the recorder and the macro editor) do not agree on what they
    style. The result was a Qt-default scrollbar on the macro page next
    to the app's slim one everywhere else. Matches app/recorder_widget's
    rules exactly; changing one means changing both.
    """
    return f"""
QListWidget {{
    background: transparent;
    border: none;
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
"""


class PointPipeDialog(QDialog):
    """
    The editor behind a {point} pipe - an (x, y) screen position.

    Offers "Pick from screen" alongside the two spin boxes because the
    useful coordinate is nearly always one you can see rather than one
    you know: it hides this dialog, waits a few seconds for the pointer
    to be moved somewhere, and reads it back. A full click-to-capture
    overlay would be the nicer version of that, and also a whole
    transparent-fullscreen-window feature; this gets the same job done
    from one button.
    """

    PICK_DELAY_SECONDS = 4

    def __init__(self, x: int, y: int, theme_name: str = "dark", parent=None):
        super().__init__(parent)
        c = theme.colors(theme_name)
        self.setWindowTitle(tr("Screen position"))
        self.setStyleSheet(
            f"QDialog{{background-color:{c['bg_base']};}}"
            f"QLabel{{color:{c['text_primary']};font-family:'Segoe UI';}}"
            f"QSpinBox{{background-color:{c['bg_panel']};color:{c['text_primary']};"
            f"border:1px solid {c['bg_input']};border-radius:6px;padding:4px;}}"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(10)

        form = QFormLayout()
        self._x = QSpinBox()
        self._x.setRange(0, 100000)
        self._x.setValue(int(x))
        self._y = QSpinBox()
        self._y.setRange(0, 100000)
        self._y.setValue(int(y))
        form.addRow("X:", self._x)
        form.addRow("Y:", self._y)
        v.addLayout(form)

        self._status = QLabel("")
        self._status.setStyleSheet(f"color:{c['text_muted']};font-size:11px;")
        self._status.setWordWrap(True)
        v.addWidget(self._status)

        btn_pick = QPushButton(f"Pick from screen ({self.PICK_DELAY_SECONDS}s)")
        btn_pick.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_pick.setToolTip(
            "Hides this window, then reads wherever the mouse pointer is "
            f"after {self.PICK_DELAY_SECONDS} seconds."
        )
        btn_pick.clicked.connect(self._on_pick)
        v.addWidget(btn_pick)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def _on_pick(self) -> None:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtGui import QCursor

        self.hide()

        def _capture():
            pos = QCursor.pos()
            self._x.setValue(max(0, pos.x()))
            self._y.setValue(max(0, pos.y()))
            self._status.setText(f"Picked ({pos.x()}, {pos.y()}).")
            self.show()

        # QCursor.pos() rather than pyautogui.position(): this is UI
        # code that already has Qt loaded, and it keeps the block editor
        # free of an import that only exists for desktop automation.
        QTimer.singleShot(self.PICK_DELAY_SECONDS * 1000, _capture)

    def get_point(self) -> "tuple[int, int]":
        return self._x.value(), self._y.value()


class BlockListWidget(QWidget):
    """
    The block strip itself. Owns a plain list of editor-step dicts (see
    ai/macro_blocks.py) and edits them in place; hosts read them back
    with steps() and are told about every change through `changed`.

    `kind` mirrors the two saved-macro shapes: a "literal" macro's
    clicks are raw recorded coordinates with no vision target to
    describe, so that pipe isn't offered for them.
    """

    changed = pyqtSignal()

    def __init__(self, theme_name: str = "dark", kind: str = "literal", parent=None):
        super().__init__(parent)
        self._theme = theme_name
        self._kind = kind
        self._steps: "list[dict]" = []
        self._depths: "list[int]" = []
        self._generation = 0  # see set_steps / _on_rows_moved
        # Which macro is being edited, when one is. Only used to keep it
        # out of its own "run a macro" list - a macro that calls itself
        # is refused at run time anyway, so offering it would produce a
        # block that cannot work.
        self._editing_macro_name: "str | None" = None
        self._build_ui()
        # Removing a block is undoable rather than confirmed up front:
        # a prompt on every delete is friction on the common case, and
        # the rare mis-click is one click back (see app/undo_bar.py).
        self._undo_bar = UndoBar(self, theme_name)

    # ── Public API ───────────────────────────────────────────────────

    def set_editing_macro_name(self, name: "str | None") -> None:
        """Tell the strip which macro it is editing, so that macro does
        not appear in its own palette. Optional - the recorder is
        building something unnamed and simply never calls this."""
        self._editing_macro_name = name

    def set_steps(self, steps: "list[dict]") -> None:
        self._steps = [dict(s) for s in steps]
        # Bumped so a drag whose rebuild is still queued can tell that
        # the list it was computed against is gone - the recorder
        # replaces these wholesale every time it captures another
        # action, which can land between a drop and the tick that
        # applies it.
        self._generation += 1
        self.refresh()

    def steps(self) -> "list[dict]":
        return [dict(s) for s in self._steps]

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self.refresh()

    def set_theme(self, theme_name: str) -> None:
        self._theme = theme_name
        self._undo_bar.set_theme(theme_name)
        # The list carries its own stylesheet, so a theme switch has to
        # re-apply it: a parent's cascade does not reach past it.
        c = theme.colors(theme_name)
        self._list.setStyleSheet(_list_style(c))
        # Same reason as the list: these carry their own stylesheets, so
        # a theme switch has to re-apply them or they keep the old one's
        # colours while everything around them changes.
        self._btn_add.setStyleSheet(_add_block_style(c))
        self._btn_add.setIcon(icons.icon("plus", c, "text_primary"))
        self._add_hint.setStyleSheet(
            f"color:{c['text_faint']};font-size:11px;background:transparent;"
        )
        self.refresh()

    def scroll_to_bottom(self) -> None:
        """Keeps the newest block in view - the recorder appends one per
        captured action while you're off using another app, so the strip
        should be showing the end of the recording, not the start."""
        self._list.scrollToBottom()

    def structure_error(self) -> str:
        """"" when the blocks balance, else why they don't - hosts show
        this before saving rather than letting a half-open repeat reach
        the storage layer."""
        ok, err = blocks.validate_structure(self._steps)
        return "" if ok else err

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self._list = QListWidget()
        self._list.setObjectName("blockList")
        self._list.setSpacing(2)
        self._list.setStyleSheet(_list_style(theme.colors(self._theme)))
        # Drag to reorder, which is how blocks are moved in Scratch and
        # what replaced the pair of up/down arrows that used to sit on
        # every row: two buttons per block, on every block, to do what
        # dragging says on its own.
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        root.addWidget(self._list, 1)

        c = theme.colors(self._theme)
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self._btn_add = QPushButton(tr(" Add block"))
        self._btn_add.setObjectName("btnAddBlock")
        self._btn_add.setIcon(icons.icon("plus", c, "text_primary"))
        self._btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add.setToolTip(tr("Insert a block after the selected one"))
        self._btn_add.setStyleSheet(_add_block_style(c))
        self._btn_add.clicked.connect(self._on_add_step)
        add_row.addWidget(self._btn_add)

        self._add_hint = QLabel(tr("Drag a block to move it · click a value inside it to change it"))
        self._add_hint.setStyleSheet(
            f"color:{c['text_faint']};font-size:11px;background:transparent;"
        )
        add_row.addWidget(self._add_hint)
        add_row.addStretch()
        # A little air above the row, so the button reads as a separate
        # action rather than as one more item stuck to the end of the
        # list it sits under.
        root.addSpacing(4)
        root.addLayout(add_row)

    # ── Drag to reorder ──────────────────────────────────────────────

    def _on_rows_moved(self, _parent, start, _end, _dest, row) -> None:
        """
        Qt has just moved a row inside its own model; this mirrors that
        onto the real step list and rebuilds from it.

        Deferred by a tick rather than applied inline: refresh() tears
        down and recreates every row widget, which can't happen while
        the model is still mid-move. Rebuilding wholesale (rather than
        patching the one row) is also what keeps item widgets correct -
        a QListWidget doesn't carry setItemWidget contents across an
        internal move.
        """
        QTimer.singleShot(
            0, lambda s=start, r=row, g=self._generation: self._apply_row_move(s, r, g)
        )

    def _apply_row_move(self, start: int, row: int, generation: int) -> None:
        """
        Moves the dragged block - and, if it's a C-block, everything it
        wraps - to where it was dropped.

        Moving a `repeat` on its own would leave its `end` behind and
        the list unbalanced, so the whole span travels together: drag a
        repeat and its contents come with it, exactly like dragging a
        C-block in Scratch. Dropping a block inside its own body can't
        happen either, since the span is lifted out before the
        insertion point is worked out.
        """
        if generation != self._generation:
            return  # the list was replaced between the drop and this tick
        if not (0 <= start < len(self._steps)):
            return
        span_start, span_end = self._block_span(start)
        moving = self._steps[span_start:span_end + 1]
        rest = self._steps[:span_start] + self._steps[span_end + 1:]

        # `row` is Qt's destinationRow: an insertion point expressed in
        # the list as it was BEFORE the move ("insert ahead of what is
        # currently at this index"). So the position in `rest` is simply
        # how many of the blocks that AREN'T moving sat ahead of it
        # no off-by-one adjustment, and no mixing of before/after
        # coordinates, which is what made dragging a C-block to the end
        # of the list quietly do nothing.
        insert_at = sum(
            1 for i in range(len(self._steps))
            if i < row and not (span_start <= i <= span_end)
        )
        insert_at = max(0, min(len(rest), insert_at))

        self._steps = rest[:insert_at] + moving + rest[insert_at:]
        self.refresh()
        self.changed.emit()
        self._list.setCurrentRow(insert_at)

    def _block_span(self, index: int) -> "tuple[int, int]":
        """The full range a block occupies - just itself for an ordinary
        one, opener through matching `end` for a C-block (see
        _block_partner_indices, which finds the partners)."""
        partners = self._block_partner_indices(index)
        return min(partners), max(partners)

    def refresh(self):
        # Recomputed on every refresh rather than tracked incrementally:
        # any move/delete/insert can change how deeply everything after
        # it nests, and one pass over the list is far cheaper than
        # getting incremental updates subtly wrong.
        self._depths = blocks.block_depths(self._steps)
        self._list.clear()
        for i, step in enumerate(self._steps):
            item = QListWidgetItem()
            row = self._make_step_row(i, step)
            item.setSizeHint(row.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row)

    def _make_step_row(self, index: int, step: dict) -> QWidget:
        """
        One block: a rounded, category-tinted pill carrying its label
        text with each editable value rendered inline as a "pipe" -
        Scratch's rounded input slot, clickable to edit in place.

        Compact on purpose: no drag-and-drop canvas, no palette panel
        taking half the dialog. It's still a vertical list (so the
        existing move/delete/save plumbing is untouched), just one that
        reads as blocks - indentation for nesting, colour for
        category, and the values sitting in the sentence rather than
        behind a separate "edit this step" dialog.
        """
        c = theme.colors(self._theme)
        spec = blocks.spec_for(step.get("type", ""))
        category = spec.category if spec else blocks.CATEGORY_ACTION
        accent = CATEGORY_COLORS.get(category, CATEGORY_COLORS[blocks.CATEGORY_ACTION])
        depth = self._depths[index] if index < len(self._depths) else 0

        w = QWidget()
        outer = QHBoxLayout(w)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(0)

        if depth:
            # The nesting "spine" - a thin indent rail rather than a
            # drawn C-block mouth, which is what keeps this compact
            # while still showing what sits inside what.
            rail = QWidget()
            rail.setFixedWidth(depth * _INDENT_PX)
            rail.setStyleSheet(
                f"background:transparent;border-left:2px solid {_block_tint(accent, 0.35)};"
            )
            outer.addWidget(rail)

        block = QWidget()
        block.setStyleSheet(
            f"background-color:{_block_tint(accent, 0.14)};"
            f"border:1px solid {_block_tint(accent, 0.45)};"
            "border-radius:9px;"
        )
        h = QHBoxLayout(block)
        h.setContentsMargins(10, 5, 6, 5)
        h.setSpacing(5)
        outer.addWidget(block, 1)

        # Grip + number: the grip is the "you can drag this" cue that
        # the up/down arrows used to be (badly), and the number keeps
        # the row identifiable when talking about a macro's steps.
        grip = QLabel("⠿")
        grip.setToolTip(tr("Drag to move this block"))
        grip.setCursor(Qt.CursorShape.SizeAllCursor)
        grip.setStyleSheet(
            f"color:{_block_tint(accent, 0.8)};font-size:12px;background:transparent;border:none;"
        )
        h.addWidget(grip, 0, Qt.AlignmentFlag.AlignVCenter)

        number = QLabel(str(index + 1))
        number.setFixedWidth(16)
        number.setStyleSheet(
            f"color:{c['text_faint']};font-size:10.5px;background:transparent;border:none;"
        )
        h.addWidget(number, 0, Qt.AlignmentFlag.AlignVCenter)

        self._build_block_content(h, index, step, spec, accent, c)
        h.addStretch()

        def _small_btn(icon_name: str, tooltip: str, handler, enabled: bool = True) -> QPushButton:
            btn = QPushButton()
            btn.setIcon(icons.icon(icon_name, c, "text_muted"))
            btn.setToolTip(tooltip)
            btn.setFixedSize(26, 26)
            btn.setEnabled(enabled)
            btn.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
            btn.setStyleSheet(
                "QPushButton{background:transparent;border:none;border-radius:5px;}"
                f"QPushButton:hover{{background-color:{c['bg_hover']};}}"
            )
            btn.clicked.connect(handler)
            return btn

        # No up/down arrows: reordering is dragging now (see
        # _on_rows_moved), which also moves a C-block's contents with
        # it - something a pair of one-step arrows could never do
        # without silently unbalancing the list.
        h = block.layout()
        h.addWidget(_small_btn("trash", "Remove this block", lambda: self._on_delete_step(index)))
        return w

    # ── Block content: label text with inline, clickable pipes ───────────

    def _build_block_content(self, layout, index: int, step: dict, spec, accent: str, c: dict) -> None:
        """
        Lays the block's template out as alternating text and pipes -
        "wait [1.5] seconds", "if colour at [100, 200] is [■]" - by
        splitting the template string on its {pipe} tokens. One shared
        template drives both this and the plain-text describe() in
        ai/macro_blocks.py, so a block can't read one way in the editor
        and another in a log line.
        """
        if spec is None:
            # A recorded shape the vocabulary doesn't recognise (an old
            # macro, or a hand-edited spec). It still gets a row, so it
            # can be seen and removed - it just has nothing editable.
            label = QLabel(blocks.describe(step))
            label.setWordWrap(True)
            label.setStyleSheet(f"color:{c['text_muted']};font-size:12px;background:transparent;border:none;")
            layout.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
            return

        pipes = {p.key: p for p in spec.pipes}
        # A literal macro's clicks are raw recorded coordinates - there
        # is no vision "target" to describe, so that pipe isn't offered
        # for them (it would just save a field nothing ever reads).
        hidden = {"target"} if self._kind != "generalized" else set()

        remaining = spec.template
        while remaining:
            token_start = remaining.find("{")
            if token_start == -1:
                self._add_block_text(layout, remaining, c)
                break
            if token_start > 0:
                self._add_block_text(layout, remaining[:token_start], c)
            token_end = remaining.find("}", token_start)
            if token_end == -1:
                self._add_block_text(layout, remaining[token_start:], c)
                break
            key = remaining[token_start + 1:token_end]
            pipe = pipes.get(key)
            if pipe is not None and key not in hidden:
                layout.addWidget(
                    self._make_pipe_chip(index, step, pipe, accent, c), 0, Qt.AlignmentFlag.AlignVCenter
                )
            remaining = remaining[token_end + 1:]

        # Pipes that aren't mentioned in the template (tolerance,
        # timeout - the fine-tuning ones) hang off the end as smaller
        # chips, so the block's sentence stays readable while they're
        # still one click away rather than buried in a dialog.
        for pipe in spec.pipes:
            if pipe.key in hidden or ("{" + pipe.key + "}") in spec.template:
                continue
            self._add_block_text(layout, f"· {pipe.label}", c, faint=True)
            layout.addWidget(
                self._make_pipe_chip(index, step, pipe, accent, c, small=True),
                0, Qt.AlignmentFlag.AlignVCenter,
            )

    def _add_block_text(self, layout, text: str, c: dict, faint: bool = False) -> None:
        text = text.strip()
        if not text:
            return
        label = QLabel(text)
        color = c["text_faint"] if faint else c["text_primary"]
        size = "10.5px" if faint else "12.5px"
        label.setStyleSheet(
            f"color:{color};font-size:{size};background:transparent;border:none;"
        )
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)

    def _make_pipe_chip(self, index: int, step: dict, pipe, accent: str, c: dict, small: bool = False) -> QPushButton:
        """One editable slot, rendered as Scratch's rounded input and
        clickable to change its value in place."""
        value = blocks.pipe_value(step, pipe)
        chip = QPushButton()
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setToolTip(f"{pipe.label} - click to change")

        if pipe.kind == blocks.PIPE_COLOR:
            # A colour slot shows the colour itself rather than its hex
            # string: the point of this block is "watch for THAT
            # colour", which a swatch answers at a glance.
            swatch = str(value or "#000000")
            chip.setText(f"  {swatch}  ")
            chip.setStyleSheet(
                f"QPushButton{{background-color:{swatch};color:{_readable_on(swatch)};"
                f"border:1px solid {_block_tint(accent, 0.7)};border-radius:8px;"
                f"padding:1px 6px;font-size:{'10px' if small else '11px'};font-family:'Consolas';}}"
            )
        else:
            chip.setText(f" {blocks.format_pipe(value, pipe)} ")
            chip.setStyleSheet(
                f"QPushButton{{background-color:{c['bg_base']};color:{c['text_primary']};"
                f"border:1px solid {_block_tint(accent, 0.7)};border-radius:8px;"
                f"padding:1px 8px;font-size:{'10.5px' if small else '12px'};}}"
                f"QPushButton:hover{{border-color:{accent};}}"
            )
        chip.clicked.connect(lambda _checked=False, i=index, p=pipe: self._on_edit_pipe(i, p))
        return chip

    def _on_edit_pipe(self, index: int, pipe) -> None:
        """Opens the right small editor for one slot's kind and writes
        the result straight back into the step."""
        step = self._steps[index]
        current = blocks.pipe_value(step, pipe)

        if pipe.kind == blocks.PIPE_COLOR:
            initial = QColor(str(current or "#ffffff"))
            # Qt's own colour dialog carries a "Pick Screen Color"
            # eyedropper, which is exactly what this slot needs - the
            # colour being watched for is one that's already on screen
            # somewhere, not one anybody knows the hex of.
            chosen = QColorDialog.getColor(
                initial, self, f"Pick the {pipe.label}",
                QColorDialog.ColorDialogOption.DontUseNativeDialog,
            )
            if chosen.isValid():
                step[pipe.key] = chosen.name()
                self.refresh()
            self.changed.emit()
            return

        if pipe.kind == blocks.PIPE_POINT:
            dlg = PointPipeDialog(current[0], current[1], self._theme, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                step["x"], step["y"] = dlg.get_point()
                self.refresh()
            self.changed.emit()
            return

        if pipe.kind == blocks.PIPE_INT:
            value, ok = QInputDialog.getInt(
                self, pipe.label, f"{pipe.label}:", int(current or 0),
                int(pipe.minimum if pipe.minimum is not None else -10_000),
                int(pipe.maximum if pipe.maximum is not None else 10_000),
            )
            if ok:
                step[pipe.key] = value
                self.refresh()
            self.changed.emit()
            return

        if pipe.kind == blocks.PIPE_NUMBER:
            value, ok = QInputDialog.getDouble(
                self, pipe.label, f"{pipe.label}:", float(current or 0.0),
                float(pipe.minimum if pipe.minimum is not None else 0.0),
                float(pipe.maximum if pipe.maximum is not None else 10_000.0), 2,
            )
            if ok:
                step[pipe.key] = value
                self.refresh()
            self.changed.emit()
            return

        text, ok = QInputDialog.getText(self, pipe.label, f"{pipe.label}:", text=str(current or ""))
        if ok:
            step[pipe.key] = text
            self.refresh()
            self.changed.emit()

    # ── Step list actions ────────────────────────────────────────────────

    def _on_delete_step(self, index: int) -> None:
        if len(self._steps) <= 1:
            QMessageBox.warning(
                self, "Can't remove the last step",
                "A macro needs at least one step. Edit it instead, or delete "
                "the whole macro from the Macros page.",
            )
            return

        # Removing one half of a C-block would leave the list
        # unbalanced and unsaveable, so the opener, its `else` and its
        # `end` go together. The CONTENTS stay - they just unindent one
        # level, which is what "I don't want this wrapped in a repeat
        # any more" almost always means. (Deleting the contents too
        # would be one undo-less click away from losing real work.)
        partners = self._block_partner_indices(index)
        step_type = self._steps[index].get("type", "")
        spec = blocks.spec_for(step_type)

        label = (spec.menu_label if spec else step_type) or "block"
        if len(partners) > 1:
            message = f"Removed the “{label}” wrapper. Its blocks are still here."
        else:
            message = f"Removed “{label}”."

        # A copy of the whole list is what Undo puts back. Restoring
        # individual indices is not enough: removing a C-block takes its
        # else and end with it, so the indices around it all move.
        before = [dict(s) for s in self._steps]
        for i in sorted(partners, reverse=True):
            del self._steps[i]
        self.refresh()
        self.changed.emit()
        self._undo_bar.offer(message, lambda snapshot=before: self._restore_steps(snapshot))

    def _restore_steps(self, snapshot: list) -> None:
        self._steps[:] = [dict(s) for s in snapshot]
        self.refresh()
        self.changed.emit()

    def _block_partner_indices(self, index: int) -> "list[int]":
        """Every index that has to be removed along with `index` - for a
        C-block opener that's its matching `else`/`end`; for a closer,
        the opener it belongs to; for anything else, just itself."""
        step_type = self._steps[index].get("type", "")
        spec = blocks.spec_for(step_type)
        if spec is None or not (spec.opens or spec.closes or spec.midpoint):
            return [index]

        if spec.opens:
            start = index
        else:
            # Walk backwards to the opener this closer/midpoint belongs to.
            depth = 0
            start = index
            for i in range(index - 1, -1, -1):
                candidate = blocks.spec_for(self._steps[i].get("type", ""))
                if candidate is None:
                    continue
                if candidate.closes:
                    depth += 1
                elif candidate.opens:
                    if depth == 0:
                        start = i
                        break
                    depth -= 1
            else:
                return [index]  # orphaned closer - remove just it

        partners = [start]
        depth = 0
        for i in range(start + 1, len(self._steps)):
            candidate = blocks.spec_for(self._steps[i].get("type", ""))
            if candidate is None:
                continue
            if candidate.opens:
                depth += 1
            elif candidate.midpoint and depth == 0:
                partners.append(i)
            elif candidate.closes:
                if depth == 0:
                    partners.append(i)
                    break
                depth -= 1
        return partners

    def _on_add_step(self) -> None:
        """
        The block palette: one menu, grouped by category, instead of a
        palette panel permanently occupying half the dialog. Picking a
        block inserts it immediately with its defaults already filled
        in - the values are then edited in place through the block's
        own pipes, so there's no "fill in this form first" step between
        wanting a block and having one.
        """
        c = theme.colors(self._theme)
        menu = QMenu(self)
        # Styled to read as a block palette rather than as a right-click
        # menu, which is what it looked like: a flat list of indented
        # text with greyed-out rows standing in for section headers.
        #
        # Three things do the work. Each entry carries a swatch in its
        # own category's colour, so the list is scannable by hue the way
        # the blocks themselves are. The headers are styled through
        # ::item:disabled - the only hook QMenu gives for a
        # non-selectable row - as small accent-tinted labels rather than
        # as disabled controls. And the rows are given real padding, so
        # it reads as a menu of things to pick rather than a compact
        # list of commands.
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {c['bg_elevated']};
                color: {c['text_primary']};
                border: 1px solid {c['border_strong']};
                border-radius: 12px;
                padding: 8px 6px;
                font-family: "Segoe UI";
                font-size: 12px;
            }}
            QMenu::item {{
                padding: 8px 22px 8px 10px;
                margin: 1px 4px;
                border-radius: 7px;
            }}
            QMenu::item:selected {{
                background-color: rgba({c['accent_rgb']},0.16);
                color: {c['text_primary']};
            }}
            QMenu::item:disabled {{
                color: {c['text_faint']};
                font-size: 10px;
                font-weight: 700;
                padding: 10px 10px 4px 10px;
                margin: 0px;
                background: transparent;
            }}
            QMenu::icon {{ padding-left: 10px; }}
            QMenu::separator {{
                height: 1px;
                background: {c['border']};
                margin: 6px 10px;
            }}
        """)

        for category in blocks.CATEGORY_ORDER:
            types_in_category = [
                t for t in blocks.ADDABLE_TYPES
                if blocks.BLOCK_SPECS[t].category == category
                # call_macro gets its own section, one entry per saved
                # macro, rather than a single generic row with an empty
                # name to fill in by hand.
                and t != "call_macro"
            ]
            if not types_in_category:
                continue
            header = QAction(blocks.CATEGORY_LABELS[category], menu)
            header.setEnabled(False)
            menu.addAction(header)
            for step_type in types_in_category:
                spec = blocks.BLOCK_SPECS[step_type]
                action = QAction(spec.menu_label, menu)
                action.setIcon(_category_swatch(category))
                action.triggered.connect(
                    lambda _checked=False, t=step_type: self._insert_block(t)
                )
                menu.addAction(action)
                # A colour test is offered both ways, exactly like
                # Scratch's separate "if" and "if/else" blocks - which
                # branch structure you want is a decision made when the
                # block goes in, not something to retrofit afterwards.
                if step_type == "if_color":
                    with_else = QAction(tr("If colour is… / else"), menu)
                    with_else.setIcon(_category_swatch(category))
                    with_else.triggered.connect(
                        lambda _checked=False: self._insert_block("if_color", with_else=True)
                    )
                    menu.addAction(with_else)
            menu.addSeparator()

        self._add_macro_entries(menu)
        menu.exec(self._btn_add.mapToGlobal(self._btn_add.rect().bottomLeft()))

    def _add_macro_entries(self, menu: QMenu) -> None:
        """
        Every saved macro, as a block you can drop into this one.

        This is what makes a macro library composable rather than a flat
        list of recordings. A login sequence recorded once becomes the
        first step of everything that needs a login, and when the login
        changes you fix it in one place instead of re-recording every
        macro that happened to contain it.

        Listed by name rather than offered as one generic "run a macro"
        row, because the useful question is "which of mine?" and the
        answer is already on disk. Read fresh each time the palette
        opens, so a macro saved a minute ago is in here.
        """
        try:
            from ai import macro_store
            names = macro_store.list_macro_names()
        except Exception as e:
            log_debug(f"[block_list] Couldn't list macros for the palette: {e}")
            return

        # A macro cannot contain itself. Offering it would only produce a
        # block that refuses to run (see actions._run_saved_macro).
        current = (self._editing_macro_name or "").strip().lower()
        names = [n for n in names if n.strip().lower() != current]
        if not names:
            return

        header = QAction(tr("Macros"), menu)
        header.setEnabled(False)
        menu.addAction(header)

        for name in names:
            label = name.replace("_", " ")
            action = QAction(label, menu)
            action.setIcon(_category_swatch(blocks.CATEGORY_ACTION))
            action.triggered.connect(
                lambda _checked=False, n=name: self._insert_macro_call(n)
            )
            menu.addAction(action)

    def _insert_macro_call(self, macro_name: str) -> None:
        """Insert a `run macro <name>` block with the name already
        filled in - picking it from the list IS choosing the name, so
        there is nothing left to type."""
        self._insert_block("call_macro", values={"macro": macro_name})

    def _insert_block(self, step_type: str, with_else: bool = False,
                      values: "dict | None" = None) -> None:
        """
        Inserts a new block after whichever one is selected (or at the
        end), and - for a C-block - its matching `end` along with it.

        Creating the `end` here, rather than leaving it to be added by
        hand, is what keeps the list balanced by construction: an
        editor where you can create a `repeat` with no `end` is one
        where saving can fail with a structure error you then have to
        work out how to fix.
        """
        first = blocks.new_step(step_type)
        if values:
            # Pipe values known at insert time, so the block arrives
            # complete. Picking a macro by name from the palette IS
            # choosing the value; making the user then click the slot
            # and retype what they just picked would be busywork.
            first.update(values)
        new_steps = [first]
        spec = blocks.BLOCK_SPECS[step_type]
        if spec.opens:
            if with_else:
                new_steps.append({"type": "else"})
            new_steps.append({"type": "end"})

        row = self._list.currentRow()
        at = len(self._steps) if row < 0 else row + 1
        self._steps[at:at] = new_steps
        self.refresh()
        self.changed.emit()
        # Leave the new block selected so several in a row can be added
        # without reaching back for the list each time.
        self._list.setCurrentRow(at)

    # ── Save ─────────────────────────────────────────────────────────────
