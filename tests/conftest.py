# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/conftest.py
==================
Shared pytest setup.

ai/actions.py imports pyautogui, keyboard, and AppOpener at module level.
These are hardware/OS-specific (pyautogui needs a display, keyboard needs
elevated permissions on Linux, AppOpener is Windows-oriented) and aren't
things we want tests actually driving. We stub them out with MagicMocks
here, at import time - pytest always imports conftest.py before it
collects/imports test files in the same directory, so this stubbing is
guaranteed to happen before `import ai.actions` runs anywhere in the
test suite. (An autouse fixture would run too late: test-file imports
happen during collection, before any fixture executes.)

Effect:
  - ai/actions.py (and anything else) imports cleanly on any machine/CI
    runner - no display, no Windows, no root permissions needed.
  - Tests can assert on *what the code tried to do* (e.g. "did it call
    pyautogui.click with these coordinates?") without actually moving
    the mouse or typing anything. Use the mock_pyautogui/mock_keyboard
    fixtures below to get a freshly-reset handle on them.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

for _name in ("pyautogui", "keyboard", "mouse"):
    if _name not in sys.modules or not isinstance(sys.modules[_name], MagicMock):
        sys.modules[_name] = MagicMock(name=_name)

# pyautogui.FailSafeException is a real exception class in the actual
# library (raised when the mouse hits a screen corner) - ai/actions.py and
# ai/main.py both `except pyautogui.FailSafeException:` in several places.
# Left as a bare MagicMock attribute, `except pyautogui.FailSafeException:`
# raises its own TypeError ("catching classes that do not inherit from
# BaseException is not allowed") the moment any test actually exercises
# that branch - so give it a real, minimal stand-in here rather than
# leaving it to whichever test discovers the gap.
if not isinstance(sys.modules["pyautogui"].FailSafeException, type):
    class _FailSafeException(Exception):
        pass
    sys.modules["pyautogui"].FailSafeException = _FailSafeException

if "AppOpener" not in sys.modules:
    _appopener_mock = MagicMock(name="AppOpener")
    _appopener_mock.open = MagicMock(return_value="opened")
    sys.modules["AppOpener"] = _appopener_mock

# ai/main.py does `from google import genai` and `from google.genai import types`.
# The real google-genai SDK isn't installed in every dev/test environment (and
# isn't needed to test the module's pure logic, e.g. the AUTONOMOUS_SUGGEST
# protocol parser), so stub it the same way.
if "google.genai" not in sys.modules:
    _google_mock = MagicMock(name="google")
    _genai_mock = MagicMock(name="google.genai")
    _types_mock = MagicMock(name="google.genai.types")
    _genai_mock.types = _types_mock
    _google_mock.genai = _genai_mock
    sys.modules["google"] = _google_mock
    sys.modules["google.genai"] = _genai_mock
    sys.modules["google.genai.types"] = _types_mock


@pytest.fixture
def mock_pyautogui():
    """Return the stubbed pyautogui module, fully reset before each test."""
    import pyautogui
    # reset_mock() alone only clears call history - side_effect/return_value
    # configured by one test (e.g. a list side_effect) would otherwise leak
    # into every later test sharing this same module-level mock.
    pyautogui.reset_mock(return_value=True, side_effect=True)
    return pyautogui


@pytest.fixture
def mock_keyboard():
    """Return the stubbed keyboard module, fully reset before each test."""
    import keyboard
    keyboard.reset_mock(return_value=True, side_effect=True)
    return keyboard


@pytest.fixture(scope="session")
def qapp():
    """
    A QApplication for GUI smoke tests (see test_smoke.py).
    Uses the offscreen Qt platform plugin so it runs without a real display
    (e.g. in CI or this sandbox). Skips the test cleanly if PyQt6 isn't
    installed, rather than failing the whole session.
    """
    pytest.importorskip("PyQt6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    return app


@pytest.fixture(autouse=True)
def isolate_macro_storage(monkeypatch, tmp_path):
    """
    Point macro storage at a throwaway directory for every test.

    Without this, the suite wrote into the real macro folder. MACROS_DIR
    is USER_DATA_PATH/macros, and when running from source USER_DATA_PATH
    is the project root - so every test that saved a macro left a JSON
    file in the repo's own macros/ directory, indistinguishable from a
    macro the user recorded. Fifteen of them had accumulated there:
    aaa_macro, zzz_macro, keep_me, macro_one, portable, solo and friends.

    Beyond the mess, it made tests order-dependent in a way that only
    showed up occasionally - list_macro_names() returns whatever is on
    disk, so a test asserting on the macro list saw leftovers from
    earlier runs as well as its own, and passed or failed depending on
    what had run before it and on which files a previous run happened to
    leave behind.

    autouse, and deliberately not opt-in: a test that writes a macro
    without asking for isolation is exactly the case that caused this,
    and it would not have thought to request the fixture.

    Patches the module attribute rather than USER_DATA_PATH, because
    every path helper in macro_store reads MACROS_DIR from the module
    global at call time (_path_for, _ensure_dir, list_macro_names), so
    rebinding it redirects all of them. Discovery trees are pointed
    somewhere disposable too, for the same reason.
    """
    try:
        import ai.macro_store as macro_store
    except ImportError:                      # module not importable in this run
        yield
        return

    sandbox = tmp_path / "macros"
    sandbox.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(macro_store, "MACROS_DIR", str(sandbox))

    try:
        import ai.discovery.tree as tree
    except ImportError:
        pass
    else:
        trees = tmp_path / "trees"
        trees.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(tree, "TREES_DIR", str(trees))

    yield
