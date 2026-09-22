# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_paths.py
=====================
Tests for paths.py - the single source of truth for BASE_PATH, used
instead of __file__-based resolution so file lookups (ai/instructions.txt,
app/ui/*.ui, assets/) keep working once packaged with PyInstaller.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import paths  # noqa: E402


def test_base_path_points_at_the_real_project_root_when_not_frozen():
    # paths.py sits at the project root, so BASE_PATH should be its directory,
    # and that directory should contain the project's top-level files.
    assert os.path.isfile(os.path.join(paths.BASE_PATH, "main.py"))
    assert os.path.isdir(os.path.join(paths.BASE_PATH, "ai"))
    assert os.path.isdir(os.path.join(paths.BASE_PATH, "app"))


def test_compute_base_path_uses_meipass_when_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/fake/meipass/dir", raising=False)

    assert paths._compute_base_path() == "/fake/meipass/dir"


def test_compute_base_path_falls_back_to_executable_dir_when_frozen_without_meipass(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", "/fake/dist/Kestrel/Kestrel.exe", raising=False)

    assert paths._compute_base_path() == "/fake/dist/Kestrel"


def test_compute_base_path_ignores_frozen_state_being_falsy(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert paths._compute_base_path() == paths.BASE_PATH


def test_user_data_path_equals_base_path_when_not_frozen(monkeypatch):
    # Running from source: no separate user-data location - same as
    # always, so dev workflows (macros/DB/.env sitting in the project
    # folder) don't change.
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert paths._compute_user_data_path("/some/project/root") == "/some/project/root"


def test_user_data_path_uses_localappdata_when_frozen(monkeypatch):
    # Packaged build: BASE_PATH (sys._MEIPASS for --onefile, or the
    # install dir for --onedir) is either wiped on exit or often not
    # writable without admin - user data has to live somewhere else.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", "/fake/AppData/Local")
    monkeypatch.delenv("APPDATA", raising=False)

    result = paths._compute_user_data_path("/fake/meipass")

    assert result == os.path.join("/fake/AppData/Local", "Kestrel")


def test_user_data_path_falls_back_to_appdata_then_home_when_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("APPDATA", "/fake/AppData/Roaming")

    assert paths._compute_user_data_path("/fake/meipass") == os.path.join("/fake/AppData/Roaming", "Kestrel")

    monkeypatch.delenv("APPDATA", raising=False)
    result = paths._compute_user_data_path("/fake/meipass")
    assert result == os.path.join(os.path.expanduser("~"), "Kestrel")
