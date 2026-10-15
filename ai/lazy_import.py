# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/lazy_import.py
=================
Two small tools for keeping expensive imports off the startup path.

Everything main.py imports runs before the window can appear, and most
of it is imported by modules that only need it once a feature is used.
Three of those imports were most of the wait:

  google.genai.types  builds several hundred pydantic models at import
  pyautogui           pulls in tkinter (the Tcl/Tk DLLs), PIL, pyscreeze
  AppOpener           runs cmd.exe on every import, and PowerShell's
                      Get-StartApps on the first one in an install

LazyModule stands in for a module until an attribute is first read, so
`types.Part` or `pyautogui.click(...)` inside a function keeps working
unchanged and only pays for the import at that moment.

Console windows: Kestrel.exe is a windowed program with no console, so
every console program it starts (cmd.exe, powershell.exe, attrib.exe)
gets a console window of its own and flashes it on screen, unless it is
started with CREATE_NO_WINDOW. hide_console_windows_everywhere() does
that for the whole process; hidden_console_windows() for one block of
code on one thread.
"""

from __future__ import annotations

import functools
import importlib
import os
import subprocess
import sys
import threading
from contextlib import contextmanager


class LazyModule:
    """
    A module that is imported the first time one of its attributes is
    read or written.

    Tests that replace a module in sys.modules with a mock keep working:
    the import goes through the normal machinery, which returns whatever
    sys.modules already holds.

    on_load runs once, right after the real import, for settings that
    used to be applied at module level (pyautogui.FAILSAFE, say).
    """

    __slots__ = ("_lazy_name", "_lazy_module", "_lazy_on_load", "_lazy_lock")

    def __init__(self, name: str, on_load=None):
        object.__setattr__(self, "_lazy_name", name)
        object.__setattr__(self, "_lazy_module", None)
        object.__setattr__(self, "_lazy_on_load", on_load)
        object.__setattr__(self, "_lazy_lock", threading.Lock())

    def _lazy_load(self):
        module = object.__getattribute__(self, "_lazy_module")
        if module is not None:
            return module
        with object.__getattribute__(self, "_lazy_lock"):
            module = object.__getattribute__(self, "_lazy_module")
            if module is None:
                module = importlib.import_module(object.__getattribute__(self, "_lazy_name"))
                on_load = object.__getattribute__(self, "_lazy_on_load")
                if on_load is not None:
                    on_load(module)
                object.__setattr__(self, "_lazy_module", module)
        return module

    def __getattr__(self, attr: str):
        return getattr(self._lazy_load(), attr)

    def __setattr__(self, attr: str, value) -> None:
        setattr(self._lazy_load(), attr, value)

    def __dir__(self):
        return dir(self._lazy_load())

    def __repr__(self) -> str:
        name = object.__getattribute__(self, "_lazy_name")
        loaded = object.__getattribute__(self, "_lazy_module") is not None
        return f"<lazy module {name!r} ({'loaded' if loaded else 'not loaded yet'})>"

    @property
    def is_loaded(self) -> bool:
        return object.__getattribute__(self, "_lazy_module") is not None


def _pyautogui_loaded(module) -> None:
    # Fail-safe kill switch. PyAutoGUI defaults to it, but it is set
    # explicitly because it is safety-critical: never silently off
    # because something elsewhere touched the default. This used to be a
    # module-level line in ai/actions.py.
    module.FAILSAFE = True


# One shared instance, so the fail-safe is applied whichever module
# happens to touch pyautogui first (ai.actions, ai.main or the recorder).
pyautogui = LazyModule("pyautogui", on_load=_pyautogui_loaded)


# ── Console windows ────────────────────────────────────────────────────

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
# A caller that set one of these has decided about the console itself.
_CONSOLE_CHOICES = _CREATE_NO_WINDOW | _CREATE_NEW_CONSOLE | _DETACHED_PROCESS

_hide_state = threading.local()
_patch_lock = threading.Lock()
_original_popen_init = None
_original_os_system = None
_everywhere = False

# Popen's creationflags is its 14th parameter, counting the command.
_CREATIONFLAGS_POSITION = 13


def _popen_init_hiding_console(self, *args, **kwargs):
    # Everywhere once hide_console_windows_everywhere() has run; before
    # that, only for a thread inside hidden_console_windows(). A caller
    # that asked for a console of its own (CREATE_NEW_CONSOLE: the tool
    # that opens Command Prompt for the user) or for none at all
    # (DETACHED_PROCESS) keeps what it asked for, and so does one that
    # passed creationflags by position (adding it by name would be a
    # TypeError).
    if (_everywhere or getattr(_hide_state, "depth", 0) > 0) and len(args) <= _CREATIONFLAGS_POSITION:
        flags = kwargs.get("creationflags") or 0
        if not flags & _CONSOLE_CHOICES:
            kwargs["creationflags"] = flags | _CREATE_NO_WINDOW
    _original_popen_init(self, *args, **kwargs)


def _install_popen_patch() -> None:
    global _original_popen_init
    with _patch_lock:
        if _original_popen_init is None:
            _original_popen_init = subprocess.Popen.__init__
            # Popen's own name, docstring and signature, for anything that
            # inspects it (inspect.signature(subprocess.Popen), help()).
            functools.update_wrapper(_popen_init_hiding_console, _original_popen_init)
            subprocess.Popen.__init__ = _popen_init_hiding_console


@contextmanager
def hidden_console_windows():
    """
    Inside this block, console programs started through subprocess by
    the current thread run without a visible console window.

    A no-op outside Windows. CREATE_NO_WINDOW only affects console
    programs; a GUI program started inside the block opens normally.
    """
    if sys.platform != "win32":
        yield
        return
    _install_popen_patch()
    _hide_state.depth = getattr(_hide_state, "depth", 0) + 1
    try:
        yield
    finally:
        _hide_state.depth -= 1


def _has_console() -> bool:
    """Whether this process has a console window of its own."""
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        return bool(kernel32.GetConsoleWindow())
    except Exception:
        return True     # can't tell: leave everything as it was


def _system_through_subprocess(command) -> int:
    """os.system, run through subprocess so that it gets the same
    treatment. Same result: the command interpreter's exit status."""
    try:
        return subprocess.call(command, shell=True)
    except OSError:
        return -1


def hide_console_windows_everywhere() -> bool:
    """
    From now on, no console program started by any thread of this
    process opens a console window, unless it asks for one. main.py
    calls this once, at the very start. Returns whether it took effect.

    Only when this process has no console of its own, which is the case
    for Kestrel.exe (and pythonw.exe). `python main.py` in a terminal
    shares that terminal with everything it starts, so nothing flashes
    there, and the programs it runs should keep writing to it.

    hidden_console_windows() used to be the only guard, around AppOpener.
    It was not enough: libraries start console programs in places nobody
    wraps. The platform module runs `ver` through cmd.exe the first time
    anything asks which Windows this is, AppOpener falls back to a
    series of os.system() calls when its PowerShell query fails, and
    every one of those flashed a console window after the app had
    started. os.system goes through subprocess from here on, so it is
    covered too.
    """
    global _everywhere, _original_os_system
    if sys.platform != "win32" or _has_console():
        return False
    _install_popen_patch()
    with _patch_lock:
        _everywhere = True
        if _original_os_system is None:
            _original_os_system = os.system
            os.system = _system_through_subprocess
    return True
