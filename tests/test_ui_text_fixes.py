# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_ui_text_fixes.py
=============================
Small, targeted regression tests for three UI-text/label bugs fixed
together:

  1. The edit-message confirm button said "Save & submit" - misleading,
     since confirming actually deletes the message and everything after
     it, then resends (see ChatWidget._confirm_edit). Now says "Update".
  2. The Macros page toolbar had a French-language "⏺  Enregistrer"
     label with an emoji, and an emoji+text "⟳  Refresh" - both are now
     icon-only (see app/ui/macros_widget.ui + _apply_toolbar_icons()).
  3. RecorderWidget's shortcut descriptions ("F6 = right-click ...")
     were hardcoded to the F6-F10 defaults and went stale the moment a
     user changed a recorder shortcut in Settings - now built from
     ai_main.get_shortcut() live.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


# ══════════════════════════════════════════════════════════════════
# 1. Edit-message button text
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def chat_widget(qapp, tmp_path, monkeypatch):
    import app.chat_database as db
    db_path = str(tmp_path / "test_ui_text_chats.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    chat_id = db.create_chat("Test chat")

    from app.chat_widget import ChatWidget
    widget = ChatWidget()
    widget.load_chat(chat_id)
    return widget, chat_id


def test_edit_confirm_button_says_update_not_save_and_submit(chat_widget):
    from PyQt6.QtWidgets import QPushButton
    widget, chat_id = chat_widget
    import app.chat_database as db
    message_id = db.add_message(chat_id, "user", "hello")
    widget.load_chat(chat_id)

    btn = widget.findChild(QPushButton, "editSaveBtn")
    assert btn is not None
    assert btn.text() == "Update"
    assert btn.text() != "Save & submit"


# ══════════════════════════════════════════════════════════════════
# 2. Macros toolbar - icon-only, no French text, no emoji
# ══════════════════════════════════════════════════════════════════

def test_macros_toolbar_buttons_have_no_text(qapp):
    from app.macros_widget import MacrosWidget
    widget = MacrosWidget()
    assert widget.btnRecordMacro.text() == ""
    assert widget.btnRefresh.text() == ""


def test_macros_toolbar_buttons_have_icons(qapp):
    from app.macros_widget import MacrosWidget
    widget = MacrosWidget()
    assert not widget.btnRecordMacro.icon().isNull()
    assert not widget.btnRefresh.icon().isNull()


def test_macros_toolbar_icons_survive_theme_change(qapp):
    from app.macros_widget import MacrosWidget
    widget = MacrosWidget()
    widget.set_theme("light")
    assert not widget.btnRecordMacro.icon().isNull()
    assert not widget.btnRefresh.icon().isNull()


def test_set_theme_does_not_rebuild_existing_cards(qapp):
    """
    Regression test: set_theme() used to call self.refresh(), destroying
    and recreating every card on every theme switch - unnecessary extra
    work (the stylesheet cascade already re-skins existing cards live)
    that contributed to a noticeable delay switching themes. The SAME
    MacroCard instances must survive a theme switch, not get replaced.
    """
    from app.macros_widget import MacrosWidget
    widget = MacrosWidget()
    widget.load_macros([{"id": "tool_a", "name": "A", "badge": "user", "params": []}])
    card_before = widget._cards[0]

    widget.set_theme("light")

    assert widget._cards[0] is card_before


def test_macro_card_retheme_updates_delete_button_icon_color(qapp, monkeypatch):
    """
    Direct test of MacroCard.retheme() itself - verified by checking
    icons.icon() was called with the new theme's actual color value,
    rather than comparing rendered pixmaps (unreliable under this
    offscreen test environment's icon color tinting).
    """
    import app.macros_widget as macros_widget_module
    from app.macros_widget import MacroCard
    import app.theme as theme

    card = MacroCard({"id": "tool_a", "name": "A", "deletable": True})

    calls = []
    original_icon = macros_widget_module.icons.icon

    def _tracking_icon(name, colors, color_key="text_primary"):
        calls.append((name, color_key, colors.get(color_key)))
        return original_icon(name, colors, color_key)

    monkeypatch.setattr(macros_widget_module.icons, "icon", _tracking_icon)

    card.retheme("light")

    assert ("trash", "text_disabled", theme.colors("light")["text_disabled"]) in calls


def test_set_theme_calls_retheme_on_every_existing_card(qapp, monkeypatch):
    """
    The delete button's icon color IS theme-dependent (text_disabled),
    unlike the Run button's fixed "white" - without MacroCard.retheme()
    being called on every theme switch, removing the old full-rebuild
    would have left it stale (the actual regression risk that made a
    naive "just delete self.refresh()" fix wrong). Checked behaviorally
    (retheme() gets called with the right theme) rather than by
    comparing rendered pixmaps, which qtawesome's color tinting doesn't
    reliably resolve under this offscreen test environment anyway.
    """
    from app.macros_widget import MacrosWidget, MacroCard
    widget = MacrosWidget()
    widget.load_macros([
        {"id": "tool_a", "name": "A", "badge": "user", "params": []},
        {"id": "tool_b", "name": "B", "badge": "user", "params": []},
    ])

    calls = []
    monkeypatch.setattr(MacroCard, "retheme", lambda self, theme_name: calls.append((self, theme_name)))

    widget.set_theme("light")

    assert calls == [(card, "light") for card in widget._cards]


# ══════════════════════════════════════════════════════════════════
# Locking starter (built-in, hand-written) macros against deletion
# ══════════════════════════════════════════════════════════════════

def test_parse_tools_to_macros_marks_builtin_function_as_not_deletable(qapp, monkeypatch):
    from app.main_window import _parse_tools_to_macros
    from ai import macro_store

    monkeypatch.setattr(macro_store, "list_macro_names", lambda: [])  # nothing saved

    def tool_open_application(app_name: str) -> str:
        """Opens an application."""
        return ""

    macros = _parse_tools_to_macros([tool_open_application])

    assert macros[0]["deletable"] is False
    assert macros[0]["badge"] == "starter"


def test_parse_tools_to_macros_marks_saved_macro_as_deletable(qapp, monkeypatch):
    from app.main_window import _parse_tools_to_macros
    from ai import macro_store

    monkeypatch.setattr(macro_store, "list_macro_names", lambda: ["play_ymca_youtube"])

    def tool_play_ymca_youtube() -> str:
        """A recorded macro."""
        return ""

    macros = _parse_tools_to_macros([tool_play_ymca_youtube])

    assert macros[0]["deletable"] is True
    assert macros[0]["badge"] == "ai"


def test_macro_card_omits_delete_button_when_not_deletable(qapp):
    from app.macros_widget import MacroCard
    card = MacroCard({"id": "tool_open_application", "name": "Open application", "deletable": False})
    btn = card.findChild(object, "btnDeleteMacro")
    assert btn is None


def test_macro_card_shows_delete_button_when_deletable(qapp):
    from app.macros_widget import MacroCard
    card = MacroCard({"id": "tool_example", "name": "Example macro", "deletable": True})
    btn = card.findChild(object, "btnDeleteMacro")
    assert btn is not None


def test_macro_card_defaults_to_deletable_when_key_is_missing(qapp):
    """Backward compatibility: a macro dict built without the new 'deletable' key (an older caller, a hand-built test fixture) should still show the button rather than silently hiding it."""
    from app.macros_widget import MacroCard
    card = MacroCard({"id": "tool_example", "name": "Example macro"})
    btn = card.findChild(object, "btnDeleteMacro")
    assert btn is not None


# ══════════════════════════════════════════════════════════════════
# Macro deletion from the Macros page
# ══════════════════════════════════════════════════════════════════

def test_macro_card_has_a_delete_button(qapp):
    from app.macros_widget import MacroCard
    card = MacroCard({"id": "tool_example", "name": "Example macro"})
    btn = card.findChild(object, "btnDeleteMacro")
    assert btn is not None


def test_macro_card_delete_button_emits_delete_requested(qapp):
    from app.macros_widget import MacroCard
    card = MacroCard({"id": "tool_example", "name": "Example macro"})
    captured = []
    card.delete_requested.connect(lambda macro_id, name: captured.append((macro_id, name)))

    card._on_delete()

    assert captured == [("tool_example", "Example macro")]


def test_macros_widget_relays_delete_requests_from_cards(qapp):
    from app.macros_widget import MacrosWidget
    widget = MacrosWidget()
    widget.load_macros([{"id": "tool_a", "name": "A", "badge": "user", "params": []}])

    captured = []
    widget.macro_delete_requested.connect(lambda macro_id, name: captured.append((macro_id, name)))
    widget._cards[0]._on_delete()

    assert captured == [("tool_a", "A")]


def test_main_window_delete_handler_confirms_then_calls_tool_delete_macro(qapp, monkeypatch):
    from types import SimpleNamespace
    from PyQt6.QtWidgets import QMessageBox
    from app.main_window import MainWindow
    import ai.actions as actions

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    delete_calls = []
    monkeypatch.setattr(actions, "tool_delete_macro", lambda name: delete_calls.append(name) or "Deleted.")
    reload_calls = []

    fake_self = SimpleNamespace(_reload_macros=lambda: reload_calls.append(True))
    MainWindow._on_macro_delete_requested(fake_self, "tool_example", "Example macro")

    assert delete_calls == ["tool_example"]
    assert reload_calls == [True]


def test_main_window_delete_handler_does_nothing_when_declined(qapp, monkeypatch):
    from types import SimpleNamespace
    from PyQt6.QtWidgets import QMessageBox
    from app.main_window import MainWindow
    import ai.actions as actions

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    delete_calls = []
    monkeypatch.setattr(actions, "tool_delete_macro", lambda name: delete_calls.append(name) or "Deleted.")
    reload_calls = []

    fake_self = SimpleNamespace(_reload_macros=lambda: reload_calls.append(True))
    MainWindow._on_macro_delete_requested(fake_self, "tool_example", "Example macro")

    assert delete_calls == []
    assert reload_calls == []


# ══════════════════════════════════════════════════════════════════
# 3. Recorder shortcut descriptions are dynamic, not hardcoded
# ══════════════════════════════════════════════════════════════════

def test_shortcuts_subtitle_reflects_current_shortcut(qapp, monkeypatch):
    import ai.main as ai_main
    from app.recorder_widget import _shortcuts_subtitle

    monkeypatch.setattr(ai_main, "get_shortcut", lambda sid: {
        "recorder_right_click": "f6", "recorder_click": "f7",
        "recorder_toggle_typing": "f8", "recorder_enter": "f9",
        "recorder_stop": "f10",
    }[sid])
    assert "F6" in _shortcuts_subtitle()
    assert "F10" in _shortcuts_subtitle()

    monkeypatch.setattr(ai_main, "get_shortcut", lambda sid: {
        "recorder_right_click": "f13", "recorder_click": "f7",
        "recorder_toggle_typing": "f8", "recorder_enter": "f9",
        "recorder_stop": "f10",
    }[sid])
    assert "F13" in _shortcuts_subtitle()   # a changed shortcut is reflected, not stuck at F6


def test_shortcuts_status_line_reflects_current_shortcut(qapp, monkeypatch):
    import ai.main as ai_main
    from app.recorder_widget import _shortcuts_status_line

    monkeypatch.setattr(ai_main, "get_shortcut", lambda sid: {
        "recorder_right_click": "f6", "recorder_click": "f7",
        "recorder_toggle_typing": "f8", "recorder_enter": "f9",
        "recorder_stop": "capslock",
    }[sid])
    assert "CAPSLOCK" in _shortcuts_status_line()


def test_recorder_widget_subtitle_label_built_from_current_shortcuts(qapp):
    from app.recorder_widget import RecorderWidget, _shortcuts_subtitle
    widget = RecorderWidget()
    assert widget._subtitle_label.text() == _shortcuts_subtitle()


def test_refresh_shortcuts_description_picks_up_a_changed_shortcut(qapp, monkeypatch):
    """
    Regression test: the subtitle used to only be rebuilt at construction
    time and when a NEW recording started - navigating back to this page
    after rebinding a shortcut in Settings kept showing the stale text
    until the app was relaunched.
    """
    import ai.main as ai_main
    from app.recorder_widget import RecorderWidget

    monkeypatch.setattr(ai_main, "get_shortcut", lambda sid: {
        "recorder_right_click": "f6", "recorder_click": "f7",
        "recorder_toggle_typing": "f8", "recorder_enter": "f9",
        "recorder_stop": "f10",
    }[sid])
    widget = RecorderWidget()
    assert "F6" in widget._subtitle_label.text()

    monkeypatch.setattr(ai_main, "get_shortcut", lambda sid: {
        "recorder_right_click": "capslock", "recorder_click": "f7",
        "recorder_toggle_typing": "f8", "recorder_enter": "f9",
        "recorder_stop": "f10",
    }[sid])
    widget.refresh_shortcuts_description()

    assert "CAPSLOCK" in widget._subtitle_label.text()
    assert "F6" not in widget._subtitle_label.text()


def test_refresh_shortcuts_description_is_safe_while_recording(qapp):
    """Must only touch the subtitle label - never actions_list or the controller, since it can be called (via page navigation) while a recording is in progress."""
    from app.recorder_widget import RecorderWidget
    import ai.actions as actions

    widget = RecorderWidget()
    widget._controller._recording = True
    actions.actions_list.clear()
    actions.actions_list.append(("click", 10, 20))

    widget.refresh_shortcuts_description()

    assert actions.actions_list == [("click", 10, 20)]  # untouched
    assert widget._controller.is_recording is True       # untouched


# ── Code blocks while a reply is still streaming ──────────────────────
# A fenced block only renders properly once its CLOSING fence arrives,
# so until then the code used to stream in as raw escaped text
# unstyled, re-wrapping on every chunk. It's now stood in for by a
# compact "writing code" placeholder. These cover the fence detection
# that decides which of the two a partial reply gets.

def test_open_code_fence_is_detected_mid_stream():
    from app.chat_widget import _split_open_code_fence
    partial = "Here you go:\n\n```python\ndef hi():\n    print('hey')"
    split = _split_open_code_fence(partial)
    assert split is not None
    before, lang, body = split
    assert before.strip() == "Here you go:"
    assert lang == "python"
    assert "def hi():" in body


def test_closed_code_fence_is_left_alone():
    """Once the block is closed it renders for real - the placeholder
    must get out of the way."""
    from app.chat_widget import _split_open_code_fence
    assert _split_open_code_fence("a\n```py\nx = 1\n```\nb") is None
    assert _split_open_code_fence("no code here at all") is None


def test_second_open_fence_after_a_closed_one_is_the_one_detected():
    from app.chat_widget import _split_open_code_fence
    split = _split_open_code_fence("```py\nfirst\n```\nthen\n```js\nlet x")
    assert split is not None
    before, lang, body = split
    assert lang == "js"
    assert body == "let x"
    assert "first" in before  # the finished block stays rendered


def test_placeholder_names_the_language_and_counts_lines():
    from app.chat_widget import _coding_placeholder_html
    html = _coding_placeholder_html("python", "one\ntwo\nthree")
    assert "Writing python code" in html
    assert "3 lines" in html
    # Singular for one line - it's read by a human, not a log parser.
    assert "1 line" in _coding_placeholder_html("", "just one")
    assert "1 lines" not in _coding_placeholder_html("", "just one")


def test_streaming_bubble_keeps_the_full_text_for_copy():
    """The placeholder is display only: Copy and Read aloud mid-stream
    must still get what the model actually said."""
    from PyQt6.QtWidgets import QLabel
    from app.chat_widget import set_streaming_bubble_text, _bubble_plain_text

    label = QLabel()
    partial = "Sure:\n\n```python\nprint('hi')"
    set_streaming_bubble_text(label, partial)

    assert _bubble_plain_text(label) == partial      # unchanged underneath
    assert "Writing python code" in label.text()      # placeholder on screen
    assert "print" not in label.text()                # the raw code is not
