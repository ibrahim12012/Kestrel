# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/discovery/runtime.py
=========================
Called by the atomic functions ai/discovery/compiler.py generates (see
ai/generated/<App>_actions.py) - never by hand. Two entry points,
resolve_and_activate / resolve_and_input, both follow the same pattern
the rest of the app already uses for a moved/changed UI target (compare
ai/actions.py's click_at_target_or_position, which tries vision first and
falls back to the originally recorded (x, y)):

  1. Re-resolve the element live, right now, by the accessibility path
     recorded at mapping time.
  2. If that path no longer resolves (the UI changed since the mapping
  run - a redesign, an app update, a different window state), fall back
  to
     clicking the CENTER of the bounding box recorded at mapping time.

This is "accessibility anchor resolution and execution verification"
(spec section 5) - bounding boxes are a fallback snapping target, not the
primary mechanism, exactly as spec section 4's "Parameter Generalization"
describes.
"""

from __future__ import annotations

import time

from ai.discovery.accessibility import AccessibilityElement, get_backend
from ai.discovery.errors import DiscoveryError
from ai.log import log as print


def _center(fallback_box: dict) -> "tuple[int, int]":
    """The middle of a box recorded at mapping time, in UI Automation's
    screen pixels: the coordinates ai/direct_input.py clicks in. (This
    used to be converted to pyautogui's, with a ratio that compared the
    whole desktop to the main screen and put every fallback click at half
    its position on a two-screen setup.)"""
    x = int(fallback_box.get("x", 0) + fallback_box.get("w", 0) / 2)
    y = int(fallback_box.get("y", 0) + fallback_box.get("h", 0) / 2)
    return x, y


def _fallback_click(fallback_box: dict) -> str:
    from ai import direct_input
    from ai.actions import _check_emergency_stop

    refusal = _check_emergency_stop()
    if refusal:
        return refusal
    x, y = _center(fallback_box)
    outcome = direct_input.click(x, y)
    if outcome.stopped:
        return _check_emergency_stop() or "Stopped."
    if not outcome.done:
        return f"Accessibility path no longer resolved, and the fallback click at ({x}, {y}) couldn't be made: {outcome.note}."
    return f"Accessibility path no longer resolved - clicked fallback position ({x}, {y})."


def _fallback_type(fallback_box: dict, text_value: str) -> str:
    from ai import direct_input
    from ai.actions import _check_emergency_stop, _handle_pyautogui_exception, _type_text

    refusal = _check_emergency_stop()
    if refusal:
        return refusal
    x, y = _center(fallback_box)
    outcome = direct_input.click(x, y)
    if outcome.stopped:
        return _check_emergency_stop() or "Stopped."
    if not outcome.done:
        return f"Accessibility path no longer resolved, and the field at ({x}, {y}) couldn't be clicked: {outcome.note}."
    try:
        time.sleep(0.15)
        typed = _type_text(text_value)
        if not typed:
            return (f"Accessibility path no longer resolved - clicked fallback position ({x}, {y}), "
                    f"but the value wasn't typed: {typed.note}.")
        return f"Accessibility path no longer resolved - clicked fallback position ({x}, {y}) and typed the value."
    except Exception as e:
        handled = _handle_pyautogui_exception(e)
        return handled or f"Fallback input failed: {e}"


def resolve_and_activate(app_name: str, accessibility_path: str, fallback_box: dict) -> str:
    """Re-resolve `accessibility_path` in `app_name`'s live window and click it; bounding-box fallback on failure."""
    from ai.actions import _check_emergency_stop
    refusal = _check_emergency_stop()
    if refusal:
        return refusal

    try:
        backend = get_backend(app_name, take=False)
        element = backend.find_by_path(accessibility_path)
        if element is not None and backend.activate(element):
            return f"Activated {accessibility_path!r} via accessibility anchor."
    except DiscoveryError as e:
        print(f"[discovery.runtime] Backend unavailable for {app_name!r}: {e}")
    except Exception as e:
        print(f"[discovery.runtime] Accessibility activation of {accessibility_path!r} failed: {e}")

    return _fallback_click(fallback_box)


def resolve_and_input(app_name: str, accessibility_path: str, text_value: str, fallback_box: dict) -> str:
    """Re-resolve `accessibility_path` in `app_name`'s live window and type into it; bounding-box fallback on failure."""
    from ai.actions import _check_emergency_stop
    refusal = _check_emergency_stop()
    if refusal:
        return refusal

    try:
        backend = get_backend(app_name, take=False)
        element = backend.find_by_path(accessibility_path)
        if element is not None and backend.input_value(element, text_value):
            return f"Typed into {accessibility_path!r} via accessibility anchor."
    except DiscoveryError as e:
        print(f"[discovery.runtime] Backend unavailable for {app_name!r}: {e}")
    except Exception as e:
        print(f"[discovery.runtime] Accessibility input on {accessibility_path!r} failed: {e}")

    return _fallback_type(fallback_box, text_value)
