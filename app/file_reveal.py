# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/file_reveal.py
==================
"Show in folder" for files the AI creates - opens File Explorer on the
folder holding the file, with the file itself pre-selected when it still
exists (the same thing Explorer's own right-click > "Show in folder" /
a browser's "Show in folder" download button does).

Kept out of chat_widget.py so the platform/subprocess logic is testable
without building any Qt widgets.
"""

from __future__ import annotations

import os
import subprocess
import sys


def reveal_in_explorer(path: str) -> bool:
    """
    Opens File Explorer at `path`. Returns True if something was
    launched, False if there was nothing to open (empty path, or neither
    the file nor its parent folder exists anymore).

    - File still exists  -> Explorer opens its folder with the file selected.
    - File is gone but its folder isn't (moved/deleted since the AI made it)
      -> just opens the folder.
    - Neither exists -> returns False so the caller can tell the user,
      rather than Explorer silently falling back to opening Documents.
    """
    if not path or not path.strip():
        return False

    path = os.path.normpath(path)
    folder = os.path.dirname(path)

    if os.path.isfile(path):
        target_is_file = True
    elif folder and os.path.isdir(folder):
        target_is_file = False
    else:
        return False

    try:
        if sys.platform == "win32":
            if target_is_file:
                # A single string on purpose, with the path quoted by hand:
                # Explorer's /select switch has its own idiosyncratic
                # argument parsing (comma right after the switch, path
                # quoted), and a list-form argv gets re-quoted in a way that
                # doesn't reliably round-trip for paths with spaces - and
                # "Documents\Kestrel Files" has one. Explorer also returns a
                # non-zero exit code even on success, so the result is
                # deliberately never checked.
                subprocess.Popen(f'explorer /select,"{path}"')
            else:
                os.startfile(folder)  # type: ignore[attr-defined]
            return True
    except Exception as e:
        print(f"[file_reveal] Explorer launch failed ({e}); falling back to the Qt opener.")

    # Non-Windows (dev machines) or the Windows launch above failed: open
    # the folder with whatever the desktop associates with directories.
    try:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))
    except Exception as e:
        print(f"[file_reveal] Couldn't open {folder}: {e}")
        return False
