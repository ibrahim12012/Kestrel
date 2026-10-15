# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/web_render.py
==================
Reading a page that builds itself with JavaScript, with nothing on screen.

Plenty of sites send an empty shell and fill it in from scripts. Fetched
the plain way (ai/web_reader.py) such a page has next to no text, and the
only way left to read it was to drive the user's own browser through
autonomous mode. Chat assistants render these pages in a browser of
their own, off screen. This does the same with the Chromium browser
Windows already has, Microsoft Edge (or Chrome): started headless, with a
throwaway profile, so none of the user's sign-ins, cookies or history
are involved; with the same Kestrel-User identity as every other request
(ai.web_reader.USER_AGENT); and with no window. It hands back the page's
DOM once its scripts have run.

Only for a page that was served normally and came back nearly empty. A
response that was a human check is never rendered: getting past one is
a person's job (see ai/web_reader.py). The rendered page is checked
again, and a check that shows up after rendering stops the read the
same way.

KESTREL_HEADLESS_BROWSER points at a different Chromium-based browser;
KESTREL_RENDER_PAGES=0 turns rendering off.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from ai.log import log_debug

# Seconds of page time scripts get to fill the page in (virtual time: a
# page that is done sooner is dumped sooner).
SCRIPT_TIME_MS = 6000
# Wall-clock limit for the whole browser run, start to exit.
TIMEOUT = 20
MAX_DOM_BYTES = 3_000_000

_NO_CONSOLE_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class RenderError(Exception):
    pass


def _windows_candidates() -> "list[str]":
    roots = [os.environ.get(name, "") for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    tails = (
        ("Microsoft", "Edge", "Application", "msedge.exe"),
        ("Google", "Chrome", "Application", "chrome.exe"),
    )
    return [os.path.join(root, *tail) for tail in tails for root in roots if root]


def enabled() -> bool:
    return os.getenv("KESTREL_RENDER_PAGES", "1").strip().lower() not in ("0", "false", "no", "off")


def find_browser() -> "str | None":
    """The browser to render with: KESTREL_HEADLESS_BROWSER if it is set
    (and exists), else Edge, else Chrome. None when there is none."""
    override = os.getenv("KESTREL_HEADLESS_BROWSER", "").strip().strip('"')
    if override:
        return override if os.path.isfile(override) else None
    if sys.platform != "win32":
        return None
    for path in _windows_candidates():
        if os.path.isfile(path):
            return path
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def available() -> bool:
    return enabled() and find_browser() is not None


def _command(browser: str, url: str, user_agent: str, profile: str) -> "list[str]":
    args = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-breakpad",
        "--mute-audio",
        "--hide-scrollbars",
        "--window-size=1280,2000",
        f"--user-data-dir={profile}",
        f"--user-agent={user_agent}",
        f"--virtual-time-budget={SCRIPT_TIME_MS}",
    ]
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")    # Chromium refuses to run as root otherwise (development only)
    args += ["--dump-dom", url]
    return args


_PROFILE_PREFIX = "kestrel-render-"


def _clean_old_profiles(max_age: float = 3600.0) -> None:
    """Throwaway profiles a browser still held open when its run ended
    (a helper process exiting late) are removed on a later run."""
    import time
    root = tempfile.gettempdir()
    try:
        names = [n for n in os.listdir(root) if n.startswith(_PROFILE_PREFIX)]
    except OSError:
        return
    for name in names:
        path = os.path.join(root, name)
        try:
            if time.time() - os.path.getmtime(path) > max_age:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def render(url: str, user_agent: str) -> str:
    """The page's HTML after its scripts ran. Raises RenderError."""
    browser = find_browser() if enabled() else None
    if browser is None:
        raise RenderError("no browser to render with")
    if not url.lower().startswith(("http://", "https://")):
        raise RenderError(f"not a web address: {url}")
    _clean_old_profiles()
    profile = tempfile.mkdtemp(prefix=_PROFILE_PREFIX)
    try:
        result = subprocess.run(
            _command(browser, url, user_agent, profile),
            capture_output=True, timeout=TIMEOUT, creationflags=_NO_CONSOLE_WINDOW,
        )
    except subprocess.TimeoutExpired as e:
        raise RenderError(f"the page was still loading after {TIMEOUT} seconds") from e
    except OSError as e:
        raise RenderError(f"couldn't start {os.path.basename(browser)}: {e}") from e
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    dom = (result.stdout or b"")[:MAX_DOM_BYTES].decode("utf-8", errors="replace").strip()
    if not dom:
        detail = (result.stderr or b"")[-300:].decode("utf-8", errors="replace").strip()
        log_debug(f"[web_render] {url}: nothing rendered (exit {result.returncode}) {detail}")
        raise RenderError("the browser returned nothing")
    return dom
