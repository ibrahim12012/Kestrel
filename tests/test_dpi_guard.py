# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_dpi_guard.py
=======================
The window drawn twice after Alt+Tab: the check that spots Qt drawing at
a scale Windows doesn't have the window at, the DPI mode set before
anything else can set one, and the Mapping page no longer loading
uiautomation (which sets a DPI mode of its own) at startup.
"""

import ast
import os
import subprocess
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from app import dpi_guard
from app.dpi_guard import DpiGuard, out_of_step, rounded_scale


@pytest.mark.parametrize("raw, policy, expected", [
    (1.25, "PassThrough", 1.25),
    (1.25, "Round", 1.0),
    (1.5, "Round", 2.0),
    (1.25, "Ceil", 2.0),
    (1.75, "Floor", 1.0),
    (1.5, "RoundPreferFloor", 1.0),
    (1.75, "RoundPreferFloor", 2.0),
    (0.75, "PassThrough", 1.0),
])
def test_rounded_scale_follows_qt(raw, policy, expected):
    assert rounded_scale(raw, policy) == expected


def test_the_doubled_window_is_out_of_step():
    # The screenshot: Windows at 125%, the window 1598x982 pixels, Qt
    # drawing its 1278x785 layout at 100%.
    assert out_of_step(1.0, (1278, 785), (1598, 982), 1.25, "PassThrough") is True


def test_a_window_drawn_right_is_not():
    assert out_of_step(1.25, (1278, 785), (1598, 982), 1.25, "PassThrough") is False
    assert out_of_step(1.0, (1600, 900), (1600, 900), 1.0, "PassThrough") is False


def test_half_of_a_dpi_change_is_not():
    # Windows already at 150%, Qt not told yet, the window not resized yet.
    assert out_of_step(1.25, (1278, 785), (1598, 982), 1.5, "PassThrough") is False
    # Qt already at 150%, the drawing and the window both resized.
    assert out_of_step(1.5, (1278, 785), (1917, 1178), 1.5, "PassThrough") is False


def test_a_rounding_policy_is_not_mistaken_for_a_fault():
    assert out_of_step(1.0, (1278, 785), (1278, 785), 1.25, "Round") is False


def test_a_minimized_window_is_left_alone():
    assert out_of_step(1.0, (1278, 785), (0, 0), 1.25, "PassThrough") is False


class _Guard(DpiGuard):
    """A DpiGuard without Qt or Windows: read() and repair() are fakes."""

    def __init__(self, states):
        self._states = list(states)
        self._last_repair = 0.0
        self.enabled = True
        self.repairs = 0

    def read(self):
        return self._states.pop(0) if self._states else None

    def repair(self):
        self.repairs += 1


BROKEN = (1.0, (1278, 785), (1598, 982), 1.25, "PassThrough")
FINE = (1.25, (1278, 785), (1598, 982), 1.25, "PassThrough")


def test_check_repairs_only_when_out_of_step():
    guard = _Guard([FINE, BROKEN])
    assert guard.check() is False
    assert guard.check() is True
    assert guard.repairs == 1


def test_check_does_not_repair_twice_in_a_row(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(dpi_guard.time, "monotonic", lambda: clock[0])
    guard = _Guard([BROKEN, BROKEN, BROKEN])
    assert guard.check() is True
    clock[0] += 0.5
    assert guard.check() is False
    clock[0] += 5.0
    assert guard.check() is True
    assert guard.repairs == 2


def test_a_failing_read_is_not_a_fault():
    class Failing(_Guard):
        def read(self):
            raise OSError("no window")

    assert Failing([]).check() is False


def test_main_sets_the_dpi_mode_before_anything_else():
    tree = ast.parse(open(os.path.join(ROOT, "main.py"), encoding="utf-8").read())
    first_main_block = next(node for node in tree.body
                            if isinstance(node, ast.If) and "__main__" in ast.dump(node.test))
    calls = [ast.dump(statement) for statement in first_main_block.body[:2]]
    assert "win_runtime" in calls[0]
    assert "use_per_monitor_v2" in calls[1]


_LOADS_UIAUTOMATION = ("ai.discovery.mapper", "ai.discovery.accessibility", "ai.discovery.screen_map",
                       "ai.discovery.runtime", "ai.discovery.compiler", "uiautomation")


def _modules_after(code: str) -> str:
    # The same stand-ins conftest.py puts in for the hardware libraries.
    stubs = ("import sys; from unittest.mock import MagicMock; "
             "[sys.modules.setdefault(n, MagicMock(name=n)) for n in "
             "('pyautogui', 'keyboard', 'mouse', 'AppOpener', 'google', 'google.genai', 'google.genai.types')]; ")
    code = stubs + code + ("; print(sorted(m for m in sys.modules "
                           "if m.startswith('ai.discovery') or m == 'uiautomation'))")
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                          text=True, timeout=120, check=True).stdout


def test_what_the_window_imports_at_startup_does_not_load_uiautomation():
    """The modules the main window and the Mapping page import, short of Qt
    itself (not installed where this runs). Only these load uiautomation."""
    output = _modules_after("import ai.discovery.events, ai.discovery, ai.discovery.tree, "
                            "ai.automation_lock, ai.direct_input, ai.actions")
    for module in _LOADS_UIAUTOMATION:
        assert repr(module) not in output, module


def test_showing_a_saved_map_does_not_load_uiautomation():
    output = _modules_after(
        "from ai.discovery.tree import ActionTree, AppStateNode, BacktrackAction, ScreenStatus; "
        "t = ActionTree(app_name='N', root_node_id='s'); "
        "t.add_node(AppStateNode(app_name='N', current_node_id='s', screen_status=ScreenStatus.EXPLORING, "
        "backtrack_action=BacktrackAction(type='KEY_FALLBACK', accessibility_id=None, fallback_key='ESCAPE'))); "
        "t.events()")
    for module in _LOADS_UIAUTOMATION:
        assert repr(module) not in output, module


def test_the_mapper_still_loads_when_asked_for():
    from ai.discovery import map_application_ui, compile_action_tree
    from ai.discovery.mapper import map_application_ui as from_module
    assert map_application_ui is from_module
    assert callable(compile_action_tree)
    import ai.discovery
    with pytest.raises(AttributeError):
        ai.discovery.no_such_thing
