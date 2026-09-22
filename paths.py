# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
paths.py
========
Single source of truth for "where is the project root".

Every module used to compute this itself via os.path.dirname(os.path.abspath(
__file__)) - which works fine running from source, but breaks once packaged
with PyInstaller: __file__ for frozen/bundled modules doesn't reliably point
at a real file on disk, so relative-path resolution (finding ai/instructions.txt,
the .ui files, assets/, etc.) could silently fail or point at the wrong place.

Import BASE_PATH from here instead of recomputing it locally.
"""

import os
import shutil
import sys

from branding import APP_NAME, LEGACY_APP_SLUGS


def _compute_base_path() -> str:
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys.frozen=True and extracts/places bundled data
        # files under sys._MEIPASS (--onefile, a temp dir) - and for --onedir
        # builds that attribute is set too, pointing at the folder holding
        # the executable. Either way, that's where the `datas` entries in
        # the .spec land, mirroring the source tree layout.
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))

    # Running from source: this file sits at the project root.
    return os.path.dirname(os.path.abspath(__file__))


BASE_PATH = _compute_base_path()


def _compute_user_data_path(base_path: str) -> str:
    """
    Where PERSISTENT, user-generated data (macros, the chat DB, the .env
    holding the API key) actually belongs. NOT the same thing as
    BASE_PATH once packaged:

      - --onefile: BASE_PATH is sys._MEIPASS, a fresh temp extraction
        directory PyInstaller deletes when the app closes. Anything
        written there is gone the moment Kestrel exits.
      - --onedir / installed: BASE_PATH is the install directory, which
        on Windows is very commonly Program Files - not writable by a
        standard (non-admin) user. Writes there fail with a
        PermissionError.

    Either way, saving a macro (ai/macro_store.py) - or anything else -
    under BASE_PATH in a packaged build either silently vanishes on
    restart or silently fails outright (function-tool exceptions are
    swallowed by the google-genai SDK's automatic function calling, so
    there's no visible error either way - it just looks like "the macro
    never gets created").

    Running from source, BASE_PATH already IS a real, permanent,
    user-writable directory (the project folder) - same as it's always
    been, so this intentionally returns it unchanged rather than
    introducing a new location dev workflows don't expect.
    """
    if not getattr(sys, "frozen", False):
        return base_path

    appdata = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, APP_NAME)


def _migrate_legacy_user_data(current: str) -> None:
    """
    Move a packaged install's data folder over from a name the app used
    to ship under (see branding.LEGACY_APP_SLUGS). Renaming the app
    should not lose anyone their macros, chat history or settings.

    Only runs when the new folder does not exist yet, so it can never
    overwrite live data, and any failure is left alone rather than
    raised: a failed migration means starting fresh, which is bad, but
    an exception here means the app does not start at all.
    """
    if not getattr(sys, "frozen", False) or os.path.isdir(current):
        return
    parent = os.path.dirname(current)
    for legacy in LEGACY_APP_SLUGS:
        old = os.path.join(parent, legacy)
        if os.path.isdir(old) and os.path.abspath(old) != os.path.abspath(current):
            try:
                shutil.move(old, current)
            except OSError as e:
                print(f"[paths] Could not move {old} to {current}: {e}")
            return


USER_DATA_PATH = _compute_user_data_path(BASE_PATH)
_migrate_legacy_user_data(USER_DATA_PATH)

# Single source of truth for "what build is this" - shown in the startup
# banner (see main.py) and available to anything else that wants to display
# it (About dialog, bug reports, etc.). Bump this by hand when a release
# is cut; there's no automated versioning pipeline yet, so it's just a
# plain string, not derived from git. CHANGELOG.md is a standing feature
# list rather than a release history, so it does not need an entry.
APP_VERSION = "0.2.1"
