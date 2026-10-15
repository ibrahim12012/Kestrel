# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/discovery/accessibility.py
==============================
Reading a window's structure from the OS accessibility tree, so the
mapper can enumerate an application's controls without a model call per
element.

This is not a replacement for vision. ai/main.py's
get_position_object_and_click works on anything visible and stays what
the chat and autonomous loops use; it costs a round trip per click and
cannot say what else is on the screen. This is for the mapper's
different job, and it is also what runtime.py falls back FROM to vision
when a path no longer resolves.

AccessibilityBackend is the seam. Nothing in mapper.py imports
uiautomation or pywin32 directly, which is what lets the traversal be
tested with an in-memory fake on any machine.

Only Windows UI Automation is implemented. get_backend raises
DiscoveryError elsewhere rather than silently doing nothing.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field

from ai.discovery.errors import DiscoveryError
from ai.log import log as print
from ai import direct_input, safety
from ai.log import log_debug

# ── Optional: only present on Windows, and only if the user's install
# pulled in the extra dependency (see requirements.txt). Guarded exactly
# like psutil/pywin32 in ai/actions.py - everything in this module that
# doesn't touch WindowsUIABackend still imports fine without it.
try:
    import uiautomation as auto
except ImportError:
    auto = None


# ══════════════════════════════════════════════════════════════════
# Element model
# ══════════════════════════════════════════════════════════════════

# Control types the mapper treats as "interactable" - worth adding to a
# node's discovered_actions. Deliberately narrow: a screen's every static
# label/image/group would otherwise flood every node with actions that
# can never lead anywhere, making the tree far noisier (and the mapping run far
# slower) without adding any real navigation value.
INTERACTABLE_CONTROL_TYPES = frozenset({
    "Button", "CheckBox", "RadioButton", "Hyperlink", "MenuItem",
    "TabItem", "ListItem", "TreeItem", "SplitButton", "ComboBox",
    "Edit", "Slider", "Spinner",
})

# Subset of the above that take a typed value rather than just being
# invoked - the mapper inputs a mock value into these to "expose hidden
# downstream states" per the spec, instead of just clicking them.
INPUT_CONTROL_TYPES = frozenset({"Edit", "ComboBox", "Spinner"})


@dataclass
class AccessibilityElement:
    """
    One node read from the live accessibility tree. Deliberately a plain,
    backend-independent shape - WindowsUIABackend translates a real
    uiautomation Control into this; a test's FakeAccessibilityBackend
    just builds these directly, no uiautomation needed.
    """
    id: str                 # stable path/AutomationId used to re-find this element later
    name: str                # accessible name / label, as a human would read it
    control_type: str        # "Button", "Edit", "Hyperlink", ...
    bounding_box: dict = field(default_factory=lambda: {"x": 0, "y": 0, "w": 0, "h": 0})
    is_enabled: bool = True
    is_offscreen: bool = False
    # The raw AutomationId when the app supplied one, separate from `id`
    # because `id` falls back to a positional path when it didn't. The
    # mapper prefers this for its visited set: an AutomationId survives
    # relayout, a positional path does not.
    automation_id: str = ""

    @property
    def is_input(self) -> bool:
        return self.control_type in INPUT_CONTROL_TYPES

    @property
    def is_interactable(self) -> bool:
        return self.control_type in INTERACTABLE_CONTROL_TYPES and self.is_enabled and not self.is_offscreen


# ══════════════════════════════════════════════════════════════════
# Backend interface
# ══════════════════════════════════════════════════════════════════

class AccessibilityBackend:
    """
    Everything the mapper needs from a live accessibility tree. Subclass
    and implement every method - there's no default behavior here on
    purpose, so a half-implemented backend fails loudly (NotImplementedError)
    rather than silently doing nothing.
    """

    def get_root(self) -> AccessibilityElement:
        """The current screen's root element (the foreground window/view)."""
        raise NotImplementedError

    def list_interactables(self, element: AccessibilityElement) -> list[AccessibilityElement]:
        """Every interactable descendant of `element` (see INTERACTABLE_CONTROL_TYPES)."""
        raise NotImplementedError

    def activate(self, element: AccessibilityElement) -> bool:
        """Invoke/click `element`. Returns True if it succeeded."""
        raise NotImplementedError

    def hover(self, element: AccessibilityElement) -> bool:
        """
        Move the cursor over `element` WITHOUT clicking/activating it -
        used instead of activate() for actions flagged destructive/risky
        (see ai.safety.is_destructive_label), so the mapper can still
        surface whatever a real hover reveals (a tooltip with more detail
        than the element's static accessible name, a hover-only visual
        state) without ever triggering the action itself. Returns True if
        the cursor was moved successfully; a False/exception here should
        never abort a mapping run - hovering is a best-effort enrichment,
        not something the traversal depends on.
        """
        raise NotImplementedError

    def input_value(self, element: AccessibilityElement, value: str) -> bool:
        """Type `value` into `element` (a text box, combo box, ...). Returns True on success."""
        raise NotImplementedError

    def field_text(self, element: AccessibilityElement) -> "str | None":
        """What a text field holds now, or None when that can't be read.
        The mapper doesn't type into a field that holds something."""
        return None

    def restore_window(self) -> None:
        """Put the target window back the way it was before the run
        (maximized or not). Nothing by default."""

    def show_if_minimized(self) -> None:
        """Show the target window again if it is minimized, so its
        controls can be found. Nothing by default."""

    def find_back_control(self, element: AccessibilityElement) -> "AccessibilityElement | None":
        """
        Search `element`'s screen for a back button, close arrow, or nav
        breadcrumb - anything that structurally backs out of this screen.
        Returns None if nothing was found (the mapper then falls back to
        ESCAPE - see BacktrackAction in ai/discovery/tree.py).
        """
        raise NotImplementedError

    def send_key(self, key: str) -> None:
        """Send a single named key (e.g. "ESCAPE") to the foreground window."""
        raise NotImplementedError

    def capture_state(self) -> AccessibilityElement:
        """
        The root element of whatever is now on screen - called right
        after activate()/input_value() to see what changed. Usually just
        get_root() again, but kept as its own method since a real backend
        may need to wait briefly for the new screen to settle first.
        """
        raise NotImplementedError

    def find_by_path(self, accessibility_path: str) -> "AccessibilityElement | None":
        """
        Re-resolve a previously-seen element by its stable id/path, live,
        right now. Used by ai/discovery/runtime.py at MACRO PLAYBACK time
        (not during the mapping run itself) - the UI may have changed since it
        was mapped, so this can legitimately return None.
        """
        raise NotImplementedError


# ══════════════════════════════════════════════════════════════════
# Windows UI Automation backend
# ══════════════════════════════════════════════════════════════════

# Back/close-affordance names checked (case-insensitively, substring) when
# looking for a structural back control - covers the large majority of
# apps without needing per-app configuration. Order doesn't matter; every
# interactable descendant is checked against the whole set.
_BACK_CONTROL_NAMES = (
    "back", "précédent", "précédente", "retour", "close", "fermer",
    "cancel", "annuler", "dismiss", "×", "✕",
)


class WindowsUIABackend(AccessibilityBackend):
    """
    Backed by Windows UI Automation via the `uiautomation` package, which
    wraps the real UIA COM interfaces - the same accessibility layer
    screen readers use, giving exact bounding boxes and control types
    without any visual inference.

    Constructed for one target window at a time (see get_backend()) -
    get_root()/capture_state() always mean "this window's current
    state",
    not "whatever happens to be focused system-wide", so a mapping run can't
    accidentally wander into an unrelated app if focus shifts mid-run.
    """

    def __init__(self, window_control):
        self._window = window_control
        # Its top-level window handle: where clicks and keys go when the
        # window isn't in front (ai/direct_input.py).
        try:
            self._hwnd = int(window_control.NativeWindowHandle or 0) or None
        except Exception:
            self._hwnd = None
        # Which window this is, for the saved map and the Mapping page.
        try:
            self.window_title = (window_control.Name or "").strip()
        except Exception:
            self.window_title = ""
        self.program = direct_input.program_of(self._hwnd)
        # How the window was shown before the run maximized it.
        self._state_before = None

    def take_window(self) -> None:
        """Bring the window forward and maximize it, so every run starts
        from the same layout, remembering how it was for restore_window."""
        self._state_before = direct_input.remember_window(self._hwnd)
        _bring_to_front_and_maximize(self._window)

    def restore_window(self) -> None:
        if self._state_before is not None:
            direct_input.put_back(self._hwnd, self._state_before)

    def show_if_minimized(self) -> None:
        direct_input.unminimize(self._hwnd)

    # -- element translation -----------------------------------------

    def _to_element(self, control, tree_path: str = "") -> AccessibilityElement:
        try:
            rect = control.BoundingRectangle
            box = {"x": rect.left, "y": rect.top, "w": max(0, rect.right - rect.left), "h": max(0, rect.bottom - rect.top)}
        except Exception as e:
            # A control that will not report its position still gets an
            # element, with a zero box. Logged at debug because on a busy
            # window this can fire for a lot of controls at once, and it
            # is only interesting when something is being diagnosed.
            log_debug(f"[accessibility] No bounding box for a control: {e}")
            box = {"x": 0, "y": 0, "w": 0, "h": 0}

        control_type = control.ControlTypeName.replace("Control", "") if control.ControlTypeName else "Unknown"
        name = (control.Name or "").strip()
        auto_id = (getattr(control, "AutomationId", "") or "").strip()

        # An AutomationId is the app telling us what this control is, so
        # it wins. When it is blank (routine in Electron and web-hosted
        # UI) the fallback used to be type+name+x,y, which collided on
        # every pair of unlabelled icon buttons: two different actions
        # then resolved to the same physical control and the mapper
        # clicked it twice. A positional tree path cannot collide.
        path = auto_id or tree_path or f"{control_type}:{name}:{box['x']},{box['y']}"

        return AccessibilityElement(
            id=path,
            name=name,
            control_type=control_type,
            bounding_box=box,
            is_enabled=bool(getattr(control, "IsEnabled", True)),
            is_offscreen=bool(getattr(control, "IsOffscreen", False)),
            automation_id=auto_id,
        )

    def _walk(self, max_depth: int = 12):
        """
        Every control under the target window, paired with a positional
        path like "Pane[0]/Group[1]/Button[3]" built from its place among
        its siblings. WalkControl is pre-order, so by the time a control
        at depth d is yielded its ancestors already sit in `segments`.

        Rooted at the window for every caller, so a path means the same
        thing whoever generated it.
        """
        counters: list = []     # one {control_type: next_index} per level
        segments: list = []     # the path of the control most recently yielded
        for control, depth in auto.WalkControl(self._window, includeTop=False, maxDepth=max_depth):
            try:
                control_type = control.ControlTypeName or "Unknown"
            except Exception as e:
                log_debug(f"[accessibility] _walk ignored: {e}")
                continue
            del counters[depth:]
            del segments[depth - 1:]
            while len(counters) < depth:
                counters.append({})
            level = counters[depth - 1]
            index = level.get(control_type, 0)
            level[control_type] = index + 1
            segments.append(f"{control_type}[{index}]")
            yield control, "/".join(segments)

    def _find_control_by_path(self, root_control, path: str):
        if not path:
            return None
        try:
            if self._to_element(self._window).id == path:
                return self._window
        except Exception as e:
            log_debug(f"[accessibility] _find_control_by_path ignored: {e}")
        for control, tree_path in self._walk():
            try:
                el = self._to_element(control, tree_path)
            except Exception as e:
                log_debug(f"[accessibility] _find_control_by_path ignored: {e}")
                continue
            if el.id == path:
                return control
        return None

    # -- interface ------------------------------------------------------

    def get_root(self) -> AccessibilityElement:
        return self._to_element(self._window)

    def list_interactables(self, element: AccessibilityElement) -> list[AccessibilityElement]:
        found = []
        for child, tree_path in self._walk(max_depth=10):
            try:
                child_el = self._to_element(child, tree_path)
            except Exception as e:
                log_debug(f"[accessibility] list_interactables ignored: {e}")
                continue
            if child_el.is_interactable:
                found.append(child_el)
        return found

    def activate(self, element: AccessibilityElement) -> bool:
        """What a click on it does, without the user's mouse when the app
        allows it: an invoke, toggle, select or expand through UI
        Automation, else a posted click, else the mouse borrowed and put
        back (ai/direct_input.py). It used to be SetFocus() and a real
        click every time, which pulled the window to the front and took
        the pointer for the whole run."""
        control = self._find_control_by_path(self._window, element.id)
        if control is None:
            return False
        try:
            outcome = direct_input.press_control(control, window=self._hwnd)
        except Exception as e:
            print(f"[discovery] activate({element.id!r}) failed: {e}")
            return False
        if not outcome.done:
            if not outcome.stopped:
                print(f"[discovery] activate({element.id!r}) not done: {outcome.note}")
            return False
        time.sleep(0.4)
        return True

    def hover(self, element: AccessibilityElement) -> bool:
        """Nothing. This moved the pointer onto the control so a tooltip
        could show, and nothing ever read the tooltip: the pointer is the
        user's."""
        return True

    def input_value(self, element: AccessibilityElement, value: str) -> bool:
        control = self._find_control_by_path(self._window, element.id)
        if control is None:
            return False
        try:
            # Set straight in, when the field allows it: no focus, no keys.
            # On the same thread, with the same time limit, as a click: a
            # change handler that opens a dialog doesn't hold the run.
            outcome = direct_input.set_value(control, value, window=self._hwnd)
            if outcome.stopped:
                return False
            if outcome.done:
                time.sleep(0.2)
                return True
            # Otherwise click into it and type, as a person would, with the
            # checks the AI's own typing has (ai/actions.py): into this
            # window only, stopping if another one comes in front.
            if not direct_input.press_control(control, window=self._hwnd).done:
                return False
            from ai.actions import _type_text
            if not _type_text(value):
                return False
            time.sleep(0.2)
            return True
        except Exception as e:
            print(f"[discovery] input_value({element.id!r}) failed: {e}")
            return False

    def field_text(self, element: AccessibilityElement) -> "str | None":
        """The field's text through UI Automation's value pattern. None
        when it has none (a drop-down list, a number box), won't say (a
        password box) or can't be typed into (a drop-down list with a
        read-only value): the mapper leaves those alone."""
        control = self._find_control_by_path(self._window, element.id)
        if control is None:
            return None
        try:
            pattern = control.GetPattern(auto.PatternId.ValuePattern)
            if not pattern or pattern.IsReadOnly:
                return None
            return pattern.Value or ""
        except Exception as e:
            log_debug(f"[accessibility] field_text({element.id!r}) unreadable: {e}")
            return None

    def find_back_control(self, element: AccessibilityElement) -> "AccessibilityElement | None":
        for candidate in self.list_interactables(element):
            lowered = candidate.name.strip().lower()
            if not any(needle in lowered for needle in _BACK_CONTROL_NAMES):
                continue
            # "close"/"×"/"✕" are in _BACK_CONTROL_NAMES because they're
            # legitimate in-app dismiss/back buttons - but they're ALSO
            # very commonly the exact accessible name Windows gives a
            # window's own title-bar close button. Name-matching alone
            # can't tell those apart; position can (see
            # ai.safety.is_likely_window_chrome_button's own docstring
            # for the full reasoning) - skip a name match that's
            # actually sitting where the real window controls live,
            # rather than returning it and letting _backtrack() (mapper.py)
            # activate it for real, which would close the entire target
            # window instead of navigating back within it.
            if safety.is_likely_window_chrome_button(candidate.bounding_box, element.bounding_box):
                continue
            return candidate
        return None

    def send_key(self, key: str) -> None:
        try:
            # Posted to the window's focused control when it isn't in
            # front and can take it that way; the keyboard otherwise, once
            # the window is in front (ai/actions.py).
            from ai.actions import _press_key
            _press_key(key, window=self._hwnd)
        except Exception as e:
            print(f"[discovery] send_key({key!r}) failed: {e}")

    def capture_state(self) -> AccessibilityElement:
        # A flat sleep samples whatever happens to be on screen at that
        # instant, which mid-animation or mid-load is neither the old
        # screen nor the new one. Every such sample used to become its
        # own node and get explored from scratch. Wait for two identical
        # readings instead, with a ceiling so a genuinely busy app cannot
        # stall the run.
        previous = None
        deadline = time.time() + 2.5
        while True:
            time.sleep(0.25)
            try:
                shape = tuple(sorted(
                    f"{el.control_type}:{el.name}" for el in self.list_interactables(self.get_root())
                ))
            except Exception as e:
                log_debug(f"[accessibility] capture_state ignored: {e}")
                break
            if shape == previous or time.time() >= deadline:
                break
            previous = shape
        return self.get_root()

    def find_by_path(self, accessibility_path: str) -> "AccessibilityElement | None":
        """
        The element at `accessibility_path`, with its id set to the path
        it was found by.

        That last part is the whole point, and it was the bug. This
        returned `self._to_element(control)` with no tree_path, and
        _to_element falls back through `auto_id or tree_path or
        "Type:Name:x,y"` - so for any control WITHOUT an AutomationId the
        returned element's id was the coordinate string, not the
        positional path just used to find it.

        Callers then hand that element straight to activate(), which
        re-resolves by element.id. A coordinate-form id matches nothing
        that _find_control_by_path ever produces, so activate() returned
        False and the caller fell through to clicking the stored pixel
        coordinates from mapping time.

        Which means: for every application that does not publish
        AutomationIds - Electron apps, anything web-hosted, which is
        exactly the case the positional-path fallback exists for -
        replay was silently running on stale absolute coordinates
        instead of the accessibility tree, on every single action. It
        looked like it was working right up until a window moved.
        """
        control = self._find_control_by_path(self._window, accessibility_path)
        if control is None:
            return None
        element = self._to_element(control, accessibility_path)
        # _to_element prefers an AutomationId over the path it was given.
        # For re-resolution the requested path is what must round-trip,
        # so it is restored here.
        element.id = accessibility_path
        return element


# ══════════════════════════════════════════════════════════════════
# Factory
# ══════════════════════════════════════════════════════════════════

@dataclass
class WindowCandidate:
    """An open top-level window get_backend could map."""
    control: object
    title: str
    program: str        # notepad.exe, or "" when it can't be read
    order: int = 0      # place from the top of the window stack
    in_front: bool = False


def window_match_score(app_name: str, title: str, program: str) -> int:
    """
    How well a window matches the name someone typed. 0 is no match.

    The program's own name is the best evidence (notepad.exe for
    "notepad"), then the whole title, then the name's words appearing
    together in the title ("Visual Studio Code" in "main.py - Visual
    Studio Code"), then a plain substring of four letters or more. The
    window class used to count too, and a short name like "code" then
    matched whatever window happened to come first.
    """
    needle = " ".join((app_name or "").lower().split())
    if not needle:
        return 0
    title = " ".join((title or "").lower().split())
    program = (program or "").strip().lower()
    stem = program[:-4] if program.endswith(".exe") else program
    if needle in (program, stem):
        return 100
    if title == needle:
        return 90
    needle_words = re.findall(r"\w+", needle)
    if stem and stem in needle_words:
        return 80
    title_words = re.findall(r"\w+", title)
    count = len(needle_words)
    if count and any(title_words[i:i + count] == needle_words
                     for i in range(len(title_words) - count + 1)):
        return 70
    if len(needle) >= 4 and needle in title:
        return 40
    if len(needle) >= 4 and needle in stem:
        return 30
    return 0


def pick_window(app_name: str, candidates: list) -> "WindowCandidate | None":
    """The best match: highest score, then one whose title has the name
    in it too (File Explorer's windows over another explorer.exe window),
    then the window in front, then the one nearest the top of the stack.
    None when nothing matches."""
    best, best_key = None, None
    for candidate in candidates:
        score = window_match_score(app_name, candidate.title, candidate.program)
        # A window with no title is taken only by its program's own name.
        if score <= 0 or (not candidate.title and score < 80):
            continue
        titled = window_match_score(app_name, candidate.title, "") > 0
        key = (score, titled, candidate.in_front, -candidate.order)
        if best_key is None or key > best_key:
            best, best_key = candidate, key
    return best


def _no_window_message(app_name: str, candidates: list) -> str:
    named = [c for c in candidates if c.title][:8]
    if not named:
        return f"No open window matches {app_name!r}, and no other window is open. Open the app first."
    listed = "; ".join(f"{c.title} ({c.program})" if c.program else c.title for c in named)
    return (f"No open window matches {app_name!r}. Open the app first, or use the "
            f"name of its program or its title bar. Open windows: {listed}.")


def get_backend(app_name: str, take: bool = True) -> AccessibilityBackend:
    """
    Find app_name's window and return a backend scoped to it. Raises
    DiscoveryError (never returns None) if no accessibility backend is
    available on this platform, the package isn't installed, or no
    window matches (the message lists the open ones).

    take: bring the window forward and maximize it, as a mapping run
    starts. Pressing one mapped control (ai/actions.py's
    tool_use_mapped_control) leaves the window as it is.

    Kestrel's own windows are never picked: mapping "Kestrel" would have
    the run click through the app that is running it. Neither are the
    taskbar, the desktop, tool windows or windows on another virtual
    desktop, and an untitled window only by its program's name.
    """
    if sys.platform != "win32":
        raise DiscoveryError(
            "UI discovery currently requires Windows (UI Automation). "
            "macOS/Linux accessibility backends aren't implemented yet."
        )
    if auto is None:
        raise DiscoveryError(
            "UI discovery needs the optional 'uiautomation' package "
            "(pip install uiautomation) - it isn't installed."
        )

    # UI Automation has to be called from a thread with COM set up, and a
    # mapping run is on a worker thread.
    direct_input.ui_automation()
    try:
        windows = list(auto.GetRootControl().GetChildren())
    except Exception as e:
        raise DiscoveryError(f"Could not list the open windows: {e}") from e

    front = direct_input.foreground_window()
    candidates = []
    for order, window in enumerate(windows):
        try:
            hwnd = int(window.NativeWindowHandle or 0) or None
            title = (window.Name or "").strip()
        except Exception as e:
            log_debug(f"[accessibility] a window skipped: {e}")
            continue
        # The taskbar, the desktop, tool windows, windows on another
        # virtual desktop and Kestrel's own are never what is meant: the
        # taskbar is an explorer.exe window too, and on top of every other.
        if not hwnd or not direct_input.can_be_mapped(hwnd):
            continue
        candidates.append(WindowCandidate(
            control=window, title=title, program=direct_input.program_of(hwnd),
            order=order, in_front=(hwnd == front),
        ))

    chosen = pick_window(app_name, candidates)
    if chosen is None:
        raise DiscoveryError(_no_window_message(app_name, candidates))
    backend = WindowsUIABackend(chosen.control)
    backend.window_title = chosen.title
    backend.program = chosen.program
    if take:
        print(f"[discovery] Mapping the window {chosen.title!r} ({chosen.program or 'program unknown'}).")
        backend.take_window()
    return backend


def _bring_to_front_and_maximize(window) -> None:
    """
    Foregrounds and maximizes the target window before a mapping run
    starts exploring it - requested so every run starts from the same
    known, fully-visible layout rather than whatever size/position the
    window happened to be left in. Best-effort and non-fatal: mapping can
    still proceed against a window that's merely in the foreground but
    not maximized (some windows genuinely can't be, e.g. fixed-size
    dialogs), so a failure here is logged and swallowed rather than
    raised - it would be a strange failure mode for "the window didn't
    maximize" to block "explore this window" entirely. The mapper puts
    the window back the way it was when the run ends (restore_window).
    """
    try:
        window.SetActive()
    except Exception as e:
        print(f"[discovery] Couldn't bring {getattr(window, 'Name', '?')!r} to the foreground: {e}")
    try:
        window.SetWindowVisualState(auto.WindowVisualState.Maximized)
        time.sleep(0.3)  # let the resize/layout actually finish before anything reads bounding boxes
    except Exception as e:
        print(f"[discovery] Couldn't maximize {getattr(window, 'Name', '?')!r} (continuing anyway): {e}")
