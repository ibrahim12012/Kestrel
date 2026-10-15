# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_run_screenshots.py
===============================
ai/run_screenshots.py: an autonomous run's screens are kept in a folder
of their own, and old runs are cleared out.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

PIL = pytest.importorskip("PIL.Image")

from ai import run_screenshots
from ai.run_screenshots import RunScreenshots


def _screen(colour="white"):
    return PIL.new("RGB", (64, 40), colour)


@pytest.fixture
def root(tmp_path):
    """A folder of its own: tmp_path also holds the suite's macro sandbox."""
    folder = tmp_path / "runs"
    folder.mkdir()
    return folder


def test_each_step_is_saved_in_the_runs_folder(root):
    run = RunScreenshots(str(root)).start()
    for step in (1, 2, 3):
        run.save(step, _screen())
    folder, count = run.finish()
    assert count == 3
    assert os.path.dirname(folder) == str(root)
    assert sorted(os.listdir(folder)) == ["step_001.png", "step_002.png", "step_003.png"]
    assert PIL.open(os.path.join(folder, "step_002.png")).size == (64, 40)


def test_the_saved_screen_is_a_copy_taken_when_saved(root):
    run = RunScreenshots(str(root)).start()
    image = _screen("white")
    run.save(1, image)
    image.paste((255, 0, 0), (0, 0, 64, 40))          # the caller keeps using its image
    folder, _ = run.finish()
    assert PIL.open(os.path.join(folder, "step_001.png")).getpixel((5, 5)) == (255, 255, 255)


def test_a_run_with_no_screens_leaves_nothing_behind(root):
    run = RunScreenshots(str(root)).start()
    run.save(1, None)
    assert run.finish() == ("", 0)
    assert os.listdir(root) == []


def test_two_runs_in_the_same_second_get_their_own_folders(root):
    first = RunScreenshots(str(root)).start()
    second = RunScreenshots(str(root)).start()
    assert first.folder != second.folder


def test_a_folder_that_cannot_be_made_is_not_an_error(root):
    blocked = root / "a-file"
    blocked.write_text("not a folder")
    run = RunScreenshots(str(blocked)).start()
    run.save(1, _screen())
    assert run.finish() == ("", 0)


def test_old_and_surplus_runs_are_pruned(root):
    now = time.time()
    for n in range(6):
        folder = root / f"run{n}"
        folder.mkdir()
        os.utime(folder, (now - n * 60, now - n * 60))      # run0 newest
    stale = root / "ancient"
    stale.mkdir()
    os.utime(stale, (now - 90 * 86400, now - 90 * 86400))
    removed = run_screenshots.prune(str(root), keep_runs=4, keep_days=30)
    assert removed == 3
    assert sorted(os.listdir(root)) == ["run0", "run1", "run2", "run3"]


def test_pruning_a_missing_folder_does_nothing(root):
    assert run_screenshots.prune(str(root / "nope")) == 0
