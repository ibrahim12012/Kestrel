# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_single_instance.py
===============================
app/single_instance.py, with the Windows calls replaced: a second launch
is told another Kestrel is running and hands over to it (or takes over
from one that has ended), a lock that cannot be made never stops the app
from starting, and the running copy hears a second launch and the
installer through named kernel objects, not through its window.
"""

import ctypes
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app import single_instance

_WAIT_TIMEOUT = 0x102
_WAIT_FAILED = 0xFFFFFFFF


class _FakeFunction:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        result = self.result
        return result(*args) if callable(result) else result


class _FakeKernel32:
    """kernel32 as single_instance uses it, with semaphores that count.
    `mutex_errors` is what GetLastError says after each CreateMutexW call
    in turn (the last one repeats). `on_release(handle)` stands in for the
    running copy reacting to a released semaphore."""

    def __init__(self, handle, errors):
        self.errors = list(errors)
        self.error = 0
        self.counts = {}
        self.on_release = None

        def create_mutex(*args):
            self.error = self.errors.pop(0) if len(self.errors) > 1 else self.errors[0]
            return handle

        def release(semaphore, count, previous):
            self.counts[semaphore] = self.counts.get(semaphore, 0) + count
            if self.on_release:
                self.on_release(semaphore)
            return 1

        def wait(obj, ms):
            if self.counts.get(obj, 0) > 0:
                self.counts[obj] -= 1
                return 0
            return _WAIT_TIMEOUT

        self.CreateMutexW = _FakeFunction(create_mutex)
        self.CreateEventW = _FakeFunction(lambda _sa, _manual, _initial, name: 100 + len(name))
        self.OpenEventW = _FakeFunction(0)
        self.CreateSemaphoreW = _FakeFunction(lambda _sa, _initial, _max, name: 200 + len(name))
        self.OpenSemaphoreW = _FakeFunction(0)
        self.ReleaseSemaphore = _FakeFunction(release)
        self.WaitForSingleObject = _FakeFunction(wait)
        self.WaitForMultipleObjects = _FakeFunction(_WAIT_FAILED)
        self.CloseHandle = _FakeFunction(1)


class _FakeUser32:
    def __init__(self):
        self.AllowSetForegroundWindow = _FakeFunction(1)


@pytest.fixture
def windows(monkeypatch):
    """Pretend to be Windows with a fake kernel32; returns a setter."""
    monkeypatch.setattr(single_instance.sys, "platform", "win32")
    monkeypatch.delenv("KESTREL_ALLOW_MULTIPLE", raising=False)
    monkeypatch.setattr(single_instance, "_mutex_handle", None)
    monkeypatch.setattr(single_instance, "_handles", {})
    monkeypatch.setattr(single_instance, "_listener", None)

    def install(handle=1234, error=0, errors=None):
        kernel32 = _FakeKernel32(handle, errors or [error])
        user32 = _FakeUser32()
        monkeypatch.setattr(
            ctypes, "WinDLL",
            lambda name, use_last_error=False: user32 if name == "user32" else kernel32,
            raising=False)
        monkeypatch.setattr(ctypes, "get_last_error", lambda: kernel32.error, raising=False)
        kernel32.user32 = user32
        return kernel32

    return install


_EXISTS = single_instance._ERROR_ALREADY_EXISTS


def test_outside_windows_it_always_starts(monkeypatch):
    monkeypatch.setattr(single_instance.sys, "platform", "linux")
    assert single_instance.acquire() is True
    assert single_instance.hand_over(timeout=0) == single_instance.TOOK_OVER


def test_the_first_copy_takes_the_lock_and_makes_its_objects(windows):
    kernel32 = windows(handle=1234, error=0)
    assert single_instance.acquire() is True
    assert single_instance._mutex_handle == 1234
    assert kernel32.CreateMutexW.calls[0][2] == single_instance.MUTEX_NAME
    assert kernel32.CloseHandle.calls == []
    semaphores = [call[3] for call in kernel32.CreateSemaphoreW.calls]
    assert semaphores == [single_instance.ACTIVATE_NAME, single_instance.ACTIVATED_NAME]
    assert all(call[1] == 0 for call in kernel32.CreateSemaphoreW.calls)      # nothing pending
    (event,) = kernel32.CreateEventW.calls
    assert event[3] == single_instance.QUIT_EVENT_NAME
    assert event[1] is False and event[2] is False    # auto-reset, not set
    assert set(single_instance._handles) == {"activate", "activated", "quit"}


def test_the_quit_event_name_matches_the_installer():
    root = os.path.join(os.path.dirname(__file__), "..")
    build = open(os.path.join(root, "installer", "build_installer.py"), encoding="utf-8").read()
    assert '"quit_message": "Kestrel.Installer.RequestQuit"' in build
    assert single_instance.QUIT_EVENT_NAME == "Local\\Kestrel.Installer.RequestQuit"


def test_a_second_copy_is_told_one_is_running(windows):
    kernel32 = windows(handle=5678, error=_EXISTS)
    assert single_instance.acquire() is False
    assert kernel32.CloseHandle.calls == [(5678,)]
    assert single_instance._mutex_handle is None
    assert kernel32.CreateSemaphoreW.calls == [] and kernel32.CreateEventW.calls == []


def test_a_lock_that_cannot_be_made_does_not_stop_the_app(windows):
    windows(handle=0, error=5)
    assert single_instance.acquire() is True


def test_an_error_inside_ctypes_does_not_stop_the_app(windows, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("no kernel32 here")
    monkeypatch.setattr(ctypes, "WinDLL", broken, raising=False)
    assert single_instance.acquire() is True


def test_several_copies_can_be_allowed_for_development(windows, monkeypatch):
    kernel32 = windows(handle=5678, error=_EXISTS)
    monkeypatch.setenv("KESTREL_ALLOW_MULTIPLE", "1")
    assert single_instance.acquire() is True
    assert kernel32.CreateMutexW.calls == []


_ACTIVATE, _ACTIVATED = 701, 702


def _running_copy(kernel32, answers=True, leftover=0):
    """The semaphores of a copy that is running, and whether it answers."""
    kernel32.OpenSemaphoreW.result = lambda access, inherit, name: {
        single_instance.ACTIVATE_NAME: _ACTIVATE, single_instance.ACTIVATED_NAME: _ACTIVATED}[name]
    kernel32.counts[_ACTIVATED] = leftover
    if answers:
        def answer(semaphore):
            if semaphore == _ACTIVATE:
                kernel32.counts[_ACTIVATE] -= 1
                kernel32.counts[_ACTIVATED] = kernel32.counts.get(_ACTIVATED, 0) + 1
        kernel32.on_release = answer


def test_a_second_launch_hands_over_when_the_running_copy_answers(windows):
    kernel32 = windows(handle=5678, error=_EXISTS)
    _running_copy(kernel32, answers=True, leftover=1)
    assert single_instance.hand_over(timeout=5) == single_instance.HANDED_OVER
    assert kernel32.ReleaseSemaphore.calls == [(_ACTIVATE, 1, None)]
    assert kernel32.counts[_ACTIVATED] == 0
    assert kernel32.user32.AllowSetForegroundWindow.calls     # may come to the front
    assert sorted(call[0] for call in kernel32.CloseHandle.calls) == [_ACTIVATE, _ACTIVATED]


def test_answers_left_from_earlier_launches_do_not_count(windows):
    """A hung copy must not pass for a live one on the strength of answers
    it gave before this launch."""
    kernel32 = windows(handle=5678, error=_EXISTS)
    _running_copy(kernel32, answers=False, leftover=3)
    assert single_instance.hand_over(timeout=0.3) == single_instance.NOT_RESPONDING


def test_a_waiting_launch_asks_again_now_and_then(windows, monkeypatch):
    """The copy it asked may have ended and been replaced by one that never
    saw the request."""
    kernel32 = windows(handle=5678, error=_EXISTS)
    _running_copy(kernel32, answers=False)
    monkeypatch.setattr(single_instance, "_ASK_EVERY", 0.05)
    single_instance.hand_over(timeout=0.4)
    assert len(kernel32.ReleaseSemaphore.calls) >= 3


def test_a_waiting_launch_does_not_ask_more_often_than_that(windows):
    kernel32 = windows(handle=5678, error=_EXISTS)
    _running_copy(kernel32, answers=False)
    single_instance.hand_over(timeout=0.3)
    assert len(kernel32.ReleaseSemaphore.calls) == 1


def test_a_second_launch_takes_over_from_a_copy_that_ended(windows):
    # The other copy holds the lock at first (crashed, and Windows Error
    # Reporting still has it), then it's gone.
    kernel32 = windows(handle=5678, errors=[_EXISTS, _EXISTS, 0])
    assert single_instance.acquire() is False
    assert single_instance.hand_over(timeout=5) == single_instance.TOOK_OVER
    assert single_instance._mutex_handle == 5678
    assert set(single_instance._handles) == {"activate", "activated", "quit"}
    assert len(kernel32.CreateMutexW.calls) == 3


def test_a_second_launch_does_not_take_over_while_setup_runs(windows):
    kernel32 = windows(handle=5678, error=_EXISTS)
    kernel32.OpenEventW.result = lambda access, inherit, name: (
        900 if name == single_instance.INSTALLER_BUSY_NAME else 0)
    assert single_instance.hand_over(timeout=5) == single_instance.INSTALLING
    assert kernel32.CreateMutexW.calls == []
    assert (900,) in kernel32.CloseHandle.calls


def test_the_busy_marker_name_matches_the_installer():
    root = os.path.join(os.path.dirname(__file__), "..")
    source = open(os.path.join(root, "installer", "setup", "win_util.c"), encoding="utf-8").read()
    assert 'w_cat(event_name, L".Busy")' in source
    assert single_instance.INSTALLER_BUSY_NAME == single_instance.QUIT_EVENT_NAME + ".Busy"


def test_a_second_launch_gives_up_on_a_copy_that_never_answers(windows):
    kernel32 = windows(handle=5678, error=_EXISTS)
    assert single_instance.hand_over(timeout=0) == single_instance.NOT_RESPONDING
    assert kernel32.ReleaseSemaphore.calls == []     # nothing there to ask


def test_a_new_copy_starts_with_no_requests_or_answers_pending(windows):
    """Objects left over from a copy that just ended (a waiting launch had
    them open) keep their counts; the new copy clears them."""
    kernel32 = windows()
    activate = 200 + len(single_instance.ACTIVATE_NAME)
    activated = 200 + len(single_instance.ACTIVATED_NAME)
    kernel32.counts.update({activate: 2, activated: 1})
    assert single_instance.acquire() is True
    assert kernel32.counts[activate] == 0 and kernel32.counts[activated] == 0


def test_the_answer_is_released_on_the_right_semaphore(windows):
    kernel32 = windows()
    single_instance.acquire()
    single_instance.acknowledge_activation()
    assert kernel32.ReleaseSemaphore.calls == [(single_instance._handles["activated"], 1, None)]


def test_no_answer_to_give_without_the_lock(windows):
    kernel32 = windows()
    single_instance.acknowledge_activation()
    assert kernel32.ReleaseSemaphore.calls == []


def test_the_listener_hands_each_request_to_its_callback(windows):
    kernel32 = windows()
    assert single_instance.acquire() is True
    # Activate, then quit, then the wait fails and the thread ends.
    results = iter([0, 1, _WAIT_FAILED])
    kernel32.WaitForMultipleObjects.result = lambda *args: next(results)
    heard = []
    assert single_instance.listen(lambda: heard.append("activate"), lambda: heard.append("quit")) is True
    single_instance._listener.join(timeout=5)
    assert heard == ["activate", "quit"]
    count, handles, wait_all, timeout = kernel32.WaitForMultipleObjects.calls[0]
    assert count == 2 and wait_all is False and timeout == 0xFFFFFFFF
    assert list(handles) == [single_instance._handles["activate"], single_instance._handles["quit"]]


def test_a_callback_that_fails_does_not_stop_the_listener(windows):
    kernel32 = windows()
    single_instance.acquire()
    results = iter([0, 0, _WAIT_FAILED])
    kernel32.WaitForMultipleObjects.result = lambda *args: next(results)
    calls = []

    def flaky():
        calls.append(1)
        raise RuntimeError("window gone")

    single_instance.listen(flaky, lambda: None)
    single_instance._listener.join(timeout=5)
    assert len(calls) == 2


def test_nothing_to_listen_for_without_the_lock(windows):
    windows()
    assert single_instance.listen(lambda: None, lambda: None) is False
    assert single_instance._listener is None


def test_the_listener_is_a_daemon_thread(windows):
    kernel32 = windows()
    single_instance.acquire()
    started = threading.Event()

    def first_wait_fails(*args):
        started.set()
        return _WAIT_FAILED

    kernel32.WaitForMultipleObjects.result = first_wait_fails
    single_instance.listen(lambda: None, lambda: None)
    assert started.wait(5)
    assert single_instance._listener.daemon is True
