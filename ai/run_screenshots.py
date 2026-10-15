# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/run_screenshots.py
=======================
Keeping the screens an autonomous run looked at.

run_autonomous_mode captures the screen before every step and sends it
to the model. Those captures were never kept, so afterwards there was
no way to see what the assistant had seen when it decided what to
click. Now each one is saved into a folder for the run, and the chat
shows the set when the run ends (a [[RUN_SCREENSHOTS:<folder>]] row,
see app/chat_widget.py), opening in the viewer with previous/next.

Saving happens on a background thread: encoding a full-screen PNG takes
long enough to notice, and the step loop should not wait for disk.
Runs older than KEEP_DAYS, beyond the newest KEEP_RUNS, are deleted
when a new run starts, so this does not fill the disk over months.
"""

from __future__ import annotations

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

from ai.log import log_debug

KEEP_RUNS = 40
KEEP_DAYS = 30


class RunScreenshots:
    def __init__(self, root: str):
        self.root = root
        self.folder = ""
        self.count = 0
        self._executor: "ThreadPoolExecutor | None" = None
        self._pending: list = []

    def start(self) -> "RunScreenshots":
        prune(self.root)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = os.path.join(self.root, stamp)
        suffix = 1
        while os.path.exists(folder):
            suffix += 1
            folder = os.path.join(self.root, f"{stamp}-{suffix}")
        try:
            os.makedirs(folder, exist_ok=True)
            self.folder = folder
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="run-screenshots")
        except OSError as e:
            log_debug(f"[run_screenshots] not keeping this run's screens: {e}")
        return self

    def save(self, step: int, image) -> None:
        """Queue one step's capture (a PIL image). Never raises."""
        if not self.folder or self._executor is None or image is None:
            return
        path = os.path.join(self.folder, f"step_{step:03d}.png")
        self.count += 1
        try:
            copy = image.copy()   # the caller goes on to resize/convert its own
        except Exception:
            return
        self._pending.append(self._executor.submit(_write_png, copy, path))

    def finish(self, timeout: float = 5.0) -> "tuple[str, int]":
        """Wait (briefly) for the saves still queued, and return
        (folder, how many were saved). ("", 0) when nothing was kept."""
        if self._pending:
            wait(self._pending, timeout=timeout)
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
        saved = 0
        if self.folder and os.path.isdir(self.folder):
            saved = sum(1 for n in os.listdir(self.folder) if n.endswith(".png"))
            if saved == 0:
                shutil.rmtree(self.folder, ignore_errors=True)
        return (self.folder, saved) if saved else ("", 0)


def _write_png(image, path: str) -> None:
    try:
        # compress_level=1: a fraction of the default's encode time, and a
        # screenshot is mostly flat colour, which compresses well anyway.
        image.save(path, format="PNG", compress_level=1)
    except Exception as e:
        log_debug(f"[run_screenshots] couldn't save {path}: {e}")


def prune(root: str, keep_runs: int = KEEP_RUNS, keep_days: int = KEEP_DAYS) -> int:
    """Delete run folders beyond the newest `keep_runs` or older than
    `keep_days`. Returns how many were removed."""
    try:
        entries = [os.path.join(root, n) for n in os.listdir(root)]
    except OSError:
        return 0
    folders = sorted((p for p in entries if os.path.isdir(p)), key=os.path.getmtime, reverse=True)
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for index, folder in enumerate(folders):
        if index >= keep_runs or os.path.getmtime(folder) < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed
