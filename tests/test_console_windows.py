# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_console_windows.py
===============================
Kestrel.exe has no console, so every console program it starts gets a
window of its own unless told not to. These are the console programs
the tools run (tasklist, taskkill, cmd.exe): each has to be started with
CREATE_NO_WINDOW. (AppOpener's import is covered in test_lazy_import.py.)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.actions as actions

CREATE_NO_WINDOW = 0x08000000


@pytest.fixture
def calls(monkeypatch):
    seen = []

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(args, **kwargs):
        seen.append((args, kwargs))
        return Completed()

    def fake_check_output(args, **kwargs):
        seen.append((args, kwargs))
        return "notepad.exe  1234 Console 1 10,000 K\n"

    # A limiter of its own: other tests lower the shared one's caps.
    monkeypatch.setattr(actions.safety, "limiter", actions.safety.SafetyLimiter())
    monkeypatch.setattr(actions, "_NO_CONSOLE_WINDOW", CREATE_NO_WINDOW)
    monkeypatch.setattr(actions.subprocess, "run", fake_run)
    monkeypatch.setattr(actions.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(actions.risk_gate, "require_approval", lambda desc: True)
    monkeypatch.setattr(actions, "emergency_stop", False)
    return seen


def test_running_a_command_shows_no_console(calls):
    actions.tool_run_command("dir")
    args, kwargs = calls[-1]
    assert args[0] == "cmd.exe"
    assert kwargs["creationflags"] == CREATE_NO_WINDOW


def test_listing_processes_shows_no_console(calls):
    assert "notepad.exe" in actions._get_running_processes()
    args, kwargs = calls[-1]
    assert args[0] == "tasklist"
    assert kwargs["creationflags"] == CREATE_NO_WINDOW


def test_closing_an_app_shows_no_console(calls, monkeypatch):
    monkeypatch.setattr(actions, "single_fcn", False)
    actions.tool_close_application("notepad")
    taskkill = [c for c in calls if c[0][0] == "taskkill"]
    assert taskkill and taskkill[0][1]["creationflags"] == CREATE_NO_WINDOW


def test_opening_command_prompt_gets_a_console_of_its_own(monkeypatch):
    """Every other console is hidden (ai/lazy_import.py); this is the one
    the user asked to see."""
    launched = []
    monkeypatch.setattr(actions, "_NEW_CONSOLE", 0x10)
    monkeypatch.setattr(actions.subprocess, "Popen", lambda args, **kwargs: launched.append((args, kwargs)))
    process_name, launch = actions._launch_native_app("command prompt")
    launch()
    assert process_name == "cmd"
    assert launched == [(["cmd.exe"], {"shell": False, "creationflags": 0x10})]
