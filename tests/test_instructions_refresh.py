# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_instructions_refresh.py
====================================
An installed copy of instructions.txt follows the prompt the build ships
(ai.main._refresh_instructions_file), while the preferences learned on
that machine are kept. Before this, an update's prompt changes never
reached anyone who had already run the app once.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.main as ai_main

START, END = ai_main.PREFS_START_MARKER, ai_main.PREFS_END_MARKER

OLD_PROMPT = f"""OLD RULES: open the browser for every web question.

{START}
- The user prefers a direct, no-frills tone.
- Web navigation always step by step - never construct URLs directly.
- Use autonomous mode for any task requiring multiple steps or web navigation.
- The user's browser is Firefox.
{END}
"""

NEW_PROMPT = f"""NEW RULES: read the web yourself with the web tools.

{START}
- The user prefers a direct, no-frills tone.
- In the browser, navigate step by step - never construct URLs directly.
{END}
"""


@pytest.fixture
def files(tmp_path, monkeypatch):
    shipped = tmp_path / "program" / "instructions.txt"
    installed = tmp_path / "userdata" / "instructions.txt"
    shipped.parent.mkdir()
    installed.parent.mkdir()
    shipped.write_text(NEW_PROMPT, encoding="utf-8")
    installed.write_text(OLD_PROMPT, encoding="utf-8")
    monkeypatch.setattr(ai_main, "BUNDLED_INSTRUCTIONS_PATH", str(shipped))
    monkeypatch.setattr(ai_main, "INSTRUCTIONS_PATH", str(installed))
    return shipped, installed


def test_an_installed_prompt_follows_the_shipped_one(files):
    text = ai_main.load_instructions()
    assert text.startswith("NEW RULES")
    assert "OLD RULES" not in text


def test_learned_preferences_are_kept(files):
    assert "- The user's browser is Firefox." in ai_main.load_instructions()


def test_old_default_lines_are_updated_and_not_doubled(files):
    text = ai_main.load_instructions()
    assert "Web navigation always step by step" not in text
    assert text.count("- In the browser, navigate step by step - never construct URLs directly.") == 1
    assert "look up and read the web yourself with the web tools" in text
    assert "any task requiring multiple steps or web navigation" not in text


def test_the_replaced_file_is_kept(files):
    _, installed = files
    ai_main.load_instructions()
    backup = installed.parent / "instructions.previous.txt"
    assert backup.read_text(encoding="utf-8") == OLD_PROMPT
    assert installed.read_text(encoding="utf-8").startswith("NEW RULES")


def test_an_up_to_date_file_is_left_alone(files):
    _, installed = files
    ai_main.load_instructions()
    backup = installed.parent / "instructions.previous.txt"
    backup.unlink()
    before = installed.stat().st_mtime_ns
    ai_main.load_instructions()
    assert not backup.exists()
    assert installed.stat().st_mtime_ns == before


def test_a_preference_learned_after_the_update_lands_in_the_new_prompt(files):
    _, installed = files
    ai_main.load_instructions()
    updated = ai_main._append_preference(ai_main.load_instructions(), "The user works nights.")
    installed.write_text(updated, encoding="utf-8")
    text = ai_main.load_instructions()
    assert text.startswith("NEW RULES")
    assert text.rstrip().endswith(f"- The user works nights.\n{END}")


def test_a_file_without_a_preferences_block_is_not_touched(files):
    _, installed = files
    installed.write_text("A PROMPT SOMEONE REWROTE BY HAND", encoding="utf-8")
    assert ai_main.load_instructions() == "A PROMPT SOMEONE REWROTE BY HAND"


def test_the_shipped_file_itself_has_a_preferences_block():
    shipped = open(os.path.join(os.path.dirname(ai_main.__file__), "instructions.txt"), encoding="utf-8").read()
    before, lines, after = ai_main._split_preferences(shipped)
    assert "SECTION 3b - READING THE WEB YOURSELF" in before
    assert not any(line in ai_main._RENAMED_DEFAULT_PREFERENCES for line in lines)
