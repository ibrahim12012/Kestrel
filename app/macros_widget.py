# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
macros_widget.py
----------------
"Macros" page - list of runnable macros with optional parameters.

Structure:
  MacrosWidget (QWidget loaded from macros_widget.ui)
    ├── topbar: title + counter + Refresh button
    ├── filterBar: search + filter
    └── macrosScroll > macrosContents > cardsGrid (QGridLayout)
          └── MacroCard (widgets created dynamically)

Usage:
    widget = MacrosWidget()
    widget.load_macros(macros_data)   # see format below

Data format:
    macros_data = [
        {
            "id": "macro_001",
            "name": "Close Application",
            "badge": "custom",          # "ai" | "user" | "custom"
            "description": "",          # optional
            "params": [                 # list of params (can be empty)
                {"name": "app_name", "type": "str", "placeholder": ""}
            ],
        },
        ...
    ]
"""

from __future__ import annotations

from PyQt6 import uic
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSizePolicy,
    QGridLayout, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal

from paths import BASE_PATH
import branding
import app.theme as theme
import app.icons as icons
import ai.main as ai_main
from app.styled_dropdown import StyledDropdown
from app.undo_bar import UndoBar
from app.i18n import tr

# Values shown in the filter dropdown (also doubles as its display
# labels - see MacrosWidget.__init__). Kept as a module-level constant
# rather than re-declared inline so _rebuild_cards'/_apply_filters'
# "All" default and the dropdown's own options never drift apart.
_FILTER_OPTIONS = [
    ("All", "All"),
    ("Recently used", "Recently used"),
    ("Recently created", "Recently created"),
    ("Created by AI", "Created by AI"),
    ("Created by User", "Created by User"),
]

root_path = BASE_PATH


def _build_style(c: dict) -> str:
    """Regenerates app/ui/macros_widget.ui's root styleSheet property from theme tokens - same reasoning as chat_widget._build_chrome_style. MacroCard and its children have no local stylesheet of their own (unlike chat_widget's chatInput), so this alone re-skins every already-built card live, no rebuild needed."""
    return f"""
QWidget#MacrosWidget {{
    background-color: {c['bg_base']};
}}
QWidget#topbar {{
    background-color: {c['bg_base']};
    border-bottom: 1px solid {c['bg_input']};
    min-height: 52px;
    max-height: 52px;
}}
QLabel#pageTitle {{
    color: {c['text_primary']};
    font-family: "Syne";
    font-size: 14px;
    font-weight: 600;
    background: transparent;
}}
QLabel#toolCount {{
    color: {c['text_muted']};
    font-family: "Segoe UI";
    font-size: 11px;
    background: transparent;
    padding: 2px 8px;
    border: 1px solid {c['border']};
    border-radius: 8px;
}}
QPushButton#btnRefresh, QPushButton#btnRecordMacro {{
    background-color: transparent;
    color: {c['text_disabled']};
    border: 1px solid {c['bg_input']};
    border-radius: 7px;
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    padding: 0px;
}}
QPushButton#btnRefresh:hover, QPushButton#btnRecordMacro:hover {{
    background-color: {c['bg_elevated']};
    color: {c['text_primary']};
    border-color: {c['border_strong']};
}}
QWidget#filterBar {{
    /* Bug fix: this used to be a hard 48px (min/max-height, so genuinely
       fixed either way), with 10px top+bottom margins on filterBarLayout
       eating 20px of that - leaving only 28px of actual content height
       for searchInput below, which itself needs a bit more than its own
       28px min/max-height once its border+padding are added on top (Qt's
       style sheet box model adds border/padding OUTSIDE min/max-height,
       unlike CSS's border-box default) - the box was being asked to fit
       into less room than it needed, and visibly cropped as a result.
       56px here, and searchInput's own height bumped a little too below,
       leaves comfortable room either way. */
    background-color: {c['bg_base']};
    border-bottom: 1px solid {c['bg_elevated']};
    min-height: 56px;
    max-height: 56px;
}}
QLineEdit#searchInput {{
    background-color: {c['bg_panel']};
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
    border: 1px solid {c['bg_input']};
    border-radius: 8px;
    padding: 6px 12px;
    min-height: 32px;
    max-height: 32px;
    selection-background-color: rgba({c['accent_rgb']},0.35);
}}
QLineEdit#searchInput:focus {{
    border-color: {c['border_strong']};
}}
QScrollArea#macrosScroll {{
    background-color: {c['bg_base']};
    border: none;
}}
QScrollArea#macrosScroll > QWidget > QWidget {{
    background-color: {c['bg_base']};
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
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{ height: 0px; subcontrol-position: bottom; subcontrol-origin: margin; }}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{ background: transparent; }}
QWidget#macroCard {{
    background-color: {c['bg_panel']};
    border: 1px solid {c['bg_input']};
    border-radius: 10px;
}}
QWidget#macroCard:hover {{
    border-color: {c['border_strong']};
    background-color: {c['bg_elevated']};
}}
QLabel#macroName {{
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 600;
    background: transparent;
}}
QLabel#badgeAI {{
    color: {c['accent_soft']};
    font-family: "Segoe UI";
    font-size: 9px;
    font-weight: 700;
    background-color: rgba({c['accent_rgb']},0.12);
    border: 1px solid rgba({c['accent_rgb']},0.4);
    border-radius: 4px;
    padding: 1px 6px;
}}
QLabel#badgeUser {{
    color: {c['text_muted']};
    font-family: "Segoe UI";
    font-size: 9px;
    font-weight: 700;
    background-color: transparent;
    border: 1px solid {c['border']};
    border-radius: 4px;
    padding: 1px 6px;
}}
QLabel#badgeCustom {{
    color: {c['text_faint']};
    font-family: "Segoe UI";
    font-size: 9px;
    font-weight: 700;
    background-color: transparent;
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 4px;
    padding: 1px 6px;
}}
QLabel#badgeStarter {{
    color: {c['text_faint']};
    font-family: "Segoe UI";
    font-size: 9px;
    font-weight: 700;
    background-color: transparent;
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 4px;
    padding: 1px 6px;
}}
QLabel#macroDesc {{
    color: {c['text_disabled']};
    font-family: "Segoe UI";
    font-size: 11px;
    background: transparent;
}}
QLabel#paramName {{
    color: {c['text_muted']};
    font-family: "Courier New";
    font-size: 11px;
    background: transparent;
    min-width: 100px;
}}
QLabel#paramType {{
    color: {c['text_disabled']};
    font-family: "Courier New";
    font-size: 11px;
    background: transparent;
}}
QLineEdit#paramInput {{
    background-color: {c['bg_base']};
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
    border: 1px solid {c['bg_input']};
    border-radius: 6px;
    padding: 4px 10px;
    min-height: 26px;
    max-height: 26px;
}}
QLineEdit#paramInput:focus {{
    border-color: {c['border_strong']};
}}
QPushButton#btnRun {{
    background-color: {c['accent']};
    color: {c['white']};
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    border: none;
    border-radius: 7px;
    min-width: 72px;
    max-width: 72px;
    min-height: 30px;
    max-height: 30px;
    padding: 0px 12px;
}}
QPushButton#btnRun:hover {{
    background-color: {c['accent_hover']};
}}
QPushButton#btnRun:pressed {{
    background-color: {c['accent_pressed']};
}}
"""

# ── Badge helpers ──────────────────────────────────────────────────────────────

_BADGE_OBJECT_NAME = {
    "ai": "badgeAI",
    "user": "badgeUser",
    "custom": "badgeCustom",
    "starter": "badgeStarter",
}
_BADGE_TEXT = {
    "ai": "AI",
    "user": "USER",
    "custom": "CUSTOM",
    "starter": "BUILT-IN",
}

COLUMNS = 1   # number of columns in the grid


# ── MacroCard ─────────────────────────────────────────────────────────────────

class MacroCard(QWidget):
    """Card representing a macro with its parameters and Run button."""

    run_requested = pyqtSignal(str, dict)   # (macro_id, {param_name: value})
    delete_requested = pyqtSignal(str, str)   # (macro_id, display name - for the confirmation prompt)
    edit_requested = pyqtSignal(str, str)   # (macro_id, display name)
    share_requested = pyqtSignal(str)       # (macro_id) - write this one out as a shareable file

    def __init__(self, macro: dict, parent: QWidget | None = None, theme_name: str = "dark"):
        super().__init__(parent)
        self._macro = macro
        self._theme = theme_name
        self._param_inputs: dict[str, QLineEdit] = {}

        self.setObjectName("macroCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(110)
        self._build_ui()

    # ── Building the card ──────────────────────────────────────────────────────

    def _build_ui(self):
        macro = self._macro

        root_v = QVBoxLayout(self)
        root_v.setContentsMargins(14, 12, 14, 12)
        root_v.setSpacing(8)

        # ── Title + badge row ──────────────────────────────────────────────────
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.setContentsMargins(0, 0, 0, 0)

        name_label = QLabel(macro.get("name", ""))
        name_label.setObjectName("macroName")

        badge_key = macro.get("badge", "custom").lower()
        badge = QLabel(_BADGE_TEXT.get(badge_key, "CUSTOM"))
        badge.setObjectName(_BADGE_OBJECT_NAME.get(badge_key, "badgeCustom"))

        title_row.addWidget(name_label)
        title_row.addWidget(badge)
        title_row.addStretch()

        # "starter" (built-in, hand-written) functions can't actually be
        # edited or deleted - tool_delete_macro() only ever matches
        # genuine saved macros, and there's no JSON spec file for the
        # editor to open either - so both icons are omitted entirely for
        # these rather than shown-but-silently-does-nothing when clicked,
        # which was the actually confusing part.
        if macro.get("deletable", True):
            # Share sits with Edit and Delete for the same reason they
            # do: it only means anything for a genuine saved macro. A
            # built-in ships with the app - there's nothing to hand
            # anyone that they don't already have.
            btn_share = QPushButton()
            btn_share.setObjectName("btnEditMacro")  # same card-button chrome
            btn_share.setIcon(icons.icon("share", theme.colors(self._theme), "text_disabled"))
            btn_share.setToolTip(tr("Share this macro as a file"))
            btn_share.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_share.setFixedSize(24, 24)
            btn_share.clicked.connect(self._on_share)
            title_row.addWidget(btn_share)

            btn_edit = QPushButton()
            btn_edit.setObjectName("btnEditMacro")
            btn_edit.setIcon(icons.icon("edit", theme.colors(self._theme), "text_disabled"))
            btn_edit.setToolTip(tr("Edit this macro's steps"))
            btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_edit.setFixedSize(24, 24)
            btn_edit.clicked.connect(self._on_edit)
            title_row.addWidget(btn_edit)

            btn_delete = QPushButton()
            btn_delete.setObjectName("btnDeleteMacro")
            btn_delete.setIcon(icons.icon("trash", theme.colors(self._theme), "text_disabled"))
            btn_delete.setToolTip(tr("Delete this macro"))
            btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_delete.setFixedSize(24, 24)
            btn_delete.clicked.connect(self._on_delete)
            title_row.addWidget(btn_delete)

        root_v.addLayout(title_row)

        # ── Description (optional) ─────────────────────────────────────────────
        desc_text = macro.get("description", "").strip()
        if desc_text:
            desc = QLabel(desc_text)
            desc.setObjectName("macroDesc")
            desc.setWordWrap(True)
            root_v.addWidget(desc)

        # ── Parameters ─────────────────────────────────────────────────────────
        params = macro.get("params", [])
        for param in params:
            param_row = QHBoxLayout()
            param_row.setSpacing(8)
            param_row.setContentsMargins(0, 0, 0, 0)

            pname = QLabel(f"{param['name']}:")
            pname.setObjectName("paramName")
            pname.setFixedWidth(100)

            pinput = QLineEdit()
            pinput.setObjectName("paramInput")
            # Placeholder = the parameter's type (e.g. "str", "int"…)
            pinput.setPlaceholderText(param.get("type", "str"))
            self._param_inputs[param["name"]] = pinput

            param_row.addWidget(pname)
            param_row.addWidget(pinput, 1)

            root_v.addLayout(param_row)

        # ── Spacer to push the Run button to the bottom ───────────────────────
        root_v.addStretch(1)

        # ── Run button (right-aligned) ─────────────────────────────────────────
        run_row = QHBoxLayout()
        run_row.setSpacing(0)
        run_row.setContentsMargins(0, 4, 0, 0)
        run_row.addStretch()

        btn_run = QPushButton(tr(" Run"))
        btn_run.setIcon(icons.icon("play", theme.colors(self._theme), "white"))
        btn_run.setObjectName("btnRun")
        btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_run.clicked.connect(self._on_run)

        run_row.addWidget(btn_run)
        root_v.addLayout(run_row)

    # ── Slot ──────────────────────────────────────────────────────────────────

    def _on_run(self):
        values = {name: widget.text() for name, widget in self._param_inputs.items()}
        self.run_requested.emit(self._macro["id"], values)

    def _on_delete(self):
        self.delete_requested.emit(self._macro["id"], self._macro.get("name", self._macro["id"]))

    def _on_edit(self):
        self.edit_requested.emit(self._macro["id"], self._macro.get("name", self._macro["id"]))

    def _on_share(self):
        self.share_requested.emit(self._macro["id"])

    def retheme(self, theme_name: str) -> None:
        """
        Re-applies theme-dependent icon colors to this card's buttons
        without destroying/recreating any widgets - the QSS cascade from
        MacrosWidget.setStyleSheet() already re-skins colors/borders/text
        live on its own; this only exists because a QIcon is a static
        bitmap baked in at construction time; unlike the rest of the
        card, it does NOT automatically follow a later stylesheet
        change. The delete button's icon specifically uses a
        theme-dependent color ("text_disabled") - the Run button's is
        fixed at "white" in both themes, so it's re-set here too only
        for consistency/future-proofing, not because it's currently at
        risk of going stale.
        """
        self._theme = theme_name
        c = theme.colors(theme_name)
        btn_run = self.findChild(QPushButton, "btnRun")
        if btn_run is not None:
            btn_run.setIcon(icons.icon("play", c, "white"))
        btn_delete = self.findChild(QPushButton, "btnDeleteMacro")
        if btn_delete is not None:
            btn_delete.setIcon(icons.icon("trash", c, "text_disabled"))
        btn_edit = self.findChild(QPushButton, "btnEditMacro")
        if btn_edit is not None:
            btn_edit.setIcon(icons.icon("edit", c, "text_disabled"))

    # ── Visibility (filtering) ─────────────────────────────────────────────────

    def matches(self, search: str, badge_filter: str) -> bool:
        """Returns True if the card should be visible given the active filters."""
        name = self._macro.get("name", "").lower()
        badge = self._macro.get("badge", "custom").lower()

        if search and search not in name:
            return False

        if badge_filter == "Created by AI" and badge != "ai":
            return False
        if badge_filter == "Created by User" and badge != "user":
            return False

        return True


# ── MacrosWidget ───────────────────────────────────────────────────────────────

class MacrosWidget(QWidget):
    """
    Main page listing all available macros.

    Signals:
        macro_run_requested(macro_id, params) - emitted when the user
            clicks Run on a card.
        macro_delete_requested(macro_id, display_name) - emitted when
            the user clicks the trash icon on a card. MacrosWidget
            doesn't delete anything itself or ask for confirmation - it
            just relays the request; main_window.py owns both (see
            _on_macro_delete_requested), the same division of
            responsibility as macro_run_requested.
        macro_edit_requested(macro_id, display_name) - emitted when the
            user clicks the pencil icon on a card, so the (manual, real
            step-list) editor can be opened - see _on_macro_edit_requested
            in main_window.py, same division of responsibility again.
    """

    macro_run_requested = pyqtSignal(str, dict)
    macro_delete_requested = pyqtSignal(str, str)
    macro_edit_requested = pyqtSignal(str, str)
    macro_imported = pyqtSignal()           # macros arrived from a file - the page reloads itself

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        uic.loadUi(fr"{root_path}/app/ui/macros_widget.ui", self)

        self._theme = ai_main.get_theme()
        self.setStyleSheet(_build_style(theme.colors(self._theme)))

        self._cards: list[MacroCard] = []
        self._macros_data: list[dict] = []

        # Deleting a macro is undoable from here rather than guarded by a
        # confirmation dialog on every single delete (see app/undo_bar.py).
        self._undo_bar = UndoBar(self, self._theme)

        # Shortcuts to the .ui's widgets
        self._grid: QGridLayout = self.cardsGrid.layout()

        # Filter dropdown - StyledDropdown rather than a plain QComboBox,
        # same reasoning as chat_widget.py's model picker (see the .ui
        # file's comment at this spot): a native QComboBox popup can't be
        # fully reskinned via QSS alone (square corners, mismatched arrow,
        # inconsistent row highlight), which is what "the css of the
        # dropdown" being off was. Built here and inserted into the .ui's
        # filterBarLayout (right after searchInput) instead of living in
        # the .ui itself, same pattern as StyledDropdown's other call sites.
        self._filterDropdown = StyledDropdown(_FILTER_OPTIONS, "All", self._theme, min_width=150)
        self._filterDropdown.setObjectName("filterCombo")
        # Bug fix: StyledDropdown's button has no explicit height of its
        # own (see styled_dropdown.py - it's sized off padding+font
        # sizeHint), while searchInput right next to it in this same
        # filterBar has an explicit min/max-height in the stylesheet
        # above. The two never matched, so the filter box visibly sat
        # shorter than the search box. Pin it to searchInput's own
        # rendered height instead of a hardcoded number, so it keeps
        # matching even if searchInput's height rule above ever changes.
        self._filterDropdown.set_fixed_height(self.searchInput.sizeHint().height())
        self.filterBarLayout.addWidget(self._filterDropdown)

        # Import/export live next to Record and Refresh rather than in
        # the .ui, for the same reason those two get their icons in
        # Python: the icon colour has to track theme changes. A macro
        # is a self-contained JSON spec (ai/macro_store.py), so moving
        # one between machines - or handing it to someone else - was
        # only ever missing the two buttons, not a format.
        toolbar = self.btnRefresh.parentWidget().layout()
        insert_at = toolbar.indexOf(self.btnRecordMacro)

        self.btnImportMacros = QPushButton()
        self.btnImportMacros.setObjectName("btnRecordMacro")  # same toolbar chrome
        self.btnImportMacros.setToolTip(tr("Import macros from a file"))
        self.btnImportMacros.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btnImportMacros.clicked.connect(self._on_import_macros)
        toolbar.insertWidget(insert_at, self.btnImportMacros)

        self.btnExportMacros = QPushButton()
        self.btnExportMacros.setObjectName("btnRecordMacro")
        self.btnExportMacros.setToolTip(tr("Export all your macros to a file"))
        self.btnExportMacros.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btnExportMacros.clicked.connect(self._on_export_all_macros)
        toolbar.insertWidget(insert_at + 1, self.btnExportMacros)

        # Connections
        self.searchInput.textChanged.connect(self._apply_filters)
        self._filterDropdown.currentValueChanged.connect(self._apply_filters)
        self.btnRefresh.clicked.connect(self.refresh)

        self._apply_toolbar_icons()

    def _apply_toolbar_icons(self) -> None:
        """
        btnRecordMacro/btnRefresh are icon-only (see macros_widget.ui -
        their text is blank; a recorder-shaped icon and a refresh icon
        replace what used to be a French-language emoji label). Applied
        in Python rather than baked into the .ui because the icon color
        has to track theme changes, same as every other themed icon in
        this app (see set_theme() below).
        """
        c = theme.colors(self._theme)
        self.btnRecordMacro.setIcon(icons.icon("target", c, "text_muted"))
        self.btnRefresh.setIcon(icons.icon("refresh", c))
        self.btnImportMacros.setIcon(icons.icon("arrow-down", c, "text_muted"))
        self.btnExportMacros.setIcon(icons.icon("arrow-up", c, "text_muted"))

    # ── Import / export / share ──────────────────────────────────────

    def _on_import_macros(self) -> None:
        """
        Reads macros out of an export file and writes them into this
        install. A name that's already taken is reported rather than
        overwritten unless the user says to - importing a file that
        happens to share a name shouldn't be a way to lose a macro you
        built yourself.
        """
        from PyQt6.QtWidgets import QFileDialog
        from ai import macro_store

        # The legacy suffixes are in the filter, not just accepted by the
        # parser: a file the importer would happily read is no use if the
        # dialog greys it out. Exports made before the extension changed
        # are still on people's disks.
        patterns = " ".join(
            f"*{suffix}" for suffix in
            (macro_store.EXPORT_SUFFIX, *branding.LEGACY_EXPORT_SUFFIXES)
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "Import macros", "",
            f"{branding.APP_NAME} macros ({patterns} *.json);;All files (*)",
        )
        if not path:
            return

        try:
            specs = macro_store.read_export(path)
        except macro_store.MacroTransferError as e:
            QMessageBox.warning(self, "Couldn't import", str(e))
            return

        imported, skipped = macro_store.import_macros(specs)

        if skipped:
            answer = QMessageBox.question(
                self, "Already here",
                f"{len(skipped)} of these already exist: {', '.join(skipped[:6])}"
                + ("…" if len(skipped) > 6 else "")
                + "\n\nReplace them with the imported versions?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                replaced, _ = macro_store.import_macros(
                    [s for s in specs if s.get("name") in skipped], overwrite=True
                )
                imported += replaced

        if not imported:
            QMessageBox.information(self, "Nothing imported", "No macros were added.")
            return

        # Registered immediately, so an imported macro is usable without
        # a restart - same refresh the editor and the recorder do.
        import ai.main as ai_main
        ai_main.refresh_actions()
        self.macro_imported.emit()
        QMessageBox.information(
            self, "Imported",
            f"Added {len(imported)} macro(s): {', '.join(imported[:8])}"
            + ("…" if len(imported) > 8 else ""),
        )

    def _on_export_all_macros(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        from ai import macro_store

        names = macro_store.list_macro_names()
        if not names:
            QMessageBox.information(
                self, "Nothing to export",
                "You haven't saved any macros yet - record one first.",
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export macros", f"kestrel-macros{macro_store.EXPORT_SUFFIX}",
            f"{branding.APP_NAME} macros (*{macro_store.EXPORT_SUFFIX})",
        )
        if not path:
            return
        self._write_export(names, path)

    def _on_share_macro(self, macro_id: str) -> None:
        """
        Share one macro: writes it out as a single-macro export file,
        which is the shareable artifact - a file to send, not a format
        anyone has to understand. Kept as the same export the toolbar
        writes, so a shared macro imports exactly like a backup does.
        """
        from PyQt6.QtWidgets import QFileDialog
        from ai import macro_store

        resolved = macro_id[len("tool_"):] if macro_id.startswith("tool_") else macro_id
        if not macro_store.macro_exists(resolved):
            QMessageBox.information(
                self, "Can't share this one",
                "Only macros you've recorded or imported can be shared - the "
                "built-in ones ship with the app.",
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Share macro", f"{resolved}{macro_store.EXPORT_SUFFIX}",
            f"{branding.APP_NAME} macros (*{macro_store.EXPORT_SUFFIX})",
        )
        if not path:
            return
        self._write_export([resolved], path)

    def _write_export(self, names: list, path: str) -> None:
        """Shared tail of export and share - writes the file and offers
        to show it, since the next thing anyone does with an export is
        go find it."""
        from ai import macro_store
        from app.file_reveal import reveal_in_explorer

        try:
            count = macro_store.export_macros(names, path)
        except macro_store.MacroTransferError as e:
            QMessageBox.warning(self, "Couldn't export", str(e))
            return

        answer = QMessageBox.question(
            self, "Exported",
            f"Wrote {count} macro(s) to:\n{path}\n\nShow it in the folder?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            reveal_in_explorer(path)

    def offer_undo(self, message: str, undo_callback) -> None:
        """Show the undo bar over this page. MainWindow owns the delete
        itself (it holds the spec backup), so it calls in here to offer
        the way back."""
        self._undo_bar.offer(message, undo_callback)

    def set_theme(self, theme_name: str) -> None:
        self._theme = theme_name
        self.setStyleSheet(_build_style(theme.colors(theme_name)))
        self._apply_toolbar_icons()
        self._undo_bar.set_theme(theme_name)
        # StyledDropdown paints its own button/popup from a captured color
        # dict (see its own apply_theme docstring) - it does NOT re-derive
        # colors from a parent stylesheet cascade the way a plain QWidget
        # does, so without this call it would silently keep showing the
        # OLD theme's colors after a switch.
        self._filterDropdown.apply_theme(theme_name)
        # No self.refresh() here - the stylesheet cascade above already
        # re-skins every existing card's colors/borders/text live (QSS
        # is reapplied to descendants whenever an ancestor's stylesheet
        # changes), with no need to destroy and rebuild them from
        # scratch. Rebuilding every card on every single theme switch
        # used to be extra, unnecessary work that contributed to a
        # noticeable delay when switching themes - on top of every
        # OTHER page also being asked to re-theme itself in the same
        # synchronous call chain. Card icons (a QIcon is a static bitmap,
        # unlike the rest of the card - it does NOT itself follow a
        # later stylesheet change) still need their own lightweight
        # per-card refresh, see MacroCard.retheme().
        for card in self._cards:
            card.retheme(theme_name)

    # ── Public API ─────────────────────────────────────────────────────────────

    def load_macros(self, macros: list[dict]) -> None:
        """Loads and displays the list of macros."""
        self._macros_data = macros
        self._rebuild_cards()
        self._update_count()

    def refresh(self) -> None:
        """
        Triggers a reload. Wire this up to your own data-fetching logic
        (e.g. an API call, reading a JSON file...). Here it just reloads
        the data already held in memory.
        """
        self.load_macros(self._macros_data)

    # ── Internal construction ──────────────────────────────────────────────────

    def _rebuild_cards(self) -> None:
        """Removes all existing cards and recreates them."""
        self._clear_grid()
        self._cards.clear()

        search = self.searchInput.text().strip().lower()
        badge_filter = self._filterDropdown.current_value()

        col = 0
        row = 0
        for macro in self._macros_data:
            card = MacroCard(macro, parent=self.cardsGrid, theme_name=self._theme)
            card.run_requested.connect(self._on_macro_run)
            card.delete_requested.connect(self.macro_delete_requested)
            card.edit_requested.connect(self.macro_edit_requested)
            card.share_requested.connect(self._on_share_macro)
            self._cards.append(card)

            visible = card.matches(search, badge_filter)
            if visible:
                self._grid.addWidget(card, row, col)
                col += 1
                if col >= COLUMNS:
                    col = 0
                    row += 1
            else:
                card.hide()

        # Every row gets the same stretch = uniform card height
        for r in range(self._grid.rowCount()):
            self._grid.setRowStretch(r, 1)

    def _clear_grid(self) -> None:
        """Removes all widgets from the grid."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _apply_filters(self) -> None:
        """Re-applies the filters without recreating every widget."""
        search = self.searchInput.text().strip().lower()
        badge_filter = self._filterDropdown.current_value()

        # Removes all widgets from the grid without deleting them
        for card in self._cards:
            self._grid.removeWidget(card)
            card.hide()

        col = 0
        row = 0
        for card in self._cards:
            if card.matches(search, badge_filter):
                self._grid.addWidget(card, row, col)
                card.show()
                col += 1
                if col >= COLUMNS:
                    col = 0
                    row += 1

        for r in range(self._grid.rowCount()):
            self._grid.setRowStretch(r, 1)

        self._update_count()

    def _update_count(self) -> None:
        visible = sum(1 for c in self._cards if c.isVisible())
        total = len(self._cards)
        self.toolCount.setText(f"{visible} / {total} macros")

    # ── Slot ──────────────────────────────────────────────────────────────────

    def _on_macro_run(self, macro_id: str, params: dict) -> None:
        """Relays the signal outward."""
        self.macro_run_requested.emit(macro_id, params)
