# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/settings_widget.py
========================
"Settings" page: which Gemini API key to use (bring-your-own-key), what
the assistant is named (used in chat and as the wake word), whether the
wake word listens in the background, and the app's global keyboard
shortcut(s).

All of it persists to .env via ai.main's helpers (get/save_api_key,
get/set_assistant_name, get/set_wake_word_enabled, get/set_shortcut) so
it survives a restart. The API key, assistant name, and shortcut
changes all apply immediately - no relaunch
needed.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QLineEdit, QCheckBox, QSizePolicy, QDoubleSpinBox, QComboBox,
    QSpinBox, QScrollArea, QButtonGroup, QAbstractSpinBox, QMessageBox,
    QFrame, QDialog, QTextEdit, QApplication, QFileDialog,
)
from PyQt6.QtGui import QKeySequence, QDesktopServices
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QUrl, QThread

import os

import ai.main as ai_main
import ai.profiles as profiles
import app.theme as theme
import app.icons as icons
from app.styled_dropdown import StyledDropdown
from app import i18n
from app.i18n import tr




def _keyboard_to_qt(combo: str) -> str:
    """'ctrl+shift+space' -> 'Ctrl+Shift+Space' (QKeySequence's format)."""
    return "+".join(p.strip().capitalize() for p in combo.split("+") if p.strip())


def _qt_to_keyboard(qt_combo: str) -> str:
    """'Ctrl+Shift+Space' -> 'ctrl+shift+space' (the `keyboard` lib's format)."""
    return "+".join(p.strip().lower() for p in qt_combo.split("+") if p.strip())


_MOUSE_BUTTON_LABELS = {
    "left": "Left click", "right": "Right click", "middle": "Middle click",
    "x": "Side button 1", "x2": "Side button 2",
}


def _format_trigger(trigger: str) -> str:
    """'f6' -> 'F6'; 'ctrl+alt+f6' -> 'Ctrl+Alt+F6'; 'mouse:middle' -> 'Middle click'."""
    if not trigger:
        return "(none)"
    if trigger.startswith("mouse:"):
        button = trigger.split(":", 1)[1]
        return _MOUSE_BUTTON_LABELS.get(button, f"Mouse: {button}")
    return _keyboard_to_qt(trigger)


class TriggerCaptureButton(QPushButton):
    """
    Captures a single keyboard combo OR mouse button press - used for the
    Recorder's triggers, which (unlike the four app-level global
    shortcuts below) can be bound to a mouse button too (see
    ai.main.RECORDER_SHORTCUT_IDS' docstring for why).

    Click to start listening, then press any key (with modifiers held if
    wanted) or click any mouse button - whichever comes first is
    captured and triggerCaptured is emitted. Esc cancels without
    changing anything.
    """
    triggerCaptured = pyqtSignal(str)

    _MODIFIER_KEYS = (
        Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
        Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
    )

    def __init__(self, current: str, parent=None):
        super().__init__(_format_trigger(current), parent)
        self._current = current
        self._listening = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._start_listening)

    def current_trigger(self) -> str:
        return self._current

    def set_current_trigger(self, trigger: str) -> None:
        self._current = trigger
        self._stop_listening(restore_text=True)

    def _start_listening(self) -> None:
        self._listening = True
        self.setText(tr("Press a key or click a mouse button… (Esc to cancel)"))
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.grabMouse()
        self.grabKeyboard()

    def _stop_listening(self, restore_text: bool = False) -> None:
        self._listening = False
        if self.hasFocus() or restore_text:
            self.releaseMouse()
            self.releaseKeyboard()
        self.setText(_format_trigger(self._current))

    def keyPressEvent(self, event) -> None:
        if not self._listening:
            return super().keyPressEvent(event)

        if event.key() == Qt.Key.Key_Escape:
            self._stop_listening(restore_text=True)
            return
        if event.key() in self._MODIFIER_KEYS:
            return  # wait for a real key - a bare modifier isn't a usable trigger

        sequence = QKeySequence(event.key() | int(event.modifiers().value))
        combo = _qt_to_keyboard(sequence.toString(QKeySequence.SequenceFormat.PortableText))
        if not combo:
            return
        self._current = combo
        self._stop_listening()
        self.triggerCaptured.emit(combo)

    def mousePressEvent(self, event) -> None:
        if not self._listening:
            return super().mousePressEvent(event)

        button_names = {
            Qt.MouseButton.LeftButton: "left",
            Qt.MouseButton.RightButton: "right",
            Qt.MouseButton.MiddleButton: "middle",
            Qt.MouseButton.BackButton: "x",
            Qt.MouseButton.ForwardButton: "x2",
        }
        name = button_names.get(event.button())
        if name is None:
            return
        combo = f"mouse:{name}"
        self._current = combo
        self._stop_listening()
        self.triggerCaptured.emit(combo)

    def focusOutEvent(self, event) -> None:
        if self._listening:
            self._stop_listening(restore_text=True)
        super().focusOutEvent(event)


class KeyComboCaptureButton(TriggerCaptureButton):
    """
    The four app-level global shortcuts (GLOBAL_SHORTCUT_IDS) used to be
    QKeySequenceEdit - replaced with this after three separate rounds of
    trying to make that widget's text reliably follow theme changes
    (QPalette roles, then +HighlightedText, then reaching into its
    private internal QLineEdit child) still wasn't enough on a real
    Windows session. TriggerCaptureButton, right above, never had ANY of
    those problems - it's a plain QPushButton, and plain QPushButtons
    have always painted their text color correctly from QSS alone in
    this app. Rather than chase QKeySequenceEdit's theming through a
    fourth fix, this reuses the button that was already proven reliable.

    Keyboard-only: overrides mousePressEvent to do nothing, since a
    mouse button isn't valid for keyboard.add_hotkey the way it is for a
    Recorder trigger (see RECORDER_SHORTCUT_IDS' own docstring) - a
    global shortcut MUST end up as a real key combo string.
    """

    def _start_listening(self) -> None:
        self._listening = True
        self.setText(tr("Press a key combination… (Esc to cancel)"))
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.grabKeyboard()
        # Deliberately no grabMouse() - see class docstring: mouse clicks
        # aren't a valid combo for this widget, so there's nothing to
        # capture from them, and grabbing the mouse here would just make
        # the rest of the app unclickable while listening for no reason.

    def _stop_listening(self, restore_text: bool = False) -> None:
        self._listening = False
        if self.hasFocus() or restore_text:
            self.releaseKeyboard()
        self.setText(_format_trigger(self._current))

    def mousePressEvent(self, event) -> None:
        # Always the non-capturing path - see class docstring.
        return super(TriggerCaptureButton, self).mousePressEvent(event)


def _confirm_dialog_style(c: dict) -> str:
    """
    Theme-token stylesheet for a real QMessageBox instance - see
    app/sidebar_widget.py's own _confirm_style (same idea, this page's
    own copy since object names/wording differ) for why this exists at
    all: QMessageBox.question()/.warning()'s one-line convenience
    wrappers can't be styled, so left plain they fall back to whatever
    native chrome Qt defaults to - readable in dark theme mostly by
    accident, but not actually reliable across themes (a native
    light-mode button style here could render pale-on-pale against a
    light theme just as easily). Apply with box.setStyleSheet(...) on
    a real QMessageBox(self) built with .addButton(...) rather than
    the convenience wrapper.
    """
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


def _build_style(c: dict) -> str:
    return f"""
QWidget#SettingsWidget {{ background-color: {c['bg_base']}; }}
QScrollArea#settingsScroll {{ background: transparent; border: none; }}
QWidget#settingsContent {{ background-color: {c['bg_base']}; }}
QScrollArea#settingsScroll QScrollBar:vertical {{
    background: transparent;
    border: none;
    width: 8px;
    margin: 2px 0px 2px 0px;
}}
QScrollArea#settingsScroll QScrollBar::handle:vertical {{
    background: {c['bg_hover_strong']};
    border-radius: 4px;
    min-height: 28px;
}}
QScrollArea#settingsScroll QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
QScrollArea#settingsScroll QScrollBar::add-line:vertical,
QScrollArea#settingsScroll QScrollBar::sub-line:vertical {{ height: 0px; subcontrol-position: bottom; subcontrol-origin: margin; }}
QScrollArea#settingsScroll QScrollBar::add-page:vertical,
QScrollArea#settingsScroll QScrollBar::sub-page:vertical {{ background: transparent; }}
QLabel#pageTitle {{
    color: {c['text_primary']};
    font-family: "Syne";
    font-size: 16px;
    font-weight: 700;
    background: transparent;
    padding: 3px 0px;
}}
QLabel#subtitle {{
    color: {c['text_muted']};
    font-family: "Segoe UI";
    font-size: 12px;
    background: transparent;
    padding: 2px 0px;
}}
QLabel#sectionTitle {{
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 13px;
    font-weight: 600;
    background: transparent;
    padding: 3px 0px;
    margin-top: 6px;
}}
QLabel#sectionHint {{
    color: {c['text_faint']};
    font-family: "Segoe UI";
    font-size: 11px;
    background: transparent;
    padding: 2px 0px;
}}
QLabel#statusLabel {{
    color: {c['text_muted']};
    font-family: "Segoe UI";
    font-size: 11px;
    background: transparent;
    padding: 2px 0px;
}}
QLabel#statusLabel[state="ok"] {{ color: {c['success']}; }}
QLabel#statusLabel[state="warn"] {{ color: {c['warning']}; }}
QLabel#providerKeyLabel {{
    color: {c['text_secondary']};
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    background: transparent;
    padding: 6px 0px 2px 0px;
}}
QLabel#profileName {{
    color: {c['text_secondary']};
    font-family: "Segoe UI";
    font-size: 12px;
    background: transparent;
}}
QLabel#profileNameActive {{
    color: {c['accent']};
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    background: transparent;
}}
QLineEdit#profileNameEdit, QLineEdit#profileNameEditActive {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 3px 6px;
    font-family: "Segoe UI";
    font-size: 12px;
}}
QLineEdit#profileNameEdit {{ color: {c['text_secondary']}; }}
QLineEdit#profileNameEditActive {{ color: {c['accent']}; font-weight: 600; }}
QLineEdit#profileNameEdit:hover, QLineEdit#profileNameEditActive:hover {{
    background-color: {c['bg_hover']};
}}
QLineEdit#profileNameEdit:focus, QLineEdit#profileNameEditActive:focus {{
    background-color: {c['bg_input']};
    border: 1px solid {c['accent']};
}}
QPushButton#btnProfilePrompt {{
    background-color: transparent;
    color: {c['text_muted']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    padding: 4px 10px;
    font-family: "Segoe UI";
    font-size: 11px;
}}
QPushButton#btnProfilePrompt:hover {{
    color: {c['text_primary']};
    border-color: {c['border_strong']};
    background-color: {c['bg_hover']};
}}
QPushButton#btnProfileSwitch {{
    background-color: transparent;
    color: {c['accent']};
    border: 1px solid {c['accent']};
    border-radius: 6px;
    padding: 4px 10px;
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#btnProfileSwitch:hover {{ background-color: rgba({c['accent_rgb']},0.12); }}
QPushButton#btnProfileDelete {{
    background: transparent;
    border: none;
    padding: 4px;
}}
QPushButton#btnAddProfile {{
    background: transparent;
    color: {c['accent']};
    border: 1px dashed {c['border_strong']};
    border-radius: 8px;
    padding: 8px;
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    margin-top: 4px;
}}
QPushButton#btnAddProfile:hover {{ background-color: rgba({c['accent_rgb']},0.08); }}
QLineEdit#apiKeyInput {{
    background-color: {c['bg_panel']};
    color: {c['text_primary']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 7px;
    padding: 8px 10px;
    font-family: "Segoe UI";
    font-size: 12px;
}}
QLineEdit#apiKeyInput:focus {{ border-color: {c['accent']}; }}
QSpinBox, QDoubleSpinBox {{
    background-color: {c['bg_panel']};
    color: {c['text_primary']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 7px;
    padding: 6px 8px;
    font-family: "Segoe UI";
    font-size: 12px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {c['accent']}; }}
/* Native up/down buttons are turned off entirely (see SpinStepper) - trying to reskin them via QSS ::up-button/::down-button/::up-arrow/
   ::down-arrow looked bad in practice: styling the button box makes Qt
   stop drawing its own arrow glyph correctly, and there's no reliable
   way to put a real, dynamically-colored icon INTO a QSS arrow pseudo-
   element (QSS `image: url()` needs an actual file, not a runtime
   QIcon) - a CSS-triangle-border approximation was tried and looked
   rough. Real QToolButtons with real app/icons.py icons next to the spin
   box sidesteps all of that, using the exact same icon mechanism
   already proven reliable everywhere else in this app. */
QPushButton#spinStepperBtn {{
    background-color: {c['bg_elevated']};
    border: none;
    padding: 0px;
}}
QPushButton#spinStepperBtn:hover {{ background-color: {c['bg_hover']}; }}
QPushButton#spinStepperBtn:pressed {{ background-color: {c['bg_hover_strong']}; }}
QPushButton#spinStepperBtn:disabled {{ background-color: {c['bg_panel']}; }}
QPushButton#btnSaveKey {{
    background-color: {c['accent']};
    color: {c['white']};
    border: none;
    border-radius: 7px;
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    padding: 0px 16px;
    min-height: 34px;
}}
QPushButton#btnSaveKey:hover {{ background-color: {c['accent_hover']}; }}
QPushButton#btnSaveKey:pressed {{ background-color: {c['accent_pressed']}; }}
QPushButton#btnSaveKey:disabled {{ background-color: {c['border']}; color: {c['text_disabled']}; }}
QPushButton#shotQualityBtn {{
    background-color: {c['bg_panel']};
    color: {c['text_muted']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 7px;
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 7px 14px;
}}
QPushButton#shotQualityBtn:hover {{ border-color: {c['accent']}; color: {c['text_primary']}; }}
QPushButton#shotQualityBtn:checked {{
    background-color: rgba({c['accent_rgb']},0.16);
    color: {c['accent']};
    border-color: {c['accent']};
    font-weight: 600;
}}
QCheckBox#wakeWordCheck {{
    /* Bug fix: shared by every settings checkbox now (Extended thinking,
       Stream responses, the wake word toggle itself) - those two used to
       be plain unstyled QCheckBox widgets, which render with the native
       OS checkmark glyph inside a native-styled box. Reusing this same
       custom flat "box" indicator (no checkmark glyph, just a filled
       accent-colored square when checked) makes every toggle in Settings
       look consistent instead of two of them looking like a totally
       different, unstyled control. */
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 13px;
    background: transparent;
    spacing: 10px;
    padding: 2px 0px;
}}
QCheckBox#wakeWordCheck::indicator {{
    width: 18px; height: 18px;
    border-radius: 5px;
    border: 1px solid {c['border_strong']};
    background-color: {c['bg_panel']};
}}
QCheckBox#wakeWordCheck::indicator:checked {{
    background-color: {c['accent']};
    border-color: {c['accent']};
}}
QLabel#shortcutRefName {{
    color: {c['text_secondary']};
    font-family: "Segoe UI";
    font-size: 12px;
    background: transparent;
    padding: 2px 0px;
}}
QLabel#shortcutRefKeys {{
    color: {c['accent']};
    font-family: "Segoe UI";
    font-size: 12px;
    font-weight: 600;
    background: transparent;
    padding: 2px 0px;
}}
QPushButton#triggerCaptureBtn {{
    background-color: {c['bg_panel']};
    color: {c['text_primary']};
    border: 1px solid {c['bg_hover_strong']};
    border-radius: 7px;
    padding: 6px 10px;
    font-family: "Segoe UI";
    font-size: 12px;
    min-width: 140px;
}}
QPushButton#triggerCaptureBtn:hover {{ border-color: {c['accent']}; }}
QPushButton#triggerCaptureBtn:focus {{ border-color: {c['accent']}; background-color: {c['bg_hover']}; }}
"""


class ThemeDropdown(StyledDropdown):
    """
    Thin StyledDropdown wrapper preserving this widget's original,
    theme-specific API (currentThemeChanged instead of the generic
    currentValueChanged, and set_theme() doing double duty as both
    "recolor" and "select this value" in one call) - for the theme
    picker specifically, "pick a theme" and "the app's theme changed"
    really are the same event, unlike a generic dropdown's value and
    color being two unrelated things.
    """
    currentThemeChanged = pyqtSignal(str)

    _OPTIONS = [("dark", "Dark"), ("light", "Light")]

    def __init__(self, current: str, parent: "QWidget | None" = None):
        super().__init__(self._OPTIONS, current, current, min_width=140, parent=parent)
        self.currentValueChanged.connect(self.currentThemeChanged)

    def currentData(self) -> str:
        return self.current_value()

    def set_theme(self, theme_name: str) -> None:
        self.apply_theme(theme_name)
        self.set_value(theme_name)


class SpinStepper(QWidget):
    """
    Replaces a QSpinBox/QDoubleSpinBox's native up/down buttons with two
    small real QPushButtons carrying real app/icons.py chevron icons - see
    _build_style's own comment on QSpinBox/QDoubleSpinBox for why: QSS
    styling of the native ::up-button/::down-button/::up-arrow/::down-
    arrow subcontrols looked bad in practice and there's no reliable way
    to put a real, dynamically-colored icon INTO a QSS arrow pseudo-
    element. This sidesteps that entirely by not using the native
    buttons at all.

    Usage: construct right after the spinbox, add both to the same row.
        spin = QSpinBox(); ...
        stepper = SpinStepper(spin)
        row.addWidget(spin); row.addWidget(stepper)
    Call stepper.set_theme(c) alongside every other themed widget on the
    page - it re-tints the icons, the same way icons.icon() calls
    elsewhere in this app already do on every theme switch.
    """

    def __init__(self, spin: QAbstractSpinBox, parent: QWidget | None = None):
        super().__init__(parent)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self._up = QPushButton()
        self._up.setObjectName("spinStepperBtn")
        self._up.setCursor(Qt.CursorShape.PointingHandCursor)
        self._up.setFixedSize(20, 15)
        self._up.clicked.connect(spin.stepUp)
        layout.addWidget(self._up)

        self._down = QPushButton()
        self._down.setObjectName("spinStepperBtn")
        self._down.setCursor(Qt.CursorShape.PointingHandCursor)
        self._down.setFixedSize(20, 15)
        self._down.clicked.connect(spin.stepDown)
        layout.addWidget(self._down)

    def set_theme(self, c: dict) -> None:
        self._up.setIcon(icons.icon("chevron-up", c, "text_muted"))
        self._up.setIconSize(QSize(9, 9))
        self._down.setIcon(icons.icon("chevron-down", c, "text_muted"))
        self._down.setIconSize(QSize(9, 9))


class SettingsWidget(QWidget):
    # Emitted whenever the assistant name is saved, so main_window (and
    # anything else showing the name right now, e.g. an already-visible
    # wake-word status label) can refresh without this page knowing who
    # else cares about the name.
    assistant_name_changed = pyqtSignal(str)
    # Emitted whenever the wake word checkbox changes, so main_window can
    # start/stop the background listener without this page knowing
    # anything about how that listener works.
    wake_word_toggled = pyqtSignal(bool)
    # Emitted when the user saves a new global shortcut - main_window owns
    # the actual keyboard-binding logic, this page just asks for it.
    shortcut_change_requested = pyqtSignal(str, str)  # (shortcut_id, new_combo)
    # Emitted when the user picks a different theme - main_window owns
    # re-styling every OTHER widget (this page re-styles itself directly).
    theme_changed = pyqtSignal(str)  # "dark" | "light"
    language_changed = pyqtSignal(str)  # BCP-47 code - applied live, no restart

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SettingsWidget")
        self._theme = ai_main.get_theme()
        self._spin_steppers: list[SpinStepper] = []
        self.setStyleSheet(_build_style(theme.colors(self._theme)))
        self._build_ui()
        self._refresh_key_status()
        for stepper in self._spin_steppers:
            stepper.set_theme(theme.colors(self._theme))

    def set_theme(self, theme_name: str) -> None:
        """Re-styles this page for `theme_name` without rebuilding it - called by main_window on every widget when the user switches themes."""
        self._theme = theme_name
        self.setStyleSheet(_build_style(theme.colors(theme_name)))
        for stepper in self._spin_steppers:
            stepper.set_theme(theme.colors(theme_name))
        # No per-widget palette pass needed for the shortcut capture
        # buttons anymore - see KeyComboCaptureButton's docstring for why
        # that whole category of fix (QKeySequenceEdit's theming) was
        # replaced rather than patched a fourth time. Plain QPushButtons
        # (this and TriggerCaptureButton) always recolor correctly from
        # the setStyleSheet() cascade above alone.
        # Every StyledDropdown on the page, found rather than named.
        #
        # These paint from a colours dict captured at construction and
        # deliberately do NOT inherit the page's QSS cascade, so each one
        # has to be told. Listing them by hand is how _language_dropdown
        # got missed - it sits two lines from _theme_dropdown in the
        # builder and was simply forgotten here, so it kept dark colours
        # on a light page. findChildren cannot forget one.
        for dropdown in self.findChildren(StyledDropdown):
            dropdown.apply_theme(theme_name)
        if hasattr(self, "_theme_dropdown"):
            # Re-selects as well as recolours; see ThemeDropdown.
            self._theme_dropdown.set_theme(theme_name)
        if hasattr(self, "_profiles_list_layout"):
            # Rebuilds the profile rows rather than recoloring each
            # StyledDropdown/inline name editor in place - those get
            # their colors from a direct setStyleSheet() at construction
            # time, not the cascading QSS set_theme() applies just
            # above, so a rebuild is simpler than tracking every one of
            # them separately just to re-theme them (same "clear and
            # rebuild" approach _refresh_profiles_list already uses for
            # add/switch/delete).
            self._refresh_profiles_list()

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("settingsContent")
        scroll.setWidget(content)

        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(6)

        title = QLabel(tr("Settings"))
        title.setObjectName("pageTitle")
        root.addWidget(title)

        # ── Theme ──────────────────────────────────────────────────
        theme_title = QLabel(tr("Appearance"))
        theme_title.setObjectName("sectionTitle")
        root.addWidget(theme_title)

        theme_hint = QLabel(tr("Dark or light - doesn't need a restart."))
        theme_hint.setObjectName("sectionHint")
        theme_hint.setWordWrap(True)
        root.addWidget(theme_hint)

        self._theme_dropdown = ThemeDropdown(self._theme)
        self._theme_dropdown.currentThemeChanged.connect(self._on_theme_combo_changed)
        root.addWidget(self._theme_dropdown)

        # ── Language ─────────────────────────────────────────────────
        # Only shown when there is something to switch to. A picker with
        # one entry is a question with one answer.
        languages = i18n.available_languages()
        if len(languages) > 1:
            language_title = QLabel(tr("Language"))
            language_title.setObjectName("sectionTitle")
            root.addWidget(language_title)

            language_hint = QLabel(tr(
                "Applies straight away. Anything not yet translated "
                "stays in English."
            ))
            language_hint.setObjectName("sectionHint")
            language_hint.setWordWrap(True)
            root.addWidget(language_hint)

            options = [("", tr("English (default)"))] + list(languages[1:]) + [
                (i18n.FOLLOW_SYSTEM, tr("Match my system")),
            ]
            self._language_dropdown = StyledDropdown(
                options, ai_main.get_language(), self._theme, min_width=180
            )
            self._language_dropdown.currentValueChanged.connect(self._on_language_changed)
            root.addWidget(self._language_dropdown)

            self._language_status = QLabel("")
            self._language_status.setObjectName("statusLabel")
            root.addWidget(self._language_status)

        # ── Chat ─────────────────────────────────────────────────────
        chat_title = QLabel(tr("Chat"))
        chat_title.setObjectName("sectionTitle")
        root.addWidget(chat_title)

        streaming_check = QCheckBox(tr("Stream responses as they're generated"))
        streaming_check.setObjectName("wakeWordCheck")
        streaming_check.setCursor(Qt.CursorShape.PointingHandCursor)
        streaming_check.setChecked(ai_main.get_streaming_enabled())
        streaming_check.stateChanged.connect(
            lambda state: ai_main.set_streaming_enabled(bool(state))
        )
        root.addWidget(streaming_check)

        streaming_hint = QLabel(
            tr("On: replies appear word by word as the model actually generates "
            "them (real network streaming, not a typing effect). Off: replies "
            "appear all at once, typed out locally.")
        )
        streaming_hint.setObjectName("sectionHint")
        streaming_hint.setWordWrap(True)
        root.addWidget(streaming_hint)

        voice_review_check = QCheckBox(tr("Review voice messages before sending"))
        voice_review_check.setObjectName("wakeWordCheck")
        voice_review_check.setCursor(Qt.CursorShape.PointingHandCursor)
        voice_review_check.setChecked(ai_main.get_voice_review_before_send())
        voice_review_check.stateChanged.connect(
            lambda state: ai_main.set_voice_review_before_send(bool(state))
        )
        root.addWidget(voice_review_check)

        voice_review_hint = QLabel(
            tr("On: what the microphone heard lands in the chat box first, so "
            "you can read it, fix a misheard word, or press Escape to throw "
            "it away - Enter sends it. Off: it's sent the moment "
            "transcription finishes, which is what you want for hands-free "
            "use across the room. Either way the reply is spoken back.")
        )
        voice_review_hint.setObjectName("sectionHint")
        voice_review_hint.setWordWrap(True)
        root.addWidget(voice_review_hint)

        thinking_check = QCheckBox(tr("Extended thinking"))
        thinking_check.setObjectName("wakeWordCheck")
        thinking_check.setCursor(Qt.CursorShape.PointingHandCursor)
        thinking_check.setChecked(ai_main.get_extended_thinking_enabled())
        thinking_check.stateChanged.connect(
            lambda state: ai_main.set_extended_thinking_enabled(bool(state))
        )
        root.addWidget(thinking_check)

        thinking_hint = QLabel(
            "On: reasoning-capable models think through the problem first - "
            "you'll see a collapsible \"Thinking…\" section above the reply, "
            "the same as claude.ai's own extended thinking. Works with "
            "Claude (extended thinking), Gemini (thought summaries), and "
            "GPT-5 reasoning models (reasoning summaries) - plain GPT "
            "\"-mini\"/\"-nano\" variants don't reason at all, so this has no "
            "effect on those. Uses more tokens and takes a bit longer per "
            "reply."
        )
        thinking_hint.setObjectName("sectionHint")
        thinking_hint.setWordWrap(True)
        root.addWidget(thinking_hint)

        # ── Gemini API key ─────────────────────────────────────────
        # ── API keys ───────────────────────────────────────────────
        # One row per provider, not just Gemini - the model picker
        # (app/chat_widget.py) lets a Founding-tier install switch to
        # any provider in ai.providers.base.MODEL_CHOICES (Gemini,
        # OpenAI, or Anthropic), but this section used to only ever
        # build a Gemini key field. Picking a GPT or Claude model
        # with no way to ever paste a key for it just failed with
        # "Couldn't switch model" (ai_main.set_active_provider_and_model
        # raising ProviderClientError, caught in chat_widget.py's
        # _on_model_picker_changed) - the key it needed had nowhere
        # to be entered. ai.main already had the per-provider plumbing
        # (get_api_key_for/save_api_key_for/get_api_key_masked_for,
        # keyed by ai.main._PROVIDER_KEYRING_ACCOUNTS) this section
        # just never called it for anything but "gemini".
        key_title = QLabel(tr("API keys"))
        key_title.setObjectName("sectionTitle")
        root.addWidget(key_title)

        key_hint = QLabel(
            tr("Bring your own key for whichever provider(s) you use - "
            "only needed for a provider you actually pick in the "
            "chat's model dropdown. Stored encrypted in your OS's "
            "credential store (Keychain / Credential Manager / "
            "Secret Service), never in plain text.")
        )
        key_hint.setObjectName("sectionHint")
        key_hint.setWordWrap(True)
        root.addWidget(key_hint)

        # (label, provider id, where to get a key, placeholder)
        # "Local" has no key_url: most local servers (Ollama, LM
        # Studio, llama.cpp's server) don't check for a key at all,
        # so this row is here purely for the LAN/reverse-proxy case
        # where one enforces a bearer token.
        _PROVIDERS = [
            ("Gemini",    "gemini",    "aistudio.google.com/app/apikey"),
            ("OpenAI",    "openai",    "platform.openai.com/api-keys"),
            ("Anthropic", "anthropic", "console.anthropic.com/settings/keys"),
            ("Local",     "local",     None),
        ]

        # Keyed by provider id so the per-provider handlers below
        # (_on_key_input_changed/_on_save_key_clicked/_refresh_key_status)
        # know which widgets and which ai_main call belong to which
        # row - the single self._key_input/_btn_save_key/_key_status
        # this replaces only ever had one of each to worry about.
        self._key_inputs: dict[str, QLineEdit] = {}
        self._key_save_buttons: dict[str, QPushButton] = {}
        self._key_statuses: dict[str, QLabel] = {}

        for label, pid, key_url in _PROVIDERS:
            provider_title = QLabel(label)
            provider_title.setObjectName("providerKeyLabel")
            root.addWidget(provider_title)

            key_row = QHBoxLayout()
            key_row.setSpacing(8)

            key_input = QLineEdit()
            key_input.setObjectName("apiKeyInput")
            key_input.setEchoMode(QLineEdit.EchoMode.Password)
            key_input.setPlaceholderText(
                f"Paste a {label} API key… (get one at {key_url})" if key_url
                else "Optional - most local servers don't require one"
            )
            key_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            key_input.textChanged.connect(lambda text, p=pid: self._on_key_input_changed(p, text))
            key_input.returnPressed.connect(lambda p=pid: self._on_save_key_clicked(p))
            key_row.addWidget(key_input, 1)
            self._key_inputs[pid] = key_input

            btn_save_key = QPushButton(tr("Save"))
            btn_save_key.setObjectName("btnSaveKey")
            btn_save_key.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_save_key.setEnabled(False)
            btn_save_key.clicked.connect(lambda _checked=False, p=pid: self._on_save_key_clicked(p))
            key_row.addWidget(btn_save_key)
            self._key_save_buttons[pid] = btn_save_key

            root.addLayout(key_row)

            key_status = QLabel()
            key_status.setObjectName("statusLabel")
            root.addWidget(key_status)
            self._key_statuses[pid] = key_status

        # ── Local model server ───────────────────────────────────────
        # Picking "Local model" in the chat's model dropdown
        # is what actually switches to it; these two fields are where
        # that switch gets pointed.
        local_title = QLabel(tr("Local model"))
        local_title.setObjectName("sectionTitle")
        root.addWidget(local_title)

        local_hint = QLabel(
            tr("Run a model on your own machine instead of the cloud - "
            "Ollama, LM Studio, llama.cpp's server, vLLM, or anything "
            "else that exposes an OpenAI-compatible endpoint. Pick "
            "\"Local model\" from the model dropdown in chat to use it. "
            "Nothing you send ever leaves your machine/network.")
        )
        local_hint.setObjectName("sectionHint")
        local_hint.setWordWrap(True)
        root.addWidget(local_hint)

        local_url_row = QHBoxLayout()
        local_url_row.setSpacing(8)
        self._local_url_input = QLineEdit()
        self._local_url_input.setObjectName("apiKeyInput")
        self._local_url_input.setPlaceholderText(f"Server URL, e.g. {ai_main.DEFAULT_LOCAL_BASE_URL}")
        self._local_url_input.setText(ai_main.get_local_base_url())
        self._local_url_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        local_url_row.addWidget(self._local_url_input, 1)
        btn_save_local_url = QPushButton(tr("Save"))
        btn_save_local_url.setObjectName("btnSaveKey")
        btn_save_local_url.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_local_url.clicked.connect(self._on_save_local_url_clicked)
        local_url_row.addWidget(btn_save_local_url)
        root.addLayout(local_url_row)

        local_model_row = QHBoxLayout()
        local_model_row.setSpacing(8)
        self._local_model_input = QLineEdit()
        self._local_model_input.setObjectName("apiKeyInput")
        self._local_model_input.setPlaceholderText(f"Model name, e.g. {ai_main.DEFAULT_LOCAL_MODEL_NAME}")
        self._local_model_input.setText(ai_main.get_local_model_name())
        self._local_model_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        local_model_row.addWidget(self._local_model_input, 1)
        btn_save_local_model = QPushButton(tr("Save"))
        btn_save_local_model.setObjectName("btnSaveKey")
        btn_save_local_model.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_local_model.clicked.connect(self._on_save_local_model_clicked)
        local_model_row.addWidget(btn_save_local_model)

        # Saving a URL says nothing about whether anything is listening
        # on it, which is the actual question when a local model does
        # not answer.
        self._btn_test_local = QPushButton(tr("Test connection"))
        self._btn_test_local.setObjectName("btnSaveKey")
        self._btn_test_local.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_test_local.clicked.connect(self._on_test_local_clicked)
        local_model_row.addWidget(self._btn_test_local)
        root.addLayout(local_model_row)

        self._local_status = QLabel()
        self._local_status.setObjectName("statusLabel")
        root.addWidget(self._local_status)

        # ── AI profiles ────────────────────────────────────────────
        # Superseded the old separate "Assistant name" text field +
        # Save button that used to sit here - that field only ever
        # renamed whichever profile happened to be active, with no way
        # to see or touch any OTHER profile from it. Renaming (click a
        # profile's name below), picking its model, and now its custom
        # prompt too are all just properties of a row in the list this
        # builds - one section instead of two saying almost the same
        # thing in two different places.
        self._build_profiles_section(root)

        # ── Wake word ──────────────────────────────────────────────
        wake_title = QLabel(tr("Wake word"))
        wake_title.setObjectName("sectionTitle")
        root.addWidget(wake_title)

        self._wake_hint = QLabel()
        self._wake_hint.setObjectName("sectionHint")
        self._wake_hint.setWordWrap(True)
        root.addWidget(self._wake_hint)

        self._wake_check = QCheckBox()
        self._wake_check.setObjectName("wakeWordCheck")
        self._wake_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_check.setChecked(ai_main.get_wake_word_enabled())
        self._wake_check.toggled.connect(self._on_wake_word_toggled)
        root.addWidget(self._wake_check)
        self._refresh_wake_word_copy()

        self._wake_status = QLabel()
        self._wake_status.setObjectName("statusLabel")
        root.addWidget(self._wake_status)

        # ── Sounds ─────────────────────────────────────────────────
        sounds_title = QLabel(tr("Sounds"))
        sounds_title.setObjectName("sectionTitle")
        root.addWidget(sounds_title)

        sounds_check = QCheckBox(tr("Play sound effects"))
        sounds_check.setObjectName("wakeWordCheck")
        sounds_check.setCursor(Qt.CursorShape.PointingHandCursor)
        sounds_check.setChecked(ai_main.get_sounds_enabled())

        def _on_sounds_toggled(state) -> None:
            ai_main.set_sounds_enabled(bool(state))
            if state:
                # Hearing one is the quickest way to know what "on" means.
                ai_main.play_notification_sound("macro_finished")

        sounds_check.stateChanged.connect(_on_sounds_toggled)
        root.addWidget(sounds_check)

        sounds_hint = QLabel(
            tr("Short sounds for things that happen while you're looking elsewhere: "
               "an automation finishing or stopping, the wake word being heard, the "
               "microphone opening, a screenshot being taken, and Kestrel needing "
               "you to finish a step by hand.")
        )
        sounds_hint.setObjectName("sectionHint")
        sounds_hint.setWordWrap(True)
        root.addWidget(sounds_hint)

        # ── Keyboard shortcuts ─────────────────────────────────────
        shortcuts_title = QLabel(tr("Keyboard shortcuts"))
        shortcuts_title.setObjectName("sectionTitle")
        root.addWidget(shortcuts_title)

        shortcuts_hint = QLabel(
            tr("These shortcuts work anywhere, even if Kestrel isn't in the "
            "foreground. Click in the field, then press the combination "
            "you want.")
        )
        shortcuts_hint.setObjectName("sectionHint")
        shortcuts_hint.setWordWrap(True)
        root.addWidget(shortcuts_hint)

        # One row per rebindable global shortcut. Order matches
        # ai_main.DEFAULT_SHORTCUTS; label text comes from SHORTCUT_LABELS
        # so this list is the only place that needs to grow if a new
        # global shortcut is added later.
        self._shortcut_edits: dict[str, KeyComboCaptureButton] = {}
        self._shortcut_status_labels: dict[str, QLabel] = {}

        for shortcut_id in ai_main.GLOBAL_SHORTCUT_IDS:
            row = QHBoxLayout()
            row.setSpacing(8)

            label = QLabel(ai_main.SHORTCUT_LABELS.get(shortcut_id, shortcut_id))
            label.setObjectName("shortcutRefName")
            row.addWidget(label, 1)

            edit = KeyComboCaptureButton(ai_main.get_shortcut(shortcut_id))
            edit.setObjectName("triggerCaptureBtn")
            edit.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(edit)
            self._shortcut_edits[shortcut_id] = edit

            btn_save = QPushButton(tr("Save"))
            btn_save.setObjectName("btnSaveKey")
            btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_save.clicked.connect(lambda _checked=False, sid=shortcut_id: self._on_save_shortcut_clicked(sid))
            row.addWidget(btn_save)

            root.addLayout(row)

            status = QLabel()
            status.setObjectName("statusLabel")
            root.addWidget(status)
            self._shortcut_status_labels[shortcut_id] = status

        # ── Recorder triggers ─────────────────────────────────────────
        recorder_title = QLabel(tr("Recorder triggers"))
        recorder_title.setObjectName("sectionTitle")
        root.addWidget(recorder_title)

        recorder_hint = QLabel(
            tr("What triggers each action while recording on the Recorder "
            "page. Can be a keyboard key/combo OR a mouse button (side "
            "buttons and middle-click work well here, since left/right "
            "click stay free for normal use). Click a field, then press "
            "the key or mouse button you want.")
        )
        recorder_hint.setObjectName("sectionHint")
        recorder_hint.setWordWrap(True)
        root.addWidget(recorder_hint)

        self._trigger_buttons: dict[str, TriggerCaptureButton] = {}

        for shortcut_id in ai_main.RECORDER_SHORTCUT_IDS:
            row = QHBoxLayout()
            row.setSpacing(8)

            label = QLabel(ai_main.SHORTCUT_LABELS.get(shortcut_id, shortcut_id))
            label.setObjectName("shortcutRefName")
            row.addWidget(label, 1)

            btn = TriggerCaptureButton(ai_main.get_shortcut(shortcut_id))
            btn.setObjectName("triggerCaptureBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.triggerCaptured.connect(
                lambda combo, sid=shortcut_id: self._on_trigger_captured(sid, combo)
            )
            row.addWidget(btn)
            self._trigger_buttons[shortcut_id] = btn

            root.addLayout(row)

        # ── Where generated files are saved ─────────────────────────
        files_title = QLabel(tr("Saved files"))
        files_title.setObjectName("sectionTitle")
        root.addWidget(files_title)

        files_hint = QLabel(
            "Where documents, spreadsheets and PDFs the assistant makes for "
            "you are written. These are your own files, not app data - put "
            "them on whichever drive or synced folder you actually work in. "
            "Leave it empty to use Documents\\Kestrel Files."
        )
        files_hint.setObjectName("sectionHint")
        files_hint.setWordWrap(True)
        root.addWidget(files_hint)

        files_row = QHBoxLayout()
        files_row.setSpacing(8)

        self._files_dir_input = QLineEdit()
        self._files_dir_input.setObjectName("apiKeyInput")
        import ai.file_generation as file_generation
        self._files_dir_input.setPlaceholderText(file_generation.default_generated_files_dir())
        self._files_dir_input.setText(ai_main.get_generated_files_dir())
        self._files_dir_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        files_row.addWidget(self._files_dir_input, 1)

        btn_browse_files = QPushButton(tr("Browse…"))
        btn_browse_files.setObjectName("btnSaveKey")
        btn_browse_files.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse_files.clicked.connect(self._on_browse_files_dir_clicked)
        files_row.addWidget(btn_browse_files)

        btn_open_files = QPushButton(tr("Open"))
        btn_open_files.setObjectName("btnSaveKey")
        btn_open_files.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open_files.setToolTip(tr("Open the folder these are saved to"))
        btn_open_files.clicked.connect(self._on_open_files_dir_clicked)
        files_row.addWidget(btn_open_files)

        btn_save_files_dir = QPushButton(tr("Save"))
        btn_save_files_dir.setObjectName("btnSaveKey")
        btn_save_files_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_files_dir.clicked.connect(self._on_save_files_dir_clicked)
        files_row.addWidget(btn_save_files_dir)

        root.addLayout(files_row)

        self._files_dir_status = QLabel()
        self._files_dir_status.setObjectName("statusLabel")
        root.addWidget(self._files_dir_status)

        # ── Mouse ────────────────────────────────────────────────────
        # The AI clicks without the user's mouse (ai/direct_input.py);
        # this is whether it may borrow it for an app that takes nothing
        # but a real click.
        mouse_title = QLabel(tr("Mouse"))
        mouse_title.setObjectName("sectionTitle")
        root.addWidget(mouse_title)

        mouse_check = QCheckBox(tr("Use the mouse when an app can't be clicked any other way"))
        mouse_check.setObjectName("wakeWordCheck")
        mouse_check.setCursor(Qt.CursorShape.PointingHandCursor)
        mouse_check.setChecked(ai_main.get_mouse_fallback())
        mouse_check.stateChanged.connect(lambda state: ai_main.set_mouse_fallback(bool(state)))
        root.addWidget(mouse_check)

        mouse_hint = QLabel(
            tr("The AI clicks in apps without moving your mouse, so you can keep using "
               "it while the AI works. A few apps only react to a real click: for those, "
               "Kestrel moves the mouse, clicks, and puts it back where it was. With this "
               "off, it skips those clicks and tells the AI why.")
        )
        mouse_hint.setObjectName("sectionHint")
        mouse_hint.setWordWrap(True)
        root.addWidget(mouse_hint)

        # ── Macro playback delay ────────────────────────────────────
        macro_title = QLabel(tr("Macro playback delay"))
        macro_title.setObjectName("sectionTitle")
        root.addWidget(macro_title)

        macro_hint = QLabel(
            tr("How long the AI waits after each step while replaying a macro. "
            "A step that opens a website or an app needs time to load, so "
            "this depends on your internet connection - raise it if macros "
            "click ahead of a page that hasn't finished loading yet, lower "
            "it once you know a macro doesn't need to wait that long.")
        )
        macro_hint.setObjectName("sectionHint")
        macro_hint.setWordWrap(True)
        root.addWidget(macro_hint)

        macro_row = QHBoxLayout()
        macro_row.setSpacing(8)
        macro_label = QLabel(tr("Delay between steps"))
        macro_label.setObjectName("shortcutRefName")
        macro_row.addWidget(macro_label, 1)

        self._macro_delay_spin = QDoubleSpinBox()
        self._macro_delay_spin.setObjectName("macroDelaySpin")
        self._macro_delay_spin.setSuffix(" s")
        self._macro_delay_spin.setDecimals(1)
        self._macro_delay_spin.setSingleStep(0.1)
        self._macro_delay_spin.setRange(ai_main.MIN_MACRO_ACTION_DELAY, ai_main.MAX_MACRO_ACTION_DELAY)
        self._macro_delay_spin.setValue(ai_main.get_macro_action_delay())
        self._macro_delay_spin.setCursor(Qt.CursorShape.PointingHandCursor)
        macro_row.addWidget(self._macro_delay_spin)
        macro_delay_stepper = SpinStepper(self._macro_delay_spin)
        self._spin_steppers.append(macro_delay_stepper)
        macro_row.addWidget(macro_delay_stepper)

        btn_save_delay = QPushButton(tr("Save"))
        btn_save_delay.setObjectName("btnSaveKey")
        btn_save_delay.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_delay.clicked.connect(self._on_save_macro_delay_clicked)
        macro_row.addWidget(btn_save_delay)

        root.addLayout(macro_row)

        self._macro_delay_status = QLabel()
        self._macro_delay_status.setObjectName("statusLabel")
        root.addWidget(self._macro_delay_status)

        # ── Automation step limit ────────────────────────────────────
        auto_title = QLabel(tr("Automation step limit"))
        auto_title.setObjectName("sectionTitle")
        root.addWidget(auto_title)

        auto_hint = QLabel(
            tr("A safety ceiling for autonomous mode - the AI always stops on "
            "its own as soon as the goal is reached, well before this is "
            "hit in almost every case. It only matters if the goal turns "
            "out to need more steps than expected, or the model gets "
            "stuck. 20 is the default and the recommended value.")
        )
        auto_hint.setObjectName("sectionHint")
        auto_hint.setWordWrap(True)
        root.addWidget(auto_hint)

        auto_row = QHBoxLayout()
        auto_row.setSpacing(8)
        auto_label = QLabel(tr("Max steps per run"))
        auto_label.setObjectName("shortcutRefName")
        auto_row.addWidget(auto_label, 1)

        self._auto_steps_spin = QSpinBox()
        self._auto_steps_spin.setObjectName("autoStepsSpin")
        self._auto_steps_spin.setRange(ai_main.MIN_AUTOMATION_STEPS, ai_main.MAX_AUTOMATION_STEPS)
        self._auto_steps_spin.setValue(ai_main.get_max_automation_steps())
        self._auto_steps_spin.setCursor(Qt.CursorShape.PointingHandCursor)
        auto_row.addWidget(self._auto_steps_spin)
        auto_steps_stepper = SpinStepper(self._auto_steps_spin)
        self._spin_steppers.append(auto_steps_stepper)
        auto_row.addWidget(auto_steps_stepper)

        btn_save_steps = QPushButton(tr("Save"))
        btn_save_steps.setObjectName("btnSaveKey")
        btn_save_steps.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_steps.clicked.connect(self._on_save_auto_steps_clicked)
        auto_row.addWidget(btn_save_steps)

        root.addLayout(auto_row)

        self._auto_steps_status = QLabel()
        self._auto_steps_status.setObjectName("statusLabel")
        root.addWidget(self._auto_steps_status)

        # ── Autonomous-mode screenshot quality ────────────────────────
        shot_title = QLabel(tr("Autonomous mode screenshot quality"))
        shot_title.setObjectName("sectionTitle")
        root.addWidget(shot_title)

        shot_hint = QLabel(
            "Every step of autonomous mode sends a full screenshot to the "
            "AI - by far the single biggest recurring token cost in the "
            "app. Lower settings resize the screenshot before sending "
            "(fewer pixels is what actually reduces token cost - Gemini "
            "tokenizes images by resolution, not file size) - worth "
            "trading down unless a task needs reading small/fine on-"
            "screen detail. \"Original\" sends it unchanged. The presets "
            "below \"Compact\" go all the way down to 144px specifically "
            "to help find where accuracy actually breaks down for a given "
            "task - try one, run the task, and check the Console for both "
            "the real token count per step and whether clicks still land "
            "correctly."
        )
        shot_hint.setObjectName("sectionHint")
        shot_hint.setWordWrap(True)
        root.addWidget(shot_hint)

        self._shot_quality_group = QButtonGroup(self)
        self._shot_quality_group.setExclusive(True)
        self._shot_quality_buttons: dict[str, QPushButton] = {}
        current_quality = ai_main.get_autonomous_screenshot_quality()
        shot_options = (
            ("original", "Original"), ("balanced", "Balanced"), ("compact", "Compact"),
            ("tiny", "Tiny (512px)"), ("minimal", "Minimal (384px)"),
            ("extreme", "Extreme (256px)"), ("floor", "Floor (144px)"),
        )
        # Two rows rather than one - seven buttons in a single QHBoxLayout
        # would overflow a settings-panel-width column; grouped roughly
        # "reasonable for everyday use" (top) vs. "for finding the actual
        # accuracy floor" (bottom) rather than an arbitrary split.
        shot_row_1 = QHBoxLayout()
        shot_row_1.setSpacing(8)
        shot_row_2 = QHBoxLayout()
        shot_row_2.setSpacing(8)
        for i, (value, label) in enumerate(shot_options):
            btn = QPushButton(label)
            btn.setObjectName("shotQualityBtn")
            btn.setCheckable(True)
            btn.setChecked(value == current_quality)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, v=value: self._on_screenshot_quality_clicked(v))
            self._shot_quality_group.addButton(btn)
            self._shot_quality_buttons[value] = btn
            (shot_row_1 if i < 3 else shot_row_2).addWidget(btn)
        root.addLayout(shot_row_1)
        root.addLayout(shot_row_2)

        self._shot_quality_status = QLabel()
        self._shot_quality_status.setObjectName("statusLabel")
        root.addWidget(self._shot_quality_status)

        # ── Safety limits ────────────────────────────────────────────
        safety_title = QLabel(tr("Safety limits"))
        safety_title.setObjectName("sectionTitle")
        root.addWidget(safety_title)

        safety_hint = QLabel(
            tr("Every action the AI can take unattended is tagged with a risk "
            "tier - these are how many actions of each tier can run before "
            "it stops and needs a new task/session.")
        )
        safety_hint.setObjectName("sectionHint")
        safety_hint.setWordWrap(True)
        root.addWidget(safety_hint)

        from ai.safety import RiskLevel, DEFAULT_MAX_ACTIONS
        # General categories, not specific situations - kept short and
        # tier-defining ("Deleting", not "del/rd/rmdir on your Documents
        # folder") since these sit right under a number the person is
        # about to set for the WHOLE tier, not for one example action.
        _RISK_TIER_DESCRIPTIONS = {
            RiskLevel.LOW: "Reading info, opening apps, navigating.",
            RiskLevel.MEDIUM: "Installing, copying, running scripts.",
            RiskLevel.HIGH: "Deleting, shutting down, changing system settings.",
        }
        self._safety_spins: dict[RiskLevel, QSpinBox] = {}
        for level in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH):
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(f"{level.value.capitalize()} risk actions / session")
            label.setObjectName("shortcutRefName")
            row.addWidget(label, 1)

            spin = QSpinBox()
            spin.setObjectName(f"safetyLimitSpin_{level.value}")
            spin.setRange(0, 500)
            spin.setValue(ai_main.get_safety_limit(level))
            spin.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(spin)
            safety_stepper = SpinStepper(spin)
            self._spin_steppers.append(safety_stepper)
            row.addWidget(safety_stepper)
            self._safety_spins[level] = spin

            root.addLayout(row)

            tier_desc = QLabel(_RISK_TIER_DESCRIPTIONS[level])
            tier_desc.setObjectName("sectionHint")
            root.addWidget(tier_desc)

        safety_btn_row = QHBoxLayout()
        safety_btn_row.setSpacing(8)
        btn_save_safety = QPushButton(tr("Save limits"))
        btn_save_safety.setObjectName("btnSaveKey")
        btn_save_safety.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save_safety.clicked.connect(self._on_save_safety_limits_clicked)
        safety_btn_row.addWidget(btn_save_safety)

        btn_reset_safety = QPushButton(tr("Reset session now"))
        btn_reset_safety.setObjectName("btnSaveKey")
        btn_reset_safety.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reset_safety.clicked.connect(self._on_reset_safety_clicked)
        safety_btn_row.addWidget(btn_reset_safety)
        safety_btn_row.addStretch()
        root.addLayout(safety_btn_row)

        self._safety_status = QLabel()
        self._safety_status.setObjectName("statusLabel")
        root.addWidget(self._safety_status)

        fixed_hint = QLabel(tr("Fixed conventions in the chat (not editable):"))
        fixed_hint.setObjectName("sectionHint")
        root.addWidget(fixed_hint)

        ref_grid = QGridLayout()
        ref_grid.setSpacing(6)
        ref_grid.setContentsMargins(0, 2, 0, 0)
        for i, (name, keys) in enumerate([
            ("Send the message", "Enter"),
            ("New line in the message", "Shift + Enter"),
        ]):
            name_lbl = QLabel(name)
            name_lbl.setObjectName("shortcutRefName")
            keys_lbl = QLabel(keys)
            keys_lbl.setObjectName("shortcutRefKeys")
            ref_grid.addWidget(name_lbl, i, 0)
            ref_grid.addWidget(keys_lbl, i, 1)
        root.addLayout(ref_grid)

        self._build_backup_section(root)
        self._build_telemetry_section(root)

        root.addStretch()

    # ── Backup ───────────────────────────────────────────────────────

    def _build_backup_section(self, root) -> None:
        title = QLabel(tr("Backup"))
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        hint = QLabel(tr(
            "Your chats, profiles, settings and macros in one file. API "
            "keys are left out on purpose: they live in your OS credential "
            "store, and a backup is a file that ends up on a USB stick."
        ))
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        btn_backup = QPushButton(tr("Back up now\u2026"))
        btn_backup.setObjectName("btnSaveKey")
        btn_backup.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_backup.clicked.connect(self._on_backup_clicked)
        row.addWidget(btn_backup)

        btn_restore = QPushButton(tr("Restore\u2026"))
        btn_restore.setObjectName("btnSaveKey")
        btn_restore.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_restore.clicked.connect(self._on_restore_clicked)
        row.addWidget(btn_restore)
        row.addStretch()
        root.addLayout(row)

        self._backup_status = QLabel("")
        self._backup_status.setObjectName("statusLabel")
        self._backup_status.setWordWrap(True)
        root.addWidget(self._backup_status)

    def _on_backup_clicked(self) -> None:
        from app import chat_export

        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save a backup"),
            os.path.join(os.path.expanduser("~"), chat_export.default_backup_name()),
            tr("Backup archive (*.zip)"),
        )
        if not path:
            return
        try:
            written, included = chat_export.create_backup(path)
        except chat_export.ExportError as e:
            self._backup_status.setText(str(e))
            return
        self._backup_status.setText(
            tr("Saved to {path}: {contents}.", path=written, contents=", ".join(included))
        )

    def _on_restore_clicked(self) -> None:
        from app import chat_export

        path, _ = QFileDialog.getOpenFileName(
            self, tr("Restore from a backup"),
            os.path.expanduser("~"), tr("Backup archive (*.zip)"),
        )
        if not path:
            return
        try:
            meta = chat_export.read_backup(path)
        except chat_export.ExportError as e:
            self._backup_status.setText(str(e))
            return

        # Restoring replaces live data, so it asks first. This is the one
        # place in the app where a confirmation earns its interruption:
        # it is not undoable from inside the app, only from the folder
        # the old files get moved into.
        answer = QMessageBox.warning(
            self, tr("Restore this backup?"),
            tr(
                "This replaces your current chats, profiles and settings with "
                "the ones from {date}. Your current files are moved into a "
                "dated folder rather than deleted, and the app has to be "
                "restarted afterwards.",
                date=meta.get("created_at", "this backup"),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            restored = chat_export.restore_backup(path)
        except chat_export.ExportError as e:
            self._backup_status.setText(str(e))
            return
        self._backup_status.setText(
            tr("Restored {count} file(s). Close and reopen the app to see them.",
               count=len(restored))
        )

    def _build_telemetry_section(self, root) -> None:
        import ai.telemetry as telemetry
        if not telemetry.is_configured():
            return  # no Sentry DSN configured - nothing to opt into yet

        title = QLabel(tr("Privacy"))
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        checkbox = QCheckBox(tr("Send anonymous crash reports"))
        # The shared settings checkbox look (see QCheckBox#wakeWordCheck
        # in _build_style). This one was missed when the others moved
        # over, so it was the only native-looking box on the page.
        checkbox.setObjectName("wakeWordCheck")
        checkbox.setChecked(ai_main.get_telemetry_enabled())
        checkbox.stateChanged.connect(
            lambda state: ai_main.set_telemetry_enabled(bool(state))
        )
        root.addWidget(checkbox)

        hint = QLabel(
            tr("Off by default. Sends only crash details (no prompts, files, "
            "or activity) if enabled - never turned on without your say-so.")
        )
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

    # ── API key ───────────────────────────────────────────────────────

    def _on_test_local_clicked(self) -> None:
        """
        Probes the endpoint currently typed in the box, not the saved
        one: someone who just changed the URL wants to know whether the
        new one works before committing to it.
        """
        import ai.providers as providers

        url = self._local_url_input.text().strip() or ai_main.get_local_base_url()
        model = self._local_model_input.text().strip() or ai_main.get_local_model_name()

        self._btn_test_local.setEnabled(False)
        self._btn_test_local.setText(tr("Testing\u2026"))
        self._set_local_status(tr("Contacting the server\u2026"), ok=None)
        QApplication.processEvents()   # paint the pending state before blocking
        try:
            ok, message = providers.test_local_connection(url, model)
        except Exception as e:
            ok, message = False, f"The test itself failed: {e}"
        finally:
            self._btn_test_local.setEnabled(True)
            self._btn_test_local.setText(tr("Test connection"))
        self._set_local_status(message, ok=ok)

    def _set_local_status(self, message: str, ok=None) -> None:
        self._local_status.setText(message)
        state = "" if ok is None else ("ok" if ok else "error")
        self._local_status.setProperty("state", state)
        self._local_status.style().unpolish(self._local_status)
        self._local_status.style().polish(self._local_status)

    def _on_language_changed(self, code: str) -> None:
        """
        Hand the change to MainWindow, which rebuilds the pages against
        the new catalog. This used to persist the setting and tell the
        user to restart; it now takes effect immediately.
        """
        self._language_status.setText(tr("Applying…"))
        self.language_changed.emit(code)

    def _on_theme_combo_changed(self, theme_name: str) -> None:
        """
        Apply the new theme to this page, then tell the window.

        This used to set self._theme and re-style inline, which looked
        equivalent and was not: MainWindow.apply_theme guards its call
        with `if self.settings._theme != theme_name`, and by the time the
        signal arrived this had already made those equal. So
        SettingsWidget.set_theme never ran on the only path a user can
        switch themes, and the three things only IT refreshes - the
        language dropdown, the per-profile model pickers, the spin
        steppers - kept the old theme's colours. Light text on a light
        page, which is the "light theme is broken" report.

        Calling set_theme directly instead of inlining a partial copy of
        it means there is one path and it cannot drift again.
        """
        if theme_name == self._theme:
            return
        ai_main.set_theme(theme_name)
        self.set_theme(theme_name)
        self.theme_changed.emit(theme_name)

    # ── API keys ─────────────────────────────────────────────────────

    def _on_key_input_changed(self, provider: str, text: str) -> None:
        self._key_save_buttons[provider].setEnabled(bool(text.strip()))

    def _on_save_key_clicked(self, provider: str) -> None:
        new_key = self._key_inputs[provider].text().strip()
        if not new_key:
            return
        ai_main.save_api_key_for(provider, new_key)
        self._key_inputs[provider].clear()
        self._refresh_key_status(provider)

    def _on_browse_files_dir_clicked(self) -> None:
        from PyQt6.QtWidgets import QFileDialog
        import ai.file_generation as file_generation

        start = (self._files_dir_input.text().strip()
                 or ai_main.get_generated_files_dir()
                 or file_generation.default_generated_files_dir())
        chosen = QFileDialog.getExistingDirectory(self, "Where should generated files go?", start)
        if chosen:
            self._files_dir_input.setText(chosen)
            self._on_save_files_dir_clicked()

    def _on_save_files_dir_clicked(self) -> None:
        import ai.file_generation as file_generation

        path = self._files_dir_input.text().strip()
        ai_main.set_generated_files_dir(path)
        # Report the folder that will ACTUALLY be used - an unwritable
        # path falls back rather than failing later, mid-generation,
        # and the user should find that out here instead of wondering
        # where their file went.
        effective = file_generation.generated_files_dir()
        if path and os.path.normpath(effective) != os.path.normpath(path):
            self._files_dir_status.setText(f"Couldn't use that folder - saving to {effective} instead.")
            self._files_dir_status.setProperty("state", "warn")
        else:
            self._files_dir_status.setText(f"Saving files to {effective}")
            self._files_dir_status.setProperty("state", "ok")
        self._files_dir_status.style().unpolish(self._files_dir_status)
        self._files_dir_status.style().polish(self._files_dir_status)

    def _on_open_files_dir_clicked(self) -> None:
        import ai.file_generation as file_generation
        from app.file_reveal import reveal_in_explorer

        reveal_in_explorer(file_generation.generated_files_dir())

    def _on_save_local_url_clicked(self) -> None:
        ai_main.set_local_base_url(self._local_url_input.text())
        self._local_url_input.setText(ai_main.get_local_base_url())  # reflect the fallback-to-default if left blank
        self._local_status.setText(tr("Saved."))
        self._local_status.setProperty("state", "ok")
        self._local_status.style().unpolish(self._local_status)
        self._local_status.style().polish(self._local_status)

    def _on_save_local_model_clicked(self) -> None:
        ai_main.set_local_model_name(self._local_model_input.text())
        self._local_model_input.setText(ai_main.get_local_model_name())
        self._local_status.setText(tr("Saved."))
        self._local_status.setProperty("state", "ok")
        self._local_status.style().unpolish(self._local_status)
        self._local_status.style().polish(self._local_status)

    def _refresh_key_status(self, provider: str | None = None) -> None:
        if not hasattr(self, "_key_statuses"):
            return  # the API key section has not been built yet
        providers = [provider] if provider else list(self._key_statuses)
        for pid in providers:
            status_label = self._key_statuses[pid]
            masked = ai_main.get_api_key_masked_for(pid)
            if masked:
                status_label.setText(f"Active key: {masked}")
                status_label.setProperty("state", "ok")
            elif pid == "local":
                # Unlike the other three, a missing key here almost
                # never means the assistant can't respond - most local
                # servers don't check for one at all - so this never
                # shows the "won't be able to respond" warning the
                # cloud providers get.
                status_label.setText(tr("No key set - fine for most local servers (Ollama, LM Studio, etc.)."))
                status_label.setProperty("state", None)
            else:
                # Only the currently active provider actually blocks the
                # assistant from responding if its key is missing - the
                # other two just sit unused until picked in the model
                # dropdown, so it'd be misleading to warn about all three
                # at once every time this screen opens.
                if pid == ai_main.get_active_provider():
                    status_label.setText(f"No key configured - {ai_main.get_assistant_name()} won't be able to respond.")
                    status_label.setProperty("state", "warn")
                else:
                    status_label.setText(tr("No key configured - only needed if you switch to this provider."))
                    status_label.setProperty("state", None)
            status_label.style().unpolish(status_label)
            status_label.style().polish(status_label)

    # ── AI profiles ────────────────────────────────────────────────

    def _build_profiles_section(self, root) -> None:
        """
        One row per ai.profiles.Profile: its name (● marks whichever is
        currently active) - click directly on the name to rename it in
        place, same "click text to edit it" idiom as a file name in a
        file browser, no separate edit button needed (see
        _on_profile_name_edited) - a "Prompt" button opening a small
        editor for that profile's own extra instructions (see
        _open_prompt_editor / ai.profiles.Profile.system_prompt), a
        model picker (StyledDropdown - the same widget/CSS the Dark/
        Light theme picker above uses, not a second dropdown style in
        the same page), a "Switch" button on every row except the
        active one, and a delete button (disabled on the active profile
        and on the last remaining one - see ai.profiles.delete_profile's
        own guard rails, mirrored here so the button is visibly
        disabled rather than clickable-then-rejected).
        """
        title = QLabel(tr("AI profiles"))
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        hint = QLabel(
            tr("Each profile is its own name, model, and (optionally) extra "
            "instructions. Say a profile's name out loud - with the wake "
            "word listening, below - to switch straight to it and start "
            "talking, no need to open the app or come back to this "
            "screen first.")
        )
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._profiles_list_layout = QVBoxLayout()
        self._profiles_list_layout.setSpacing(6)
        root.addLayout(self._profiles_list_layout)

        btn_add_profile = QPushButton(tr("+  Add profile"))
        btn_add_profile.setObjectName("btnAddProfile")
        btn_add_profile.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_profile.clicked.connect(self._on_add_profile_clicked)
        root.addWidget(btn_add_profile)

        self._refresh_profiles_list()

    def _refresh_profiles_list(self) -> None:
        from ai.providers.base import MODEL_CHOICES

        # Clear-and-rebuild rather than diffing - same convention the
        # sidebar's own chat list already uses for a small, infrequently
        # -changing list (see app/sidebar_widget.py); a handful of
        # profile rows is nowhere near enough to need anything smarter.
        # Also what set_theme() below leans on to recolor these rows
        # simpler than separately tracking every StyledDropdown/inline
        # name editor just to re-theme them in place.
        while self._profiles_list_layout.count():
            item = self._profiles_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        all_profiles = profiles.list_profiles()
        active_id = profiles.get_active_profile_id()
        model_options = [(f"{c.provider}|{c.model_id}", c.label) for c in MODEL_CHOICES]
        c = theme.colors(self._theme)

        for profile in all_profiles:
            is_active = profile.id == active_id
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            dot = QLabel("●" if is_active else "○")
            dot.setObjectName("profileNameActive" if is_active else "profileName")
            row.addWidget(dot)

            # Click-to-rename: a QLineEdit styled to look like plain text
            # until it's focused (no border/background, same font/color
            # as the ● label next to it) - clicking it to place a cursor
            # IS the "start editing" gesture, no separate edit button or
            # mode to enter first. editingFinished fires once whether the
            # user hits Enter or just clicks/tabs away, either way
            # committing (or silently reverting to the old name if
            # nothing actually changed - see the handler).
            name_edit = QLineEdit(profile.name)
            name_edit.setObjectName("profileNameEditActive" if is_active else "profileNameEdit")
            name_edit.setFrame(False)
            name_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            name_edit.editingFinished.connect(
                lambda edit=name_edit, pid=profile.id, old=profile.name: self._on_profile_name_edited(pid, old, edit)
            )
            row.addWidget(name_edit)

            btn_prompt = QPushButton("Prompt" + (" ●" if profile.system_prompt else ""))
            btn_prompt.setObjectName("btnProfilePrompt")
            btn_prompt.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_prompt.setToolTip(
                "Extra instructions just for this profile"
                + (" (currently set)" if profile.system_prompt else " (none set)")
            )
            btn_prompt.clicked.connect(lambda _c=False, pid=profile.id, name=profile.name: self._open_prompt_editor(pid, name))
            row.addWidget(btn_prompt)

            model_dropdown = StyledDropdown(
                model_options, f"{profile.provider}|{profile.model_id}", self._theme, min_width=190,
            )
            model_dropdown.currentValueChanged.connect(
                lambda key, pid=profile.id: self._on_profile_model_changed(pid, key)
            )
            row.addWidget(model_dropdown, 1)

            if not is_active:
                btn_switch = QPushButton(tr("Switch"))
                btn_switch.setObjectName("btnProfileSwitch")
                btn_switch.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_switch.clicked.connect(lambda _c=False, pid=profile.id: self._on_switch_profile_clicked(pid))
                row.addWidget(btn_switch)

            btn_delete = QPushButton()
            btn_delete.setObjectName("btnProfileDelete")
            btn_delete.setIcon(icons.icon("trash", c, "danger"))
            btn_delete.setIconSize(QSize(13, 13))
            btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
            can_delete = (not is_active) and len(all_profiles) > 1
            btn_delete.setEnabled(can_delete)
            btn_delete.setToolTip(
                "Switch to a different profile first" if is_active else
                ("Can't delete the only profile" if not can_delete else "Delete this profile")
            )
            btn_delete.clicked.connect(lambda _c=False, pid=profile.id, name=profile.name: self._on_delete_profile_clicked(pid, name))
            row.addWidget(btn_delete)

            self._profiles_list_layout.addWidget(row_widget)

    def _on_profile_name_edited(self, profile_id: str, old_name: str, edit: QLineEdit) -> None:
        new_name = edit.text().strip()
        if not new_name or new_name == old_name:
            edit.setText(old_name)  # nothing to do, or emptied out - revert rather than leave it blank
            return
        try:
            profiles.rename_profile(profile_id, new_name)
        except profiles.ProfileNameTakenError as e:
            QMessageBox.warning(self, "Couldn't rename profile", str(e))
            edit.setText(old_name)
            return
        self._refresh_profiles_list()
        if profile_id == profiles.get_active_profile_id():
            self._refresh_wake_word_copy()
            self.assistant_name_changed.emit(new_name)

    def _open_prompt_editor(self, profile_id: str, profile_name: str) -> None:
        """
        A small modal QDialog rather than an inline QTextEdit sitting
        permanently open in every row - a multi-line prompt would make
        every OTHER profile's row jump around vertically to make room,
        for something most profiles never touch. "however you want" is
        the point: a single free-form QTextEdit, no template or
        required structure - this profile's own system_prompt is just
        appended after the shared base instructions every profile
        already gets (see ai.profiles.create_profile's docstring), so
        it only needs to describe what makes this ONE profile different.
        """
        current = profiles.get_profile(profile_id)
        current_prompt = current.system_prompt if current else ""

        dialog = QDialog(self)
        dialog.setWindowTitle(f"{profile_name}'s prompt")
        dialog.setMinimumSize(420, 320)
        c = theme.colors(self._theme)
        dialog.setStyleSheet(f"QDialog {{ background-color: {c['bg_panel']}; }}")

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        hint = QLabel(
            f"Extra instructions for {profile_name} only - appended after "
            "the shared base instructions every profile already gets. "
            "Write it however you want: a personality, a role, rules to "
            "follow, topics to focus on or avoid. Leave it blank for a "
            "profile with no extra behavior beyond the base assistant."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {c['text_secondary']}; font-family: 'Segoe UI'; font-size: 12px; background: transparent;")
        layout.addWidget(hint)

        text_edit = QTextEdit()
        text_edit.setPlainText(current_prompt)
        text_edit.setStyleSheet(
            f"background-color:{c['bg_input']}; color:{c['text_primary']}; "
            f"border:1px solid {c['border']}; border-radius:8px; padding:8px; "
            "font-family:'Segoe UI'; font-size:13px;"
        )
        layout.addWidget(text_edit, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        btn_cancel = QPushButton(tr("Cancel"))
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {c['text_secondary']}; "
            f"border: 1px solid {c['border']}; border-radius: 7px; padding: 7px 16px; "
            "font-family:'Segoe UI'; font-size:12px; }}"
            f"QPushButton:hover {{ color: {c['text_primary']}; border-color: {c['border_strong']}; "
            f"background-color: {c['bg_hover']}; }}"
        )
        btn_cancel.clicked.connect(dialog.reject)
        button_row.addWidget(btn_cancel)
        btn_save = QPushButton(tr("Save"))
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.setStyleSheet(
            f"background-color:{c['accent']}; color:white; border:none; "
            "border-radius:7px; padding:7px 16px; font-family:'Segoe UI'; font-size:12px; font-weight:600;"
        )
        btn_save.clicked.connect(dialog.accept)
        button_row.addWidget(btn_save)
        layout.addLayout(button_row)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            profiles.update_profile_prompt(profile_id, text_edit.toPlainText())
            self._refresh_profiles_list()

    def _on_profile_model_changed(self, profile_id: str, key: str) -> None:
        if not key or "|" not in key:
            return
        provider, model_id = key.split("|", 1)
        try:
            profiles.update_profile_model(profile_id, provider, model_id)
        except Exception as e:
            QMessageBox.warning(self, "Couldn't change model", str(e))
            self._refresh_profiles_list()
            return
        if profile_id == profiles.get_active_profile_id():
            self._refresh_key_status()

    def _on_switch_profile_clicked(self, profile_id: str) -> None:
        try:
            profiles.activate_profile(profile_id)
        except Exception as e:
            # Most likely ai.providers.ProviderClientError - that profile's
            # provider has no API key configured yet. Left active exactly
            # where it was rather than half-switching.
            QMessageBox.warning(self, "Couldn't switch profile", str(e))
            return
        self._refresh_wake_word_copy()
        self._refresh_key_status()
        self._refresh_profiles_list()
        self.assistant_name_changed.emit(ai_main.get_assistant_name())

    def _on_delete_profile_clicked(self, profile_id: str, name: str) -> None:
        # QMessageBox.question() uses whatever native/default chrome Qt
        # falls back to for a plain unstyled dialog - readable in the
        # dark theme mostly by accident (native text defaults to dark,
        # which happens to show up against this app's own dark
        # surroundings), but NOT actually themed, so a native light-mode
        # button here could just as easily render pale-on-pale in the
        # app's light theme. Same fix app/sidebar_widget.py's own
        # delete-chat confirmation already uses: a real QMessageBox
        # instance with an explicit, theme-token stylesheet, rather than
        # the one-line .question() convenience wrapper that can't be
        # styled at all.
        box = QMessageBox(self)
        box.setStyleSheet(_confirm_dialog_style(theme.colors(self._theme)))
        box.setWindowTitle(tr("Delete profile"))
        box.setText(f"Delete the “{name}” profile?")
        box.setInformativeText(tr("This can't be undone."))
        box.setIcon(QMessageBox.Icon.NoIcon)
        btn_delete = box.addButton(tr("Delete"), QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = box.addButton(tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_cancel)
        box.exec()
        if box.clickedButton() is not btn_delete:
            return
        try:
            profiles.delete_profile(profile_id)
        except ValueError as e:
            QMessageBox.warning(self, "Couldn't delete profile", str(e))
            return
        self._refresh_profiles_list()
        self._refresh_wake_word_copy()

    def _on_add_profile_clicked(self) -> None:
        try:
            profiles.create_profile()
        except Exception as e:
            QMessageBox.warning(self, "Couldn't add profile", str(e))
            return
        self._refresh_profiles_list()
        self._refresh_wake_word_copy()

    def _refresh_wake_word_copy(self) -> None:
        """
        Re-renders the wake-word section's copy. Once enabled, the
        listener matches ANY configured profile's name, not just the
        active one - saying a different profile's name switches to it
        (see ai.profiles.match_profile_for_phrase /
        app/workers/wakeword_worker.py's match_wake_word). Still called
        from the same places (build time, whenever a name is saved, and
        now also whenever a profile is switched/added/deleted).
        """
        name = ai_main.get_assistant_name()
        other_names = [p.name for p in profiles.list_profiles() if p.name != name]
        if other_names:
            names_copy = " or ".join(f"« {n} »" for n in [name, *other_names])
            trigger_copy = f"hears any of its profiles' names - {names_copy}"
        else:
            trigger_copy = f"hears « {name} »"
        self._wake_hint.setText(
            f"{name} listens in the background and triggers as soon as it "
            f"{trigger_copy}. Uses online speech recognition (Google, "
            "English) - needs an internet connection, and a poorly-captured "
            "phrase might not trigger activation. When a voice request leads "
            "to an autonomous-mode suggestion, listening switches to just "
            "« accept » / « refuse » until you answer, so you can confirm "
            f"or decline out loud instead of clicking. While {name} is "
            "reading a reply aloud, it also listens for « stop » / "
            "« arrête » to interrupt it - clicking the mic button always "
            "works too, with or without this enabled."
        )
        self._wake_check.setText(f"Enable the « {name} » wake word" + (" (and any other profile's)" if other_names else ""))

    # ── Wake word ────────────────────────────────────────────────────

    def _on_wake_word_toggled(self, checked: bool) -> None:
        ai_main.set_wake_word_enabled(checked)
        self.wake_word_toggled.emit(checked)

    def set_wake_word_status(self, text: str, ok: bool = True) -> None:
        """Called by main_window to report whether the listener actually started."""
        self._wake_status.setText(text)
        self._wake_status.setProperty("state", "ok" if ok else "warn")
        self._wake_status.style().unpolish(self._wake_status)
        self._wake_status.style().polish(self._wake_status)

    # ── Shortcuts ────────────────────────────────────────────────────

    def _on_save_shortcut_clicked(self, shortcut_id: str) -> None:
        edit = self._shortcut_edits[shortcut_id]
        combo = edit.current_trigger()
        if not combo:
            self.set_shortcut_status(shortcut_id, "Choose a combination before saving.", ok=False)
            return
        self.shortcut_change_requested.emit(shortcut_id, combo)

    def _on_trigger_captured(self, shortcut_id: str, combo: str) -> None:
        """
        Recorder triggers save immediately on capture - no separate Save
        button, unlike the four global shortcuts above. Those need a
        confirm step because a bad combo can fail to register with the
        `keyboard` library (already-used-elsewhere, invalid syntax,
        etc.) and the button doubles as that error surface; a recorder
        trigger is just a value RecorderController reads next time
        recording starts, so there's nothing that can fail here.
        """
        self.shortcut_change_requested.emit(shortcut_id, combo)

    def _on_save_macro_delay_clicked(self) -> None:
        # No global side effect to coordinate through main_window here
        # (unlike a shortcut, which has to re-register with the `keyboard`
        # lib and can fail) - play_actions() just reads this value fresh
        # from the environment on its next run, so saving directly is safe.
        ai_main.set_macro_action_delay(self._macro_delay_spin.value())
        self._macro_delay_status.setText(f"Saved - {self._macro_delay_spin.value():.1f}s between steps.")
        self._macro_delay_status.setProperty("state", "ok")
        self._macro_delay_status.style().unpolish(self._macro_delay_status)
        self._macro_delay_status.style().polish(self._macro_delay_status)

    # ── Automation step limit ───────────────────────────────────────────

    def _on_save_auto_steps_clicked(self) -> None:
        ai_main.set_max_automation_steps(self._auto_steps_spin.value())
        value = self._auto_steps_spin.value()
        note = " (the recommended value)" if value == ai_main.DEFAULT_MAX_AUTOMATION_STEPS else ""
        self._auto_steps_status.setText(f"Saved - up to {value} steps per run{note}.")
        self._auto_steps_status.setProperty("state", "ok")
        self._auto_steps_status.style().unpolish(self._auto_steps_status)
        self._auto_steps_status.style().polish(self._auto_steps_status)

    def _on_screenshot_quality_clicked(self, value: str) -> None:
        ai_main.set_autonomous_screenshot_quality(value)
        preset = ai_main.SCREENSHOT_QUALITY_PRESETS[value]
        if preset["max_side"] is None:
            detail = "full resolution, sent unchanged"
        else:
            detail = f"resized to fit {preset['max_side']}px, JPEG quality {preset['jpeg_quality']}"
        self._shot_quality_status.setText(f"Saved - {value} ({detail}).")
        self._shot_quality_status.setProperty("state", "ok")
        self._shot_quality_status.style().unpolish(self._shot_quality_status)
        self._shot_quality_status.style().polish(self._shot_quality_status)

    # ── Safety limits ────────────────────────────────────────────────────


    def _on_save_safety_limits_clicked(self) -> None:
        for level, spin in self._safety_spins.items():
            ai_main.set_safety_limit(level, spin.value())
        self._safety_status.setText(tr("Saved - takes effect on the next task/session."))
        self._safety_status.setProperty("state", "ok")
        self._safety_status.style().unpolish(self._safety_status)
        self._safety_status.style().polish(self._safety_status)

    def _on_reset_safety_clicked(self) -> None:
        ai_main.reset_safety_limits()
        self._safety_status.setText(tr("Session's action counts reset."))
        self._safety_status.setProperty("state", "ok")
        self._safety_status.style().unpolish(self._safety_status)
        self._safety_status.style().polish(self._safety_status)

    def set_shortcut_status(self, shortcut_id: str, text: str, ok: bool = True) -> None:
        """Called by main_window to report whether the new binding actually took."""
        status = self._shortcut_status_labels.get(shortcut_id)
        if status is None:
            return
        status.setText(text)
        status.setProperty("state", "ok" if ok else "warn")
        status.style().unpolish(status)
        status.style().polish(status)
