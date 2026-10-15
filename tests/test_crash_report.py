# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_crash_report.py
============================
app/crash_report.py: a failure leaves a record in logs/crash.log, the run
log keeps every log line, and an exception before the window is up is
shown and ends the app while one after it only gets logged.
"""

import faulthandler
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app import crash_report


@pytest.fixture
def report(tmp_path, monkeypatch):
    """crash_report installed into tmp_path, and everything it changed put
    back afterwards (the hooks are process-wide)."""
    saved_hooks = (sys.excepthook, threading.excepthook)
    for name in ("_log_dir", "_crash_path", "_run_log_path", "_previous_log_path", "_marker_path",
                 "_crash_file", "_run_log_file", "_native_file", "_started"):
        monkeypatch.setattr(crash_report, name, getattr(crash_report, name))
    monkeypatch.setattr(crash_report, "_run", {})
    # No wevtutil here; tests that need Windows' crash record provide one.
    monkeypatch.setattr(crash_report, "windows_crash_record",
                        lambda pid, since=0.0, exe="", timeout=4.0: (False, ""))
    shown, exits = [], []
    monkeypatch.setattr(crash_report, "show_fatal_error", lambda summary, heading="": shown.append(summary))
    monkeypatch.setattr(crash_report.os, "_exit", lambda code: exits.append(code))
    crash_report.install(str(tmp_path))
    yield tmp_path / "logs", shown, exits
    faulthandler.disable()
    sys.excepthook, threading.excepthook = saved_hooks
    for f in (crash_report._crash_file, crash_report._run_log_file, crash_report._native_file):
        if f is not None:
            f.close()


def _read(path):
    return path.read_text(encoding="utf-8")


def test_a_record_lands_in_crash_log_with_the_version(report):
    logs, _, _ = report
    crash_report.record("Something broke", "details here")
    text = _read(logs / "crash.log")
    assert "Something broke" in text and "details here" in text and "Kestrel " in text


def test_an_exception_that_escapes_main_is_recorded_shown_and_fatal(report):
    logs, shown, exits = report
    try:
        raise ValueError("no such theme key")
    except ValueError:
        crash_report.fatal(*sys.exc_info())
    assert "ValueError: no such theme key" in _read(logs / "crash.log")
    assert shown and "no such theme key" in shown[0]
    assert exits == [1]


def test_an_exception_in_a_slot_is_recorded_and_the_app_goes_on(report):
    logs, shown, exits = report
    try:
        raise KeyError("slot")
    except KeyError:
        sys.excepthook(*sys.exc_info())
    assert "KeyError" in _read(logs / "crash.log")
    assert "during startup" in _read(logs / "crash.log")
    assert shown == [] and exits == []


def test_after_startup_native_exceptions_go_to_their_own_file(report):
    logs, _, _ = report
    crash_report.startup_finished()
    assert (logs / "native.log").exists()
    assert "matter only if the app closed" in _read(logs / "native.log")


def test_a_thread_that_dies_is_recorded(report):
    logs, _, exits = report
    # Called directly: pytest puts its own threading.excepthook in place
    # while a test runs.
    try:
        raise RuntimeError("worker failed")
    except RuntimeError:
        exc_type, exc, tb = sys.exc_info()
    worker = threading.Thread(name="worker")
    crash_report._thread_excepthook(threading.ExceptHookArgs([exc_type, exc, tb, worker]))
    text = _read(logs / "crash.log")
    assert "thread 'worker'" in text and "RuntimeError: worker failed" in text
    assert exits == []


def test_the_run_log_keeps_every_line_and_starts_afresh(report):
    logs, _, _ = report
    (logs / "kestrel.log").write_text("the previous run\n", encoding="utf-8")
    crash_report.start_run_log()
    crash_report.log_sink("10:00:00.000", "info", "[startup] Kestrel is starting...", None)
    crash_report.log_sink("10:00:01.000", "error", "[qt] bad", {"a": 1})
    text = _read(logs / "kestrel.log")
    assert "previous run" not in text
    assert "INFO  [startup] Kestrel is starting..." in text
    assert "ERROR [qt] bad  (input: {'a': 1})" in text
    # Kept, so starting again after a crash doesn't wipe out the lead-up.
    assert _read(logs / "kestrel.previous.log") == "the previous run\n"


def test_each_launch_is_one_line_in_crash_log_and_leaves_a_marker(report):
    logs, _, _ = report
    crash_report.start_run_log()
    assert f"started (pid {os.getpid()})" in _read(logs / "crash.log")
    marker = _read(logs / "running.txt")
    assert f"pid={os.getpid()}" in marker and "stage=starting" in marker


def test_the_marker_follows_the_stages_and_goes_on_a_normal_close(report):
    logs, _, _ = report
    crash_report.start_run_log()
    crash_report.stage("showing the window")
    assert "stage=showing the window" in _read(logs / "running.txt")
    crash_report.startup_finished()
    assert "stage=running" in _read(logs / "running.txt")
    crash_report.closed_normally()
    assert not (logs / "running.txt").exists()


def test_crash_log_says_when_the_window_is_up(report):
    """So a native exception logged during show() reads as one the app
    got past, not as the one that killed it."""
    logs, _, _ = report
    crash_report.start_run_log()
    crash_report.startup_finished()
    text = _read(logs / "crash.log")
    assert text.index(f"started (pid {os.getpid()})") < text.index(f"window up (pid {os.getpid()})")


def test_stages_before_the_run_log_starts_write_nothing(report):
    logs, _, _ = report
    crash_report.stage("showing the window")       # a test building a MainWindow, say
    assert not (logs / "running.txt").exists()


def _leave_a_run_behind(logs, pid=4321, stage="showing the window"):
    (logs / "running.txt").write_text(
        f"pid={pid}\nstarted=2026-09-28 21:14:05\nstarted_epoch=1790000000\n"
        f"version=1.2.3\nexe=Kestrel.exe\nstage={stage}\n", encoding="utf-8")
    (logs / "kestrel.log").write_text(
        "".join(f"21:14:0{i}.000 INFO  line {i}\n" for i in range(6)), encoding="utf-8")


_NATIVE_LOG = (
    "\n=== 2026-09-27 10:00:00  pid 4321  window up (entries below matter only if the app closed on its own)\n"
    "Windows fatal exception: access violation\n\nan older run with the same pid\n"
    "\n=== 2026-09-28 21:14:07  pid 1111  window up (entries below matter only if the app closed on its own)\n"
    "Windows fatal exception: access violation\n\nsomeone else's\n"
    "\n=== 2026-09-28 21:14:08  pid 4321  window up (entries below matter only if the app closed on its own)\n"
    "Windows fatal exception: code 0x8001010d\n\nhandled COM noise\n"
    "Windows fatal exception: access violation\n\nCurrent thread 0x31ec:\n  File \"x.py\", line 1\n"
)


def test_a_run_that_died_starting_is_reported_at_the_next_launch(report, monkeypatch):
    logs, _, _ = report
    _leave_a_run_behind(logs, stage="showing the window")
    (logs / "native.log").write_text(_NATIVE_LOG, encoding="utf-8")
    asked = []

    def record_found(pid, since=0.0, exe="", timeout=4.0):
        asked.append((pid, since, exe))
        return True, "Windows recorded it (...):\n  ModuleName: Qt6Gui.dll\n  ExceptionCode: c0000005"

    monkeypatch.setattr(crash_report, "windows_crash_record", record_found)
    crash_report.start_run_log()
    text = _read(logs / "crash.log")
    assert "The previous run (pid 4321, Kestrel 1.2.3, started 2026-09-28 21:14:05) ended without closing normally" in text
    assert "It got as far as: showing the window." in text
    assert "ModuleName: Qt6Gui.dll" in text
    assert asked == [(4321, 1790000000.0, "Kestrel.exe")]
    # It never got a native.log section of its own: nothing from there.
    assert "native.log" not in text
    # Its last log lines, from the log it left, now kestrel.previous.log.
    assert "INFO  line 5" in text
    # And this launch is a new run, marked as such.
    assert text.index("ended without closing normally") < text.index(f"started (pid {os.getpid()})")
    marker = _read(logs / "running.txt")
    assert f"pid={os.getpid()}" in marker and f"exe={os.path.basename(sys.executable)}" in marker


def test_a_run_that_died_running_brings_its_last_native_entry(report):
    logs, _, _ = report
    _leave_a_run_behind(logs, stage="running")
    (logs / "native.log").write_text(_NATIVE_LOG, encoding="utf-8")
    crash_report.start_run_log()
    text = _read(logs / "crash.log")
    assert "It got as far as: running." in text
    assert "Current thread 0x31ec" in text
    # Not the handled entry before it, another process's, or an older run
    # that happened to have the same process id.
    assert "handled COM noise" not in text and "someone else's" not in text
    assert "an older run with the same pid" not in text


def test_no_record_from_windows_is_said_plainly(report):
    logs, _, _ = report
    _leave_a_run_behind(logs, stage="running")
    crash_report.start_run_log()
    text = _read(logs / "crash.log")
    assert "It got as far as: running." in text
    assert "Windows has no crash record for this process" in text


def test_a_run_that_closed_normally_is_not_reported(report):
    logs, _, _ = report
    crash_report.start_run_log()
    crash_report.closed_normally()
    crash_report.start_run_log()
    assert "ended without closing normally" not in _read(logs / "crash.log")


def test_an_error_already_recorded_is_not_reported_twice(report):
    logs, _, _ = report
    crash_report.start_run_log()
    try:
        raise ValueError("boom")
    except ValueError:
        crash_report.fatal(*sys.exc_info())
    assert not (logs / "running.txt").exists()


_NS = "xmlns='http://schemas.microsoft.com/win/2004/08/events/event'"
# The layouts wevtutil prints these events in, trimmed: Windows 11 names
# the fields of event 1000; Windows 10 (and some 11 builds) doesn't, and
# writes the process id as bare hex. Event 1002 is a hang.
_NAMED_CRASH = (
    f"<Event {_NS}><System><Provider Name='Application Error' Guid='{{a0e9b465-b939-57d7-b27d-95d8e925ff57}}'/>"
    "<EventID>1000</EventID><TimeCreated SystemTime='2026-09-28T19:14:09.1234567Z'/></System><EventData>"
    "<Data Name='AppName'>Kestrel.exe</Data><Data Name='ModuleName'>Qt6Gui.dll</Data>"
    "<Data Name='ExceptionCode'>c0000005</Data><Data Name='FaultingOffset'>00000000001234ab</Data>"
    "<Data Name='ProcessId'>0x10e1</Data><Data Name='PackageFullName'></Data></EventData></Event>")
_OLDER_SAME_PID = (
    f"<Event {_NS}><System><Provider Name='Application Error'/><EventID>1000</EventID>"
    "<TimeCreated SystemTime='2026-09-20T08:00:00.0000000Z'/></System><EventData>"
    "<Data Name='AppName'>Kestrel.exe</Data><Data Name='ModuleName'>old.dll</Data>"
    "<Data Name='ProcessId'>0x10e1</Data></EventData></Event>")
_UNNAMED_CRASH = (
    f"<Event {_NS}><System><Provider Name='Application Error'/><EventID Qualifiers='0'>1000</EventID>"
    "<TimeCreated SystemTime='2026-09-28T19:14:09.0000000Z'/></System><EventData>"
    + "".join(f"<Data>{value}</Data>" for value in (
        "Kestrel.exe", "0.2.1.0", "66f0a1b2", "ucrtbase.dll", "10.0.19041.3636", "81cf5d89",
        "c0000409", "000000000007f61e", "1238", "01db1182a0b1c2d3",
        r"C:\Users\User\AppData\Local\Programs\Kestrel\Kestrel.exe",
        r"C:\Windows\System32\ucrtbase.dll", "5d1c0f5e-0000-0000-0000-000000000000", "", ""))
    + "</EventData></Event>")
_HANG = (
    f"<Event {_NS}><System><Provider Name='Application Hang'/><EventID Qualifiers='0'>1002</EventID>"
    "<TimeCreated SystemTime='2026-09-28T19:20:00.0000000Z'/></System><EventData>"
    "<Data>Kestrel.exe</Data><Data>0.2.1.0</Data><Data>28f4</Data><Data>01db1182a0b1c2d3</Data>"
    "</EventData></Event>")
_OTHER_PROGRAM = (
    f"<Event {_NS}><System><Provider Name='Application Error'/><EventID>1000</EventID>"
    "<TimeCreated SystemTime='2026-09-28T19:14:09.0000000Z'/></System><EventData>"
    "<Data Name='AppName'>notepad.exe</Data><Data Name='ProcessId'>0x2222</Data></EventData></Event>")
_EVENTS = "\n".join((_NAMED_CRASH, _OLDER_SAME_PID, _UNNAMED_CRASH, _HANG, _OTHER_PROGRAM))


def test_windows_crash_events_are_matched_by_process_id():
    found, text = crash_report.parse_crash_events(_EVENTS, 0x10e1)
    assert found
    assert "Application Error" in text and "event 1000" in text
    assert "ModuleName: Qt6Gui.dll" in text and "ExceptionCode: c0000005" in text
    assert "PackageFullName" not in text        # empty fields are left out
    assert crash_report.parse_crash_events(_EVENTS, 0x9999) == (False, "")


def test_an_older_process_with_the_same_id_does_not_count():
    since = 1790000000.0                                  # 2026-09-21
    assert crash_report.parse_crash_events(_OLDER_SAME_PID, 0x10e1, since) == (False, "")


def test_the_unnamed_event_format_is_read_by_position():
    found, text = crash_report.parse_crash_events(_EVENTS, 0x1238, exe="Kestrel.exe")
    assert found
    assert "AppName: Kestrel.exe" in text and "ModuleName: ucrtbase.dll" in text
    assert "ExceptionCode: c0000409" in text and "ProcessId: 1238" in text


def test_a_hang_is_reported_too():
    found, text = crash_report.parse_crash_events(_EVENTS, 0x28f4, exe="Kestrel.exe")
    assert found and "Application Hang" in text and "event 1002" in text


def test_another_program_with_the_same_id_does_not_count():
    assert crash_report.parse_crash_events(_EVENTS, 0x2222, exe="Kestrel.exe") == (False, "")
    found, _ = crash_report.parse_crash_events(_EVENTS, 0x2222, exe="notepad.exe")
    assert found


def test_utf16_output_is_decoded():
    for raw in (b"\xff\xfe" + "héllo".encode("utf-16-le"), "héllo".encode("utf-16-le")):
        assert crash_report._decode_console_output(raw) == "héllo"
    assert crash_report._decode_console_output("héllo".encode("utf-8")) == "héllo"


def test_unreadable_event_output_says_so():
    found, text = crash_report.parse_crash_events("<Event><oops>", 1)
    assert not found and "Couldn't read" in text


def test_a_big_crash_log_is_trimmed_to_its_newest_part(tmp_path):
    path = tmp_path / "crash.log"
    path.write_bytes(b"old " * 400_000 + b"NEWEST")
    crash_report._trim(str(path))
    data = path.read_bytes()
    assert len(data) < crash_report.MAX_CRASH_LOG_BYTES
    assert data.endswith(b"NEWEST") and data.startswith(b"[... older entries removed ...]")


def test_nowhere_to_write_is_not_an_error(tmp_path, monkeypatch):
    blocked = tmp_path / "a-file"
    blocked.write_text("x")
    monkeypatch.setattr(crash_report, "_crash_file", None)
    monkeypatch.setattr(crash_report, "_log_dir", "")
    saved = sys.excepthook
    crash_report.install(str(blocked))        # logs/ can't be made under a file
    crash_report.record("ignored")
    assert sys.excepthook is saved
