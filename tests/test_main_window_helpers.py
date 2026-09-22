# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_main_window_helpers.py
===================================
Tests for the pure-logic helpers in app/main_window.py:
_coerce_macro_params() and _parse_tools_to_macros().

Needs PyQt6 (main_window.py imports it at module level) but not a running
window - uses the qapp fixture from conftest.py just to be safe/consistent,
even though these two functions don't touch any actual widgets.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_coerce_macro_params_leaves_str_params_untouched(qapp):
    from app.main_window import _coerce_macro_params

    def tool_example(name: str) -> None:
        pass

    result = _coerce_macro_params(tool_example, {"name": "hello"})
    assert result == {"name": "hello"}


def test_coerce_macro_params_converts_int(qapp):
    from app.main_window import _coerce_macro_params

    def tool_example(seconds: int) -> None:
        pass

    result = _coerce_macro_params(tool_example, {"seconds": "5"})
    assert result == {"seconds": 5}
    assert isinstance(result["seconds"], int)


def test_coerce_macro_params_converts_float(qapp):
    from app.main_window import _coerce_macro_params

    def tool_example(delay: float) -> None:
        pass

    result = _coerce_macro_params(tool_example, {"delay": "1.5"})
    assert result == {"delay": 1.5}
    assert isinstance(result["delay"], float)


def test_coerce_macro_params_converts_bool_truthy_variants(qapp):
    from app.main_window import _coerce_macro_params

    def tool_example(confirm: bool) -> None:
        pass

    for truthy in ("1", "true", "True", "yes", "oui", "on"):
        assert _coerce_macro_params(tool_example, {"confirm": truthy}) == {"confirm": True}

    for falsy in ("0", "false", "no", "non", ""):
        assert _coerce_macro_params(tool_example, {"confirm": falsy}) == {"confirm": False}


def test_coerce_macro_params_passes_through_unannotated_params(qapp):
    from app.main_window import _coerce_macro_params

    def tool_example(anything) -> None:
        pass

    result = _coerce_macro_params(tool_example, {"anything": "42"})
    assert result == {"anything": "42"}  # stays a string, no annotation to convert against


def test_coerce_macro_params_ignores_params_not_in_signature(qapp):
    from app.main_window import _coerce_macro_params

    def tool_example(name: str) -> None:
        pass

    # Shouldn't happen in practice (UI only ever sends real params), but
    # must not crash if it does.
    result = _coerce_macro_params(tool_example, {"name": "x", "stale_param": "y"})
    assert result["name"] == "x"
    assert result["stale_param"] == "y"


def test_coerce_macro_params_handles_uninspectable_callables_gracefully(qapp):
    from app.main_window import _coerce_macro_params

    result = _coerce_macro_params(len, {"whatever": "5"})  # a builtin, no real Python signature
    assert result == {"whatever": "5"}


# ══════════════════════════════════════════════════════════════════
# _reload_macros - the guard against importlib.reload(actions) racing
# RecorderController's live keyboard/mouse hooks during an active
# recording (see app/main_window.py's _reload_macros docstring/comment).
#
# MainWindow itself is too heavy to construct in a unit test (system
# tray, global shortcuts, a wake-word worker thread, the chat DB) - but
# _reload_macros only actually touches self.recorder and self.macros, so
# it's called here as an unbound method against a minimal stand-in
# object with just those two attributes. Same technique, much lighter
# than instantiating the real window.
# ══════════════════════════════════════════════════════════════════

def test_reload_macros_skips_reload_while_recording_is_in_progress(qapp, monkeypatch):
    from types import SimpleNamespace
    from app.main_window import MainWindow
    import ai.actions as actions
    import importlib as importlib_module

    reload_calls = []
    monkeypatch.setattr(importlib_module, "reload", lambda mod: reload_calls.append(mod))
    load_macros_calls = []

    fake_self = SimpleNamespace(
        recorder=SimpleNamespace(_controller=SimpleNamespace(is_recording=True)),
        macros=SimpleNamespace(load_macros=lambda macros: load_macros_calls.append(macros)),
    )

    MainWindow._reload_macros(fake_self)

    assert reload_calls == []          # actions module was NOT reloaded
    assert load_macros_calls == []     # cards were NOT rebuilt from a reload we skipped


def test_reload_macros_reloads_normally_once_recording_has_stopped(qapp, monkeypatch):
    from types import SimpleNamespace
    from app.main_window import MainWindow
    import ai.actions as actions
    import importlib as importlib_module

    reload_calls = []
    monkeypatch.setattr(importlib_module, "reload", lambda mod: reload_calls.append(mod))
    load_macros_calls = []

    fake_self = SimpleNamespace(
        recorder=SimpleNamespace(_controller=SimpleNamespace(is_recording=False)),
        macros=SimpleNamespace(load_macros=lambda macros: load_macros_calls.append(macros)),
    )

    MainWindow._reload_macros(fake_self)

    assert reload_calls == [actions]
    assert len(load_macros_calls) == 1


def test_navigate_to_recorder_page_refreshes_shortcuts_description(qapp, monkeypatch):
    """
    Regression test: navigating to the Recorder page used to leave the
    shortcuts subtitle stale after a rebind in Settings - only fixed on
    a fresh app launch or by starting a new recording. _navigate() must
    call RecorderWidget.refresh_shortcuts_description() on every visit,
    the same "cheap enough to just always do" treatment the Macros tab
    already gets.
    """
    from types import SimpleNamespace
    from app.main_window import MainWindow

    refresh_calls = []
    fake_stack = SimpleNamespace(setCurrentIndex=lambda idx: None)
    fake_btn = SimpleNamespace(setChecked=lambda checked: None)
    fake_self = SimpleNamespace(
        _stack=fake_stack,
        sidebar=SimpleNamespace(btnNewChat=fake_btn, btnActions=fake_btn, btnRecorder=fake_btn, btnMapping=fake_btn),
        PAGE_CHAT=MainWindow.PAGE_CHAT,
        PAGE_MACROS=MainWindow.PAGE_MACROS,
        PAGE_RECORDER=MainWindow.PAGE_RECORDER,
        PAGE_MAPPING=MainWindow.PAGE_MAPPING,
        recorder=SimpleNamespace(refresh_shortcuts_description=lambda: refresh_calls.append(True)),
    )

    MainWindow._navigate(fake_self, MainWindow.PAGE_RECORDER)

    assert refresh_calls == [True]


def test_navigate_to_chat_page_does_not_touch_the_recorder(qapp):
    from types import SimpleNamespace
    from app.main_window import MainWindow

    refresh_calls = []
    fake_stack = SimpleNamespace(setCurrentIndex=lambda idx: None)
    fake_btn = SimpleNamespace(setChecked=lambda checked: None)
    fake_self = SimpleNamespace(
        _stack=fake_stack,
        sidebar=SimpleNamespace(btnNewChat=fake_btn, btnActions=fake_btn, btnRecorder=fake_btn, btnMapping=fake_btn),
        PAGE_CHAT=MainWindow.PAGE_CHAT,
        PAGE_MACROS=MainWindow.PAGE_MACROS,
        PAGE_RECORDER=MainWindow.PAGE_RECORDER,
        PAGE_MAPPING=MainWindow.PAGE_MAPPING,
        recorder=SimpleNamespace(refresh_shortcuts_description=lambda: refresh_calls.append(True)),
        chat=SimpleNamespace(refresh_profile_picker=lambda: None),
    )

    MainWindow._navigate(fake_self, MainWindow.PAGE_CHAT)

    assert refresh_calls == []
