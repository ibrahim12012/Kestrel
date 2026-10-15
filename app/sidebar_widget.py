# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

import os
from datetime import datetime, timedelta
from PyQt6 import uic
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QButtonGroup, QMenu, QInputDialog, QMessageBox,
    QSizePolicy, QSpacerItem, QScrollArea,
)
from PyQt6.QtCore import pyqtSignal, Qt, QPoint, QSize
from PyQt6.QtGui import QAction
import app.chat_database as db
import ai.main as ai_main
import app.theme as theme
import app.icons as icons

from paths import BASE_PATH
from app.i18n import tr
root_path = BASE_PATH

# ── Styles ─────────────────────────────────────────────────────────────────────

def _chat_item_style(c: dict) -> str:
    return f"""
QPushButton#chatBtn {{
    background-color: transparent;
    color: {c['text_muted']};
    font-family: "Segoe UI";
    font-size: 12px;
    text-align: left;
    padding: 8px 6px 8px 10px;
    border: none;
    border-radius: 7px;
}}
QPushButton#chatBtn:hover {{ background-color: transparent; color: {c['text_primary']}; }}
QPushButton#chatBtn:checked {{ background-color: transparent; color: {c['text_primary']}; }}
"""


def _chat_row_style(c: dict) -> str:
    return f"""
QWidget#chatRow {{
    background-color: transparent;
    border-radius: 7px;
}}
QWidget#chatRow:hover {{
    background-color: {c['bg_elevated']};
}}
QWidget#chatRow[checked="true"] {{
    background-color: rgba({c['accent_rgb']},0.12);
    border-left: 2px solid {c['accent']};
}}
"""


def _menu_btn_style(c: dict) -> str:
    """
    Visible on the selected chat, and on whichever row the pointer is
    over. Hidden everywhere else.

    Hidden means painted transparent, not setVisible(False). The button
    keeps its 22px either way, so revealing it never reflows the row and
    shoves the title sideways under the cursor - which is what made the
    earlier always-visible version look like the only option. A column
    of dots down every row reads as a column of buttons rather than a
    list of chats; showing it on the active row keeps it discoverable
    without that.
    """
    return f"""
QPushButton#menuBtn {{
    background-color: transparent;
    color: transparent;
    font-size: 14px;
    border: none;
    border-radius: 5px;
    min-width: 22px; max-width: 22px;
    min-height: 22px; max-height: 22px;
    padding: 0px;
}}
QPushButton#menuBtn[rowActive="true"] {{
    color: {c['text_secondary']};
}}
QPushButton#menuBtn[rowHovered="true"] {{
    color: {c['text_secondary']};
}}
QPushButton#menuBtn:hover {{
    background-color: {c['bg_hover_strong']};
    color: {c['text_primary']};
}}
"""


def _input_dialog_style(c: dict) -> str:
    return f"""
QInputDialog {{
    background-color: {c['bg_panel']};
    color: {c['text_primary']};
    font-family: "Segoe UI";
    border: none;
}}
QLabel {{
    color: {c['text_secondary']};
    font-family: "Segoe UI";
    font-size: 12px;
    background: transparent;
}}
QDialogButtonBox {{ background: transparent; }}
QLineEdit {{
    background-color: {c['bg_elevated']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 6px;
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 6px 10px;
    selection-background-color: rgba({c['accent_rgb']},0.35);
}}
QPushButton {{
    background-color: {c['text_primary']};
    color: {c['bg_base']};
    border: none;
    border-radius: 6px;
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 6px 16px;
    min-width: 60px;
}}
QPushButton:hover {{ background-color: {c['text_secondary']}; }}
QPushButton[text="Cancel"] {{ background-color: {c['bg_elevated']}; color: {c['text_muted']}; }}
QPushButton[text="Cancel"]:hover {{ background-color: {c['bg_hover']}; color: {c['text_primary']}; }}
"""


def _confirm_style(c: dict) -> str:
    return f"""
QMessageBox {{
    background-color: {c['bg_panel']};
    color: {c['text_primary']};
    font-family: "Segoe UI";
    border: none;
}}
QLabel {{ color: {c['text_secondary']}; font-family: "Segoe UI"; font-size: 12px; background: transparent; }}
QDialogButtonBox {{ background: transparent; }}
QPushButton {{
    background-color: {c['bg_elevated']};
    color: {c['text_muted']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 6px;
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 6px 16px;
    min-width: 60px;
}}
QPushButton:hover {{ background-color: {c['bg_hover']}; color: {c['text_primary']}; }}
QPushButton[text="Delete"] {{
    background-color: rgba({c['danger_rgb']},0.15);
    color: {c['danger']};
    border-color: rgba({c['danger_rgb']},0.3);
}}
QPushButton[text="Delete"]:hover {{ background-color: rgba({c['danger_rgb']},0.25); }}
"""


def _build_sidebar_style(c: dict) -> str:
    """Regenerates app/ui/sidebar_widget.ui's root styleSheet property from theme tokens - see chat_widget._build_chrome_style's docstring for why this is applied via Python after loadUi rather than editing the .ui file."""
    return f"""
QWidget#SidebarWidget {{
    background-color: {c['bg_base']};
    border-right: 1px solid {c['bg_input']};
}}
QPushButton#btnNewChat,
QPushButton#btnActions,
QPushButton#btnRecorder,
QPushButton#btnMapping {{
    background-color: transparent;
    color: {c['text_muted']};
    font-family: "Segoe UI";
    font-size: 13px;
    text-align: left;
    padding: 9px 10px;
    border: none;
    border-radius: 8px;
}}
QPushButton#btnNewChat:hover,
QPushButton#btnActions:hover,
QPushButton#btnRecorder:hover,
QPushButton#btnMapping:hover {{
    background-color: {c['bg_elevated']};
    color: {c['text_primary']};
}}
QPushButton#btnNewChat:checked,
QPushButton#btnActions:checked,
QPushButton#btnRecorder:checked,
QPushButton#btnMapping:checked {{
    background-color: rgba({c['accent_rgb']},0.12);
    color: {c['text_primary']};
    border-left: 3px solid {c['accent']};
    padding-left: 7px;
}}
QLabel#sectionLabel, QLabel#sectionLabelOld {{
    color: {c['text_disabled']};
    font-family: "Syne";
    font-size: 10px;
    font-weight: 500;
    background: transparent;
    padding: 10px 8px 6px;
}}
QLineEdit#chatSearchInput {{
    background-color: {c['bg_panel']};
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
    border: 1px solid {c['bg_input']};
    border-radius: 8px;
    padding: 6px 12px;
    min-height: 28px;
    max-height: 28px;
    selection-background-color: rgba({c['accent_rgb']},0.35);
}}
QLineEdit#chatSearchInput:focus {{
    border-color: {c['border_strong']};
}}
QLabel#noSearchResultsLabel {{
    color: {c['text_disabled']};
    font-family: "Segoe UI";
    font-size: 12px;
    background: transparent;
    padding: 10px 8px;
}}
QLabel#searchSnippetLabel {{
    color: {c['text_faint']};
    font-family: "Segoe UI";
    font-size: 11px;
    background: transparent;
    padding: 0px 10px 6px 18px;
}}
QScrollArea#chatHistoryScroll {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    border: none;
    width: 6px;
    margin: 2px 0px 2px 0px;
}}
QScrollBar::handle:vertical {{
    background: {c['bg_hover_strong']};
    border-radius: 3px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; subcontrol-position: bottom; subcontrol-origin: margin; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QFrame#divider1, QFrame#divider2 {{
    color: {c['bg_elevated']};
    background-color: {c['bg_elevated']};
    max-height: 1px;
    border: none;
}}
QWidget#bottomArea {{
    background-color: {c['bg_base']};
    border-top: 1px solid {c['bg_input']};
}}
QLabel#userName {{
    color: {c['text_primary']};
    font-family: "Syne";
    font-size: 12px;
    font-weight: 500;
    background: transparent;
}}
QLabel#userAvatar {{
    background-color: {c['bg_hover']};
    color: {c['text_primary']};
    font-family: "Syne";
    font-size: 11px;
    font-weight: 700;
    border: 1px solid {c['border']};
    border-radius: 14px;
    min-width: 28px; max-width: 28px;
    min-height: 28px; max-height: 28px;
    qproperty-alignment: AlignCenter;
}}
QPushButton#btnSettings {{
    background-color: transparent;
    color: {c['text_muted']};
    font-family: "Segoe UI";
    font-size: 13px;
    text-align: left;
    padding: 9px 10px;
    border: none;
    border-radius: 8px;
}}
QPushButton#btnSettings:hover {{
    background-color: {c['bg_elevated']};
    color: {c['text_primary']};
}}
"""


# ── ChatItemWidget ─────────────────────────────────────────────────────────────

class ChatItemWidget(QWidget):
    """
    Widget representing one chat in the sidebar.
    Shows the title + a ⋯ button that appears on hover.
    """

    # Signals relayed to the Sidebar
    clicked        = pyqtSignal(int)          # selected chat_id
    rename_request = pyqtSignal(int, str)     # (chat_id, new_title)
    delete_request = pyqtSignal(int)          # chat_id
    pin_request    = pyqtSignal(int, bool)    # (chat_id, pinned)
    export_request = pyqtSignal(int)          # chat_id

    def __init__(self, chat_id: int, title: str, pinned: bool = False, parent=None, theme_name: str = "dark"):
        super().__init__(parent)
        self._chat_id = chat_id
        self._pinned  = pinned
        self._checked = False
        self._theme = theme_name
        c = theme.colors(theme_name)

        self.setObjectName("chatRow")
        self.setStyleSheet(_chat_row_style(c))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Horizontal layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(0)

        # Title button (takes up all available space)
        self._title_btn = QPushButton(title)
        self._title_btn.setObjectName("chatBtn")
        self._title_btn.setCheckable(True)
        self._title_btn.setFlat(True)
        self._title_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._title_btn.setStyleSheet(_chat_item_style(c))
        self._title_btn.clicked.connect(self._on_title_clicked)
        layout.addWidget(self._title_btn)

        # ⋯ button (context menu)
        self._menu_btn = QPushButton("···")
        self._menu_btn.setObjectName("menuBtn")
        self._menu_btn.setStyleSheet(_menu_btn_style(c))
        self._menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_btn.setToolTip(tr("Rename, pin, export or delete"))
        self._menu_btn.setProperty("rowActive", "false")
        self._menu_btn.setProperty("rowHovered", "false")
        # Invisible until the row is active or hovered (see
        # _menu_btn_style), and while invisible it lets clicks through to
        # the row underneath. Without that, the 22px it occupies would be
        # a dead zone on every unselected row: the pointer has to cross
        # it to reach anything on the right-hand side, and a click landing
        # there before enterEvent has run would hit a button nobody can
        # see instead of selecting the chat.
        self._menu_btn.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._menu_btn.clicked.connect(lambda checked=False: self._show_context_menu())
        layout.addWidget(self._menu_btn)

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def chat_id(self) -> int:
        return self._chat_id

    def set_checked(self, checked: bool):
        self._checked = checked
        self._title_btn.setChecked(checked)
        # Update the CSS property that drives the background
        self.setProperty("checked", "true" if checked else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        # The selected row keeps its ⋯ permanently, so there is always one
        # visible place to reach rename/pin/export/delete without having
        # to discover that hovering does anything.
        self._set_menu_visible(rowActive="true" if checked else "false")

    def set_title(self, title: str):
        self._title_btn.setText(title)

    def set_pinned(self, pinned: bool):
        self._pinned = pinned

    # ── Revealing the ⋯ button ────────────────────────────────────────────────

    def enterEvent(self, event):
        self._set_menu_visible(rowHovered="true")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._set_menu_visible(rowHovered="false")
        super().leaveEvent(event)

    def _set_menu_visible(self, **changes: str) -> None:
        """
        Set whichever of rowActive/rowHovered changed, then repolish and
        resync click-through from the result.

        The two are independent - a row can be selected and hovered at
        once, and leaving it must not take the button away from the row
        that is still selected - so each is stored separately and the
        button is visible when either is true, exactly as the stylesheet
        reads it.
        """
        for prop, value in changes.items():
            self._menu_btn.setProperty(prop, value)

        visible = (
            self._menu_btn.property("rowActive") == "true"
            or self._menu_btn.property("rowHovered") == "true"
        )
        self._menu_btn.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, not visible
        )
        self._repolish_menu_btn()

    def _repolish_menu_btn(self) -> None:
        self._menu_btn.style().unpolish(self._menu_btn)
        self._menu_btn.style().polish(self._menu_btn)

    # ── Internal slots ─────────────────────────────────────────────────────────

    def _on_title_clicked(self):
        self.clicked.emit(self._chat_id)

    def contextMenuEvent(self, event):
        """Native right-click anywhere on the row opens the same menu as the ⋯ button."""
        self._show_context_menu(event.globalPos())

    def _show_context_menu(self, pos: QPoint | None = None):
        menu = QMenu(self)
        # In the app's palette, like the tray menu: the icons are tinted
        # from the theme, so the menu behind them has to be too.
        c = theme.colors(self._theme)
        menu.setStyleSheet(theme.menu_style(c))

        # ── Rename ──
        action_rename = QAction(tr("Rename"), self)
        action_rename.setIcon(icons.icon("edit", c, "text_secondary"))
        action_rename.triggered.connect(self._on_rename)
        menu.addAction(action_rename)

        # ── Pin / Unpin ──
        # A filled pin for "this chat is pinned", an outline one for "pin
        # it", matching what the item does rather than a star, which
        # reads as "favourite".
        if self._pinned:
            action_pin = QAction(tr("Unpin"), self)
            action_pin.setIcon(icons.icon("pin-filled", c, "accent"))
        else:
            action_pin = QAction(tr("Pin"), self)
            action_pin.setIcon(icons.icon("pin", c, "text_secondary"))
        action_pin.triggered.connect(self._on_pin_toggle)
        menu.addAction(action_pin)

        # ── Export ──
        action_export = QAction(tr("Export\u2026"), self)
        action_export.setIcon(icons.icon("share", c, "text_secondary"))
        action_export.triggered.connect(lambda: self.export_request.emit(self._chat_id))
        menu.addAction(action_export)

        menu.addSeparator()

        # ── Delete ──
        action_delete = QAction(tr("Delete"), self)
        action_delete.setIcon(icons.icon("trash", c, "danger"))
        action_delete.setProperty("danger", True)
        action_delete.triggered.connect(self._on_delete)
        menu.addAction(action_delete)

        # Right-click passes its own event position; the ⋯ button falls back
        # to appearing just below itself.
        if pos is None:
            pos = self._menu_btn.mapToGlobal(QPoint(0, self._menu_btn.height() + 2))
        menu.exec(pos)

    def _on_rename(self):
        dialog = QInputDialog(self)
        dialog.setStyleSheet(_input_dialog_style(theme.colors(self._theme)))
        dialog.setWindowTitle(tr("Rename chat"))
        dialog.setLabelText(tr("New name:"))
        dialog.setTextValue(self._title_btn.text())
        dialog.setOkButtonText("Rename")
        dialog.setCancelButtonText("Cancel")
        dialog.resize(360, 140)

        if dialog.exec():
            new_title = dialog.textValue().strip()
            if new_title and new_title != self._title_btn.text():
                self.rename_request.emit(self._chat_id, new_title)

    def _on_pin_toggle(self):
        self.pin_request.emit(self._chat_id, not self._pinned)

    def _on_delete(self):
        box = QMessageBox(self)
        box.setStyleSheet(_confirm_style(theme.colors(self._theme)))
        box.setWindowTitle(tr("Delete chat"))
        box.setText(f'Delete "{self._title_btn.text()}"?')
        box.setInformativeText(tr("This action cannot be undone."))
        box.setIcon(QMessageBox.Icon.NoIcon)

        btn_delete = box.addButton(tr("Delete"), QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton(tr("Cancel"),  QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_cancel)

        box.exec()
        if box.clickedButton() is btn_delete:
            self.delete_request.emit(self._chat_id)


# ── Sidebar ────────────────────────────────────────────────────────────────────

class Sidebar(QWidget):
    # Public signals
    new_chat_requested = pyqtSignal()
    chat_selected      = pyqtSignal(int)   # chat_id
    chat_renamed       = pyqtSignal(str)
    chat_deleted       = pyqtSignal()

    def __init__(self):
        super().__init__()
        uic.loadUi(fr"{root_path}/app/ui/sidebar_widget.ui", self)
        self._wrap_history_area_in_scroll()

        self._theme: str = ai_main.get_theme()
        self.setStyleSheet(_build_sidebar_style(theme.colors(self._theme)))
        self._apply_nav_icons()

        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)

        # Reference to ChatItemWidgets, indexed by chat_id
        self._chat_items: dict[int, ChatItemWidget] = {}

        # Current chat-search filter text (see _on_search_text_changed)
        # - "" means no filter, show the normal PINNED/RECENT/OLDER
        # grouping.
        self._search_query: str = ""

        self._history_layout: QVBoxLayout = self.historyArea.layout()

        self.btnNewChat.clicked.connect(self._on_new_chat_clicked)
        # Bug fix: the search box used to only exist behind a toggle
        # button (btnSearch) sitting up in the nav icon row alongside
        # New Chat/Macros/Recorder/Mapping - those are all TOOLS, so
        # Search stuck out as the odd one out there, and (worse) it used
        # the exact same magnifying-glass icon as the unrelated Mapping
        # button right next to it, making the two easy to mix up. It's
        # now a small always-visible field sitting directly above the
        # chat list it actually filters, with no toggle needed at all
        # see app/ui/sidebar_widget.ui's chatSearchArea, right below
        # navArea and right above historyArea.
        self.chatSearchInput.setClearButtonEnabled(True)
        self.chatSearchInput.textChanged.connect(self._on_search_text_changed)

        self.refresh_history()

    def _wrap_history_area_in_scroll(self) -> None:
        """
        app/ui/sidebar_widget.ui puts historyArea (the chat list) directly
        in the sidebar's own layout with no QScrollArea around it - unlike
        chat_widget.ui's messagesScroll or macros_widget.ui's macrosScroll,
        which both correctly wrap their equivalent growing lists. With
        nothing to scroll, historyArea's natural height (all chat rows,
        unclipped) becomes part of the WHOLE WINDOW's computed minimum
        size - so once there are enough chats, the window physically
        can't be resized shorter than "tall enough to show every chat at
        once" anymore. Reparenting it into a QScrollArea here (rather
        than hand-editing the .ui's XML) fixes that the same way the
        other two already work: the list scrolls, the window doesn't have
        to grow to fit it.
        """
        root_layout = self.layout()
        index = root_layout.indexOf(self.historyArea)
        if index == -1:
            return  # already wrapped, or historyArea isn't where expected - don't guess

        root_layout.removeWidget(self.historyArea)
        scroll = QScrollArea(self)
        scroll.setObjectName("chatHistoryScroll")
        scroll.setWidget(self.historyArea)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent; border: none;")
        root_layout.insertWidget(index, scroll)
        self._history_scroll = scroll  # kept for refresh_history()'s scroll-position preservation

    # Page buttons and their icons. Set here rather than in the .ui so
    # they are tinted from the theme; the .ui used to point at SVG files
    # with a fixed light-grey fill, which vanished in the light theme.
    _NAV_ICONS = (
        ("btnNewChat", "new-chat"),
        ("btnActions", "bolt"),
        ("btnRecorder", "record"),
        ("btnMapping", "tree"),
        ("btnSettings", "settings"),
    )

    def _apply_nav_icons(self) -> None:
        c = theme.colors(self._theme)
        for attr, name in self._NAV_ICONS:
            button = getattr(self, attr, None)
            if button is None:
                continue
            button.setIcon(icons.icon(name, c, "text_muted", checked_color_key="text_primary"))
            button.setIconSize(QSize(16, 16))

    def set_theme(self, theme_name: str) -> None:
        """Re-skins the sidebar chrome and rebuilds every chat-list row in the new theme (simplest correct way to re-theme already-existing ChatItemWidgets - see refresh_history)."""
        self._theme = theme_name
        self.setStyleSheet(_build_sidebar_style(theme.colors(theme_name)))
        self._apply_nav_icons()
        active = self._get_active_chat_id()
        self.refresh_history(active_chat_id=active)

    # ── Private slots ──────────────────────────────────────────────────────────

    def _clear_static_items(self):
        layout: QVBoxLayout = self.historyArea.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_new_chat_clicked(self):
        print("New chat request")
        self.new_chat_requested.emit()

    def _on_search_text_changed(self, text: str) -> None:
        self._search_query = text.strip()
        self.refresh_history(active_chat_id=self._get_active_chat_id())

    def _make_chat_item(self, chat_id: int, title: str, pinned: bool) -> ChatItemWidget:
        item = ChatItemWidget(chat_id, title, pinned, theme_name=self._theme)

        # Add it to the QButtonGroup via its internal button
        self._button_group.addButton(item._title_btn)

        # Wire up signals
        item.clicked.connect(self.chat_selected.emit)
        item.rename_request.connect(self._on_rename)
        item.export_request.connect(self._on_export)
        item.delete_request.connect(self._on_delete)
        item.pin_request.connect(self._on_pin)

        self._chat_items[chat_id] = item
        return item

    # ── Action handlers ────────────────────────────────────────────────────────

    def _snippet_label(self, result) -> "QLabel | None":
        """
        The matched line, for a result that matched on message content
        rather than on its title. None when there is nothing useful to
        show: a title match already explains itself.
        """
        snippet = result.get("snippet") if hasattr(result, "get") else None
        if not snippet or result.get("matched_title"):
            return None
        label = QLabel(snippet)
        label.setObjectName("searchSnippetLabel")
        label.setWordWrap(True)
        return label

    def _on_export(self, chat_id: int) -> None:
        """Write one conversation out to a file the person keeps. Markdown
        by default because it opens anywhere; JSON when they pick it."""
        from PyQt6.QtWidgets import QFileDialog
        from app import chat_export
        import ai.main as ai_main

        try:
            chat = db.export_chat(chat_id)
        except ValueError as e:
            QMessageBox.warning(self, tr("Couldn't export"), str(e))
            return
        if not chat["messages"]:
            QMessageBox.information(
                self, tr("Nothing to export"),
                tr("This conversation has no messages in it yet."),
            )
            return

        suggested = os.path.join(
            os.path.expanduser("~"),
            chat_export.suggested_filename(chat["title"]),
        )
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export conversation"), suggested,
            tr("Markdown (*.md);;JSON (*.json)"),
        )
        if not path:
            return
        try:
            chat_export.write_chat(chat, path, ai_main.get_assistant_name())
        except chat_export.ExportError as e:
            QMessageBox.warning(self, tr("Couldn't export"), str(e))

    def _on_rename(self, chat_id: int, new_title: str):
        db.update_chat_title(chat_id, new_title)
        if chat_id in self._chat_items:
            self._chat_items[chat_id].set_title(new_title)

        print(self._get_active_chat_id())
        print(chat_id)
        if self._get_active_chat_id() == chat_id:
            self.chat_renamed.emit(new_title)

    def _on_delete(self, chat_id: int):
        db.delete_chat(chat_id)
        ai_main.forget_chat(chat_id)
        self.chat_deleted.emit()
        # Reload to recompute the sections
        self.refresh_history()

    def _on_pin(self, chat_id: int, pinned: bool):
        db.set_chat_pinned(chat_id, pinned)
        if chat_id in self._chat_items:
            self._chat_items[chat_id].set_pinned(pinned)
        # Reload to move it into the right section
        active = self._get_active_chat_id()
        self.refresh_history(active_chat_id=active)

    def _get_active_chat_id(self) -> int | None:
        checked = self._button_group.checkedButton()
        if checked:
            for cid, item in self._chat_items.items():
                if item._title_btn is checked:
                    return cid
        return None

    # ── Public API ─────────────────────────────────────────────────────────────

    def refresh_history(self, active_chat_id: int | None = None):
        """
        Reloads the chat list from the DB. Called after essentially every
        message exchange (a chat's updated_at changes, which can move it
        within the sort order) - see update_history()'s many call sites
        in main_window.py - so this rebuilds every chat-list item every
        single time, not just occasionally.

        When self._search_query is set (the Search nav button's filter
        box has text in it - see _on_search_toggled /
        _on_search_text_changed), shows a flat, filtered list instead
        of the normal PINNED/RECENT/OLDER sections.

        Preserves the sidebar's OWN scroll position across that rebuild:
        without it, every rebuild reset the scroll to the top (a fresh
        widget naturally starts there), so anyone scrolled down browsing
        older chats while a conversation was active got snapped back to
        the top of the list after every single message - read as "the
        chat keeps going up" even though it's the SIDEBAR, not the
        message view, resetting.
        """
        print("History refresh")

        scrollbar = getattr(self, "_history_scroll", None)
        saved_scroll_value = scrollbar.verticalScrollBar().value() if scrollbar is not None else None

        for btn in self._button_group.buttons():
            self._button_group.removeButton(btn)

        self._chat_items.clear()
        self._clear_static_items()

        chats = db.get_all_chats()
        layout = self._history_layout

        def add_section(label_text: str, chat_list, with_snippets: bool = False):
            lbl = QLabel(label_text)
            lbl.setObjectName("sectionLabel")
            layout.addWidget(lbl)
            for chat in chat_list:
                item = self._make_chat_item(chat["id"], chat["title"], bool(chat["pinned"]))
                if active_chat_id and chat["id"] == active_chat_id:
                    item.set_checked(True)
                layout.addWidget(item)
                # The snippet goes in HERE, immediately under the row it
                # belongs to. Adding every row and then every snippet
                # reads as two separate lists: all the titles, then all
                # the matched text, with nothing saying which belongs to
                # which.
                if with_snippets:
                    snippet = self._snippet_label(chat)
                    if snippet is not None:
                        layout.addWidget(snippet)

        if self._search_query:
            # Filtering flattens pinned/recent/older into one list: a
            # match's recency or pin status isn't the point while
            # actively searching, finding it is.
            #
            # Titles used to be the only thing searched, which is close
            # to useless. Nobody remembers what a chat was called; they
            # remember something that was said in it. db.search_messages
            # matches both and hands back a snippet of whichever hit.
            results = db.search_messages(self._search_query)
            if results:
                add_section(
                    f"{len(results)} RESULT{'S' if len(results) != 1 else ''}",
                    results, with_snippets=True,
                )
            else:
                empty = QLabel(
                    tr("Nothing matches \u201c{query}\u201d, in any title or message.",
                       query=self._search_query)
                )
                empty.setObjectName("noSearchResultsLabel")
                empty.setWordWrap(True)
                layout.addWidget(empty)

            layout.addSpacerItem(
                QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
            )
            if scrollbar is not None and saved_scroll_value is not None:
                scrollbar.verticalScrollBar().setValue(saved_scroll_value)
            return

        now    = datetime.now()
        cutoff = (now - timedelta(days=7)).isoformat()

        pinned = [c for c in chats if c["pinned"]]
        recent = [c for c in chats if not c["pinned"] and c["updated_at"] >= cutoff]
        older  = [c for c in chats if not c["pinned"] and c["updated_at"] < cutoff]

        if pinned:
            add_section("PINNED", pinned)

        if recent:
            add_section("RECENT", recent)

        if older:
            add_section("OLDER", older)

        layout.addSpacerItem(
            QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )

        if scrollbar is not None and saved_scroll_value is not None:
            scrollbar.verticalScrollBar().setValue(saved_scroll_value)

    def set_active_chat(self, chat_id: int):
        """Checks the button corresponding to the active chat."""
        for cid, item in self._chat_items.items():
            item.set_checked(cid == chat_id)

    def update_chat_title_in_list(self, chat_id: int, title: str):
        """Updates one item's label without reloading the whole list."""
        if chat_id in self._chat_items:
            self._chat_items[chat_id].set_title(title)
