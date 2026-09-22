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

import sys
import time
from dataclasses import dataclass, field

from ai.discovery.errors import DiscoveryError
from ai.log import log as print
from ai import safety
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
        control = self._find_control_by_path(self._window, element.id)
        if control is None:
            return False
        try:
            control.SetFocus()
            if hasattr(control, "Click"):
                control.Click(simulateMove=False)
            else:
                control.GetInvokePattern().Invoke()
            time.sleep(0.4)
            return True
        except Exception as e:
            print(f"[discovery] activate({element.id!r}) failed: {e}")
            return False

    def hover(self, element: AccessibilityElement) -> bool:
        control = self._find_control_by_path(self._window, element.id)
        if control is None:
            return False
        try:
            # MoveCursorToMyCenter - same cursor move Click() does
            # internally before it clicks - WITHOUT the click that
            # follows it in activate() above. This is deliberately not
            # SetFocus()+Click(): the whole point here is to trigger
            # whatever a real hover reveals (a tooltip, a hover-only
            # visual state) without ever invoking the element.
            control.MoveCursorToMyCenter(simulateMove=True)
            time.sleep(0.3)  # give a hover-triggered tooltip time to appear
            return True
        except Exception as e:
            print(f"[discovery] hover({element.id!r}) failed: {e}")
            return False

    def input_value(self, element: AccessibilityElement, value: str) -> bool:
        control = self._find_control_by_path(self._window, element.id)
        if control is None:
            return False
        try:
            control.SetFocus()
            if hasattr(control, "GetValuePattern"):
                pattern = control.GetValuePattern()
                pattern.SetValue(value)
            else:
                control.SendKeys(value)
            time.sleep(0.2)
            return True
        except Exception as e:
            print(f"[discovery] input_value({element.id!r}) failed: {e}")
            return False

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
            auto.SendKeys("{" + key + "}")
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

def get_backend(app_name: str) -> AccessibilityBackend:
    """
    Find app_name's foreground/top-level window and return a backend
    scoped to it. Raises DiscoveryError (never returns None) if no
    accessibility backend is available on this platform, the package
    isn't installed, or no matching window could be found.
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

    needle = app_name.strip().lower()
    try:
        desktop = auto.GetRootControl()
        for window in desktop.GetChildren():
            name = (window.Name or "").lower()
            class_name = (getattr(window, "ClassName", "") or "").lower()
            if needle in name or needle in class_name:
                _bring_to_front_and_maximize(window)
                return WindowsUIABackend(window)
    except Exception as e:
        raise DiscoveryError(f"Could not enumerate windows: {e}") from e

    raise DiscoveryError(
        f"No open window matching {app_name!r} was found. Make sure the "
        f"app is running and its window title contains that name."
    )


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
    maximize" to block "explore this window" entirely.
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
