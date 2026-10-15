# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_win_runtime.py
===========================
app/win_runtime.py: reading a DLL's version from the file itself, and
picking the newest copy of the C++ runtime among the folders it can be
loaded from.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app import win_runtime


def _fake_dll(path, version, junk=b"MZ" + b"\x00" * 300):
    """Bytes laid out like the version resource of a real DLL: the
    VS_VERSION_INFO key, padding, then VS_FIXEDFILEINFO."""
    major, minor, build, revision = version
    header = struct.pack("<HHH", 0x2F4, 0x34, 0) + "VS_VERSION_INFO\0".encode("utf-16-le") + b"\x00\x00"
    fixed = struct.pack("<IIII", 0xFEEF04BD, 0x10000, (major << 16) | minor, (build << 16) | revision)
    path.write_bytes(junk + header + fixed + b"\x00" * 64)


def test_the_version_is_read_from_the_file(tmp_path):
    dll = tmp_path / "msvcp140.dll"
    _fake_dll(dll, (14, 44, 35211, 0))
    assert win_runtime.file_version(str(dll)) == (14, 44, 35211, 0)


def test_a_file_without_a_version_resource_has_none(tmp_path):
    dll = tmp_path / "msvcp140.dll"
    dll.write_bytes(b"MZ" + b"\x00" * 500)
    assert win_runtime.file_version(str(dll)) == ()
    assert win_runtime.file_version(str(tmp_path / "missing.dll")) == ()


def test_the_newest_copy_wins_wherever_it_is(tmp_path):
    bundle, qt_bin, system = (tmp_path / name for name in ("bundle", "qt", "system32"))
    for folder, version in ((bundle, (14, 29, 30133, 0)), (qt_bin, (14, 40, 33810, 0)), (system, (14, 44, 35211, 0))):
        folder.mkdir()
        _fake_dll(folder / "msvcp140.dll", version)
    path, version, copies = win_runtime.newest_copy([str(bundle), str(qt_bin), str(system)])
    assert path == str(system / "msvcp140.dll")
    assert version == (14, 44, 35211, 0) and copies == 3


def test_the_first_folder_wins_a_tie(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    for folder in (first, second):
        folder.mkdir()
        _fake_dll(folder / "msvcp140.dll", (14, 40, 33810, 0))
    path, _, _ = win_runtime.newest_copy([str(first), str(second)])
    assert path == str(first / "msvcp140.dll")


def test_no_readable_copy_means_no_choice(tmp_path):
    (tmp_path / "msvcp140.dll").write_bytes(b"not a dll")
    assert win_runtime.newest_copy([str(tmp_path), str(tmp_path / "nothing-here")]) is None


def test_a_frozen_app_looks_in_its_bundle_and_in_qt_first(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    folders = win_runtime.candidate_folders()
    assert folders[0] == str(tmp_path)
    assert folders[1] == os.path.join(str(tmp_path), "PyQt6", "Qt6", "bin")
    assert folders[-1] == os.path.join(r"C:\Windows", "System32")


def test_outside_windows_nothing_is_loaded(monkeypatch):
    monkeypatch.setattr(win_runtime, "_attempted", False)
    monkeypatch.setattr(win_runtime.sys, "platform", "linux")
    assert win_runtime.pin_newest_cpp_runtime() == "not Windows"
    assert win_runtime.dpi_awareness() == "n/a"


def test_main_py_finds_it_done_when_the_runtime_hook_did_it(monkeypatch):
    """Kestrel.exe pins the runtime from a PyInstaller runtime hook, before
    main.py runs; main.py's own call must then change nothing."""
    monkeypatch.setattr(win_runtime, "_attempted", True)
    monkeypatch.setattr(win_runtime, "summary", "msvcp140.dll 14.44.35211.0 from X (newest of 3)")
    monkeypatch.setattr(win_runtime, "candidate_folders", lambda: pytest.fail("looked again"))
    assert win_runtime.pin_newest_cpp_runtime() == "msvcp140.dll 14.44.35211.0 from X (newest of 3)"


def test_the_spec_runs_the_runtime_hook():
    root = os.path.join(os.path.dirname(__file__), "..")
    spec = open(os.path.join(root, "Kestrel.spec"), encoding="utf-8").read()
    assert "pyi_rth_kestrel_runtime.py" in spec and "runtime_hooks=RUNTIME_HOOKS" in spec
    hook = open(os.path.join(root, "installer", "pyi_rth_kestrel_runtime.py"), encoding="utf-8").read()
    assert "win_runtime.pin_newest_cpp_runtime()" in hook


def test_a_failed_first_attempt_is_what_gets_reported(monkeypatch):
    """If the runtime hook's attempt failed, main.py's call must not look
    again: Qt has loaded a runtime of its own by then, and the report would
    blame that one instead of saying the pin failed."""
    monkeypatch.setattr(win_runtime, "_attempted", False)
    monkeypatch.setattr(win_runtime, "_loaded", [])
    monkeypatch.setattr(win_runtime.sys, "platform", "win32")

    def broken():
        raise OSError("no access")

    monkeypatch.setattr(win_runtime, "_loaded_path", lambda name: broken())
    first = win_runtime.pin_newest_cpp_runtime()
    assert first.startswith("not pinned: ")
    monkeypatch.setattr(win_runtime, "_loaded_path", lambda name: "C:/elsewhere/msvcp140.dll")
    assert win_runtime.pin_newest_cpp_runtime() == first
