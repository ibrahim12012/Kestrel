# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/win_runtime.py
====================
Load the newest copy of Microsoft's C++ runtime before anything needs it.

A frozen Kestrel can see several copies of msvcp140.dll: the one PyQt6
ships next to Qt, one PyInstaller may copy into the top of the bundle,
and the one in System32 from whatever Visual C++ Redistributable the
machine has. Windows loads the first one its search finds, and every
other DLL in the process that asks for msvcp140.dll then gets that same
copy, including DLLs other programs inject into every window (overlays,
mouse and keyboard utilities, screen recorders).

When that copy is older than the code calling it was built for, the
result is not an error message. It is an access violation the first time
that code locks a std::mutex (Visual Studio 17.10 changed how a
std::mutex is built, and runtimes before 14.40 can't lock the new kind).
Which copy wins depends on the DLL search order, which is different for
Kestrel.exe and for `python main.py`, and different between machines.

The runtime is backward compatible: the newest copy runs code built with
any older compiler. So the newest one is loaded first, by its full path,
and every later request for msvcp140.dll gets it.

"First" means before anything imports PyQt6. In Kestrel.exe that is
earlier than main.py: PyInstaller's own runtime hook for PyQt6 imports
QtCore before the app's code runs. So Kestrel.exe does this from a
runtime hook of its own that runs before PyInstaller's
(installer/pyi_rth_kestrel_runtime.py), and main.py's call, which is the
one that counts for `python main.py`, then finds it done.

Windows only, standard library only, and nothing here can stop the app
from starting: on any problem it does nothing, which is how it was before.
"""

from __future__ import annotations

import os
import struct
import sys

RUNTIME_DLL = "msvcp140.dll"
# One set with msvcp140.dll: loaded from the same folder when they are
# there, so they match it.
COMPANION_DLLS = (
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
)

_VS_VERSION_INFO = "VS_VERSION_INFO".encode("utf-16-le")
_FIXED_FILE_INFO_SIGNATURE = struct.pack("<I", 0xFEEF04BD)

# What pin_newest_cpp_runtime() did, for the startup log line.
summary = "not checked"
_attempted = False
_loaded = []      # keeps the loaded DLLs referenced for the life of the process


def file_version(path: str) -> tuple:
    """(major, minor, build, revision) from a DLL's version resource, read
    straight from the file. () when there is none or it can't be read."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return ()
    start = data.rfind(_VS_VERSION_INFO)
    if start < 0:
        return ()
    signature = data.find(_FIXED_FILE_INFO_SIGNATURE, start, start + 96)
    if signature < 0 or signature + 16 > len(data):
        return ()
    most, least = struct.unpack_from("<II", data, signature + 8)
    return (most >> 16, most & 0xFFFF, least >> 16, least & 0xFFFF)


def candidate_folders() -> list:
    """Every folder a copy of the runtime may be loaded from, most likely
    first. Found without importing PyQt6, which would load Qt, and with
    it a runtime, before there is a choice to make."""
    folders = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        folders.append(bundle)
        folders.append(os.path.join(bundle, "PyQt6", "Qt6", "bin"))
    else:
        try:
            import importlib.util

            spec = importlib.util.find_spec("PyQt6")
            for location in (spec.submodule_search_locations or []) if spec else []:
                folders.append(os.path.join(location, "Qt6", "bin"))
        except (ImportError, ValueError):
            pass
    folders.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
    return folders


def newest_copy(folders: list) -> "tuple[str, tuple, int] | None":
    """(path, version, copies found) for the newest msvcp140.dll in
    `folders`, or None when there is none with a readable version."""
    found = []
    for folder in folders:
        path = os.path.join(folder, RUNTIME_DLL)
        if os.path.isfile(path):
            version = file_version(path)
            if version:
                found.append((version, path))
    if not found:
        return None
    # The earliest folder wins a tie: the same file in two places.
    best = max(found, key=lambda item: item[0])
    return best[1], best[0], len(found)


def _loaded_path(name: str) -> str:
    """Full path of `name` if it is already loaded in this process, else ""."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleFileNameW.restype = wintypes.DWORD
    kernel32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    module = kernel32.GetModuleHandleW(name)
    if not module:
        return ""
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetModuleFileNameW(module, buffer, len(buffer))
    return buffer.value[:length] if length else name


def _version_text(version: tuple) -> str:
    return ".".join(str(n) for n in version)


def pin_newest_cpp_runtime() -> str:
    """Load the newest msvcp140.dll (and its companions from the same
    folder). Call before anything imports PyQt6 or another C++ extension.
    Returns a one-line summary, also kept in `summary`."""
    global summary, _attempted
    if _attempted:
        # Done already, by the runtime hook in Kestrel.exe. Its result
        # stands even if it failed: by now Qt has loaded a runtime of its
        # own, and looking again would only report that one instead.
        return summary
    _attempted = True
    if sys.platform != "win32":
        summary = "not Windows"
        return summary
    try:
        import ctypes

        already = _loaded_path(RUNTIME_DLL)
        if already:
            version = file_version(already)
            summary = f"{RUNTIME_DLL} {_version_text(version) or '?'} was already loaded from {os.path.dirname(already)}"
            return summary
        best = newest_copy(candidate_folders())
        if best is None:
            summary = f"no readable {RUNTIME_DLL} found; left to Windows"
            return summary
        path, version, copies = best
        _loaded.append(ctypes.WinDLL(path))
        folder = os.path.dirname(path)
        for name in COMPANION_DLLS:
            companion = os.path.join(folder, name)
            if os.path.isfile(companion):
                try:
                    _loaded.append(ctypes.WinDLL(companion))
                except OSError:
                    pass
        summary = f"{RUNTIME_DLL} {_version_text(version)} from {folder} (newest of {copies})"
    except Exception as e:  # noqa: BLE001 - this must never stop the app from starting
        summary = f"not pinned: {e}"
    return summary


def loaded_runtimes() -> str:
    """Which copy of each C/C++ runtime DLL the process has loaded, with
    its version, for the startup log line. vcruntime140 comes with Python
    itself and is loaded before any of this runs, so it is reported, not
    chosen."""
    if sys.platform != "win32":
        return "n/a"
    parts = []
    for name in ("vcruntime140.dll", "vcruntime140_1.dll", RUNTIME_DLL):
        try:
            path = _loaded_path(name)
        except Exception:  # noqa: BLE001 - diagnostics only
            path = ""
        if path:
            parts.append(f"{name} {_version_text(file_version(path)) or '?'} ({os.path.dirname(path)})")
        else:
            parts.append(f"{name} not loaded")
    return "; ".join(parts)


def use_per_monitor_v2() -> bool:
    """
    Put the process in per-monitor v2 DPI awareness: the mode Qt picks for
    itself, set before anything else gets to pick another one.

    A process gets one say. The first call wins and every later one fails,
    Qt's included. The uiautomation package sets per-monitor v1 when it is
    imported, and the Mapping page's imports used to load it before
    QApplication existed, so the app ran in v1 ("DPI awareness per-monitor"
    in the startup log line, not "per-monitor v2"). Qt is built and tested
    for v2. In v1 the window could end up drawn at the wrong scale, which
    showed as the window drawn twice after Alt+Tab (see app/dpi_guard.py).

    Called first thing in main.py. False when it couldn't be set (Windows
    10 before 1703, or something set a mode already); Qt then tries, as
    before.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_int
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        return bool(user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))
    except Exception:  # noqa: BLE001 - Qt sets it instead
        return False


def dpi_awareness() -> str:
    """The process's DPI awareness, in words, for the startup log line.
    Qt sets it when QApplication is created; call after that."""
    if sys.platform != "win32":
        return "n/a"
    try:
        import ctypes

        user32 = ctypes.WinDLL("user32")
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
        user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.AreDpiAwarenessContextsEqual.restype = ctypes.c_int
        context = user32.GetThreadDpiAwarenessContext()
        per_monitor_v2 = ctypes.c_void_p(-4)    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if user32.AreDpiAwarenessContextsEqual(context, per_monitor_v2):
            return "per-monitor v2"
        return {0: "unaware", 1: "system", 2: "per-monitor"}.get(
            user32.GetAwarenessFromDpiAwarenessContext(context), "unknown")
    except Exception:  # noqa: BLE001 - diagnostics only
        return "unknown"
