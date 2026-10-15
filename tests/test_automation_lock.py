# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""tests/test_automation_lock.py: one automation at a time."""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai import automation_lock


def test_nothing_runs_at_first():
    assert automation_lock.running() == ""


def test_the_holder_is_named_and_released():
    with automation_lock.hold("a macro"):
        assert automation_lock.running() == "a macro"
    assert automation_lock.running() == ""


def test_released_when_the_run_fails():
    with pytest.raises(RuntimeError):
        with automation_lock.hold("a macro"):
            raise RuntimeError("the macro broke")
    assert automation_lock.running() == ""


def test_the_same_thread_can_take_it_again_and_keeps_its_name():
    with automation_lock.hold("an AI run"):
        with automation_lock.hold("a mapping run"):
            assert automation_lock.running() == "an AI run"
        assert automation_lock.running() == "an AI run"
    assert automation_lock.running() == ""


def test_another_thread_is_refused_with_what_runs():
    errors = []

    def second():
        try:
            with automation_lock.hold("a macro"):
                pass
        except automation_lock.Busy as busy:
            errors.append(str(busy))

    with automation_lock.hold("a mapping run"):
        thread = threading.Thread(target=second)
        thread.start()
        thread.join(5)
    assert errors == ["Another automation is running (a mapping run). Stop it first, or wait for it to end."]
    assert automation_lock.running() == ""
