# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_macro_run_worker.py
================================
Tests for app/workers/macro_run_worker.py - the fix for macros freezing
the whole GUI thread while they ran (see MainWindow._on_macro_run, which
used to call func(**params) directly instead of going through a
background worker like every other long-running operation in the app).

Drives MacroRunWorker.run() directly (not through a real QThread - consistent with how the rest of this app's tests exercise Qt logic:
qapp gives QObject/pyqtSignal machinery a real QApplication to live in,
without needing actual thread hand-off for the assertions below).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.actions as actions
from app.workers.macro_run_worker import MacroRunWorker


def test_run_emits_finished_with_str_of_result(qapp, monkeypatch):
    monkeypatch.setattr(actions, "tool_example_macro", lambda: "did the thing", raising=False)

    worker = MacroRunWorker()
    captured = {}
    errors = []
    worker.finished.connect(lambda macro_id, result: captured.update(macro_id=macro_id, result=result))
    worker.error.connect(lambda *a: errors.append(a))

    worker.run("tool_example_macro", {})

    assert not errors
    assert captured == {"macro_id": "tool_example_macro", "result": "did the thing"}


def test_run_emits_finished_with_empty_string_when_result_is_none(qapp, monkeypatch):
    monkeypatch.setattr(actions, "tool_example_macro", lambda: None, raising=False)

    worker = MacroRunWorker()
    captured = {}
    worker.finished.connect(lambda macro_id, result: captured.update(macro_id=macro_id, result=result))

    worker.run("tool_example_macro", {})

    assert captured == {"macro_id": "tool_example_macro", "result": ""}


def test_run_coerces_params_before_calling(qapp, monkeypatch):
    received = {}

    def fake_tool(seconds: int):
        received["seconds"] = seconds
        received["type"] = type(seconds).__name__
        return "ok"

    monkeypatch.setattr(actions, "tool_example_macro", fake_tool, raising=False)

    worker = MacroRunWorker()
    worker.run("tool_example_macro", {"seconds": "5"})   # UI gives strings

    assert received == {"seconds": 5, "type": "int"}


def test_run_emits_error_when_macro_not_found(qapp):
    worker = MacroRunWorker()
    captured = {}
    worker.error.connect(lambda macro_id, msg: captured.update(macro_id=macro_id, message=msg))

    worker.run("tool_this_does_not_exist_at_all", {})

    assert captured["macro_id"] == "tool_this_does_not_exist_at_all"
    assert "introuvable" in captured["message"]


def test_run_emits_error_when_attribute_is_not_callable(qapp, monkeypatch):
    monkeypatch.setattr(actions, "not_a_function", "just a string", raising=False)

    worker = MacroRunWorker()
    captured = {}
    worker.error.connect(lambda macro_id, msg: captured.update(macro_id=macro_id, message=msg))

    worker.run("not_a_function", {})

    assert captured["macro_id"] == "not_a_function"
    assert "not a function" in captured["message"]


def test_run_emits_error_when_the_macro_itself_raises(qapp, monkeypatch):
    def boom():
        raise RuntimeError("something went wrong mid-click")

    monkeypatch.setattr(actions, "tool_example_macro", boom, raising=False)

    worker = MacroRunWorker()
    captured = {}
    worker.error.connect(lambda macro_id, msg: captured.update(macro_id=macro_id, message=msg))

    worker.run("tool_example_macro", {})

    assert captured["macro_id"] == "tool_example_macro"
    assert "something went wrong mid-click" in captured["message"]


def test_run_does_not_emit_finished_when_it_errors(qapp, monkeypatch):
    monkeypatch.setattr(actions, "tool_example_macro", lambda: 1 / 0, raising=False)

    worker = MacroRunWorker()
    finished_calls = []
    worker.finished.connect(lambda *a: finished_calls.append(a))

    worker.run("tool_example_macro", {})

    assert finished_calls == []
