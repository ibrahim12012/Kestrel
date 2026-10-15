# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/workers/recorder_worker.py
===============================
GUI-friendly version of ai/main.py's No_code() recorder.

No_code() works by busy-polling keyboard.is_pressed() in a `while True`
loop and blocking on input() at the end to ask for a macro name - both
would freeze the Qt event loop if called directly from the GUI thread.

RecorderController instead registers global key/mouse hooks (keyboard.hook
+ mouse.hook), which those libraries service on their own OS-level
threads - so it never blocks the GUI. Qt automatically queues the signal
emissions back to whichever thread is listening (the main/GUI thread),
so no manual QThread plumbing is needed here.

Triggers (configurable in Settings - see ai.main.RECORDER_SHORTCUT_IDS;
defaults match the terminal recorder for consistency):
  recorder_right_click   (default F6)  → record a right-click at the current mouse position
  recorder_click         (default F7)  → record a click at the current mouse position
  recorder_toggle_typing (default F8)  → toggle "typing mode": subsequent keys are
                                          captured as text until pressed again (or Esc),
                                          then saved as one write action
  recorder_enter         (default F9)  → record an Enter key press
  recorder_stop          (default F10) → stop recording

Each trigger is either a keyboard combo (handled by the `keyboard`
library, same format as the app's other global shortcuts) or a mouse
button, written "mouse:<button>" - "mouse:left", "mouse:right",
"mouse:middle", "mouse:x", "mouse:x2" (handled by the sibling `mouse`
library instead). Re-read fresh from Settings every time start() is
called, so a rebind takes effect on the next recording without needing
to reconstruct this controller.

Note: this depends on OS-level keyboard/mouse hooks (Windows/Linux with
the right permissions) - it isn't something we can meaningfully
unit-test end-to-end in this sandbox, unlike the pure-logic pieces in
ai/actions.py. What IS tested here: trigger parsing/matching and the
typing-mode state machine, by calling _on_key_event/_on_mouse_event
directly with fake events instead of going through the real hooks.
"""

import keyboard
import mouse
# Imported on first use, not at startup; see ai/lazy_import.py.
from ai.lazy_import import pyautogui
from PyQt6.QtCore import QObject, pyqtSignal

import ai.actions as actions
import ai.main as ai_main


def _current_triggers() -> dict[str, str]:
    """Fresh snapshot of all 5 recorder trigger bindings from Settings."""
    return {sid: ai_main.get_shortcut(sid) for sid in ai_main.RECORDER_SHORTCUT_IDS}


# Dead keys, by the name the `keyboard` library reports, mapped to the
# Unicode combining mark that composes them onto the next letter.
#
# A dead key produces no character of its own: pressing ^ then a is two
# key events and one resulting character, â. The library reports the
# first as a multi-character name, which the typing buffer used to drop
# on the floor - so "â" recorded as "a", and replaying the macro typed
# the wrong word.
_DEAD_KEY_MARKS = {
    "circumflex": "̂",
    "dead_circumflex": "̂",
    "asciicircum": "̂",
    "diaeresis": "̈",
    "dead_diaeresis": "̈",
    "acute": "́",
    "dead_acute": "́",
    "grave": "̀",
    "dead_grave": "̀",
    "tilde": "̃",
    "dead_tilde": "̃",
    "cedilla": "̧",
    "dead_cedilla": "̧",
}


def _compose(mark: "str | None", ch: str) -> str:
    """
    Apply a pending dead-key mark to `ch`, as NFC.

    unicodedata.normalize turns "a" + COMBINING CIRCUMFLEX into the
    single character "â", which is what the application would have
    received. A combination with no precomposed form (there is no
    "q with circumflex") normalizes to itself, leaving the letter
    followed by the mark - which is still what was typed, rather than a
    silently missing accent.
    """
    if not mark:
        return ch
    import unicodedata
    return unicodedata.normalize("NFC", ch + mark)


def _remove_hook(module, remover, callback) -> None:
    """
    Take down one hook and only that hook.

    Both libraries hand back a zero-argument remover from hook(), and
    both also accept the original callback in unhook(). Which of the two
    works depends on the installed version, so this tries the remover
    first, falls back to unhook(callback), and gives up quietly if
    neither lands. What it never does is fall back to unhook_all(): a
    failure to remove one hook leaves a stale listener, which is a
    nuisance, while unhook_all() disarms every global shortcut in the
    app, which is a bug someone has to restart to get out of.
    """
    for attempt in (remover, lambda: module.unhook(callback)):
        if attempt is None:
            continue
        try:
            attempt()
            return
        except Exception:
            continue


class RecorderController(QObject):
    action_recorded = pyqtSignal(str)   # human-readable description of the action just recorded
    typing_started = pyqtSignal()
    typing_stopped = pyqtSignal(str)    # the text that was typed
    stopped = pyqtSignal()              # stop trigger pressed, or stop() called programmatically

    def __init__(self):
        super().__init__()
        self._recording = False
        self._typing = False
        self._typed_buffer = ""
        self._triggers: dict[str, str] = {}
        self._unhook_keyboard = None
        self._unhook_mouse = None
        # A dead key waiting for the letter it modifies - see _compose.
        self._pending_dead_key = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        """
        Begin recording. Deliberately does NOT clear what was recorded
        before: new actions are appended.

        Building a macro is rarely one sitting. You record the first few
        steps, stop, go and work out what the rest should be, and come
        back. Wiping the list on start meant that pause cost you
        everything, with no warning and no way back. Emptying it is the
        Clear button's job, and that one is undoable.
        """
        if self._recording:
            return
        self._recording = True
        self._typing = False
        self._typed_buffer = ""
        self._pending_dead_key = None
        self._triggers = _current_triggers()
        # Both libraries return a callable that removes this one hook.
        # Keeping it is what lets stop() take down exactly what start()
        # put up - see stop()'s own comment for why that matters.
        self._unhook_keyboard = keyboard.hook(self._on_key_event)
        self._unhook_mouse = mouse.hook(self._on_mouse_event)

    def stop(self) -> None:
        """
        Stop listening for hotkeys. Recorded actions stay in
        actions.actions_list.

        Removes only this recorder's own two hooks, never
        keyboard.unhook_all()/mouse.unhook_all(), which is what this used
        to call. Those two clear every hook and hotkey registered in the
        process, which includes the four application-wide shortcuts
        MainWindow registers with keyboard.add_hotkey at startup (show
        window, always on top, screenshot, stop automation). Stopping a
        recording therefore silently killed all of them until the app was
        restarted, which is the "all the shortcuts stopped working"
        behaviour that had no obvious cause: nothing about pressing stop
        on a recorder suggests it would disarm the rest of the app.
        """
        if not self._recording:
            return
        self._recording = False
        self._typing = False
        _remove_hook(keyboard, getattr(self, "_unhook_keyboard", None), self._on_key_event)
        _remove_hook(mouse, getattr(self, "_unhook_mouse", None), self._on_mouse_event)
        self._unhook_keyboard = None
        self._unhook_mouse = None
        self.stopped.emit()

    # ── Internal ──────────────────────────────────────────────────────

    def _on_key_event(self, event) -> None:
        if event.event_type != keyboard.KEY_DOWN:
            return
        # Two readings of the same key. `name` is lowercased, and trigger
        # matching needs that: a shortcut bound to "f8" has to match
        # whatever case the library reports.
        #
        # `raw` is the character as it was actually typed, and the typing
        # buffer needs THAT. Buffering the lowercased form meant every
        # recording came back in lower case - typing "Hello" recorded
        # "hello", and replaying it typed the wrong thing into a
        # case-sensitive field.
        raw = event.name or ""
        name = raw.lower()

        if self._typing:
            if name in ("esc",) or self._triggers.get("recorder_toggle_typing") == name:
                self._finish_typing()
                return

            # Ctrl+C, Ctrl+A, Alt+Tab. Typing mode used to swallow these
            # whole, so recording "select all, copy, paste" produced an
            # empty macro. A combo is not text: whatever has been typed
            # so far is flushed as its own write action first, then the
            # combo is recorded as a hotkey, which keeps the order right.
            combo = self._modifier_combo(name)
            if combo:
                pending = self._typed_buffer
                self._typed_buffer = ""
                if pending:
                    actions.actions_list.append((pending, "write"))
                    self.action_recorded.emit(f'Text: "{pending}"')
                actions.actions_list.append(("hotkey", combo))
                self.action_recorded.emit(f"Keys: {combo}")
                return

            if name == "backspace":
                self._typed_buffer = self._typed_buffer[:-1]
            elif name == "space":
                self._typed_buffer += " "
            elif name == "tab":
                self._typed_buffer += "\t"
            elif len(raw) == 1:
                # `raw`, not `name`: this is the character, not a key
                # identifier. A directly-keyed accent (é, è, à, ç on
                # AZERTY) arrives here as a one-character name and is
                # captured correctly.
                self._typed_buffer += _compose(self._pending_dead_key, raw)
                self._pending_dead_key = None
            else:
                # A dead key (^ then a -> â) arrives as two events, and
                # the first has a multi-character name like "circumflex"
                # that would otherwise be dropped silently, leaving the
                # bare vowel recorded. Hold it and combine it with the
                # next character.
                dead = _DEAD_KEY_MARKS.get(name)
                if dead is not None:
                    self._pending_dead_key = dead
            return  # swallow everything else while in typing mode

        self._handle_trigger(name)

    _MODIFIERS = ("ctrl", "alt", "shift", "windows")

    def _modifier_combo(self, name: str) -> str:
        """
        "ctrl+shift+n" when `name` was pressed with modifiers held, else
        "".

        A modifier pressed on its own is not a combo: it's the first
        half of one, and recording "ctrl" by itself would replay as a
        stray keypress.
        """
        if name in self._MODIFIERS or not name:
            return ""
        try:
            held = [m for m in self._MODIFIERS if keyboard.is_pressed(m)]
        except Exception:
            return ""  # keyboard state unavailable; treat it as plain text
        if not held:
            return ""
        return "+".join(held + [name])

    def _on_mouse_event(self, event) -> None:
        # mouse.hook() delivers every mouse event (moves, wheel, button
        # up/down); only button DOWN events are candidate triggers here
        # same "only the press, not the release" filtering _on_key_event
        # does for keyboard.KEY_DOWN.
        button = getattr(event, "button", None)
        event_type = getattr(event, "event_type", None)
        if button is None or event_type != mouse.DOWN:
            return
        name = f"mouse:{button}"

        if self._typing:
            if self._triggers.get("recorder_toggle_typing") == name:
                self._finish_typing()
            # Any other mouse click during typing mode isn't a text
            # character (unlike a keyboard key) - nothing to buffer,
            # just fall through and swallow it like the keyboard branch does.
            return

        self._handle_trigger(name)

    def _handle_trigger(self, name: str) -> None:
        if name and name == self._triggers.get("recorder_right_click"):
            x, y = pyautogui.position()
            actions.actions_list.append(("right_click", x, y))
            self.action_recorded.emit(f"Right-click ({x}, {y})")
        elif name and name == self._triggers.get("recorder_click"):
            x, y = pyautogui.position()
            actions.actions_list.append(("click", x, y))
            self.action_recorded.emit(f"Click ({x}, {y})")
        elif name and name == self._triggers.get("recorder_toggle_typing"):
            self._typing = True
            self._typed_buffer = ""
            self.typing_started.emit()
        elif name and name == self._triggers.get("recorder_enter"):
            actions.actions_list.append(("enter", "enter"))
            self.action_recorded.emit("Enter key")
        elif name and name == self._triggers.get("recorder_stop"):
            self.stop()

    def _finish_typing(self) -> None:
        self._typing = False
        text = self._typed_buffer
        self._typed_buffer = ""
        if text:
            actions.actions_list.append((text, "write"))
            self.action_recorded.emit(f'Text: "{text}"')
        self.typing_stopped.emit(text)
