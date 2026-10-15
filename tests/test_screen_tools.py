# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_screen_tools.py
============================
What the model is told alongside a screenshot: the controls in the
window in front, by name and coordinates (ai/discovery/screen_map.py),
through tool_screenshot and tool_check_control_at.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.actions as actions
import ai.main as ai_main
from ai.discovery import screen_map


def test_a_screenshot_comes_with_the_controls_on_screen(monkeypatch):
    monkeypatch.setattr(ai_main, "trigger_screenshot", lambda: "")
    monkeypatch.setattr(screen_map, "describe", lambda: "Controls in \"Notepad\":\n  [1] Button 'Save' at (140, 215)")
    result = actions.tool_screenshot()
    assert result.startswith("Screenshot taken successfully.")
    assert "Button 'Save' at (140, 215)" in result


def test_a_screenshot_without_ui_automation_is_just_the_screenshot(monkeypatch):
    monkeypatch.setattr(ai_main, "trigger_screenshot", lambda: "")
    monkeypatch.setattr(screen_map, "describe", lambda: "")
    assert actions.tool_screenshot() == "Screenshot taken successfully. It will be attached to your next message."


def test_a_failing_screen_map_never_fails_the_screenshot(monkeypatch):
    monkeypatch.setattr(ai_main, "trigger_screenshot", lambda: "")

    def broken():
        raise RuntimeError("COM error")

    monkeypatch.setattr(screen_map, "describe", broken)
    assert actions.tool_screenshot().startswith("Screenshot taken successfully.")


def test_checking_a_point_asks_the_screen_map(monkeypatch):
    monkeypatch.setattr(screen_map, "check_point", lambda x, y: f"At ({x}, {y}) now: Button 'OK'.")
    assert actions.tool_check_control_at("140", 215.0) == "At (140, 215) now: Button 'OK'."
