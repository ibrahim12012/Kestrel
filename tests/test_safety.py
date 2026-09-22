# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_safety.py
=====================
Tests for ai/safety.py - the allowed-commands list and the per-tier
action limiter used by tool_run_command / tool_close_application.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.safety as safety
from ai.safety import RiskLevel, SafetyLimiter


def test_is_destructive_label_matches_known_keywords():
    assert safety.is_destructive_label("Delete Account") is True
    assert safety.is_destructive_label("delete_account") is True
    assert safety.is_destructive_label("Submit Order") is True
    assert safety.is_destructive_label("Format Drive") is True
    assert safety.is_destructive_label("Save") is False
    assert safety.is_destructive_label("Filter Results") is False


def test_is_destructive_label_handles_none_and_empty():
    assert safety.is_destructive_label(None) is False
    assert safety.is_destructive_label("") is False


def test_is_destructive_label_is_case_insensitive():
    assert safety.is_destructive_label("PURGE CACHE") is True
    assert safety.is_destructive_label("purge cache") is True


# ══════════════════════════════════════════════════════════════════
# is_likely_window_chrome_button
# ══════════════════════════════════════════════════════════════════

def _bbox(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def test_chrome_button_detects_real_close_button_top_right():
    root = _bbox(0, 0, 1200, 800)
    close_btn = _bbox(1155, 8, 45, 30)  # hugging the top-right corner
    assert safety.is_likely_window_chrome_button(close_btn, root) is True


def test_chrome_button_ignores_in_app_button_top_left():
    root = _bbox(0, 0, 1200, 800)
    ordinary_btn = _bbox(20, 20, 80, 30)
    assert safety.is_likely_window_chrome_button(ordinary_btn, root) is False


def test_chrome_button_ignores_in_app_button_that_happens_to_sit_top_right_but_is_large():
    root = _bbox(0, 0, 1200, 800)
    wide_button = _bbox(900, 10, 280, 40)  # too wide to be a titlebar button
    assert safety.is_likely_window_chrome_button(wide_button, root) is False


def test_chrome_button_never_flags_everything_in_a_small_window():
    # Regression test: on a small window, the fixed-pixel corner zone
    # used to be BIGGER than the window itself, so every element in it
    # regardless of actual position - registered as "near the top-right
    # corner" and got misclassified as window chrome. A button the same
    # size as its own tiny window is never a titlebar button.
    tiny_root = _bbox(0, 0, 10, 10)
    tiny_button = _bbox(0, 0, 10, 10)
    assert safety.is_likely_window_chrome_button(tiny_button, tiny_root) is False


def test_chrome_button_still_detects_real_close_button_on_a_small_dialog():
    # A genuinely small dialog (300x200) with an in-app button that is
    # still clearly small relative to its own window, and clearly in the
    # corner, should still be caught.
    small_root = _bbox(0, 0, 300, 200)
    small_close = _bbox(275, 5, 20, 18)
    assert safety.is_likely_window_chrome_button(small_close, small_root) is True


def test_chrome_button_ignores_relatively_large_button_on_small_dialog():
    # Same small dialog, but the button takes up a big chunk of it
    # not a titlebar control no matter where it sits.
    small_root = _bbox(0, 0, 300, 200)
    big_button = _bbox(150, 5, 140, 90)
    assert safety.is_likely_window_chrome_button(big_button, small_root) is False


def test_chrome_button_handles_missing_or_malformed_bbox():
    assert safety.is_likely_window_chrome_button({}, _bbox(0, 0, 100, 100)) is False
    assert safety.is_likely_window_chrome_button(_bbox(0, 0, 10, 10), {}) is False


def test_classify_command_known_commands():
    assert safety.classify_command("dir") == RiskLevel.LOW
    assert safety.classify_command("git") == RiskLevel.MEDIUM
    assert safety.classify_command("del") == RiskLevel.HIGH


def test_classify_command_is_case_insensitive_and_strips_exe():
    assert safety.classify_command("GIT") == RiskLevel.MEDIUM
    assert safety.classify_command("git.exe") == RiskLevel.MEDIUM
    assert safety.classify_command("  Python.EXE  ") == RiskLevel.MEDIUM


def test_classify_command_returns_none_for_unlisted_command():
    assert safety.classify_command("some_random_tool") is None


def test_is_blocked_command():
    assert safety.is_blocked_command("format") is True
    assert safety.is_blocked_command("diskpart.exe") is True
    assert safety.is_blocked_command("git") is False


def test_blocked_commands_are_never_also_allowed():
    # A command can't be both refused-as-blocked AND accepted via the
    # allow-list - that would make the block meaningless.
    for name in safety.BLOCKED_COMMANDS:
        assert name not in safety.ALLOWED_COMMANDS


def test_limiter_allows_actions_up_to_the_limit():
    limiter = SafetyLimiter({RiskLevel.LOW: 2})
    assert limiter.check_and_record(RiskLevel.LOW) is None
    assert limiter.check_and_record(RiskLevel.LOW) is None


def test_limiter_refuses_once_limit_is_reached():
    limiter = SafetyLimiter({RiskLevel.HIGH: 1})
    assert limiter.check_and_record(RiskLevel.HIGH) is None
    refusal = limiter.check_and_record(RiskLevel.HIGH)
    assert refusal is not None
    assert "SAFETY LIMIT REACHED" in refusal


def test_limiter_reset_clears_counts_not_limits():
    limiter = SafetyLimiter({RiskLevel.MEDIUM: 1})
    limiter.check_and_record(RiskLevel.MEDIUM)
    assert limiter.check_and_record(RiskLevel.MEDIUM) is not None  # exhausted

    limiter.reset()

    assert limiter.check_and_record(RiskLevel.MEDIUM) is None  # budget refreshed
    assert limiter.get_limit(RiskLevel.MEDIUM) == 1  # limit itself unchanged


def test_limiter_set_limit_updates_cap():
    limiter = SafetyLimiter({RiskLevel.LOW: 1})
    limiter.set_limit(RiskLevel.LOW, 5)
    assert limiter.get_limit(RiskLevel.LOW) == 5


def test_limiter_tiers_are_independent():
    limiter = SafetyLimiter({RiskLevel.LOW: 0, RiskLevel.HIGH: 5})
    assert limiter.check_and_record(RiskLevel.LOW) is not None  # LOW exhausted immediately
    assert limiter.check_and_record(RiskLevel.HIGH) is None  # HIGH unaffected


def test_default_max_actions_ordering_is_low_to_high_risk():
    # The whole point of the framework: riskier tiers get a tighter cap.
    assert (
        safety.DEFAULT_MAX_ACTIONS[RiskLevel.LOW]
        > safety.DEFAULT_MAX_ACTIONS[RiskLevel.MEDIUM]
        > safety.DEFAULT_MAX_ACTIONS[RiskLevel.HIGH]
    )
