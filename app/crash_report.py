# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/crash_report.py
=====================
Never die without saying why.

Kestrel.exe is a windowed program with no console, so a failure at
startup left no trace at all: the window never came, or came and went.
Everything below ends up in <USER_DATA_PATH>/logs/:

  crash.log    a line when a launch starts ("started (pid 1234)") and one
               when its window is up ("window up (pid 1234)"), and between
               and after them anything that went wrong in that run:
                 - a Python exception nobody caught, on any thread
                   (sys.excepthook, threading.excepthook)
                 - Qt aborting the process (main.py's Qt message handler
                   calls record() just before)
                 - a native crash inside a DLL (faulthandler writes the
                   Python stack of every thread at that moment)
                 - how the previous run ended, when it didn't close
                   normally (see below)
               Newest at the end, trimmed when it passes 1 MB.
  kestrel.log  every log() line of the current run.
  kestrel.previous.log
               the same for the run before, so starting Kestrel again
               after a crash doesn't wipe out what led up to it.
  native.log   native exceptions once the window is up (see below).
  running.txt  this run's process id and how far it got. Deleted when the
               app closes normally.

How the previous run ended: if running.txt is still there at the next
launch, that run never closed normally. The next launch records it in
crash.log with the stage it reached, the last lines of its log, and what
Windows' own crash reporting says about it (Event Viewer > Windows Logs >
Application, "Application Error": the name of the DLL that crashed and
the exception code). That is the one piece faulthandler can't give.

faulthandler writes to crash.log only until the window is up. After that
it writes to native.log instead: Windows also reports exceptions that the
code raising them goes on to handle (COM does this often, and UI
Automation is COM), and faulthandler cannot tell those from a real crash.
An entry there only matters if the app then closed on its own, and the
previous-run report copies the last one into crash.log when it did.

Installing an exception hook also changes what PyQt does with an
exception raised inside a slot or a Qt override: instead of aborting
the whole app (its behaviour with the default hook), it hands the
exception to the hook, and the app carries on. One broken button no
longer takes everything down with it.

An exception that escapes main() itself is fatal: main.py hands it to
fatal(), which records it, says so in a message box naming the file,
and ends the process.

Standard library only: main.py installs this before importing Qt.
"""

from __future__ import annotations

import calendar
import datetime
import faulthandler
import os
import re
import subprocess
import sys
import threading
import time
import traceback

MAX_CRASH_LOG_BYTES = 1_000_000
# How much of the previous run's log goes into its report.
PREVIOUS_LOG_LINES = 25
# The last native exception from native.log, at most this many characters.
NATIVE_DUMP_CHARS = 4000

_log_dir = ""
_crash_path = ""
_run_log_path = ""
_previous_log_path = ""
_marker_path = ""
_crash_file = None       # kept open for faulthandler, which writes to a file descriptor
_run_log_file = None
_native_file = None
_lock = threading.Lock()
_started = False         # the event loop is running (see startup_finished)
_run: dict = {}          # this run's running.txt fields, once start_run_log() has run


def crash_log_path() -> str:
    return _crash_path


def run_log_path() -> str:
    return _run_log_path


def install(user_data_path: str) -> None:
    """Set up crash.log and the hooks. As early as possible, and before
    the single-instance check, so a launch that hands over to a running
    Kestrel can say so."""
    global _log_dir, _crash_path, _run_log_path, _previous_log_path, _marker_path, _crash_file
    try:
        _log_dir = os.path.join(user_data_path, "logs")
        os.makedirs(_log_dir, exist_ok=True)
        _crash_path = os.path.join(_log_dir, "crash.log")
        _run_log_path = os.path.join(_log_dir, "kestrel.log")
        _previous_log_path = os.path.join(_log_dir, "kestrel.previous.log")
        _marker_path = os.path.join(_log_dir, "running.txt")
        _trim(_crash_path)
        # Append mode: a second launch writes to the same file at the same
        # time as the running one, and appends don't overwrite each other.
        _crash_file = open(_crash_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return  # nowhere to write: run as before rather than not at all
    try:
        faulthandler.enable(file=_crash_file, all_threads=True)
    except (OSError, RuntimeError, ValueError):
        pass
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


def start_run_log() -> None:
    """
    For the one Kestrel that runs (never a second launch that hands over
    to it, which must not touch the running one's files): keep the
    previous run's log, start this run's, report the previous run if it
    didn't close normally, and mark this one as started.
    """
    global _run_log_file
    if not _run_log_path:
        return
    previous = _read_marker()
    try:
        if os.path.exists(_run_log_path):
            os.replace(_run_log_path, _previous_log_path)
    except OSError:
        pass
    try:
        _run_log_file = open(_run_log_path, "w", encoding="utf-8", buffering=1)
    except OSError:
        _run_log_file = None
    if previous:
        try:
            _report_previous_run(previous)
        except Exception:  # noqa: BLE001 - a report must never stop the app from starting
            pass
    now = time.time()
    _run.update({
        "pid": str(os.getpid()),
        "started": datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
        "started_epoch": f"{now:.0f}",
        "version": _app_version(),
        # What Windows' crash records will call this process: Kestrel.exe,
        # or python.exe when running from source.
        "exe": os.path.basename(sys.executable),
        "stage": "starting",
    })
    _write_marker()
    record(f"started (pid {os.getpid()})")


def stage(name: str) -> None:
    """How far this run has got, kept in running.txt so that the next
    launch can say where it stopped if it never closes normally."""
    if not _run:
        return   # start_run_log() never ran: a test, or a second launch
    _run["stage"] = name
    _write_marker()


def closed_normally() -> None:
    """The app is quitting the way it should (QApplication.aboutToQuit,
    which also fires when Windows ends the session): the next launch has
    nothing to report about this run, and no later stage() brings the
    marker back."""
    _run.clear()
    _remove_marker()


def _app_version() -> str:
    try:
        from paths import APP_VERSION
        return str(APP_VERSION)
    except Exception:
        return "?"


def _write_marker() -> None:
    if not _marker_path:
        return
    try:
        with open(_marker_path, "w", encoding="utf-8") as f:
            for key, value in _run.items():
                f.write(f"{key}={value}\n")
    except OSError:
        pass


def _read_marker() -> dict:
    if not _marker_path:
        return {}
    try:
        with open(_marker_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}
    fields = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()
    return fields if fields.get("pid") else {}


def _remove_marker() -> None:
    if not _marker_path:
        return
    try:
        os.remove(_marker_path)
    except OSError:
        pass


def _trim(path: str) -> None:
    """Keep the newest half of a log that has grown too big."""
    try:
        if os.path.getsize(path) <= MAX_CRASH_LOG_BYTES:
            return
        with open(path, "rb") as f:
            f.seek(-MAX_CRASH_LOG_BYTES // 2, os.SEEK_END)
            tail = f.read()
        with open(path, "wb") as f:
            f.write(b"[... older entries removed ...]\n" + tail)
    except OSError:
        pass


def record(title: str, text: str = "") -> None:
    """Append one timestamped entry to crash.log."""
    if _crash_file is None:
        return
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n=== {stamp}  Kestrel {_app_version()}  {title}\n"
    if text.strip():
        entry += text.rstrip() + "\n"
    with _lock:
        try:
            _crash_file.write(entry)
            _crash_file.flush()
        except (OSError, ValueError):
            pass


def log_sink(ts: str, level: str, message: str, input_data) -> None:
    """ai.log sink: every log() line of this run, into kestrel.log."""
    if _run_log_file is None:
        return
    line = f"{ts} {level.upper():<5} {message}"
    if input_data is not None:
        line += f"  (input: {input_data!r})"
    with _lock:
        try:
            _run_log_file.write(line + "\n")
        except (OSError, ValueError):
            pass


def startup_finished() -> None:
    """The window is up and the event loop is about to run: native
    exceptions go to native.log from now on (see the module docstring)."""
    global _started, _native_file
    _started = True
    stage("running")
    if not _log_dir:
        return
    # Says, in crash.log itself, that anything logged above for this run
    # (a native exception during show(), say) didn't stop it.
    if _run:
        record(f"window up (pid {os.getpid()}); native exceptions from here on go to native.log")
    try:
        native_path = os.path.join(_log_dir, "native.log")
        _trim(native_path)
        _native_file = open(native_path, "a", encoding="utf-8", buffering=1)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _native_file.write(
            f"\n=== {stamp}  pid {os.getpid()}  window up "
            "(entries below matter only if the app closed on its own)\n")
        faulthandler.enable(file=_native_file, all_threads=True)
    except (OSError, RuntimeError, ValueError):
        pass


# ── The previous run ───────────────────────────────────────────────────────

def _report_previous_run(previous: dict) -> None:
    """The previous run left running.txt behind: it never closed normally.
    Say so in crash.log, with everything that says why."""
    pid = previous.get("pid", "?")
    stage = previous.get("stage", "unknown")
    parts = [f"It got as far as: {stage}."]

    try:
        pid_number = int(pid)
    except ValueError:
        pid_number = -1
    try:
        since = float(previous.get("started_epoch", "0"))
    except ValueError:
        since = 0.0
    _found, report = windows_crash_record(pid_number, since, previous.get("exe", ""))
    if report:
        parts += ["", report]
    else:
        parts += ["", "Windows has no crash record for this process (Event Viewer > Windows Logs > "
                      "Application). Either it was ended from outside (Task Manager, the installer, "
                      "Windows shutting down), or Windows hadn't written its report yet."]

    # native.log only has a section for a run whose window was up.
    if stage == "running":
        native = _last_native_dump(pid, previous.get("started", ""))
        if native:
            parts += ["", "The last native exception it logged (native.log):", native]

    tail = _tail(_previous_log_path, PREVIOUS_LOG_LINES)
    if tail:
        parts += ["", "The last lines of its log (kestrel.previous.log):", tail]

    started = previous.get("started", "?")
    version = previous.get("version", "?")
    record(f"The previous run (pid {pid}, Kestrel {version}, started {started}) "
           "ended without closing normally", "\n".join(parts))


def _tail(path: str, lines: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64_000))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(data.splitlines()[-lines:])


_NATIVE_HEADER = re.compile(r"^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)  pid (\d+)  window up", re.MULTILINE)


def _last_native_dump(pid: str, started: str = "") -> str:
    """The last faulthandler entry native.log has for the run of process
    `pid` that started at `started` (a pid can come back in a later run)."""
    path = os.path.join(_log_dir, "native.log") if _log_dir else ""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    headers = list(_NATIVE_HEADER.finditer(text))
    mine = [h for h in headers if h.group(2) == str(pid) and h.group(1) >= started]
    if not mine:
        return ""
    section = text[mine[-1].start():]
    following = section.find("\n=== ", 1)
    if following >= 0:
        section = section[:following]
    last = max(section.rfind("Windows fatal exception"), section.rfind("Fatal Python error"))
    if last < 0:
        return ""
    return section[last:last + NATIVE_DUMP_CHARS].rstrip()


def _decode_console_output(data: bytes) -> str:
    if data[:2] == b"\xff\xfe":
        return data[2:].decode("utf-16-le", errors="replace")
    if b"\x00" in data[:200]:
        return data.decode("utf-16-le", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        import locale
        return data.decode(locale.getpreferredencoding(False) or "latin-1", errors="replace")


def _event_epoch(system_time: str) -> float:
    """'2026-09-28T12:00:00.1234567Z' (UTC) -> seconds since the epoch."""
    try:
        whole = system_time.split(".")[0].rstrip("Z")
        return float(calendar.timegm(time.strptime(whole, "%Y-%m-%dT%H:%M:%S")))
    except (ValueError, AttributeError):
        return 0.0


# The field names of events whose fields come unnamed (Windows 10, and
# some Windows 11 builds, write event 1000 that way), in order.
_UNNAMED_FIELDS = {
    "1000": ("AppName", "AppVersion", "AppTimeStamp", "ModuleName", "ModuleVersion",
             "ModuleTimeStamp", "ExceptionCode", "FaultingOffset", "ProcessId",
             "ProcessCreationTime", "AppPath", "ModulePath", "ReportId",
             "PackageFullName", "PackageRelativeAppId"),
    "1002": ("AppName", "AppVersion", "ProcessId", "StartTime", "TerminationTime",
             "AppPath", "ReportId", "PackageFullName", "PackageRelativeAppId", "HangType"),
}


def _is_pid(value: str, pid: int) -> bool:
    """Windows writes the process id in hex, with ("0x31ec") or without
    ("31ec") the prefix depending on the event's format."""
    try:
        return int(value.strip(), 16) == pid
    except ValueError:
        return False


def _run_wevtutil(args: list, timeout: float):
    return subprocess.run(
        ["wevtutil"] + args,
        stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def windows_crash_record(pid: int, since: float = 0.0, exe: str = "",
                         timeout: float = 4.0) -> "tuple[bool, str]":
    """
    What Windows recorded about process `pid` (an `exe`, started at
    `since`) crashing or hanging, read from the Application event log:
    (True, text) when there is a record, (False, "") when there is none,
    (False, why) when it couldn't be read.

    Event 1000 ("Application Error") names the module the crash happened
    in and the exception code; 1002 ("Application Hang") is a window that
    stopped responding and was closed. Read with wevtutil, as XML, whose
    field names don't depend on the language Windows is in, and only as
    far back as the start of that run, so it's quick.
    """
    if sys.platform != "win32":
        return False, ""
    window_ms = int(max(60.0, time.time() - since + 60.0) * 1000) if since else 7 * 86_400_000
    query = ("*[System[Provider[@Name='Application Error' or @Name='Application Hang']"
             f" and TimeCreated[timediff(@SystemTime) <= {window_ms}]]]")
    base = ["qe", "Application", f"/q:{query}", "/c:50", "/rd:true", "/f:xml"]
    try:
        result = _run_wevtutil(base + ["/uni:true"], timeout)
        if result.returncode != 0:
            result = _run_wevtutil(base, timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Couldn't read Windows' crash records: {e}"
    if result.returncode != 0:
        why = _decode_console_output(result.stderr or b"").strip() or f"exit code {result.returncode}"
        return False, f"Couldn't read Windows' crash records (wevtutil: {why[:300]})"
    return parse_crash_events(_decode_console_output(result.stdout), pid, since, exe)


def parse_crash_events(xml_text: str, pid: int, since: float = 0.0, exe: str = "") -> "tuple[bool, str]":
    """The part of windows_crash_record() that reads wevtutil's output."""
    import xml.etree.ElementTree as ET

    ns = "{http://schemas.microsoft.com/win/2004/08/events/event}"
    try:
        root = ET.fromstring(f"<Events>{xml_text.strip()}</Events>")
    except ET.ParseError as e:
        return False, f"Couldn't read Windows' crash records: {e}"
    for event in root.iter(f"{ns}Event"):
        event_id = (event.findtext(f"{ns}System/{ns}EventID") or "").strip()
        positional = _UNNAMED_FIELDS.get(event_id, ())
        fields = []
        for index, data in enumerate(event.iter(f"{ns}Data")):
            name = data.get("Name") or (positional[index] if index < len(positional) else str(index + 1))
            fields.append((name, (data.text or "").strip()))
        values = dict(fields)
        if not _is_pid(values.get("ProcessId", ""), pid):
            continue
        app_name = values.get("AppName", "")
        if exe and app_name and app_name.lower() != exe.lower():
            continue   # another program that had the same id
        created = event.find(f"{ns}System/{ns}TimeCreated")
        when = created.get("SystemTime", "") if created is not None else ""
        if since and _event_epoch(when) and _event_epoch(when) < since - 5:
            continue   # an older process that had the same id
        provider = event.find(f"{ns}System/{ns}Provider")
        source = provider.get("Name", "?") if provider is not None else "?"
        lines = [f"Windows recorded it (Event Viewer > Windows Logs > Application, "
                 f"\"{source}\", event {event_id or '?'}, {when}):"]
        lines += [f"  {name}: {value}" for name, value in fields if value]
        return True, "\n".join(lines)
    return False, ""


# ── Exceptions ─────────────────────────────────────────────────────────────

def _excepthook(exc_type, exc, tb) -> None:
    """Record it and carry on. PyQt calls this for an exception raised in
    a slot or a Qt override; the app keeps running."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    record("Unhandled exception" + ("" if _started else " during startup"), text)
    try:
        from ai.log import log
        log(f"[crash] {exc_type.__name__}: {exc} (details in {_crash_path})", level="error")
    except Exception:
        pass
    if exc_type is KeyboardInterrupt:
        sys.__excepthook__(exc_type, exc, tb)


def fatal(exc_type, exc, tb) -> None:
    """An exception escaped main(): record it, say so, and end the
    process (os._exit: background threads must not keep it alive)."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    record("Kestrel stopped on an error", text)
    # Recorded just above: the next launch doesn't need to report it again.
    _remove_marker()
    show_fatal_error(f"{exc_type.__name__}: {exc}")
    os._exit(1)


def _thread_excepthook(args) -> None:
    if args.exc_type is SystemExit:
        return
    name = getattr(args.thread, "name", "?")
    text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    record(f"Unhandled exception in thread {name!r}", text)
    try:
        from ai.log import log
        log(f"[crash] thread {name}: {args.exc_type.__name__}: {args.exc_value}", level="error")
    except Exception:
        pass


def show_fatal_error(summary: str, heading: str = "Kestrel couldn't start.", details: bool = True) -> None:
    """A message box that needs nothing but Windows itself, for failures
    that happen before (or instead of) the window. details=False leaves
    out the pointer to crash.log, for a message that isn't about an
    error (an information icon instead of an error one)."""
    text = f"{heading}\n\n{summary}"
    if details:
        text += f"\n\nThe details are in:\n{_crash_path or '(no log folder)'}"
    if sys.platform == "win32":
        try:
            import ctypes
            MB_ICONERROR, MB_ICONINFORMATION, MB_SETFOREGROUND = 0x10, 0x40, 0x10000
            icon = MB_ICONERROR if details else MB_ICONINFORMATION
            ctypes.windll.user32.MessageBoxW(None, text, "Kestrel", icon | MB_SETFOREGROUND)
            return
        except Exception:
            pass
    try:
        sys.__stderr__.write(text + "\n")
    except Exception:
        pass
