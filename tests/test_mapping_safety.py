# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_mapping_safety.py
============================
What a mapping run is allowed to touch: which window it picks, which
fields it types into, which buttons it never presses, and putting the
window back afterwards.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai import direct_input, safety
from ai.discovery import accessibility, mapper
from ai.discovery.accessibility import (
    AccessibilityBackend, AccessibilityElement, WindowCandidate, pick_window, window_match_score,
)
from ai.discovery.errors import DiscoveryError


# ── which window ─────────────────────────────────────────────────────────

def test_the_program_name_beats_a_title_that_merely_contains_the_word():
    barcode = WindowCandidate(control="barcode", title="Barcode Scanner", program="scan.exe", order=0)
    vscode = WindowCandidate(control="vscode", title="main.py - kestrel", program="Code.exe", order=1)
    assert pick_window("code", [barcode, vscode]).control == "vscode"


def test_a_window_class_no_longer_counts_and_short_names_need_a_real_match():
    assert window_match_score("pad", "Untitled - Notepad", "notepad.exe") == 0
    assert window_match_score("note", "Untitled - Notepad", "notepad.exe") == 40


def test_title_words_and_program_names_are_matched_whole():
    assert window_match_score("notepad", "Untitled - Notepad", "notepad.exe") == 100
    assert window_match_score("notepad.exe", "", "Notepad.exe") == 100
    assert window_match_score("Settings", "Settings", "ApplicationFrameHost.exe") == 90
    assert window_match_score("google chrome", "News - Google Chrome", "chrome.exe") == 80
    assert window_match_score("visual studio code", "a.py - Visual Studio Code", "Code.exe") == 80
    assert window_match_score("visual studio code", "a.py - Visual Studio Code", "") == 70
    assert window_match_score("word", "Report - Word", "WINWORD.EXE") == 70


def test_the_window_in_front_wins_a_tie_then_the_one_on_top():
    back = WindowCandidate(control="back", title="Untitled - Notepad", program="notepad.exe", order=0)
    front = WindowCandidate(control="front", title="notes - Notepad", program="notepad.exe", order=3, in_front=True)
    assert pick_window("notepad", [back, front]).control == "front"
    lower = WindowCandidate(control="lower", title="b - Notepad", program="notepad.exe", order=5)
    assert pick_window("notepad", [lower, back]).control == "back"


def test_no_match_is_none():
    assert pick_window("photoshop", [WindowCandidate(control=1, title="Untitled - Notepad", program="notepad.exe")]) is None


def test_a_window_with_the_name_in_its_title_too_wins_among_one_programs_windows():
    # Two explorer.exe windows on top of each other: a Control Panel one
    # nearer the top, and File Explorer.
    panel = WindowCandidate(control="panel", title="Sound", program="explorer.exe", order=0, in_front=True)
    files = WindowCandidate(control="files", title="Downloads - File Explorer", program="explorer.exe", order=4)
    assert pick_window("explorer", [panel, files]).control == "files"
    assert pick_window("file explorer", [panel, files]).control == "files"


class _FakeWindowControl:
    def __init__(self, hwnd, name):
        self.NativeWindowHandle = hwnd
        self.Name = name


class _FakeRoot:
    def __init__(self, windows):
        self._windows = windows

    def GetChildren(self):
        return list(self._windows)


@pytest.fixture
def windows_desktop(monkeypatch):
    """get_backend on a pretend desktop: hwnd 1 is Kestrel's own window,
    4 the taskbar (explorer.exe, untitled, on top), 5 the desktop, 2 is
    Notepad, 3 Chrome, 6 File Explorer. Returns what took the window."""
    windows = [_FakeWindowControl(1, "Kestrel - Notepad helper"),
               _FakeWindowControl(4, ""),
               _FakeWindowControl(5, "Program Manager"),
               _FakeWindowControl(2, "Untitled - Notepad"),
               _FakeWindowControl(3, "News - Google Chrome"),
               _FakeWindowControl(6, "Downloads - File Explorer")]
    programs = {1: "python.exe", 2: "notepad.exe", 3: "chrome.exe", 4: "explorer.exe",
                5: "explorer.exe", 6: "explorer.exe"}
    taken = []

    class FakeAuto:
        @staticmethod
        def GetRootControl():
            return _FakeRoot(windows)

    monkeypatch.setattr(accessibility.sys, "platform", "win32")
    monkeypatch.setattr(accessibility, "auto", FakeAuto)
    monkeypatch.setattr(direct_input, "ui_automation", lambda multithreaded=False: None)
    monkeypatch.setattr(direct_input, "foreground_window", lambda: 1)
    # Kestrel's own window and the desktop aren't windows to map; the
    # taskbar isn't either, and has no title besides.
    monkeypatch.setattr(direct_input, "can_be_mapped", lambda hwnd: hwnd not in (1, 4, 5))
    monkeypatch.setattr(direct_input, "program_of", lambda hwnd: programs.get(hwnd, ""))
    monkeypatch.setattr(direct_input, "remember_window", lambda hwnd: {"state": "normal", "placement": None})
    monkeypatch.setattr(accessibility, "_bring_to_front_and_maximize", lambda window: taken.append(window.Name))
    return taken


def test_get_backend_never_maps_kestrel_itself_and_lists_the_open_windows(windows_desktop):
    # "Kestrel" is in Kestrel's own title only. That window is skipped, so
    # this fails with the list of windows instead of mapping itself.
    with pytest.raises(DiscoveryError) as caught:
        accessibility.get_backend("Kestrel")
    message = str(caught.value)
    assert "Untitled - Notepad (notepad.exe)" in message
    assert "News - Google Chrome (chrome.exe)" in message
    assert "Kestrel - Notepad helper" not in message
    assert windows_desktop == []


def test_get_backend_takes_the_matching_window_and_says_which(windows_desktop):
    backend = accessibility.get_backend("notepad")
    assert backend.window_title == "Untitled - Notepad"
    assert backend.program == "notepad.exe"
    assert windows_desktop == ["Untitled - Notepad"]


def test_an_untitled_window_is_taken_by_its_programs_name_only():
    untitled = WindowCandidate(control="tool", title="", program="mytool.exe")
    assert pick_window("mytool", [untitled]).control == "tool"
    assert pick_window("tool", [untitled]) is None              # part of the name: not enough


def test_explorer_means_file_explorer_not_the_taskbar_or_the_desktop(windows_desktop):
    assert accessibility.get_backend("explorer").window_title == "Downloads - File Explorer"
    assert accessibility.get_backend("File Explorer").window_title == "Downloads - File Explorer"


def test_pressing_a_mapped_control_leaves_the_window_as_it_is(windows_desktop):
    backend = accessibility.get_backend("notepad", take=False)
    assert backend.window_title == "Untitled - Notepad"
    assert windows_desktop == []


# ── putting the window back ────────────────────────────────────────────────

def test_the_backend_puts_the_window_back_as_it_found_it(monkeypatch):
    calls = []
    before = {"state": "minimized", "placement": None}
    monkeypatch.setattr(direct_input, "program_of", lambda hwnd: "notepad.exe")
    monkeypatch.setattr(direct_input, "remember_window", lambda hwnd: before)
    monkeypatch.setattr(direct_input, "put_back", lambda hwnd, state: calls.append((hwnd, state)))
    monkeypatch.setattr(accessibility, "_bring_to_front_and_maximize", lambda window: None)
    backend = accessibility.WindowsUIABackend(_FakeWindowControl(7, "Untitled - Notepad"))
    backend.restore_window()                 # never taken: nothing to put back
    assert calls == []
    backend.take_window()
    backend.restore_window()
    assert calls == [(7, before)]


class _FakeValuePattern:
    def __init__(self, value, read_only=False):
        self.Value, self.IsReadOnly = value, read_only


class _FakeField:
    def __init__(self, pattern):
        self.pattern = pattern

    def GetPattern(self, pattern_id):
        return self.pattern


@pytest.mark.parametrize("pattern, expected", [
    (_FakeValuePattern(""), ""),
    (_FakeValuePattern("my notes"), "my notes"),
    (_FakeValuePattern("", read_only=True), None),     # a drop-down list's value
    (None, None),                                      # a number box: no value pattern
])
def test_what_a_field_holds(monkeypatch, pattern, expected):
    class FakeAuto:
        class PatternId:
            ValuePattern = 10002

    monkeypatch.setattr(accessibility, "auto", FakeAuto)
    monkeypatch.setattr(direct_input, "program_of", lambda hwnd: "")
    backend = accessibility.WindowsUIABackend(_FakeWindowControl(7, "Form"))
    monkeypatch.setattr(backend, "_find_control_by_path", lambda root, path: _FakeField(pattern))
    assert backend.field_text(AccessibilityElement(id="f", name="f", control_type="Edit")) == expected


class _RecordingBackend(AccessibilityBackend):
    """One screen with nothing on it, and a note of restore_window."""

    def __init__(self, fail=False):
        self.restored = 0
        self.fail = fail

    def get_root(self):
        if self.fail:
            raise RuntimeError("the window went away")
        return AccessibilityElement(id="root", name="Main", control_type="Window")

    def list_interactables(self, element):
        return []

    def find_back_control(self, element):
        return None

    def capture_state(self):
        return self.get_root()

    def restore_window(self):
        self.restored += 1


def test_a_run_puts_the_window_back_when_it_ends(monkeypatch):
    backend = _RecordingBackend()
    monkeypatch.setattr(mapper, "get_backend", lambda name: backend)
    mapper.map_application_ui("Notepad")
    assert backend.restored == 1


def test_a_run_puts_the_window_back_when_it_fails(monkeypatch):
    backend = _RecordingBackend(fail=True)
    monkeypatch.setattr(mapper, "get_backend", lambda name: backend)
    with pytest.raises(RuntimeError):
        mapper.map_application_ui("Notepad")
    assert backend.restored == 1


def test_the_window_is_named_before_the_run_starts(monkeypatch):
    backend = _RecordingBackend(fail=True)
    backend.window_title, backend.program = "Untitled - Notepad", "notepad.exe"
    monkeypatch.setattr(mapper, "get_backend", lambda name: backend)
    seen = []
    with pytest.raises(RuntimeError):
        mapper.map_application_ui("Notepad", on_window=lambda title, program: seen.append((title, program)))
    assert seen == [("Untitled - Notepad", "notepad.exe")]


def test_a_backend_passed_in_is_left_alone():
    backend = _RecordingBackend()
    mapper.map_application_ui("Notepad", backend=backend)
    assert backend.restored == 0


class _Placement:
    showCmd = 0
    flags = 0x0002                           # WPF_RESTORETOMAXIMIZED: kept


class _FakeWin:
    def __init__(self, minimized=False, maximized=False, alive=True, hung=False, placement_works=True):
        self.minimized, self.maximized, self.alive, self.hung = minimized, maximized, alive, hung
        self.placement_works = placement_works
        self.shown = []
        self.placed = []

    def is_window(self, hwnd):
        return self.alive

    def is_hung(self, hwnd):
        return self.hung

    def is_minimized(self, hwnd):
        return self.minimized

    def is_maximized(self, hwnd):
        return self.maximized

    def placement(self, hwnd):
        return _Placement()

    def set_placement(self, hwnd, placement):
        self.placed.append(placement.showCmd)
        return self.placement_works

    def show_async(self, hwnd, how):
        self.shown.append(how)
        return True


@pytest.mark.parametrize("before, now, expected", [
    ("normal", dict(maximized=True), [direct_input.SW_RESTORE]),
    ("maximized", dict(maximized=True), []),
    ("minimized", dict(maximized=True), [direct_input.SW_SHOWMINNOACTIVE]),
    ("normal", dict(maximized=True, alive=False), []),
    ("normal", dict(maximized=True, hung=True), []),
    ("", dict(maximized=True), []),
])
def test_put_back_by_state(monkeypatch, before, now, expected):
    fake = _FakeWin(**now)
    monkeypatch.setattr(direct_input, "_IS_WINDOWS", True)
    monkeypatch.setattr(direct_input, "_win_layer", fake)
    direct_input.put_back(5, before)
    assert fake.shown == expected


def test_put_back_restores_the_exact_placement(monkeypatch):
    fake = _FakeWin(maximized=True)
    monkeypatch.setattr(direct_input, "_IS_WINDOWS", True)
    monkeypatch.setattr(direct_input, "_win_layer", fake)
    direct_input.put_back(5, direct_input.remember_window(5) | {"state": "minimized"})
    placement = _Placement()
    direct_input.put_back(5, {"state": "normal", "placement": placement})
    # Not brought to the front, and posted rather than waited on.
    assert fake.placed == [direct_input.SW_SHOWMINNOACTIVE, direct_input.SW_SHOWNOACTIVATE]
    assert placement.flags == 0x0002 | direct_input.WPF_ASYNCWINDOWPLACEMENT
    assert fake.shown == []
    # A placement Windows turns down falls back to showing it by state.
    fake.placement_works = False
    direct_input.put_back(5, {"state": "normal", "placement": _Placement()})
    assert fake.shown == [direct_input.SW_RESTORE]


def test_a_minimized_window_is_shown_without_coming_to_the_front(monkeypatch):
    fake = _FakeWin(minimized=True)
    monkeypatch.setattr(direct_input, "_IS_WINDOWS", True)
    monkeypatch.setattr(direct_input, "_win_layer", fake)
    # It takes a while to come back: the controls are looked for after.
    readings = [True, True, False]
    fake.is_minimized = lambda hwnd: readings.pop(0) if readings else False
    assert direct_input.unminimize(5, settle=0) is True
    assert fake.shown == [direct_input.SW_SHOWNOACTIVATE] and readings == []
    fake.shown = []
    assert direct_input.unminimize(5, settle=0) is False and fake.shown == []


def test_window_state(monkeypatch):
    monkeypatch.setattr(direct_input, "_IS_WINDOWS", True)
    monkeypatch.setattr(direct_input, "_win_layer", _FakeWin(minimized=True, maximized=True))
    assert direct_input.window_state(5) == "minimized"
    monkeypatch.setattr(direct_input, "_win_layer", _FakeWin(maximized=True))
    assert direct_input.window_state(5) == "maximized"
    monkeypatch.setattr(direct_input, "_win_layer", _FakeWin())
    assert direct_input.window_state(5) == "normal"


# ── which fields get typed into ──────────────────────────────────────────

class _FieldsBackend(AccessibilityBackend):
    """One screen of text fields; `texts` is what each one reads as
    (None: can't be read)."""

    def __init__(self, texts, heights=None):
        self.texts = texts
        self.heights = heights or {}
        self.typed = []
        self.read = []

    def get_root(self):
        return AccessibilityElement(id="root", name="Form", control_type="Window",
                                    bounding_box={"x": 0, "y": 0, "w": 800, "h": 600})

    def list_interactables(self, element):
        return [AccessibilityElement(id=name, name=name, control_type="Edit",
                                     bounding_box={"x": 10, "y": 10 + 40 * i, "w": 200,
                                                   "h": self.heights.get(name, 24)})
                for i, name in enumerate(self.texts)]

    def field_text(self, element):
        self.read.append(element.id)
        return self.texts[element.id]

    def input_value(self, element, value):
        self.typed.append(element.id)
        return True

    def find_back_control(self, element):
        return None

    def send_key(self, key):
        pass

    def capture_state(self):
        return self.get_root()


def test_only_empty_fields_that_can_be_read_are_typed_into():
    backend = _FieldsBackend({"blank": "", "filled": "my notes", "unreadable": None})
    mapper.map_application_ui("Form", backend=backend)
    assert backend.typed == ["blank"]


def test_a_tall_field_is_skipped_without_reading_it():
    backend = _FieldsBackend({"notes": ""}, heights={"notes": 400})
    mapper.map_application_ui("Form", backend=backend)
    assert backend.typed == []
    assert backend.read == []


# ── what is never pressed ────────────────────────────────────────────────

@pytest.mark.parametrize("label", [
    "Send", "Reply all", "Post", "Pay now", "Buy", "Don't Save", "Don’t save",
    "Log out", "Empty", "Call", "Accept", "Install",
    "action_send_feedback_3",
    "Ne pas enregistrer", "Envoyer", "Supprimer", "Se déconnecter",
    "No guardar", "Enviar", "Nicht speichern", "Löschen", "Non salvare",
    "إرسال", "भेजें", "删除", "不保存", "送信", "보내기",
])
def test_actions_that_reach_people_or_lose_work_are_destructive(label):
    assert safety.is_destructive_label(label) is True


@pytest.mark.parametrize("label", [
    "Save", "Filter Results", "Postal code", "Sender", "Installed apps", "Calls",
    "Settings", "Search", "Display", "Payload", "Following list",
    "Aceptar", "Seguir",          # OK and Continue in Spanish dialogs
])
def test_lookalike_words_are_not(label):
    assert safety.is_destructive_label(label) is False
