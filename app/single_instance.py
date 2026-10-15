# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/single_instance.py
========================
One running Kestrel per signed-in user.

A second launch (the Start menu shortcut clicked while the app sits in
the tray, or the sign-in shortcut plus a manual start) used to start a
second, independent copy. Both registered the same global shortcuts,
both listened for the wake word, and every hotkey then fired twice:
two screenshots, two sounds. Now the second launch shows the window of
the one already running and exits.

Standard library only, so main.py can call it before importing Qt and
the rest of the app. A second launch costs the time the running copy
takes to answer, a fraction of a second when it's idle, instead of a
full startup.

How it works on Windows:

  - A named mutex in the session's Local\\ namespace means "running".
    The OS removes it when the process ends, however it ends, so there
    is no lock file that can go stale after a crash.
  - The copy that holds it also creates three named objects, and a
    thread in it waits on two of them (listen()):
      Kestrel.Instance.Activate   a semaphore a second launch releases:
                                  "show your window"
      Kestrel.Instance.Activated  a semaphore the running copy releases
                                  once it has done that, from its GUI
                                  thread, so the launch knows it's alive
      Kestrel.Installer.RequestQuit
                                  an event the installer sets: "close, I
                                  need to replace your files"
    Semaphores, not events, for the first two: two launches in quick
    succession both get answered instead of one request being lost.
  - A second launch clears any answer left over from earlier launches,
    asks, and asks again every two seconds while it waits (the copy it
    asked may have ended and been replaced). Meanwhile it keeps trying
    to take the lock: a copy that is shutting down, or that crashed and
    is still held by Windows Error Reporting, holds it for a few more
    seconds, and once it's gone the new launch carries on as the only
    copy instead of exiting without a word.
  - Unless setup is the reason that copy is closing: the installer
    creates Kestrel.Installer.RequestQuit.Busy when it asks the app to
    close, and a launch that sees it exits rather than start from files
    that are being replaced.

This used to be a registered window message handled in
MainWindow.nativeEvent. That meant Python code ran for every message the
main window received, starting with the ones Windows sends while it
first shows the window, and a startup crash inside show() pointed
straight at it. None of this touches the window.

Elsewhere this does nothing: Kestrel only runs on Windows, and the
portable parts poked at during development don't need it.

Set KESTREL_ALLOW_MULTIPLE=1 to start another copy anyway (development).
"""

from __future__ import annotations

import os
import sys
import threading
import time

MUTEX_NAME = "Local\\Kestrel.SingleInstance"
ACTIVATE_NAME = "Local\\Kestrel.Instance.Activate"
ACTIVATED_NAME = "Local\\Kestrel.Instance.Activated"
# The installer builds this name from its quit_message setting
# (installer/build_installer.py): "Local\\" + "Kestrel.Installer.RequestQuit".
QUIT_EVENT_NAME = "Local\\Kestrel.Installer.RequestQuit"
# Created by the installer when it asks the app to close, and kept until
# setup exits: a launch meanwhile must not start from files being replaced.
INSTALLER_BUSY_NAME = QUIT_EVENT_NAME + ".Busy"

# What hand_over() found.
HANDED_OVER = "handed over"         # the running copy showed its window
TOOK_OVER = "took over"             # it ended; this launch is now the one
NOT_RESPONDING = "not responding"   # it holds the lock and never answered
INSTALLING = "installing"           # setup is closing it to replace its files

_ERROR_ALREADY_EXISTS = 183
_ASFW_ANY = 0xFFFFFFFF
_SYNCHRONIZE = 0x00100000
_SEMAPHORE_MODIFY_STATE = 0x0002
_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0
_MAX_COUNT = 0x7FFFFFFF
# How often a waiting launch asks again. The copy it asked may have been
# replaced meanwhile (it ended and another launch took over), and the new
# one never saw the first request.
_ASK_EVERY = 2.0

# Held for the life of the process. Closing the mutex would let a second
# copy in; closing the others would make them vanish for whoever uses them.
_mutex_handle = None
_handles: dict = {}          # "activate" / "activated" / "quit" -> HANDLE
_listener = None


def _enabled() -> bool:
    if sys.platform != "win32":
        return False
    return os.getenv("KESTREL_ALLOW_MULTIPLE", "").strip().lower() not in ("1", "true", "yes", "on")


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateSemaphoreW.restype = wintypes.HANDLE
    kernel32.CreateSemaphoreW.argtypes = [ctypes.c_void_p, wintypes.LONG, wintypes.LONG, wintypes.LPCWSTR]
    kernel32.OpenSemaphoreW.restype = wintypes.HANDLE
    kernel32.OpenSemaphoreW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.ReleaseSemaphore.restype = wintypes.BOOL
    kernel32.ReleaseSemaphore.argtypes = [wintypes.HANDLE, wintypes.LONG, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
    kernel32.WaitForMultipleObjects.argtypes = [
        wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]
    return kernel32


def _drain(kernel32, semaphore) -> None:
    """Take whatever count a semaphore has left, so that only what is
    released from now on counts."""
    for _ in range(10_000):
        if kernel32.WaitForSingleObject(semaphore, 0) != _WAIT_OBJECT_0:
            return


def acquire() -> bool:
    """
    True when this process is the only Kestrel running (it then holds the
    lock until it exits). False when another one already is.

    Any failure to create the mutex counts as "only one": refusing to
    start because of a lock problem would be worse than a rare duplicate.
    """
    global _mutex_handle
    if not _enabled():
        return True
    if _mutex_handle is not None:
        return True
    try:
        import ctypes

        kernel32 = _kernel32()
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        error = ctypes.get_last_error()
        if not handle:
            return True
        if error == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
        # Created now, before anything else runs, so a second launch or
        # the installer can always find them. A request made while this
        # copy is still starting stays pending until listen() takes it.
        created = (
            ("activate", kernel32.CreateSemaphoreW(None, 0, _MAX_COUNT, ACTIVATE_NAME)),
            ("activated", kernel32.CreateSemaphoreW(None, 0, _MAX_COUNT, ACTIVATED_NAME)),
            ("quit", kernel32.CreateEventW(None, False, False, QUIT_EVENT_NAME)),   # auto-reset
        )
        for key, object_handle in created:
            if object_handle:
                _handles[key] = object_handle
        # They may be left over from a copy that has just ended (a launch
        # waiting on it was holding them open), with its requests and
        # answers still counted. Start from nothing.
        for key in ("activate", "activated"):
            if key in _handles:
                _drain(kernel32, _handles[key])
        return True
    except Exception:
        return True


def listen(on_activate, on_quit) -> bool:
    """
    Call on_activate() when a second launch asks this copy to show itself,
    and on_quit() when the installer asks it to close. Both are called on
    a background thread: hand them to the GUI thread yourself, and call
    acknowledge_activation() from there once the window is up.

    False (and nothing started) when there is nothing to listen for: not
    Windows, a development copy started with KESTREL_ALLOW_MULTIPLE, or
    acquire() was never called, as in the tests.
    """
    global _listener
    if _listener is not None:
        return True
    if "activate" not in _handles or "quit" not in _handles:
        return False
    try:
        from ctypes import wintypes

        kernel32 = _kernel32()
        handles = (wintypes.HANDLE * 2)(_handles["activate"], _handles["quit"])
    except Exception:
        return False
    callbacks = (on_activate, on_quit)

    def run() -> None:
        while True:
            # ctypes lets go of the GIL for the wait, so this costs
            # nothing while nobody asks for anything.
            result = kernel32.WaitForMultipleObjects(2, handles, False, _INFINITE)
            index = result - _WAIT_OBJECT_0
            if not 0 <= index < 2:
                return  # WAIT_FAILED: stop rather than spin
            try:
                callbacks[index]()
            except Exception:
                pass

    _listener = threading.Thread(target=run, name="instance-requests", daemon=True)
    _listener.start()
    return True


def acknowledge_activation() -> None:
    """Tell the launch that asked (see hand_over) that the window is up."""
    handle = _handles.get("activated")
    if not handle:
        return
    try:
        _kernel32().ReleaseSemaphore(handle, 1, None)
    except Exception:
        pass


def _installer_busy(kernel32) -> bool:
    handle = kernel32.OpenEventW(_SYNCHRONIZE, False, INSTALLER_BUSY_NAME)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


def hand_over(timeout: float = 20.0) -> str:
    """
    For a launch that found another Kestrel holding the lock: ask it to
    show its window and wait for it to say it has. Meanwhile keep trying
    to take the lock, in case that copy is on its way out.

    HANDED_OVER: it answered; exit.
    TOOK_OVER: it ended and this process now holds the lock; carry on
    starting as the only copy.
    INSTALLING: setup is closing it to replace its files; exit.
    NOT_RESPONDING: it still holds the lock after `timeout` seconds and
    never answered: stuck, or still starting on a very slow machine.

    Any answer counts, not only the one to this launch's own request:
    two launches at once share them, and what matters is that the
    running copy's GUI thread is alive right now. Answers left from
    before this launch started are cleared first.
    """
    if not _enabled():
        return TOOK_OVER
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = _kernel32()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    except Exception:
        return NOT_RESPONDING
    deadline = time.monotonic() + timeout
    next_ask = 0.0
    drained = False
    while True:
        # Opened afresh each time and closed again: while this process
        # holds them, a copy that has ended would leave them behind.
        activate = kernel32.OpenSemaphoreW(_SEMAPHORE_MODIFY_STATE, False, ACTIVATE_NAME)
        activated = kernel32.OpenSemaphoreW(_SYNCHRONIZE, False, ACTIVATED_NAME)
        try:
            if activate and activated:
                if not drained:
                    _drain(kernel32, activated)
                    drained = True
                now = time.monotonic()
                if now >= next_ask:
                    # This process was just started by the user, so it may
                    # take the foreground; passing that on is what lets the
                    # running copy come to the front instead of flashing
                    # in the taskbar.
                    user32.AllowSetForegroundWindow(_ASFW_ANY)
                    kernel32.ReleaseSemaphore(activate, 1, None)
                    next_ask = now + _ASK_EVERY
                if kernel32.WaitForSingleObject(activated, 250) == _WAIT_OBJECT_0:
                    return HANDED_OVER
            else:
                time.sleep(0.25)
        finally:
            for handle in (activate, activated):
                if handle:
                    kernel32.CloseHandle(handle)
        if _installer_busy(kernel32):
            return INSTALLING
        if acquire():
            return TOOK_OVER
        if time.monotonic() >= deadline:
            return NOT_RESPONDING
