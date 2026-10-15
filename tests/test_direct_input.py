# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_direct_input.py
=============================
ai/direct_input.py: the AI's clicks, keys and typing reach the app
without the user's mouse. Driven through a fake Win32 layer and fake UI
Automation controls, since neither exists here; the order of preference
and what is (and isn't) done with the real mouse is what's checked.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.actions as actions
import ai.direct_input as direct_input


# ── Fakes ───────────────────────────────────────────────────────────────

class FakeWin:
    """The Win32 layer: windows by handle, each with a class, a process
    and a top-level window; everything done to them is recorded."""

    def __init__(self):
        self.windows = {}             # hwnd -> {"cls", "pid", "root"}
        self.at_point = None
        self.front = None
        self.focus = {}
        self.client = {}              # hwnd -> (cx, cy) for any point
        self.cursor = (500, 400)
        self.screen = (1920, 1080)
        self.posted = []
        self.keys = []
        self.inserted = []
        self.insert_works = True
        self.mouse_clicks = []
        self.cursor_moves = []
        self.raised = []
        self.windows_by_pid = {}
        self.held_buttons = False
        self.held_modifiers = False
        self.can_raise = True
        self.cursor_readable = True
        self.popups = {}              # owner -> its last active popup
        self.restored = []
        self.idle = 10_000            # ms since the user's last input
        self.minimized = set()
        self.stack = []               # top-level windows from the top down

    def add(self, hwnd, cls, pid, root=None, owner=None, parent=None, tool=False, style_ok=True,
            enabled=True, cloaked=False, elsewhere=False, through=False, exe=""):
        self.windows[hwnd] = {"cls": cls, "pid": pid, "root": root or hwnd, "owner": owner,
                              "parent": parent if parent is not None else (root if root and root != hwnd else None),
                              "tool": tool, "style_ok": style_ok, "enabled": enabled, "cloaked": cloaked,
                              "elsewhere": elsewhere, "through": through, "exe": exe}
        return hwnd

    # the methods direct_input calls
    def window_from_point(self, x, y):
        return self.at_point

    def root_of(self, hwnd):
        return self.windows[hwnd]["root"] if hwnd in self.windows else None

    def root_owner(self, hwnd):
        current = self.root_of(hwnd) or hwnd
        while self.windows.get(current, {}).get("owner"):
            current = self.windows[current]["owner"]
        return current

    def parent_of(self, hwnd):
        return self.windows.get(hwnd, {}).get("parent")

    def owner_of(self, hwnd):
        return self.windows.get(hwnd, {}).get("owner")

    def last_active_popup(self, hwnd):
        return self.popups.get(hwnd, hwnd)

    def is_enabled(self, hwnd):
        return self.windows.get(hwnd, {}).get("enabled", True)

    def is_cloaked(self, hwnd):
        return self.windows.get(hwnd, {}).get("cloaked", False)

    def on_another_desktop(self, hwnd):
        return self.windows.get(hwnd, {}).get("elsewhere", False)

    def is_minimized(self, hwnd):
        return hwnd in self.minimized

    def idle_ms(self):
        return self.idle

    def shown_windows(self):
        return [hwnd for hwnd, w in self.windows.items() if not w.get("cloaked")]

    def restore(self, hwnd):
        self.restored.append(hwnd)
        self.minimized.discard(hwnd)

    def is_visible(self, hwnd):
        return hwnd in self.windows

    def is_tool_window(self, hwnd):
        return self.windows.get(hwnd, {}).get("tool", False)

    def is_click_through(self, hwnd):
        return self.windows.get(hwnd, {}).get("through", False)

    def top_window_at(self, x, y, skip=None):
        for hwnd in self.stack:
            w = self.windows.get(hwnd)
            if w and not w.get("cloaked") and hwnd not in self.minimized and not (skip and skip(hwnd)):
                return hwnd
        return None

    def process_name(self, hwnd):
        return self.windows.get(hwnd, {}).get("exe", "")

    def text_box_accepts(self, hwnd, text):
        return self.windows.get(hwnd, {}).get("style_ok", True)

    def class_name(self, hwnd):
        return self.windows.get(hwnd, {}).get("cls", "")

    def process_id(self, hwnd):
        return self.windows.get(hwnd, {}).get("pid", 0)

    def is_window(self, hwnd):
        return hwnd in self.windows

    def foreground(self):
        return self.front

    def child_at(self, root, x, y):
        inside = self.at_point in self.windows and self.root_of(self.at_point) == root
        return self.at_point if inside else root

    def client_point(self, hwnd, x, y):
        return self.client.get(hwnd, (x - 100, y - 50))

    def visible_windows(self, pid):
        return set(self.windows_by_pid.get(pid, ()))

    def focus_of(self, hwnd):
        return self.focus.get(hwnd)

    def bring_to_front(self, hwnd):
        self.raised.append(hwnd)
        if self.can_raise:
            self.front = hwnd
        return self.can_raise

    def post(self, hwnd, message, wparam, lparam):
        self.posted.append((hwnd, message, wparam, lparam))
        return True

    def replace_selection(self, hwnd, text):
        self.inserted.append((hwnd, text))
        return self.insert_works

    def post_key(self, hwnd, vk, extended):
        self.keys.append((hwnd, vk, extended))
        return True

    def cursor_pos(self):
        return self.cursor if self.cursor_readable else None

    def set_cursor_pos(self, x, y):
        self.cursor_moves.append((x, y))
        self.cursor = (x, y)

    def primary_size(self):
        return self.screen

    def buttons_down(self):
        return self.held_buttons

    def modifiers_down(self):
        return self.held_modifiers

    def mouse_click(self, x, y, button):
        self.mouse_clicks.append((x, y, button))
        self.cursor = (x, y)


class Pattern:
    def __init__(self, **attributes):
        self.calls = []
        self.__dict__.update(attributes)

    def _record(self, name, result=True):
        self.calls.append(name)
        return result

    def Invoke(self, waitTime=0):
        return self._record("invoke")

    def Toggle(self, waitTime=0):
        return self._record("toggle")

    def Select(self, waitTime=0):
        self.IsSelected = True
        return self._record("select")

    def Expand(self, waitTime=0):
        return self._record("expand")

    def Collapse(self, waitTime=0):
        return self._record("collapse")

    def DoDefaultAction(self, waitTime=0):
        return self._record("default")

    def RangeFromPoint(self, x, y):
        self.calls.append(("range", x, y))
        return Pattern()


class Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class Control:
    def __init__(self, kind, name="", pid=42, parent=None, enabled=True, handle=0, patterns=None,
                 box=(100, 100, 140, 120)):
        self.ControlTypeName = f"{kind}Control"
        self.Name = name
        self.ProcessId = pid
        self.IsEnabled = enabled
        self.NativeWindowHandle = handle
        self.BoundingRectangle = Rect(*box)
        self.parent = parent
        self.patterns = patterns or {}
        self.focused = False

    def GetParentControl(self):
        return self.parent

    def GetPattern(self, pattern_id):
        return self.patterns.get(pattern_id)

    def SetFocus(self):
        self.focused = True
        return True


INVOKE, VALUE, EXPAND, SELECT = 10000, 10002, 10005, 10010
TEXT, TOGGLE, LEGACY = 10014, 10015, 10018


@pytest.fixture
def win(monkeypatch):
    fake = FakeWin()
    fake.add(10, "Notepad", 42)                          # a classic app's top-level window
    fake.at_point = fake.add(11, "Button", 42, root=10)
    monkeypatch.setattr(direct_input, "_IS_WINDOWS", True)
    monkeypatch.setattr(direct_input, "_win_layer", fake)
    for name, value in (("_target_hwnd", None), ("_target_set_at", 0.0), ("_front_when_set", None),
                        ("_target_owner", None), ("_target_lifetime", direct_input._TARGET_LIFETIME),
                        ("_keys_pending", None), ("_vanished_at", None), ("_pending_focus", None)):
        monkeypatch.setattr(direct_input, name, value)
    monkeypatch.setattr(direct_input, "mouse_fallback_allowed", lambda: True)
    monkeypatch.setattr(direct_input, "_focused_control", lambda: None)
    monkeypatch.setattr(direct_input, "_element3", lambda control: None)
    monkeypatch.setattr(direct_input, "_control_at", lambda x, y: None)
    monkeypatch.setattr(direct_input, "_control_in_window_at", lambda root, x, y, budget=1.0: None)
    monkeypatch.setattr(direct_input.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(actions, "emergency_stop", False)
    return fake


def _under_point(monkeypatch, control):
    monkeypatch.setattr(direct_input, "_control_at", lambda x, y: control)


# ── Clicks through UI Automation ───────────────────────────────────────

def test_a_button_is_invoked_and_the_mouse_never_moves(win, monkeypatch):
    invoke = Pattern()
    _under_point(monkeypatch, Control("Button", "Save", patterns={INVOKE: invoke}))

    outcome = direct_input.click(120, 110)

    assert outcome.done and outcome.how == "invoke" and outcome.control == 'Button "Save"'
    assert invoke.calls == ["invoke"]
    assert win.mouse_clicks == [] and win.cursor_moves == [] and win.posted == []


def test_the_label_inside_a_button_passes_the_click_to_the_button(win, monkeypatch):
    invoke = Pattern()
    button = Control("Button", "OK", patterns={INVOKE: invoke})
    _under_point(monkeypatch, Control("Text", "OK", parent=Control("Image", parent=button)))

    assert direct_input.click(120, 110).how == "invoke"
    assert invoke.calls == ["invoke"]


@pytest.mark.parametrize("kind, pattern_id, done_as", [
    ("CheckBox", TOGGLE, "toggle"),
    ("RadioButton", SELECT, "select"),
    ("TabItem", SELECT, "select"),
    ("ListItem", SELECT, "select"),
    ("Hyperlink", INVOKE, "invoke"),
])
def test_each_kind_of_control_gets_what_a_click_on_it_does(win, monkeypatch, kind, pattern_id, done_as):
    pattern = Pattern(IsSelected=False)
    _under_point(monkeypatch, Control(kind, patterns={pattern_id: pattern}))
    assert direct_input.click(120, 110).how == done_as


def test_a_list_item_is_selected_not_opened(win, monkeypatch):
    """Its default action is a double click: it would open the file."""
    invoke, select, legacy = Pattern(), Pattern(IsSelected=False), Pattern(DefaultAction="Double Click")
    _under_point(monkeypatch, Control("ListItem", "report.docx",
                                      patterns={INVOKE: invoke, SELECT: select, LEGACY: legacy}))

    direct_input.click(120, 110)

    assert select.calls == ["select"] and invoke.calls == [] and legacy.calls == []


def test_a_menu_opens_when_closed_and_closes_when_open(win, monkeypatch):
    closed = Pattern(ExpandCollapseState=0)
    _under_point(monkeypatch, Control("MenuItem", "File", patterns={EXPAND: closed}))
    assert direct_input.click(120, 110).how == "expand"

    opened = Pattern(ExpandCollapseState=1)
    _under_point(monkeypatch, Control("MenuItem", "File", patterns={EXPAND: opened}))
    assert direct_input.click(120, 110).how == "collapse"


def test_a_menu_item_with_nothing_under_it_is_invoked(win, monkeypatch):
    leaf, invoke = Pattern(ExpandCollapseState=3), Pattern()
    _under_point(monkeypatch, Control("MenuItem", "Save as", patterns={EXPAND: leaf, INVOKE: invoke}))

    assert direct_input.click(120, 110).how == "invoke"
    assert leaf.calls == []


def test_a_disabled_control_is_not_pressed_and_the_ai_is_told(win, monkeypatch):
    invoke = Pattern()
    _under_point(monkeypatch, Control("Button", "Send", enabled=False, patterns={INVOKE: invoke}))

    outcome = direct_input.click(120, 110)

    assert outcome.done and outcome.how == "disabled" and "disabled" in outcome.note
    assert invoke.calls == [] and win.mouse_clicks == []


def test_a_control_from_another_app_is_not_trusted(win, monkeypatch):
    """UI Automation answered with something that isn't the window at the
    point (an overlay, a stale tree): it isn't pressed."""
    invoke = Pattern()
    _under_point(monkeypatch, Control("Button", pid=999, patterns={INVOKE: invoke}))

    direct_input.click(120, 110)

    assert invoke.calls == []


def test_a_store_app_frame_and_its_content_count_as_one_app(win, monkeypatch):
    win.add(20, "ApplicationFrameWindow", 7)
    win.at_point = win.add(21, "Windows.UI.Core.CoreWindow", 8, root=20)
    invoke = Pattern()
    _under_point(monkeypatch, Control("Button", "=", pid=8, patterns={INVOKE: invoke}))

    assert direct_input.click(120, 110).how == "invoke"


# ── Text fields ─────────────────────────────────────────────────────────

def test_a_classic_text_box_is_clicked_with_a_message_not_focused(win, monkeypatch):
    """A message puts the caret where the click was and leaves the window
    where it is; SetFocus would bring it to the front."""
    win.at_point = win.add(12, "Edit", 42, root=10)
    field = Control("Edit", handle=12)
    _under_point(monkeypatch, field)

    outcome = direct_input.click(300, 250)

    assert outcome.how == "window message" and not field.focused
    assert [m[1] for m in win.posted] == [direct_input.WM_MOUSEMOVE, direct_input.WM_LBUTTONDOWN,
                                          direct_input.WM_LBUTTONUP]


def test_a_browser_text_field_gets_the_focus_and_the_caret(win, monkeypatch):
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)
    text = Pattern()
    field = Control("Edit", "Search", pid=55, patterns={TEXT: text, VALUE: Pattern(IsReadOnly=False)})
    _under_point(monkeypatch, field)

    outcome = direct_input.click(300, 250)

    assert outcome.how == "focus" and field.focused
    assert ("range", 300, 250) in text.calls
    assert win.mouse_clicks == []


# ── Window messages ─────────────────────────────────────────────────────

def test_a_classic_window_with_nothing_to_press_gets_a_posted_click(win, monkeypatch):
    win.at_point = win.add(13, "SysListView32", 42, root=10)
    win.client[13] = (40, 7)
    _under_point(monkeypatch, Control("Pane"))

    outcome = direct_input.click(300, 250)

    assert outcome.done and outcome.how == "window message"
    lparam = 7 << 16 | 40
    assert win.posted == [
        (13, direct_input.WM_MOUSEMOVE, 0, lparam),
        (13, direct_input.WM_LBUTTONDOWN, direct_input.MK_LBUTTON, lparam),
        (13, direct_input.WM_LBUTTONUP, 0, lparam),
    ]
    assert win.mouse_clicks == []


def test_outside_the_client_area_no_message_is_posted(win, monkeypatch):
    """A title bar or a frame: a posted button message wouldn't mean a
    click there."""
    win.client[11] = None
    direct_input.click(300, 5)
    assert win.posted == []


# ── The mouse, borrowed and put back ────────────────────────────────────

def test_a_chromium_window_gets_a_real_click_and_the_mouse_goes_back(win, monkeypatch):
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)
    _under_point(monkeypatch, Control("Group", pid=55))      # a div with no role
    win.cursor = (900, 700)

    outcome = direct_input.click(300, 250)

    assert outcome.done and outcome.how == "mouse"
    assert win.posted == []                                   # Chromium ignores posted clicks
    assert win.mouse_clicks == [(300, 250, "left")]
    assert win.cursor == (900, 700)                           # back where the user left it


def test_with_the_mouse_setting_off_nothing_is_clicked_and_the_reason_is_given(win, monkeypatch):
    monkeypatch.setattr(direct_input, "mouse_fallback_allowed", lambda: False)
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)

    outcome = direct_input.click(300, 250)

    assert not outcome.done and "Settings" in outcome.note and "Chromium" in outcome.note
    assert win.mouse_clicks == [] and win.cursor_moves == []


def test_it_waits_for_the_user_to_let_go_of_a_button(win, monkeypatch):
    """Not in the middle of the user's own drag."""
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)
    readings = [True, True]
    win.buttons_down = lambda: readings.pop(0) if readings else False

    direct_input.click(300, 250)

    assert win.mouse_clicks == [(300, 250, "left")]


def test_kestrels_own_window_is_looked_through_to_the_app_beneath(win, monkeypatch):
    """The AI works on other apps; Kestrel's window on top of the spot
    isn't what it means. The app's own window is asked for the control."""
    win.add(40, "Qt6QWindowIcon", os.getpid())
    win.at_point = 40
    win.stack = [40, 10]
    kestrels = Pattern()
    _under_point(monkeypatch, Control("Button", pid=os.getpid(), patterns={INVOKE: kestrels}))
    notepads = Pattern()
    monkeypatch.setattr(direct_input, "_control_in_window_at",
                        lambda root, x, y, budget=1.0: Control("Button", "Save", patterns={INVOKE: notepads})
                        if root == 10 else None)

    outcome = direct_input.click(300, 250)

    assert outcome.how == "invoke" and notepads.calls == ["invoke"] and kestrels.calls == []
    assert win.mouse_clicks == [] and direct_input._target() == 10


# ── Stopping ────────────────────────────────────────────────────────────

def test_the_mouse_in_a_corner_stops_everything(win, monkeypatch):
    """The fail-safe pyautogui used to check before every move."""
    invoke = Pattern()
    _under_point(monkeypatch, Control("Button", patterns={INVOKE: invoke}))
    win.cursor = (1919, 0)

    outcome = direct_input.click(120, 110)

    assert outcome.stopped and not outcome.done
    assert actions.emergency_stop is True
    assert invoke.calls == []


def test_nothing_is_done_once_stopped(win, monkeypatch):
    monkeypatch.setattr(actions, "emergency_stop", True)
    invoke = Pattern()
    _under_point(monkeypatch, Control("Button", patterns={INVOKE: invoke}))

    assert direct_input.click(120, 110).stopped
    assert direct_input.press_key("enter").stopped
    assert direct_input.type_text("hi").stopped
    assert invoke.calls == []


# ── Right clicks ────────────────────────────────────────────────────────

class Element3:
    def __init__(self, win, pid, menu_window):
        self.win, self.pid, self.menu_window = win, pid, menu_window

    def ShowContextMenu(self):
        self.win.windows_by_pid.setdefault(self.pid, set()).add(self.menu_window)


def test_a_right_click_opens_the_menu_through_ui_automation(win, monkeypatch):
    item = Control("ListItem", "notes.txt", patterns={SELECT: Pattern(IsSelected=False)})
    _under_point(monkeypatch, Control("Text", "notes.txt", parent=item))
    monkeypatch.setattr(direct_input, "_element3", lambda control: Element3(win, 42, 777))

    outcome = direct_input.click(120, 110, button="right")

    assert outcome.done and outcome.how == "context menu"
    assert item.patterns[SELECT].calls == ["select"]       # the item is selected first, as a real right click does
    assert win.mouse_clicks == []


def test_a_context_menu_the_app_refuses_is_tried_another_way(win, monkeypatch):
    """No such menu through UI Automation: the next way is tried (here, the
    mouse, since Chromium ignores posted clicks)."""
    class Refusing:
        def ShowContextMenu(self):
            raise COMError(-2147467263)            # E_NOTIMPL

    monkeypatch.setattr(direct_input, "_element3", lambda control: Refusing())
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)
    _under_point(monkeypatch, Control("Pane", pid=55))

    outcome = direct_input.click(300, 250, button="right")

    assert outcome.how == "mouse" and win.mouse_clicks == [(300, 250, "right")]


def test_a_context_menu_the_app_accepted_is_not_asked_for_twice(win, monkeypatch):
    """Accepted, but no menu seen yet (a slow shell extension): another
    route would open a second one on top."""
    monkeypatch.setattr(direct_input, "_element3",
                        lambda control: type("E", (), {"ShowContextMenu": lambda self: None})())
    monkeypatch.setattr(direct_input, "_menu_opened", lambda win, pid, before, timeout=1.5: False)
    _under_point(monkeypatch, Control("Pane"))

    outcome = direct_input.click(300, 250, button="right")

    assert outcome.done and outcome.how == "context menu" and "no menu" in outcome.note
    assert win.mouse_clicks == [] and win.posted == []


# ── Typing and keys ─────────────────────────────────────────────────────

def test_typing_into_the_window_in_front_uses_the_keyboard(win):
    direct_input.set_target(10)
    win.front = 10
    outcome = direct_input.type_text("hello")
    assert not outcome.done and outcome.how == "keyboard"
    assert win.inserted == []


def test_typing_into_a_background_text_box_goes_straight_in(win):
    win.add(12, "Edit", 42, root=10)
    win.focus[10] = 12
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 50                              # the user is in their browser
    direct_input.set_target(10)

    outcome = direct_input.type_text("café")

    assert outcome.done and outcome.how == "text message"
    assert win.inserted == [(12, "café")]
    assert win.raised == []                     # the user's window stays in front


def test_a_text_box_that_did_not_change_falls_back_to_the_keyboard(win):
    win.add(12, "Edit", 42, root=10)
    win.focus[10] = 12
    win.insert_works = False
    win.front = None
    direct_input.set_target(10)

    outcome = direct_input.type_text("x")

    assert not outcome.done and win.raised == [10]


def test_typing_into_a_background_browser_brings_it_forward_first(win):
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)
    win.focus[30] = 31
    win.front = 10
    direct_input.set_target(30)

    outcome = direct_input.type_text("lofi")

    assert not outcome.done and outcome.how == "keyboard"
    assert win.inserted == [] and win.raised == [30]


def test_enter_is_posted_to_a_background_classic_window(win):
    """After the AI's own click put the focus in the box (a posted click
    gives a background window's thread a focus)."""
    win.add(12, "Edit", 42, root=10)
    win.focus[10] = 12
    win.front = None
    direct_input.set_target(10)

    outcome = direct_input.press_key("enter")

    assert outcome.done and win.keys == [(12, 0x0D, False)]


def test_a_background_window_with_no_focus_gets_its_key_from_the_keyboard(win):
    """A deactivated app's thread usually has no focus; a key posted to the
    window itself reaches nothing, so it isn't posted."""
    win.front = None
    direct_input.set_target(10)

    outcome = direct_input.press_key("enter")

    assert not outcome.done and outcome.how == "keyboard"
    assert win.keys == [] and win.raised == [10]


def test_nothing_is_typed_when_the_window_cannot_be_brought_forward(win):
    """The keystrokes would land in whatever the user is using."""
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 50
    win.can_raise = False
    direct_input.set_target(10)

    assert direct_input.type_text("hello").how == "blocked"
    assert direct_input.press_key("enter").how == "blocked"
    assert direct_input.prepare_keyboard() is False


def test_a_dialog_the_target_owns_counts_as_the_target_in_front(win):
    """The AI clicked Close; "Save changes?" is in front. Its Enter goes to
    the dialog, with the keyboard, not to the main window, which a modal
    dialog disables."""
    win.add(70, "#32770", 42, owner=10)
    win.windows[10]["enabled"] = False
    win.front = 70
    direct_input.set_target(10)

    assert direct_input.press_key("enter").how == "keyboard"
    assert direct_input.type_text("report.txt").how == "keyboard"
    assert win.keys == [] and win.raised == []


def test_a_read_only_text_box_is_not_written_into(win):
    win.add(12, "Edit", 42, root=10, style_ok=False)
    win.focus[10] = 12
    win.front = None
    direct_input.set_target(10)

    outcome = direct_input.type_text("x")

    assert win.inserted == [] and outcome.how == "keyboard"


def test_delete_is_an_extended_key(win):
    win.add(12, "Edit", 42, root=10)
    win.focus[10] = 12
    direct_input.set_target(10)
    direct_input.press_key("Delete")
    assert win.keys == [(12, 0x2E, True)]


def test_keys_for_the_window_in_front_use_the_keyboard(win):
    direct_input.set_target(10)
    win.front = 10
    assert direct_input.press_key("enter").how == "keyboard"
    assert win.keys == []


def test_an_unknown_key_uses_the_keyboard(win):
    direct_input.set_target(10)
    assert direct_input.press_key("ctrl+t").how == "keyboard"
    assert win.keys == []


def test_a_key_combination_brings_the_target_forward(win):
    direct_input.set_target(10)
    win.front = 50
    direct_input.prepare_keyboard()
    assert win.raised == [10]
    win.raised.clear()
    direct_input.prepare_keyboard()               # already in front now
    assert win.raised == []


def test_a_click_makes_its_window_the_target_and_a_new_run_forgets_it(win, monkeypatch):
    _under_point(monkeypatch, Control("Button", patterns={INVOKE: Pattern()}))
    direct_input.click(120, 110)
    assert direct_input._target() == 10
    direct_input.forget_target()
    assert direct_input._target() is None


def test_without_a_target_typing_goes_to_the_window_in_front(win):
    """...which is then where it has to stay until the last key."""
    win.front = 10
    typed = direct_input.type_text("hi")
    assert typed.how == "keyboard" and typed.window == 10
    assert direct_input.press_key("enter").window == 10


# ── A control the mapper already found ──────────────────────────────────

def test_the_mappers_control_is_pressed_through_ui_automation(win):
    invoke = Pattern()
    outcome = direct_input.press_control(Control("Button", "Open", patterns={INVOKE: invoke}), window=10)
    assert outcome.how == "invoke" and invoke.calls == ["invoke"]
    assert direct_input._target() == 10


def test_a_covered_window_gets_its_click_as_a_message_to_its_own_child(win):
    """The mapper's window may be behind another one; a posted click goes
    to its own control at that point, not to whatever covers it."""
    win.add(14, "ToolbarWindow32", 42, root=10)
    win.at_point = 14
    outcome = direct_input.press_control(Control("Pane"), window=10)
    assert outcome.how == "window message" and win.posted[0][0] == 14


def test_a_covered_window_is_brought_forward_before_a_borrowed_click(win):
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.add(60, "Notepad", 61)
    win.at_point = 60                              # another app covers the point
    win.child_at = lambda root, x, y: root

    def raised(hwnd):
        win.raised.append(hwnd)
        win.at_point = hwnd                        # on top there now
        return True

    win.bring_to_front = raised
    outcome = direct_input.press_control(Control("Group", pid=55, box=(100, 100, 140, 120)), window=30)
    assert outcome.how == "mouse" and win.raised == [30]
    assert win.mouse_clicks == [(120, 110, "left")]


# ── Outside Windows ─────────────────────────────────────────────────────

def test_outside_windows_the_click_is_a_real_one(monkeypatch, mock_pyautogui):
    monkeypatch.setattr(direct_input, "_IS_WINDOWS", False)
    monkeypatch.setattr(direct_input, "mouse_fallback_allowed", lambda: True)
    monkeypatch.setattr(actions, "emergency_stop", False)
    outcome = direct_input.click(5, 6, "right")
    assert outcome.how == "mouse"
    mock_pyautogui.click.assert_called_once_with(x=5, y=6, button="right")
    assert direct_input.type_text("x").how == "keyboard"
    assert direct_input.press_key("enter").how == "keyboard"


def test_the_setting_defaults_to_on(monkeypatch):
    import ai.main as ai_main
    monkeypatch.delenv("KESTREL_MOUSE_FALLBACK", raising=False)
    monkeypatch.setattr(ai_main, "_read_env_file", lambda: {})
    assert ai_main.get_mouse_fallback() is True
    monkeypatch.setenv("KESTREL_MOUSE_FALLBACK", "0")
    assert ai_main.get_mouse_fallback() is False


# ── Review fixes ────────────────────────────────────────────────────────

class COMError(Exception):
    def __init__(self, hresult):
        super().__init__(hresult, "error", None)
        self.hresult = hresult


def test_a_press_that_blocks_counts_as_done_and_is_not_repeated(win, monkeypatch):
    """A WinForms button whose Invoke opens a dialog doesn't return until
    the dialog closes. The run goes on, and nothing presses it again."""
    import threading
    release = threading.Event()

    class Blocking(Pattern):
        def Invoke(self, waitTime=0):
            release.wait(5)
            return True

    monkeypatch.setattr(direct_input, "_UIA_WAIT", 0.3)
    _under_point(monkeypatch, Control("Button", "Open...", patterns={INVOKE: Blocking()}))
    try:
        outcome = direct_input.click(120, 110)
    finally:
        release.set()

    assert outcome.done and outcome.how == "invoke" and "busy" in outcome.note
    assert win.posted == [] and win.mouse_clicks == []


def test_an_unanswered_read_falls_through_without_acting(win, monkeypatch):
    import threading
    release = threading.Event()

    def slow_lookup(x, y):
        release.wait(5)
        return None

    monkeypatch.setattr(direct_input, "_UIA_WAIT", 0.2)
    monkeypatch.setattr(direct_input, "_control_at", slow_lookup)
    win.at_point = win.add(13, "SysListView32", 42, root=10)
    try:
        outcome = direct_input.click(300, 250)
    finally:
        release.set()

    assert outcome.how == "window message"


def test_a_refused_action_tries_the_next_one(win, monkeypatch):
    class Refusing(Pattern):
        def Invoke(self, waitTime=0):
            raise COMError(-2147467263)            # E_NOTIMPL: nothing happened

    toggle = Pattern()
    _under_point(monkeypatch, Control("Button", patterns={INVOKE: Refusing(), TOGGLE: toggle}))

    assert direct_input.click(120, 110).how == "toggle"
    assert toggle.calls == ["toggle"]


def test_an_error_after_acting_is_not_pressed_again(win, monkeypatch):
    """An error that may have come after the app acted: no second press, by
    UI Automation, a message or the mouse."""
    class Failing(Pattern):
        def Invoke(self, waitTime=0):
            raise COMError(0x80040201 - (1 << 32))   # UIA_E_ELEMENTNOTAVAILABLE

    legacy = Pattern(DefaultAction="Press")
    _under_point(monkeypatch, Control("Button", "Send", patterns={INVOKE: Failing(), LEGACY: legacy}))

    outcome = direct_input.click(120, 110)

    assert outcome.done and outcome.how == "invoke" and "error" in outcome.note
    assert legacy.calls == [] and win.posted == [] and win.mouse_clicks == []


def test_a_web_page_is_clicked_not_focused(win, monkeypatch):
    """A Document that isn't editable (a page, a PDF): a click on it is a
    click, not a request for the caret."""
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)
    page = Control("Document", "Example", pid=55, patterns={VALUE: Pattern(IsReadOnly=True)})
    _under_point(monkeypatch, page)

    outcome = direct_input.click(300, 250)

    assert not page.focused and outcome.how == "mouse"


def test_an_editable_field_needs_a_value_that_can_be_set(win, monkeypatch):
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)
    field = Control("Edit", "Search", pid=55, patterns={VALUE: Pattern(IsReadOnly=False)})
    _under_point(monkeypatch, field)

    assert direct_input.click(300, 250).how == "focus" and field.focused


def test_a_classic_list_box_item_gets_a_posted_click(win, monkeypatch):
    """The accessibility proxy would select it without telling the app."""
    win.at_point = win.add(15, "ListBox", 42, root=10)
    select = Pattern(IsSelected=False)
    _under_point(monkeypatch, Control("ListItem", "Arial", parent=Control("List", handle=15),
                                      patterns={SELECT: select}))

    outcome = direct_input.click(300, 250)

    assert outcome.how == "window message" and select.calls == []


def test_a_selected_list_item_gets_the_keyboard_focus_like_a_click(win, monkeypatch):
    win.front = 10
    item = Control("ListItem", "report.docx", patterns={SELECT: Pattern(IsSelected=False)})
    _under_point(monkeypatch, item)
    direct_input.click(120, 110)
    assert item.focused


def test_a_xaml_island_in_a_classic_window_gets_no_posted_click(win, monkeypatch):
    win.add(80, "CASCADIA_HOSTING_WINDOW_CLASS", 88)
    win.add(81, "Windows.UI.Input.InputSite.WindowClass", 88, root=80, parent=80)
    win.at_point = win.add(82, "Static", 88, root=80, parent=81)
    _under_point(monkeypatch, Control("Pane", pid=88))

    outcome = direct_input.click(300, 250)

    assert win.posted == [] and outcome.how == "mouse"


def test_the_taskbar_never_becomes_the_target(win, monkeypatch):
    """Nor does the window from before it: keys meant for what the click
    did shouldn't go there."""
    direct_input.set_target(10)
    win.add(90, "Shell_TrayWnd", 4)
    win.at_point = 90
    _under_point(monkeypatch, Control("Button", "Chrome", pid=4, patterns={INVOKE: Pattern()}))

    direct_input.click(500, 1060)

    assert direct_input._target() is None


def test_a_window_a_click_brings_up_becomes_the_target(win, monkeypatch):
    win.add(90, "Shell_TrayWnd", 4)
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = 90

    class Activating(Pattern):
        def Invoke(self, waitTime=0):
            win.front = 30                       # the taskbar button brought Chrome up
            return True

    _under_point(monkeypatch, Control("Button", "Chrome", pid=4, patterns={INVOKE: Activating()}))

    direct_input.click(500, 1060)

    assert direct_input._target() == 30


def test_a_held_modifier_or_button_stops_a_borrowed_click(win, monkeypatch):
    """It would be a Ctrl+click, or drop what the user is dragging."""
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)
    win.held_modifiers = True

    outcome = direct_input.click(300, 250)

    assert not outcome.done and "holding" in outcome.note and win.mouse_clicks == []


def test_an_unreadable_pointer_is_not_a_corner(win, monkeypatch):
    """A locked screen: GetCursorPos fails. That isn't the fail-safe."""
    win.cursor_readable = False
    _under_point(monkeypatch, Control("Button", patterns={INVOKE: Pattern()}))

    assert direct_input.click(120, 110).how == "invoke"
    assert actions.emergency_stop is False


def test_typing_stops_when_the_target_leaves_the_front(win):
    direct_input.set_target(10)
    win.front = 10
    assert direct_input.keyboard_reaches(10) is True
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 50                               # the user clicked into their browser
    assert direct_input.keyboard_reaches(10) is False


def test_a_target_left_alone_for_long_is_forgotten(win, monkeypatch):
    direct_input.set_target(10)
    monkeypatch.setattr(direct_input, "_target_set_at", direct_input.time.monotonic() - 400)
    assert direct_input._target() is None


# ── Second review ───────────────────────────────────────────────────────

def test_an_abandoned_press_does_nothing_more_after_the_caller_gave_up(win, monkeypatch):
    """Invoke blocked past the wait, then failed in a way that means it did
    nothing. The job must not go on to press another way, after the run
    has moved on."""
    import threading
    release, finished = threading.Event(), threading.Event()

    class Blocking(Pattern):
        def Invoke(self, waitTime=0):
            release.wait(5)
            raise COMError(0x80131509 - (1 << 32))      # UIA_E_INVALIDOPERATION

    real_run = direct_input._run

    def watched(*args):
        try:
            return real_run(*args)
        finally:
            finished.set()

    monkeypatch.setattr(direct_input, "_run", watched)
    monkeypatch.setattr(direct_input, "_UIA_WAIT", 0.3)
    legacy = Pattern(DefaultAction="Press")
    _under_point(monkeypatch, Control("Button", "Send", patterns={INVOKE: Blocking(), LEGACY: legacy}))
    try:
        outcome = direct_input.click(120, 110)
    finally:
        release.set()

    assert outcome.done and outcome.how == "invoke"
    assert finished.wait(5)
    assert legacy.calls == [] and win.posted == [] and win.mouse_clicks == []


def test_an_error_after_acting_counts_as_done(win):
    """A list that rebuilds itself after a Select can leave the item
    unreadable: the click was made, and isn't made again another way."""
    def selects_then_fails(gate):
        with gate.act("select", 'ListItem "a"'):
            pass
        raise RuntimeError("element not available")

    outcome = direct_input._attempt(selects_then_fails)

    assert outcome.done and outcome.how == "select" and "error" in outcome.note


def test_ui_automation_waits_while_too_many_calls_are_stuck(win, monkeypatch):
    """Each call stuck in a hung app is a thread; past a few, no more."""
    import queue
    import types
    stuck = types.SimpleNamespace(busy=True, abandoned=False, jobs=queue.Queue())
    monkeypatch.setattr(direct_input, "_worker", stuck)
    monkeypatch.setattr(direct_input, "_stuck", direct_input._MAX_STUCK)
    called = []

    assert direct_input._attempt(lambda gate: called.append(1)) is None
    assert called == []


def test_a_window_the_user_switches_to_during_a_click_is_not_the_target(win, monkeypatch):
    win.add(50, "OpusApp", 99)                            # the user's own document

    class Switching(Pattern):
        def Invoke(self, waitTime=0):
            win.front = 50                                # they clicked into it meanwhile
            return True

    _under_point(monkeypatch, Control("Button", "Bold", patterns={INVOKE: Switching()}))

    direct_input.click(120, 110)

    assert direct_input._target() == 10


def test_a_dialog_the_click_opened_becomes_the_target(win, monkeypatch):
    win.add(70, "#32770", 42, owner=10)

    class Opening(Pattern):
        def Invoke(self, waitTime=0):
            win.front = 70
            return True

    _under_point(monkeypatch, Control("Button", "Save as", patterns={INVOKE: Opening()}))

    direct_input.click(120, 110)

    assert direct_input._target() == 70


def test_a_find_box_is_not_where_the_main_windows_keys_go(win):
    """A dialog that isn't modal: bringing the AI's window forward brings
    that window, not its Find box, and the Find box in front doesn't
    count as the window being there."""
    win.add(71, "#32770", 42, owner=10)                   # Find, open from earlier
    win.popups[10] = 71
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 71
    direct_input.set_target(10)                           # the AI clicked the text behind it
    assert direct_input.keyboard_reaches(10) is False
    win.front = 50                                        # the user is in their browser

    outcome = direct_input.type_text("hello")

    assert outcome.how == "keyboard" and outcome.window == 10
    assert win.raised == [10] and win.front == 10


def test_text_is_not_put_into_a_find_box_the_target_owns(win):
    win.add(71, "#32770", 42, owner=10)
    win.add(72, "Edit", 42, root=71)                      # the Find box's field has the focus
    win.focus[10] = 72
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 50
    direct_input.set_target(10)

    direct_input.type_text("hello")

    assert win.inserted == [] and win.raised == [10]


def test_a_modal_dialog_is_what_comes_forward_for_the_keys(win):
    win.add(70, "#32770", 42, owner=10)
    win.windows[10]["enabled"] = False                    # "Save changes?" is up
    win.popups[10] = 70
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 50
    direct_input.set_target(10)

    assert direct_input.prepare_keyboard() is True
    assert win.raised == [70]


def test_a_minimized_window_is_restored_before_it_is_brought_forward(win):
    direct_input.set_target(10)
    direct_input.prepare_keyboard()
    assert win.restored == [10] and win.raised == [10]


def test_a_window_that_came_up_in_the_same_app_gets_the_next_keys(win):
    """Ctrl+F opened Notepad's Find box: what the AI types next is the
    search, as a person's typing would be."""
    win.front = 10
    direct_input.set_target(10)
    win.add(71, "#32770", 42, owner=10)
    win.front = 71

    assert direct_input._target() == 71
    assert direct_input.type_text("needle").window == 71


def test_when_a_dialog_closes_keys_go_back_to_the_window_it_belonged_to(win):
    """Not to whatever the user has in front by then."""
    win.add(70, "#32770", 42, owner=10)
    win.front = 70
    direct_input.set_target(70)
    del win.windows[70]                                   # Enter closed it
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 50

    assert direct_input._target() == 10


def test_a_cloaked_window_is_not_a_target(win):
    """The Start menu once it closed: put away, not hidden."""
    win.add(95, "Windows.UI.Core.CoreWindow", 7)
    direct_input.set_target(95)
    win.windows[95]["cloaked"] = True
    assert direct_input._target() is None


def test_a_desktop_icon_click_makes_the_desktop_the_target(win, monkeypatch):
    """F2, Delete or Enter on the icon go there, not to an older window."""
    direct_input.set_target(10)
    win.add(96, "WorkerW", 4)
    win.at_point = win.add(97, "SysListView32", 4, root=96)
    _under_point(monkeypatch, Control("ListItem", "notes.txt", pid=4,
                                      patterns={SELECT: Pattern(IsSelected=False)}))

    direct_input.click(300, 300)

    assert direct_input._target() == 96


def test_with_no_target_kestrels_own_window_gets_no_keys(win):
    """The AI's text would land in the chat box, and Enter would send it."""
    win.add(40, "Qt6QWindowIcon", os.getpid())
    win.front = 40

    typed = direct_input.type_text("hello")

    assert typed.how == "blocked" and "click" in typed.note
    assert direct_input.press_key("enter").how == "blocked"
    assert direct_input.prepare_keyboard() is False


def test_with_no_target_and_nothing_in_front_nothing_is_typed(win):
    win.front = None
    assert direct_input.type_text("hello").how == "blocked"


def test_text_with_a_line_break_goes_through_the_keyboard_in_one_go(win):
    """Text sent to a box and an Enter posted after it can reach the app
    out of order."""
    win.add(12, "Edit", 42, root=10)
    win.focus[10] = 12
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 50
    direct_input.set_target(10)

    outcome = direct_input.type_text("line1\nline2")

    assert outcome.how == "keyboard" and win.inserted == [] and win.raised == [10]


def test_an_item_selected_behind_gets_the_focus_when_its_window_comes_forward(win, monkeypatch):
    """Not before: SetFocus would pull the window over the user's, and
    their next Delete would hit the item."""
    win.add(13, "SysListView32", 42, root=10)
    win.focus[10] = 13
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 50
    item = Control("ListItem", "report.docx", patterns={SELECT: Pattern(IsSelected=False)})
    _under_point(monkeypatch, item)

    direct_input.click(120, 110)
    assert not item.focused and win.front == 50

    direct_input.press_key("delete")

    assert item.focused and win.front == 10
    assert win.keys == []                                 # the keyboard, after the focus moved


def test_the_mapper_sets_a_value_without_keys(win):
    value = Pattern(IsReadOnly=False)
    value.SetValue = lambda text, waitTime=0: value.calls.append(("set", text)) or True

    outcome = direct_input.set_value(Control("Edit", "Name", patterns={VALUE: value}), "Ada", window=10)

    assert outcome.done and outcome.how == "set value" and value.calls == [("set", "Ada")]


def test_a_value_that_opens_a_dialog_does_not_hold_the_mapper(win, monkeypatch):
    import threading
    release = threading.Event()
    value = Pattern(IsReadOnly=False)
    value.SetValue = lambda text, waitTime=0: release.wait(5)
    monkeypatch.setattr(direct_input, "_UIA_WAIT", 0.3)
    try:
        outcome = direct_input.set_value(Control("Edit", patterns={VALUE: value}), "x")
    finally:
        release.set()

    assert outcome.done and "busy" in outcome.note


def test_a_read_only_field_is_typed_into_instead(win):
    outcome = direct_input.set_value(Control("Edit", patterns={VALUE: Pattern(IsReadOnly=True)}), "x")
    assert not outcome.done and outcome.how == "keyboard"


def test_the_mappers_typing_has_the_same_checks_as_the_ais(win, monkeypatch):
    """A field with no value to set is clicked and typed into through
    ai.actions, which stops if another window comes in front."""
    import types
    from ai.discovery.accessibility import AccessibilityElement, WindowsUIABackend
    field = Control("Edit", "Name", patterns={VALUE: Pattern(IsReadOnly=True)})
    backend = WindowsUIABackend(types.SimpleNamespace(NativeWindowHandle=10))
    monkeypatch.setattr(backend, "_find_control_by_path", lambda root, path: field)
    monkeypatch.setattr(direct_input, "press_control",
                        lambda control, window=None: direct_input.Outcome(True, "focus"))
    typed, keys = [], []
    monkeypatch.setattr(actions, "_type_text",
                        lambda text: typed.append(text) or actions.Typed(False, "left the front"))
    monkeypatch.setattr(actions, "_press_key",
                        lambda key, window=None: keys.append((key, window)) or actions.Typed(True))

    ok = backend.input_value(AccessibilityElement(id="Edit[0]", name="Name", control_type="Edit"), "Ada")
    backend.send_key("ESCAPE")

    assert ok is False and typed == ["Ada"]
    assert keys == [("ESCAPE", 10)]


# ── Third review ────────────────────────────────────────────────────────

def test_a_window_the_ais_own_keys_bring_up_gets_the_next_keys(win):
    """Win+R from the browser: the Run box, not the browser, gets what the
    AI types next."""
    win.front = 10
    direct_input.set_target(10)
    before = direct_input.before_keys()
    win.add(80, "#32770", 4)                              # Run, another process
    win.front = 80

    direct_input.follow_keys(before, system=True)

    assert direct_input._target() == 80


def test_a_window_the_user_switches_to_right_after_a_key_is_not_followed(win):
    """It was already on screen: the key didn't bring it up."""
    win.add(50, "OpusApp", 99)                            # the user's document
    win.front = 10
    direct_input.set_target(10)
    before = direct_input.before_keys()
    win.front = 50

    direct_input.follow_keys(before)

    assert direct_input._target() == 10


def test_alt_tab_switches_on_purpose(win):
    win.add(50, "OpusApp", 99)
    win.front = 10
    direct_input.set_target(10)
    before = direct_input.before_keys()
    win.front = 50

    direct_input.follow_keys(before, switch=True)

    assert direct_input._target() == 50


def test_the_window_behind_one_a_key_closed_is_not_followed(win):
    """Enter closed the dialog; what comes up next may be the user's."""
    win.add(70, "#32770", 42, owner=10)
    win.add(50, "Chrome_WidgetWin_1", 99)                 # the user's, already there
    win.front = 70
    direct_input.set_target(70)
    before = direct_input.before_keys()
    del win.windows[70]
    win.front = 50

    direct_input.follow_keys(before)

    assert direct_input._target() == 10


def test_a_window_that_takes_a_while_to_open_is_followed_at_the_next_key(win):
    """Win+E: the Explorer window can take a second or two."""
    win.front = 10
    direct_input.set_target(10)
    before = direct_input.before_keys()
    direct_input.follow_keys(before, system=True)
    assert direct_input._target() == 10                   # nothing yet
    win.add(81, "CabinetWClass", 4)
    win.front = 81

    assert direct_input._target() == 81


def test_a_window_the_user_opens_later_isnt_taken_once_the_ai_typed_on(win):
    """Ctrl+C brought nothing up and the AI went on typing in its window:
    a new window of the user's after that isn't the key's."""
    win.front = 10
    direct_input.set_target(10)
    direct_input.follow_keys(direct_input.before_keys())
    assert direct_input.keyboard_window().front == 10
    win.add(52, "OpusApp", 99)
    win.front = 52

    assert direct_input._target() == 10


def test_a_window_the_user_restores_is_not_taken_for_one_the_key_opened(win):
    """It was there before the key, minimized."""
    win.add(50, "OpusApp", 99)
    win.minimized.add(50)
    win.front = 10
    direct_input.set_target(10)
    before = direct_input.before_keys()
    direct_input.follow_keys(before)
    win.minimized.discard(50)
    win.front = 50

    assert direct_input._target() == 10


def test_typing_waits_a_moment_for_the_app_a_key_starts(win, monkeypatch):
    """Enter in the Run box closes it; Notepad comes up a little later."""
    win.add(80, "#32770", 4)
    win.add(50, "OpusApp", 99)                            # the user's, behind the Run box
    win.front = 80
    direct_input.set_target(80)
    before = direct_input.before_keys()
    direct_input.follow_keys(before)
    del win.windows[80]
    win.add(82, "Notepad", 83)
    fronts = iter([50, 50, 50, 82])
    win.foreground = lambda: next(fronts, 82)

    assert direct_input._target() == 82


def test_keys_wait_for_a_click_after_the_ais_window_closed(win):
    """What is in front then may be the user's."""
    win.add(80, "#32770", 4)
    win.front = 80
    direct_input.set_target(80)
    del win.windows[80]
    win.add(50, "OpusApp", 99)
    win.front = 50

    typed = direct_input.type_text("hello")

    assert typed.how == "blocked" and "closed" in typed.note and win.raised == []


def test_a_window_typed_into_without_a_target_isnt_kept_into_a_later_turn(win, monkeypatch):
    win.front = 10
    direct_input.type_text("hello")
    assert direct_input._target() == 10
    monkeypatch.setattr(direct_input, "_target_set_at",
                        direct_input.time.monotonic() - direct_input._ANCHOR_LIFETIME - 1)

    assert direct_input._target() is None


def test_no_handover_after_the_user_clicked(win):
    """Their click closed the Start menu; the rest isn't for what comes up."""
    win.add(95, "Windows.UI.Core.CoreWindow", 7)
    win.add(96, "Windows.UI.Core.CoreWindow", 8, cloaked=True)
    win.front = 95
    shown = direct_input.shown_windows()
    last_key_at = direct_input.time.monotonic() - 0.5     # the AI's last key, half a second ago
    win.idle = 100                                        # input 0.1 s ago: not the AI's
    win.windows[95]["cloaked"] = True
    win.windows[96]["cloaked"] = False
    win.front = 96

    assert direct_input.handed_over(95, shown, last_key_at) is None


def test_the_user_minimizing_a_window_during_a_click_doesnt_stop_the_target(win, monkeypatch):
    """Only a taskbar click is taken to have minimized it."""
    win.add(50, "OpusApp", 99)
    win.add(51, "OpusApp", 99)

    class Busy(Pattern):
        def Invoke(self, waitTime=0):
            win.minimized.add(50)                         # the user minimized their window
            win.front = 51
            return True

    win.front = 50
    _under_point(monkeypatch, Control("Button", "Save", patterns={INVOKE: Busy()}))

    direct_input.click(120, 110)

    assert direct_input._target() == 10


def test_a_menu_click_hands_over_only_to_a_window_it_opened(win, monkeypatch):
    direct_input.set_target(10)
    win.add(86, "#32768", 42)                             # an open menu
    win.add(50, "OpusApp", 99)                            # the user's
    win.at_point = 86
    win.front = 10

    class Switching(Pattern):
        def Invoke(self, waitTime=0):
            win.front = 50                                # the user switched meanwhile
            return True

    _under_point(monkeypatch, Control("MenuItem", "Copy", patterns={INVOKE: Switching()}))
    direct_input.click(300, 300)
    assert direct_input._target() == 10

    class Opening(Pattern):
        def Invoke(self, waitTime=0):
            win.add(87, "Notepad", 88)                    # "Open with Notepad"
            win.front = 87
            return True

    win.front = 10
    _under_point(monkeypatch, Control("MenuItem", "Open with", patterns={INVOKE: Opening()}))
    direct_input.click(300, 300)
    assert direct_input._target() == 87


def test_picking_from_a_dropdown_list_keeps_the_window_it_belongs_to(win, monkeypatch):
    """The list is a top-level window of its own that keys never go to."""
    direct_input.set_target(10)
    win.add(85, "ComboLBox", 42)
    win.at_point = 85
    _under_point(monkeypatch, Control("ListItem", "Large", parent=Control("List", handle=85),
                                      patterns={SELECT: Pattern(IsSelected=False)}))

    direct_input.click(300, 300)

    assert direct_input._target() == 10


def test_with_no_target_the_window_typed_into_is_where_the_next_keys_go(win):
    """The user switching windows between two steps doesn't move them."""
    win.front = 10
    direct_input.type_text("Best regards")
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 50                                        # the user went to their chat

    outcome = direct_input.press_key("enter")

    assert outcome.window == 10 and win.raised == [10]


def test_a_right_click_reads_what_it_needs_before_selecting_the_item(win, monkeypatch):
    """A list that rebuilds itself on selection leaves the item unreadable."""
    import types

    class Fragile(Control):
        broken = False

        @property
        def Name(self):
            if self.broken:
                raise RuntimeError("element not available")
            return "notes.txt"

        @Name.setter
        def Name(self, value):
            pass

    item = Fragile("ListItem")

    class Breaking(Pattern):
        def Select(self, waitTime=0):
            item.broken = True
            return super().Select(waitTime)

    item.patterns[SELECT] = Breaking(IsSelected=False)
    shown = []
    monkeypatch.setattr(direct_input, "_element3",
                        lambda control: types.SimpleNamespace(ShowContextMenu=lambda: shown.append(1)))
    monkeypatch.setattr(direct_input, "_menu_opened", lambda win, pid, before, timeout=1.5: True)
    _under_point(monkeypatch, item)

    outcome = direct_input.click(120, 110, button="right")

    assert outcome.done and outcome.how == "context menu" and outcome.note == "" and shown == [1]


def test_a_refused_press_and_then_a_slow_read_is_not_taken_for_a_press(win, monkeypatch):
    """Invoke refused (nothing happened), then the next read hangs: the
    click isn't lost as "done", it is made another way."""
    import threading
    release = threading.Event()

    class Refusing(Pattern):
        def Invoke(self, waitTime=0):
            raise COMError(-2147467263)                   # E_NOTIMPL

    class Slow(Control):
        def GetPattern(self, pattern_id):
            if pattern_id == TOGGLE:
                release.wait(5)
            return super().GetPattern(pattern_id)

    monkeypatch.setattr(direct_input, "_UIA_WAIT", 0.3)
    win.at_point = win.add(13, "Button", 42, root=10)
    _under_point(monkeypatch, Slow("Button", "OK", patterns={INVOKE: Refusing()}))
    try:
        outcome = direct_input.click(300, 250)
    finally:
        release.set()

    assert outcome.how == "window message"


def test_a_dialog_that_pops_up_midway_stops_the_typing(win):
    """The rest of the text wasn't meant for it."""
    win.front = 10
    direct_input.set_target(10)
    front = direct_input.keyboard_window().front
    win.add(70, "#32770", 42, owner=10)
    win.windows[10]["enabled"] = False
    win.front = 70

    assert direct_input.keyboard_reaches(front) is False


def test_the_window_behind_one_a_taskbar_click_minimized_is_not_the_target(win, monkeypatch):
    win.front = 10
    direct_input.set_target(10)
    win.add(90, "Shell_TrayWnd", 4)
    win.add(50, "OpusApp", 99)                            # the user's, next in line
    win.at_point = 90

    class Minimizing(Pattern):
        def Invoke(self, waitTime=0):
            win.minimized.add(10)
            win.front = 50
            return True

    _under_point(monkeypatch, Control("Button", "Notepad", pid=4, patterns={INVOKE: Minimizing()}))

    direct_input.click(500, 1060)

    assert direct_input._target() == 10


def test_a_target_on_another_virtual_desktop_is_not_brought_over(win):
    """Bringing it forward would switch the user's desktop. Text can still
    go straight into it."""
    win.add(12, "Edit", 42, root=10)
    win.focus[10] = 12
    direct_input.set_target(10)
    win.windows[10].update(cloaked=True, elsewhere=True)
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 50

    assert direct_input._target() == 10
    assert direct_input.type_text("hi").how == "text message"
    assert direct_input.press_key("ctrl+s").how == "blocked" and win.raised == []


def test_typing_follows_the_start_menu_into_search(win):
    win.add(95, "Windows.UI.Core.CoreWindow", 7)          # Start, open
    win.add(96, "Windows.UI.Core.CoreWindow", 8, cloaked=True)  # Search, put away
    win.front = 95
    shown = direct_input.shown_windows()                  # as typing starts
    win.windows[95]["cloaked"] = True                     # Search takes over
    win.windows[96]["cloaked"] = False
    win.front = 96

    assert direct_input.handed_over(95, shown) == 96
    assert direct_input._target() == 96


def test_a_click_into_the_users_window_that_closes_start_is_not_a_handover(win):
    """The user's window was on screen when typing started: the rest of
    the text isn't for it."""
    win.add(95, "Windows.UI.Core.CoreWindow", 7)
    win.add(50, "OpusApp", 99)
    win.front = 95
    shown = direct_input.shown_windows()
    win.windows[95]["cloaked"] = True
    win.front = 50

    assert direct_input.handed_over(95, shown) is None


def test_a_window_the_user_switched_to_is_not_a_handover(win):
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 50
    assert direct_input.handed_over(10) is None


def test_nothing_is_brought_forward_while_the_user_is_typing(win, monkeypatch):
    """Their next keys would land in it."""
    monkeypatch.setattr(direct_input, "_QUIET_WAIT", 0.05)
    win.idle = 0
    win.add(50, "Chrome_WidgetWin_1", 99)
    win.front = 50
    direct_input.set_target(10)

    outcome = direct_input.type_text("hello")

    assert outcome.how == "blocked" and "typing" in outcome.note and win.raised == []


def test_the_mouse_is_not_borrowed_while_the_user_is_using_it(win, monkeypatch):
    monkeypatch.setattr(direct_input, "_QUIET_WAIT", 0.05)
    win.idle = 0
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)

    outcome = direct_input.click(300, 250)

    assert not outcome.done and "moving the mouse" in outcome.note and win.mouse_clicks == []


def test_with_no_target_the_taskbar_gets_no_keys(win):
    win.add(90, "Shell_TrayWnd", 4)
    win.front = 90
    assert direct_input.type_text("hello").how == "blocked"


# ── Fifth review ────────────────────────────────────────────────────────

def test_the_next_key_waits_a_moment_for_a_window_the_last_one_opens(win, monkeypatch):
    """Ctrl+N in Word, the new document a second later: the next step's
    text goes there, not into the old one raised back over it."""
    win.front = 10
    direct_input.set_target(10)
    direct_input.follow_keys(direct_input.before_keys())

    def the_new_document_comes_up(seconds):
        win.add(81, "OpusApp", 42)
        win.front = 81

    monkeypatch.setattr(direct_input.time, "sleep", the_new_document_comes_up)

    outcome = direct_input.keyboard_window()

    assert outcome.how == "keyboard" and outcome.front == 81 and win.raised == []


def test_after_enter_in_a_text_field_the_next_key_doesnt_wait(win, monkeypatch):
    """Enter in a search box or a document rarely opens a window."""
    monkeypatch.setattr(direct_input, "_WAIT_FOR_SLOW_WINDOW", 60.0)
    win.front = 10
    direct_input.set_target(10)
    direct_input.follow_keys(direct_input.before_keys(into_text=True))

    started = direct_input.time.monotonic()
    outcome = direct_input.keyboard_window()

    assert outcome.front == 10 and direct_input.time.monotonic() - started < 5


def test_a_new_window_after_the_user_was_active_isnt_typed_into(win, monkeypatch):
    """It may be theirs: an Outlook message they opened during the model's
    round trip."""
    win.front = 10
    direct_input.set_target(10)
    direct_input.follow_keys(direct_input.before_keys())
    before, _when, system = direct_input._keys_pending
    monkeypatch.setattr(direct_input, "_keys_pending",
                        (before, direct_input.time.monotonic() - 2, system))   # the key, 2 s ago
    win.idle = 0                                          # the user is busy
    win.add(52, "rctrl_renwnd32", 99)
    win.front = 52

    typed = direct_input.type_text("second line")

    assert typed.how == "blocked" and "click" in typed.note
    assert direct_input._target() is None and win.raised == []


def test_an_app_started_from_the_run_box_is_waited_for(win):
    """Enter closed the Run box; Word takes a few seconds to start."""
    win.add(80, "#32770", 4)
    win.add(50, "OpusApp", 99)                            # the user's, behind
    win.front = 80
    direct_input.set_target(80)
    direct_input.follow_keys(direct_input.before_keys(into_text=True))
    del win.windows[80]
    win.add(83, "OpusApp", 84)
    fronts = iter([50] * 30 + [83])
    win.foreground = lambda: next(fronts, 83)

    assert direct_input.type_text("Hello").front == 83


def test_an_app_that_comes_up_after_the_wait_is_still_taken(win, monkeypatch):
    monkeypatch.setattr(direct_input, "_WAIT_FOR_STARTING_APP", 0.05)
    win.add(80, "#32770", 4)
    win.add(50, "OpusApp", 99)
    win.front = 80
    direct_input.set_target(80)
    direct_input.follow_keys(direct_input.before_keys(into_text=True))
    del win.windows[80]
    win.front = 50
    assert direct_input.type_text("Hello").how == "blocked"   # the user's window: not that

    win.add(83, "OpusApp", 84)
    win.front = 83
    assert direct_input.type_text("Hello").front == 83


def test_a_new_request_lets_typing_go_ahead_again(win):
    win.add(80, "#32770", 4)
    win.front = 80
    direct_input.set_target(80)
    del win.windows[80]
    win.add(50, "OpusApp", 99)
    win.front = 50
    assert direct_input.type_text("x").how == "blocked"

    direct_input.new_turn()

    assert direct_input.type_text("x").how == "keyboard"


# ── Kestrel's own windows over the spot ─────────────────────────────────

def test_the_run_border_doesnt_turn_clicks_into_real_ones(win, monkeypatch):
    """During an autonomous run a click-through border covers the screen,
    and Windows reports it at every point."""
    win.add(45, "Qt6QWindowToolSaveBits", os.getpid(), through=True, tool=True)
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)
    win.at_point = 45
    win.stack = [45, 30]
    win.child_at = lambda root, x, y: 31 if root == 30 else root
    invoke = Pattern()
    _under_point(monkeypatch, Control("Hyperlink", "Hacker News", pid=55, patterns={INVOKE: invoke}))

    outcome = direct_input.click(819, 78)

    assert outcome.how == "invoke" and invoke.calls == ["invoke"]
    assert win.mouse_clicks == [] and direct_input._target() == 30


def test_typing_goes_ahead_after_a_click_through_the_border(win, monkeypatch):
    win.add(45, "Qt6QWindowToolSaveBits", os.getpid(), through=True, tool=True)
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = 45
    win.stack = [45, 30]
    win.front = 30
    _under_point(monkeypatch, Control("Edit", "Address and search bar", pid=55,
                                      patterns={VALUE: Pattern(IsReadOnly=False)}))

    direct_input.click(819, 78)
    typed = direct_input.type_text("news.ycombinator.com")

    assert typed.how == "keyboard" and typed.front == 30


def test_a_real_click_brings_the_app_over_kestrels_window_first(win, monkeypatch):
    """Only the mouse reaches this app there, and Kestrel's window is on
    top of the spot: the app comes forward, then the click."""
    win.add(40, "Qt6QWindowIcon", os.getpid())
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)
    win.at_point = 40
    win.stack = [40, 30]
    win.child_at = lambda root, x, y: 31 if root == 30 else root

    def raised(hwnd):
        win.raised.append(hwnd)
        win.at_point = 31                                 # now on top there
        return True

    win.bring_to_front = raised

    outcome = direct_input.click(300, 250)

    assert outcome.how == "mouse" and win.raised == [30] and win.mouse_clicks == [(300, 250, "left")]


def test_no_real_click_when_kestrel_stays_on_top_of_the_spot(win, monkeypatch):
    """"Always on top": the click would land on Kestrel."""
    win.add(40, "Qt6QWindowIcon", os.getpid(), exe="python.exe")
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = 40
    win.stack = [40, 30]
    win.can_raise = False

    outcome = direct_input.click(300, 250)

    assert not outcome.done and "over that spot" in outcome.note and win.mouse_clicks == []


def test_another_apps_click_through_overlay_is_looked_through(win, monkeypatch):
    win.add(46, "GreenBorder", 77, through=True)
    win.at_point = 46
    win.stack = [46, 10]
    invoke = Pattern()
    monkeypatch.setattr(direct_input, "_control_in_window_at",
                        lambda root, x, y, budget=1.0: Control("Button", patterns={INVOKE: invoke})
                        if root == 10 else None)

    assert direct_input.click(300, 250).how == "invoke" and invoke.calls == ["invoke"]


def test_under_the_run_border_kestrels_window_still_covers_the_spot(win, monkeypatch):
    """The border on top, Kestrel's window under it, the app last: a real
    click would land on Kestrel's window, so the app comes forward first."""
    win.add(45, "Qt6QWindowToolSaveBits", os.getpid(), through=True, tool=True)
    win.add(40, "Qt6QWindowIcon", os.getpid())
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.add(31, "Chrome_RenderWidgetHostHWND", 55, root=30)
    win.at_point = 45
    win.stack = [45, 40, 30]
    win.child_at = lambda root, x, y: 31 if root == 30 else root

    def raised(hwnd):
        win.raised.append(hwnd)
        win.stack = [45, 30, 40]                          # the app now above Kestrel's window
        return True

    win.bring_to_front = raised

    outcome = direct_input.click(300, 250)

    assert outcome.how == "mouse" and win.raised == [30] and win.mouse_clicks == [(300, 250, "left")]


def test_kestrels_window_at_a_point_is_seen_past_the_border(win):
    win.add(45, "Qt6QWindowToolSaveBits", os.getpid(), through=True, tool=True)
    win.add(40, "Qt6QWindowIcon", os.getpid())
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.at_point = 45
    win.stack = [45, 40, 30]
    assert direct_input.kestrel_window_at(300, 250) is True
    win.stack = [45, 30, 40]
    assert direct_input.kestrel_window_at(300, 250) is False


def test_which_windows_a_mapping_run_may_take(win):
    win.add(30, "Chrome_WidgetWin_1", 55)
    win.add(40, "Qt6QWindowIcon", os.getpid())
    win.add(50, "Shell_TrayWnd", 66)
    win.add(51, "Progman", 66)
    win.add(52, "WorkerW", 66)
    win.add(53, "Floating palette", 77, tool=True)
    assert direct_input.can_be_mapped(30) is True
    assert not any(direct_input.can_be_mapped(h) for h in (40, 50, 51, 52, 53))


def test_a_hidden_panel_over_the_point_is_not_what_is_there(monkeypatch):
    """Walking down a covered window to the point: a panel that is off
    screen keeps its box, and isn't the control under the point."""

    class Box:
        def __init__(self, left, top, right, bottom):
            self.left, self.top, self.right, self.bottom = left, top, right, bottom

    class Node:
        def __init__(self, name, box, children=(), offscreen=False):
            self.Name, self.BoundingRectangle, self.IsOffscreen = name, box, offscreen
            self.children = list(children)
            self.next = None
            for a, b in zip(self.children, self.children[1:]):
                a.next = b

        def GetFirstChildControl(self):
            return self.children[0] if self.children else None

        def GetNextSiblingControl(self):
            return self.next

    button = Node("Save", Box(10, 10, 60, 30))
    hidden = Node("Old panel", Box(0, 0, 200, 200), offscreen=True)
    root = Node("Window", Box(0, 0, 400, 300), children=[Node("Pane", Box(0, 0, 200, 200), [button]), hidden])

    class FakeAuto:
        @staticmethod
        def ControlFromHandle(hwnd):
            return root

    monkeypatch.setattr(direct_input, "ui_automation", lambda multithreaded=False: FakeAuto)
    assert direct_input._control_in_window_at(10, 20, 20).Name == "Save"


def test_a_window_on_another_virtual_desktop_is_not_mapped(win):
    win.add(30, "Chrome_WidgetWin_1", 55, cloaked=True)
    assert direct_input.can_be_mapped(30) is False


def test_the_window_in_front_is_named_for_the_log(win):
    win.add(30, "Chrome_WidgetWin_1", 55, exe="chrome.exe")
    win.front = 30
    assert direct_input.describe_front() == "Chrome_WidgetWin_1 (chrome.exe)"


# ── The Win32 layer itself ──────────────────────────────────────────────

class _FakeFunction:
    def __init__(self, name, calls, results):
        self.name, self.calls, self.results = name, calls, results
        self.restype = self.argtypes = None

    def __call__(self, *args):
        self.calls.append((self.name, args))
        result = self.results.get(self.name, 1)
        return result(*args) if callable(result) else result


class _FakeDll:
    def __init__(self, calls, results, missing=()):
        self._calls, self._results, self._missing, self._made = calls, results, set(missing), {}

    def __getattr__(self, name):
        if name.startswith("_") or name in self._missing:
            raise AttributeError(name)
        return self._made.setdefault(name, _FakeFunction(name, self._calls, self._results))


def _win32_with(monkeypatch, results=None, missing=()):
    import ctypes
    calls = []
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: _FakeDll(calls, results or {}, missing),
                        raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    return direct_input._Win32(), calls


def test_the_win32_layer_declares_every_call_it_makes(monkeypatch):
    layer, _calls = _win32_with(monkeypatch)
    assert layer._dpi is True
    for name in ("WindowFromPoint", "RealChildWindowFromPoint", "SendMessageTimeoutW", "SendInput",
                 "GetGUIThreadInfo", "PostMessageW", "GetLastActivePopup", "GetWindow", "IsWindowEnabled"):
        assert layer._u._made[name].argtypes is not None, name


def test_without_the_windows_10_dpi_calls_it_still_builds(monkeypatch):
    layer, _calls = _win32_with(monkeypatch, missing=("GetWindowDpiAwarenessContext",))
    assert layer._dpi is False


def test_32_bit_windows_has_no_getwindowlongptr(monkeypatch):
    layer, _calls = _win32_with(monkeypatch, missing=("GetWindowLongPtrW",))
    assert layer._get_long.name == "GetWindowLongW"


def test_key_messages_carry_the_scan_code_and_the_transition_bits(monkeypatch):
    layer, calls = _win32_with(monkeypatch, results={"MapVirtualKeyW": 0x53})
    assert layer.post_key(12, 0x2E, True)
    posted = [args for name, args in calls if name == "PostMessageW"]
    down, up = posted[0][3], posted[1][3]
    assert posted[0][1] == direct_input.WM_KEYDOWN and posted[1][1] == direct_input.WM_KEYUP
    assert down == 1 | 0x53 << 16 | 1 << 24
    assert up == down | 1 << 30 | 1 << 31


@pytest.mark.parametrize("cls, accepted", [("RichEdit20W", True), ("Edit", False)])
def test_the_digits_only_bit_means_something_else_in_a_richedit(monkeypatch, cls, accepted):
    """0x2000 is ES_NUMBER in an Edit and ES_DISABLENOSCROLL in a RichEdit."""
    def class_name(hwnd, buffer, size):
        buffer.value = cls
        return len(cls)

    layer, _calls = _win32_with(monkeypatch, results={"GetWindowLongPtrW": direct_input.ES_NUMBER,
                                                      "GetClassNameW": class_name})
    assert layer.text_box_accepts(12, "abc") is accepted


def test_start_and_search_are_on_this_desktop_when_the_check_fails(monkeypatch):
    """The virtual desktop call may not know the shell's own windows."""
    import types

    def refuse(hwnd):
        raise OSError("TYPE_E_ELEMENTNOTFOUND")

    monkeypatch.setattr(direct_input, "_desktops",
                        lambda: types.SimpleNamespace(IsWindowOnCurrentVirtualDesktop=refuse))

    def layer_for(cls):
        def class_name(hwnd, buffer, size):
            buffer.value = cls
            return len(cls)
        layer, _calls = _win32_with(monkeypatch, results={"GetClassNameW": class_name,
                                                          "GetWindowLongPtrW": 0})
        return layer

    assert layer_for("Windows.UI.Core.CoreWindow").on_another_desktop(95) is False
    assert layer_for("Notepad").on_another_desktop(10) is True
