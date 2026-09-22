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


def _physical_to_logical(x: int, y: int) -> "tuple[int, int]":
    """
    Convert a UIA coordinate to one pyautogui can use.

    UIA's BoundingRectangle is in physical device pixels. pyautogui's
    mouse functions take logical, DPI-scaled coordinates. On a display
    at 100% those are the same number and the difference never shows;
    at 150% they differ by half again, so a fallback click computed
    straight from a bounding box landed roughly 1.5x further from the
    origin than intended - further off the further right and further
    down the target was.

    The ratio is measured rather than read from a scale-factor API:
    pyautogui.size() is the logical screen size and the virtual screen
    metric is the physical one, so their quotient is exactly the
    conversion pyautogui itself will apply, including on a mixed-DPI
    multi-monitor setup where a single reported scale factor would be
    wrong for at least one screen.
    """
    import pyautogui

    try:
        import ctypes
        user32 = ctypes.windll.user32
        SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
        physical_w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        physical_h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        logical_w, logical_h = pyautogui.size()
        if physical_w > 0 and physical_h > 0 and logical_w and logical_h:
            return (
                int(round(x * logical_w / physical_w)),
                int(round(y * logical_h / physical_h)),
            )
    except Exception as e:
        print(f"[discovery.runtime] DPI conversion unavailable, using raw coordinates: {e}")
    return x, y


def _fallback_click(fallback_box: dict) -> str:
    import pyautogui
    from ai.actions import _check_emergency_stop, _handle_pyautogui_exception

    refusal = _check_emergency_stop()
    if refusal:
        return refusal
    x = int(fallback_box.get("x", 0) + fallback_box.get("w", 0) / 2)
    y = int(fallback_box.get("y", 0) + fallback_box.get("h", 0) / 2)
    x, y = _physical_to_logical(x, y)
    try:
        pyautogui.moveTo(x, y, duration=0.2)
        pyautogui.click()
        return f"Accessibility path no longer resolved - clicked fallback position ({x}, {y})."
    except Exception as e:
        handled = _handle_pyautogui_exception(e)
        return handled or f"Fallback click failed: {e}"


def _fallback_type(fallback_box: dict, text_value: str) -> str:
    import pyautogui
    from ai.actions import _check_emergency_stop, _handle_pyautogui_exception, _type_text

    refusal = _check_emergency_stop()
    if refusal:
        return refusal
    x = int(fallback_box.get("x", 0) + fallback_box.get("w", 0) / 2)
    y = int(fallback_box.get("y", 0) + fallback_box.get("h", 0) / 2)
    x, y = _physical_to_logical(x, y)          # same reason as _fallback_click
    try:
        pyautogui.moveTo(x, y, duration=0.2)
        pyautogui.click()
        time.sleep(0.15)
        _type_text(text_value)
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
        backend = get_backend(app_name)
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
        backend = get_backend(app_name)
        element = backend.find_by_path(accessibility_path)
        if element is not None and backend.input_value(element, text_value):
            return f"Typed into {accessibility_path!r} via accessibility anchor."
    except DiscoveryError as e:
        print(f"[discovery.runtime] Backend unavailable for {app_name!r}: {e}")
    except Exception as e:
        print(f"[discovery.runtime] Accessibility input on {accessibility_path!r} failed: {e}")

    return _fallback_type(fallback_box, text_value)
