# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_lazy_import.py
=========================
The startup-time helpers in ai/lazy_import.py, and the two places that
depend on them staying lazy: ai.actions' AppOpener wrapper and the
shared pyautogui proxy.
"""

import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.lazy_import as lazy_import
from ai.lazy_import import LazyModule


@pytest.fixture
def fake_module_on_path(tmp_path, monkeypatch):
    """A real, importable module that nothing has imported yet."""
    name = "kestrel_lazy_probe"
    (tmp_path / f"{name}.py").write_text("VALUE = 42\nIMPORT_COUNT = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)
    yield name
    sys.modules.pop(name, None)


def test_lazy_module_does_not_import_until_an_attribute_is_used(fake_module_on_path):
    proxy = LazyModule(fake_module_on_path)
    assert fake_module_on_path not in sys.modules
    assert not proxy.is_loaded

    assert proxy.VALUE == 42
    assert fake_module_on_path in sys.modules
    assert proxy.is_loaded


def test_lazy_module_runs_on_load_once(fake_module_on_path):
    calls = []
    proxy = LazyModule(fake_module_on_path, on_load=lambda m: calls.append(m.VALUE))
    proxy.VALUE
    proxy.IMPORT_COUNT
    assert calls == [42]


def test_lazy_module_forwards_attribute_writes(fake_module_on_path):
    proxy = LazyModule(fake_module_on_path)
    proxy.VALUE = 7
    assert sys.modules[fake_module_on_path].VALUE == 7


def test_lazy_module_returns_whatever_sys_modules_holds(monkeypatch):
    # Tests replace real modules with mocks in sys.modules; the proxy
    # must hand those back rather than importing the real thing.
    sentinel = type(sys)("kestrel_lazy_mock")
    sentinel.marker = "mock"
    monkeypatch.setitem(sys.modules, "kestrel_lazy_mock", sentinel)
    assert LazyModule("kestrel_lazy_mock").marker == "mock"


def test_shared_pyautogui_proxy_turns_the_failsafe_on():
    fake = sys.modules["pyautogui"]          # conftest's MagicMock
    fake.FAILSAFE = False
    proxy = LazyModule("pyautogui", on_load=lazy_import._pyautogui_loaded)
    proxy.position                            # any attribute read loads it
    assert fake.FAILSAFE is True


@pytest.fixture
def recorded_popen(monkeypatch):
    """The patch as if installed, pointing at a recorder, so nothing
    touches the real subprocess.Popen; and 'everywhere' switched off."""
    seen = []

    def fake_original(self, *args, **kwargs):
        seen.append(kwargs.get("creationflags", 0))

    monkeypatch.setattr(lazy_import, "_original_popen_init", fake_original)
    monkeypatch.setattr(lazy_import, "_everywhere", False)
    monkeypatch.setattr(lazy_import.sys, "platform", "win32")
    return seen


def test_hidden_console_windows_adds_create_no_window_only_inside_the_block(recorded_popen):
    lazy_import._popen_init_hiding_console(object(), ["cmd"])
    with lazy_import.hidden_console_windows():
        lazy_import._popen_init_hiding_console(object(), ["cmd"])
        lazy_import._popen_init_hiding_console(object(), ["cmd"], creationflags=0x200)
    lazy_import._popen_init_hiding_console(object(), ["cmd"])

    no_window = lazy_import._CREATE_NO_WINDOW
    assert recorded_popen == [0, no_window, 0x200 | no_window, 0]


def test_a_caller_that_chose_its_console_keeps_it(recorded_popen):
    with lazy_import.hidden_console_windows():
        lazy_import._popen_init_hiding_console(object(), ["cmd"], creationflags=lazy_import._CREATE_NEW_CONSOLE)
        lazy_import._popen_init_hiding_console(object(), ["cmd"], creationflags=lazy_import._DETACHED_PROCESS)
    assert recorded_popen == [lazy_import._CREATE_NEW_CONSOLE, lazy_import._DETACHED_PROCESS]


def test_creationflags_given_by_position_is_left_alone(monkeypatch):
    seen = []
    monkeypatch.setattr(lazy_import, "_original_popen_init", lambda self, *args, **kwargs: seen.append((args, kwargs)))
    monkeypatch.setattr(lazy_import, "_everywhere", True)
    positional = ["cmd"] + [None] * 12 + [0x200]
    lazy_import._popen_init_hiding_console(object(), *positional)
    assert seen == [(tuple(positional), {})]


def test_everywhere_covers_every_thread_and_os_system(recorded_popen, monkeypatch):
    monkeypatch.setattr(lazy_import, "_has_console", lambda: False)
    monkeypatch.setattr(lazy_import, "_original_os_system", None)
    monkeypatch.setattr(lazy_import.os, "system", lambda command: 99)
    calls = []
    monkeypatch.setattr(lazy_import.subprocess, "call", lambda command, shell=False: calls.append((command, shell)) or 7)

    assert lazy_import.hide_console_windows_everywhere() is True
    import threading
    worker = threading.Thread(target=lambda: lazy_import._popen_init_hiding_console(object(), ["ver"]))
    worker.start()
    worker.join()
    assert recorded_popen == [lazy_import._CREATE_NO_WINDOW]
    # os.system now goes through subprocess, with the same result.
    assert lazy_import.os.system("mode 800") == 7
    assert calls == [("mode 800", True)]


def test_everywhere_leaves_a_process_with_a_console_alone(recorded_popen, monkeypatch):
    """python main.py in a terminal: its children share that terminal."""
    monkeypatch.setattr(lazy_import, "_has_console", lambda: True)
    assert lazy_import.hide_console_windows_everywhere() is False
    lazy_import._popen_init_hiding_console(object(), ["ver"])
    assert recorded_popen == [0]


def test_everywhere_does_nothing_outside_windows(monkeypatch):
    monkeypatch.setattr(lazy_import, "_everywhere", False)
    monkeypatch.setattr(lazy_import.sys, "platform", "linux")
    assert lazy_import.hide_console_windows_everywhere() is False
    assert lazy_import._everywhere is False


def test_actions_imports_appopener_only_when_an_app_is_opened():
    import ai.actions as actions
    importlib.reload(actions)
    assert actions._appopen_function is None

    assert actions.appopen("notepad", match_closest=True) == "opened"
    assert actions._appopen_function is sys.modules["AppOpener"].open
