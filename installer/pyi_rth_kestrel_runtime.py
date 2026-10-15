# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
installer/pyi_rth_kestrel_runtime.py
======================================
A PyInstaller runtime hook, listed in Kestrel.spec. It runs inside
Kestrel.exe before main.py, and before PyInstaller's own runtime hook for
PyQt6, which imports PyQt6.QtCore and so loads Qt's DLLs and a C++
runtime along with them. Choosing which C++ runtime the process uses has
to happen before that, so it happens here. See app/win_runtime.py.

(PyInstaller runs the hooks a spec file lists before the ones it adds for
the packages it finds.)

Nothing here may stop the app from starting: an error in a runtime hook
ends Kestrel.exe with PyInstaller's own error box, before crash.log exists.
"""

try:
    from app import win_runtime

    win_runtime.pin_newest_cpp_runtime()
except Exception:  # noqa: BLE001
    pass
