# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_shortcut_edit_theme.py
====================================
Was: tests for _apply_key_edit_palette, the QKeySequenceEdit text-
visibility fix for the four global shortcuts (Show/Hide window, Always
on top, Screenshot, Stop automation).

That whole approach - three separate rounds of trying to make
QKeySequenceEdit's captured-text color reliably follow theme switches
(QPalette roles only, then +HighlightedText, then reaching into its
private internal QLineEdit child) - was still reportedly showing stale
white text on a real Windows session after all three. Rather than a
fourth patch, QKeySequenceEdit was replaced outright with
KeyComboCaptureButton (app.settings_widget), a keyboard-only subclass of
TriggerCaptureButton - a plain QPushButton, which has always painted its
text color correctly from this page's shared stylesheet cascade alone,
with zero widget-specific palette/stylesheet handling anywhere. See
KeyComboCaptureButton's own docstring for the full reasoning.

These tests now check the much simpler thing that's actually true post-
replacement: the global shortcut buttons are real KeyComboCaptureButton
instances sharing the SAME "#triggerCaptureBtn" stylesheet selector as
the Recorder's TriggerCaptureButton widgets, with no per-widget override
of any kind - so there's nothing widget-specific left that could regress
into "stuck on the old theme" the way the old per-widget setStyleSheet()
calls did.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app.theme as theme
from app.settings_widget import SettingsWidget, KeyComboCaptureButton, TriggerCaptureButton


def test_global_shortcut_edits_are_key_combo_capture_buttons(qapp):
    widget = SettingsWidget()
    for edit in widget._shortcut_edits.values():
        assert isinstance(edit, KeyComboCaptureButton)
        assert isinstance(edit, TriggerCaptureButton)  # keyboard-only subclass, not a rewrite from scratch


def test_global_shortcut_buttons_share_the_same_selector_as_recorder_triggers(qapp):
    """
    Both widget families use "#triggerCaptureBtn" - the whole point of
    the replacement was reusing the ONE styling path that's never had a
    theme bug, rather than giving global shortcuts their own selector
    that could independently drift or regress.
    """
    widget = SettingsWidget()
    for edit in widget._shortcut_edits.values():
        assert edit.objectName() == "triggerCaptureBtn"
    for btn in widget._trigger_buttons.values():
        assert btn.objectName() == "triggerCaptureBtn"


def test_global_shortcut_buttons_have_no_per_widget_stylesheet(qapp):
    """
    Regression guard: the old QKeySequenceEdit fix worked by setting a
    styleSheet() directly on each individual widget - which is exactly
    what caused it to go stale on a later theme switch when that call
    wasn't (or wasn't reliably) repeated. KeyComboCaptureButton should
    have NO local stylesheet at all - everything comes from the page's
    single shared setStyleSheet() cascade in _build_style(), the same
    mechanism every other themed widget on this page already relies on.
    """
    widget = SettingsWidget()
    for edit in widget._shortcut_edits.values():
        assert edit.styleSheet() == ""


def test_settings_page_stylesheet_carries_the_dark_theme_color(qapp):
    widget = SettingsWidget()
    widget.set_theme("dark")
    assert theme.colors("dark")["text_primary"] in widget.styleSheet()


def test_settings_page_stylesheet_carries_the_light_theme_color(qapp):
    widget = SettingsWidget()
    widget.set_theme("light")
    assert theme.colors("light")["text_primary"] in widget.styleSheet()


def test_settings_page_stylesheet_updates_on_live_theme_switch(qapp):
    """
    THE regression test carried over from the old QKeySequenceEdit-era
    file: a stylesheet set once and never refreshed would still show the
    OLD theme's color here - this is exactly what "black on black" (and
    later "still white") looked like after switching themes. Checking
    the PAGE's stylesheet now, not each widget's individually, since
    that's the one and only place theme color lives for these buttons.
    """
    widget = SettingsWidget()
    dark_color = theme.colors("dark")["text_primary"]
    light_color = theme.colors("light")["text_primary"]

    widget.set_theme("dark")
    assert dark_color in widget.styleSheet()
    assert light_color not in widget.styleSheet()

    widget.set_theme("light")
    assert light_color in widget.styleSheet()
    assert dark_color not in widget.styleSheet()

    widget.set_theme("dark")
    assert dark_color in widget.styleSheet()
    assert light_color not in widget.styleSheet()


def test_global_shortcut_ids_are_all_covered_by_shortcut_edits(qapp):
    """
    _shortcut_edits only ever holds GLOBAL_SHORTCUT_IDS entries
    (KeyComboCaptureButton widgets) - RECORDER_SHORTCUT_IDS uses
    TriggerCaptureButton directly instead (it also needs to capture
    mouse buttons, which a global shortcut must never do - see
    KeyComboCaptureButton's own docstring), tracked in
    self._trigger_buttons.
    """
    import ai.main as ai_main
    widget = SettingsWidget()
    assert set(widget._shortcut_edits.keys()) == set(ai_main.GLOBAL_SHORTCUT_IDS)
    assert set(widget._trigger_buttons.keys()) == set(ai_main.RECORDER_SHORTCUT_IDS)


def test_key_combo_capture_button_ignores_mouse_clicks_while_listening(qapp):
    """
    The one behavioral difference from TriggerCaptureButton: a mouse
    click while listening must NOT be captured as a trigger (global
    shortcuts have to be real keyboard.add_hotkey-compatible combos).
    """
    from PyQt6.QtCore import QEvent, QPoint, Qt as QtNS
    from PyQt6.QtGui import QMouseEvent

    btn = KeyComboCaptureButton("ctrl+alt+p")
    btn._start_listening()
    assert btn._listening is True

    event = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPoint(5, 5), QtNS.MouseButton.LeftButton,
        QtNS.MouseButton.LeftButton, QtNS.KeyboardModifier.NoModifier,
    )
    btn.mousePressEvent(event)

    # Still listening, and the trigger is unchanged - the click did nothing.
    assert btn._listening is True
    assert btn.current_trigger() == "ctrl+alt+p"
