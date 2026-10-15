# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_ai_main.py
======================
Tests for ai/main.py's pure logic - currently just the AUTONOMOUS_SUGGEST
protocol parser (extraction of the tag ai/instructions.txt teaches the
model to emit when it wants to propose autonomous mode).

Everything else in ai/main.py (Gemini calls, screenshots, keyboard hooks,
the voice assistant) needs real credentials/hardware and isn't something
we unit test here - see conftest.py for how the module-level imports
(google.genai, pyautogui, keyboard, AppOpener) are stubbed so this module
can even be imported.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.main as ai_main  # noqa: E402


def test_extract_autonomous_suggestion_returns_none_when_absent():
    text = "Sure, opening Chrome now."
    clean, goal = ai_main._extract_autonomous_suggestion(text)
    assert clean == text
    assert goal is None


def test_extract_autonomous_suggestion_extracts_goal_and_strips_tag():
    text = (
        "This needs a few steps. Shall I go ahead?\n"
        "[[AUTONOMOUS_SUGGEST: open Gmail and reply to John]]"
    )
    clean, goal = ai_main._extract_autonomous_suggestion(text)
    assert goal == "open Gmail and reply to John"
    assert "AUTONOMOUS_SUGGEST" not in clean
    assert clean == "This needs a few steps. Shall I go ahead?"


# ══════════════════════════════════════════════════════════════════
# _StreamMarkerFilter - the live "don't leak a control tag mid-stream"
# gate ai_stream() puts between a provider's raw deltas and whatever
# on_delta callback the UI passed in.
# ══════════════════════════════════════════════════════════════════

def _run_filter(chunks: list) -> str:
    out = []
    f = ai_main._StreamMarkerFilter(out.append)
    for chunk in chunks:
        f.feed(chunk)
    f.finalize()
    return "".join(out)


def test_stream_marker_filter_passes_plain_text_through_untouched():
    assert _run_filter(["Hello ", "world, ", "no markers here."]) == "Hello world, no markers here."


def test_stream_marker_filter_hides_autonomous_suggest_tag_split_across_chunks():
    visible = _run_filter([
        "Sure, doing", " it now.", "\n\n[[AUTONOMOUS", "_SUGGEST: open ", "chrome]]",
    ])
    assert "AUTONOMOUS_SUGGEST" not in visible
    assert "chrome" not in visible
    assert visible.startswith("Sure, doing it now.")


def test_stream_marker_filter_hides_update_start_tag():
    visible = _run_filter([
        "Got it, I'll remember that.", "[[UPDATE_START]]", "prefers dark mode", "[[UPDATE_END]]",
    ])
    assert "UPDATE_START" not in visible
    assert visible == "Got it, I'll remember that."


def test_stream_marker_filter_shows_nothing_when_reply_is_only_a_tag():
    # _process_full_response substitutes a placeholder text for this
    # case (see ai_stream()'s own docstring on why the live filtered
    # view and the final persisted text can differ here) - the filter
    # itself is only responsible for never leaking the raw tag.
    assert _run_filter(["[[AUTONOMOUS_SUGGEST: do the thing]]"]) == ""


def test_stream_marker_filter_does_not_mistake_literal_double_brackets_for_a_tag():
    # The model writing literal "[[...]]" that ISN'T one of the two
    # known control tags is real content, not something to hide.
    visible = _run_filter(["Use ", "[[", "not-a-marker", "]] syntax."])
    assert visible == "Use [[not-a-marker]] syntax."


def test_stream_marker_filter_handles_character_by_character_delivery():
    visible = _run_filter(list("Hi![[AUTONOMOUS_SUGGEST: go]]"))
    assert visible == "Hi!"


def test_stream_marker_filter_never_leaks_a_tag_truncated_by_a_dropped_stream():
    # The stream ends (network drop, provider error) mid control-tag
    # finalize() should still never flush the partial tag fragment.
    visible = _run_filter(["All done. ", "[[AUT"])
    assert visible == "All done. "


# ══════════════════════════════════════════════════════════════════
# Chat-dropped image attachments (queue_chat_images) and the
# streaming setting (get/set_streaming_enabled)
# ══════════════════════════════════════════════════════════════════

def test_queue_chat_images_round_trips_and_clears():
    sentinel = ["fake-image-1", "fake-image-2"]
    ai_main.queue_chat_images(sentinel)
    assert ai_main._pop_pending_chat_images() == sentinel
    # Popping clears it - a second pop with nothing newly queued is empty.
    assert ai_main._pop_pending_chat_images() == []


def test_streaming_enabled_defaults_on_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_main, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.delenv("KESTREL_STREAMING_ENABLED", raising=False)

    assert ai_main.get_streaming_enabled() is True  # unlike wake word/telemetry, no privacy downside to default on

    ai_main.set_streaming_enabled(False)
    assert ai_main.get_streaming_enabled() is False

    ai_main.set_streaming_enabled(True)
    assert ai_main.get_streaming_enabled() is True


def test_local_base_url_defaults_to_ollama_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_main, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.delenv("KESTREL_LOCAL_BASE_URL", raising=False)
    monkeypatch.setattr(ai_main, "_initialized", False)  # skip the chat-session rebuild path

    assert ai_main.get_local_base_url() == ai_main.DEFAULT_LOCAL_BASE_URL

    ai_main.set_local_base_url("http://192.168.1.50:1234/v1")
    assert ai_main.get_local_base_url() == "http://192.168.1.50:1234/v1"


def test_local_base_url_blank_falls_back_to_default(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_main, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.delenv("KESTREL_LOCAL_BASE_URL", raising=False)
    monkeypatch.setattr(ai_main, "_initialized", False)

    ai_main.set_local_base_url("   ")
    assert ai_main.get_local_base_url() == ai_main.DEFAULT_LOCAL_BASE_URL


def test_local_model_name_defaults_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_main, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.delenv("KESTREL_LOCAL_MODEL", raising=False)
    monkeypatch.setattr(ai_main, "_initialized", False)

    assert ai_main.get_local_model_name() == ai_main.DEFAULT_LOCAL_MODEL_NAME

    ai_main.set_local_model_name("qwen2.5-coder:7b")
    assert ai_main.get_local_model_name() == "qwen2.5-coder:7b"


def test_local_base_url_change_rebuilds_chat_only_when_local_is_active(tmp_path, monkeypatch):
    """Mirrors save_api_key_for()'s own "only rebuild if this provider
    is the active one" behavior - changing the local server URL while
    actively chatting with Gemini shouldn't touch the live session."""
    from unittest.mock import MagicMock, patch

    monkeypatch.setattr(ai_main, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr(ai_main, "_initialized", True)
    monkeypatch.setattr(ai_main, "get_active_provider", lambda: "gemini")
    sentinel_chat = MagicMock()
    monkeypatch.setattr(ai_main, "chat", sentinel_chat)

    with patch("ai.providers.switch_provider") as mock_switch:
        ai_main.set_local_base_url("http://localhost:9999/v1")

    mock_switch.assert_not_called()
    assert ai_main.chat is sentinel_chat


def test_extract_autonomous_suggestion_handles_multiline_goal():
    text = "Let's do this.\n[[AUTONOMOUS_SUGGEST: search for\nlo-fi music]]"
    clean, goal = ai_main._extract_autonomous_suggestion(text)
    assert goal == "search for\nlo-fi music"
    assert clean == "Let's do this."


def test_pop_pending_autonomous_goal_returns_and_clears():
    ai_main._pending_autonomous_goal = "test goal"
    assert ai_main.pop_pending_autonomous_goal() == "test goal"
    assert ai_main.pop_pending_autonomous_goal() is None


def test_pop_pending_autonomous_goal_defaults_to_none():
    ai_main._pending_autonomous_goal = None
    assert ai_main.pop_pending_autonomous_goal() is None


# ══════════════════════════════════════════════════════════════════
# get_theme / set_theme - persisted the same way as the wake-word toggle
# and shortcuts (see _read_env_file/_write_env_value). Isolated from the
# REAL .env via tmp_path + monkeypatching ENV_PATH and os.environ, since
# these touch actual disk state that must not leak between tests (or
# between this process and a real dev's .env with their live API key).
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(ai_main, "ENV_PATH", str(env_path))
    monkeypatch.delenv("KESTREL_THEME", raising=False)
    return env_path


def test_get_theme_defaults_to_dark_when_unset(isolated_env):
    assert ai_main.get_theme() == "dark"


def test_set_theme_then_get_theme_roundtrips(isolated_env):
    ai_main.set_theme("light")
    assert ai_main.get_theme() == "light"


def test_set_theme_rejects_unknown_value_falls_back_to_default(isolated_env):
    ai_main.set_theme("solarized")
    assert ai_main.get_theme() == "dark"


def test_get_theme_falls_back_to_dark_for_unrecognized_stored_value(isolated_env, monkeypatch):
    # Simulate a hand-edited or corrupted .env rather than going through
    # set_theme's own validation.
    monkeypatch.setattr(ai_main, "_read_env_file", lambda: {"KESTREL_THEME": "purple"})
    assert ai_main.get_theme() == "dark"


# ══════════════════════════════════════════════════════════════════
# _POINT_RE - extracting a "point": [y, x] pair from the vision model's
# response. Regex-based (not strict JSON parsing) so it tolerates markdown
# fences, extra prose, and float coordinates instead of breaking outright.
# ══════════════════════════════════════════════════════════════════

def test_point_re_matches_exact_expected_format():
    text = '[{"point": [500, 300], "label": "search bar"}]'
    match = ai_main._POINT_RE.search(text)
    assert match is not None
    assert match.group(1) == "500"
    assert match.group(2) == "300"


def test_point_re_matches_inside_markdown_fences():
    text = '```json\n[{"point": [123, 456], "label": "button"}]\n```'
    match = ai_main._POINT_RE.search(text)
    assert (match.group(1), match.group(2)) == ("123", "456")


def test_point_re_matches_with_surrounding_prose():
    text = 'Sure, here it is:\n[{"point": [10, 20], "label": "x"}]\nHope that helps!'
    match = ai_main._POINT_RE.search(text)
    assert (match.group(1), match.group(2)) == ("10", "20")


def test_point_re_matches_float_coordinates():
    text = '{"point": [500.5, 300.25]}'
    match = ai_main._POINT_RE.search(text)
    assert float(match.group(1)) == 500.5
    assert float(match.group(2)) == 300.25


def test_point_re_matches_with_extra_whitespace():
    text = '{"point":   [  500  ,   300  ]}'
    match = ai_main._POINT_RE.search(text)
    assert (match.group(1), match.group(2)) == ("500", "300")


def test_point_re_no_match_returns_none():
    assert ai_main._POINT_RE.search("no coordinates here at all") is None


# ══════════════════════════════════════════════════════════════════
# _try_generalize_last_macro - the glue between autonomous mode and
# ai/macro_generalizer.py. Verifies the exact integration contract:
# correct args passed through from actions.last_saved_*, and generalizer
# failures never propagate and break autonomous mode.
# ══════════════════════════════════════════════════════════════════

def test_try_generalize_last_macro_passes_through_correct_args(monkeypatch):
    import ai.actions as actions
    import ai.macro_generalizer as gen

    monkeypatch.setattr(actions, "last_saved_name", "search_youtube_soprano")
    monkeypatch.setattr(actions, "last_saved_actions", [("click", 1, 2), ("enter", "enter")])
    monkeypatch.setattr(actions, "last_appended_block", "\n\ndef tool_search_youtube_soprano():\n    pass\n")

    calls = []
    monkeypatch.setattr(
        gen, "generalize_and_rewrite_macro",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    ai_main._try_generalize_last_macro("search for Soprano on YouTube")

    assert len(calls) == 1
    assert calls[0]["goal"] == "search for Soprano on YouTube"
    assert calls[0]["name"] == "search_youtube_soprano"
    assert calls[0]["actions_list"] == [("click", 1, 2), ("enter", "enter")]
    assert calls[0]["appended_block"] == "\n\ndef tool_search_youtube_soprano():\n    pass\n"


def test_try_generalize_last_macro_noop_when_nothing_was_saved(monkeypatch):
    import ai.actions as actions
    import ai.macro_generalizer as gen

    monkeypatch.setattr(actions, "last_saved_name", None)
    monkeypatch.setattr(actions, "last_saved_actions", None)

    called = []
    monkeypatch.setattr(gen, "generalize_and_rewrite_macro", lambda **kwargs: called.append(kwargs))

    ai_main._try_generalize_last_macro("some goal")

    assert called == []


def test_try_generalize_last_macro_never_raises_on_generalizer_failure(monkeypatch):
    import ai.actions as actions
    import ai.macro_generalizer as gen

    monkeypatch.setattr(actions, "last_saved_name", "some_macro")
    monkeypatch.setattr(actions, "last_saved_actions", [("click", 1, 2)])
    monkeypatch.setattr(actions, "last_appended_block", "irrelevant")

    def _boom(**kwargs):
        raise RuntimeError("network error, API down, whatever")

    monkeypatch.setattr(gen, "generalize_and_rewrite_macro", _boom)

    # Must not raise - autonomous mode calls this right before refresh_actions()
    # and must be able to finish cleanly even if generalization blows up.
    ai_main._try_generalize_last_macro("some goal")


def test_try_generalize_last_macro_never_raises_on_import_failure(monkeypatch):
    import ai.actions as actions

    monkeypatch.setattr(actions, "last_saved_name", "some_macro")
    monkeypatch.setattr(actions, "last_saved_actions", [("click", 1, 2)])

    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "ai.macro_generalizer":
            raise ImportError("simulated broken import")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    ai_main._try_generalize_last_macro("some goal")  # must not raise


# ══════════════════════════════════════════════════════════════════
# _append_preference - the append-only preference update mechanism.
#
# Regression coverage for a real bug: the old implementation rebuilt the
# ENTIRE preferences block from only whatever the model's UPDATE_START/
# UPDATE_END message contained, silently erasing every previously learned
# preference. A user who taught the AI "prefers Chrome" and later "prefers
# Edge" ended up with a preferences section containing ONLY the Edge line
# - even the app's own default preferences were wiped the same way.
# ══════════════════════════════════════════════════════════════════

def test_append_preference_keeps_existing_lines():
    current = (
        "some static rules\n\n"
        f"{ai_main.PREFS_START_MARKER}\n"
        "- The user prefers using the Chrome browser.\n"
        f"{ai_main.PREFS_END_MARKER}\n"
    )
    updated = ai_main._append_preference(current, "The user prefers using the Microsoft Edge browser.")

    assert "Chrome" in updated
    assert "Edge" in updated


def test_append_preference_puts_new_line_after_old_ones():
    """The most-recent (bottom) line is what ai/instructions.txt tells the
    model to treat as authoritative on conflict - so ordering matters."""
    current = (
        f"{ai_main.PREFS_START_MARKER}\n"
        "- The user prefers using the Chrome browser.\n"
        f"{ai_main.PREFS_END_MARKER}\n"
    )
    updated = ai_main._append_preference(current, "The user prefers using the Microsoft Edge browser.")

    block = updated.split(ai_main.PREFS_START_MARKER)[1].split(ai_main.PREFS_END_MARKER)[0]
    lines = [line for line in block.splitlines() if line.strip()]
    assert "Chrome" in lines[0]
    assert "Edge" in lines[1]


def test_append_preference_does_not_touch_default_preferences():
    """Regression test for the exact reported bug: the app's own default
    preferences (tone, navigation rules, etc.) must survive an update,
    not get wiped down to only the newest line."""
    current = (
        f"{ai_main.PREFS_START_MARKER}\n"
        "- The user prefers a direct, no-frills tone.\n"
        "- Web navigation always step by step.\n"
        f"{ai_main.PREFS_END_MARKER}\n"
    )
    updated = ai_main._append_preference(current, "The user prefers using the Microsoft Edge browser.")

    assert "no-frills tone" in updated
    assert "Web navigation" in updated
    assert "Edge" in updated


def test_append_preference_creates_section_if_missing():
    current = "just some static instructions, no preferences section yet"
    updated = ai_main._append_preference(current, "The user likes short replies.")

    assert ai_main.PREFS_START_MARKER in updated
    assert ai_main.PREFS_END_MARKER in updated
    assert "short replies" in updated
    assert "just some static instructions" in updated


def test_append_preference_skips_exact_duplicates():
    current = (
        f"{ai_main.PREFS_START_MARKER}\n"
        "- The user likes short replies.\n"
        f"{ai_main.PREFS_END_MARKER}\n"
    )
    updated = ai_main._append_preference(current, "The user likes short replies.")

    assert updated.count("short replies") == 1


def test_append_preference_handles_multiple_lines_in_one_update():
    current = f"{ai_main.PREFS_START_MARKER}\n{ai_main.PREFS_END_MARKER}\n"
    updated = ai_main._append_preference(
        current,
        "The user likes coffee.\nThe user works in the evening.",
    )

    assert "coffee" in updated
    assert "evening" in updated


def test_append_preference_noop_on_empty_input():
    current = f"{ai_main.PREFS_START_MARKER}\n- existing\n{ai_main.PREFS_END_MARKER}\n"
    assert ai_main._append_preference(current, "") == current
    assert ai_main._append_preference(current, "   ") == current


def test_append_preference_never_removes_lines_across_several_updates():
    current = f"{ai_main.PREFS_START_MARKER}\n{ai_main.PREFS_END_MARKER}\n"

    current = ai_main._append_preference(current, "Preference A.")
    current = ai_main._append_preference(current, "Preference B.")
    current = ai_main._append_preference(current, "Preference C.")

    assert "Preference A." in current
    assert "Preference B." in current
    assert "Preference C." in current
    # And in the right order (most recent last)
    pos_a = current.index("Preference A.")
    pos_b = current.index("Preference B.")
    pos_c = current.index("Preference C.")
    assert pos_a < pos_b < pos_c


# ══════════════════════════════════════════════════════════════════
# ai() - empty/blank response fallbacks.
#
# Regression coverage: a blank or filtered Gemini response, or a reply
# that was ONLY a protocol tag with nothing else, used to show up as a
# completely empty chat bubble with zero explanation. Uses a fake `chat`
# object so no real network/credentials are needed.
# ══════════════════════════════════════════════════════════════════

def _fake_screen():
    """A stand-in capture for the autonomous-mode tests. A real image, not
    a string: the step loop resizes and re-encodes what it captures
    (prepare_autonomous_screenshot), so a string fails there, before the
    part a test is about."""
    from PIL import Image
    return Image.new("RGB", (64, 40), "white")


def _fake_chat(response_text):
    fake_response = type("Response", (), {"text": response_text})()
    return type("FakeChat", (), {"send_message": lambda self, content: fake_response})()


def _prep_ai_main(monkeypatch, response_text):
    monkeypatch.setattr(ai_main, "_initialized", True)
    monkeypatch.setattr(ai_main, "screenshot", None)
    monkeypatch.setattr(ai_main, "chat", _fake_chat(response_text))


def test_ai_returns_fallback_for_completely_empty_response(monkeypatch):
    _prep_ai_main(monkeypatch, "")

    result = ai_main.ai("hello")

    assert result.strip() != ""
    assert "rephrase" in result.lower()


def test_ai_returns_fallback_for_whitespace_only_response(monkeypatch):
    _prep_ai_main(monkeypatch, "   \n  ")

    result = ai_main.ai("hello")

    assert result.strip() != ""


def test_ai_returns_normal_text_unchanged(monkeypatch):
    _prep_ai_main(monkeypatch, "Here is the normal response.")

    result = ai_main.ai("hello")

    assert result == "Here is the normal response."


def test_ai_handles_response_that_is_only_an_autonomous_suggest_tag(monkeypatch):
    """Against the instructions (which require asking in plain language
    first) but the code must still not show a blank bubble if it happens."""
    _prep_ai_main(monkeypatch, "[[AUTONOMOUS_SUGGEST: do the thing]]")

    result = ai_main.ai("hello")

    assert result.strip() != ""
    assert ai_main.pop_pending_autonomous_goal() == "do the thing"


def test_ai_handles_update_block_with_no_leading_message(monkeypatch, tmp_path):
    # This path calls load_file()/save_file() on INSTRUCTIONS_PATH for
    # real - redirect it to a throwaway file so the test can never touch
    # (or pollute, as it once did) the real ai/instructions.txt.
    fake_instructions = tmp_path / "instructions.txt"
    fake_instructions.write_text(
        f"{ai_main.PREFS_START_MARKER}\n{ai_main.PREFS_END_MARKER}\n", encoding="utf-8"
    )
    monkeypatch.setattr(ai_main, "INSTRUCTIONS_PATH", str(fake_instructions))
    _prep_ai_main(monkeypatch, "[[UPDATE_START]]\n- some preference\n[[UPDATE_END]]")

    result = ai_main.ai("hello")

    assert result.strip() != ""  # "Got it." fallback, not blank
    assert "some preference" in fake_instructions.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════
# run_autonomous_mode - human handoff pause/resume (tier 2)
# ══════════════════════════════════════════════════════════════════

def test_run_autonomous_mode_resumes_after_human_handoff(monkeypatch):
    """
    Drives the real run_autonomous_mode loop through: step 1 requests a
    human handoff (tool_request_human_handoff) -> a background thread
    calls actions.resume_human_handoff() shortly after (standing in for
    clicking "Continue" in the UI) -> step 2 finishes the goal
    (tool_name_action). Fails (times out) if the pause/resume plumbing
    itself is broken.
    """
    import threading
    import time as time_mod
    import ai.actions as actions

    monkeypatch.setattr(ai_main, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_main, "get_max_automation_steps", lambda: 5)
    monkeypatch.setattr(ai_main, "reset_safety_limits", lambda: None)
    monkeypatch.setattr(ai_main, "load_file", lambda path: "instructions")
    monkeypatch.setattr(ai_main, "play_notification_sound", lambda *a, **k: None)
    # NOTE: deliberately NOT monkeypatching time.sleep - ai_main.time IS the
    # real global `time` module (same object this test's `import time`
    # would get), so patching its .sleep would also silently no-op the
    # background thread's delay below, making it fire before the main
    # thread ever reaches the human_handoff_requested check and defeating
    # the whole point of this test. A couple of real 2-second inter-step
    # sleeps is a small price for testing the actual race correctly.
    monkeypatch.setattr(ai_main, "ImageGrab", type("FakeGrab", (), {"grab": staticmethod(_fake_screen)}))
    monkeypatch.setattr(ai_main.pyautogui, "failSafeCheck", lambda: None)

    actions.emergency_stop = False
    actions.human_handoff_requested = False
    actions.human_handoff_reason = None
    actions.human_handoff_event.set()
    actions.single_fcn = False
    actions.fcn_name = False

    steps = {"n": 0}

    class FakeResponse:
        text = ""

    def fake_send_message(parts):
        steps["n"] += 1
        if steps["n"] == 1:
            actions.tool_request_human_handoff("A CAPTCHA is on screen")

            def _resolve_later():
                time_mod.sleep(0.05)
                actions.resume_human_handoff()

            threading.Thread(target=_resolve_later, daemon=True).start()
        else:
            actions.fcn_name = True  # tool_name_action equivalent: goal complete
        return FakeResponse()

    fake_chat = type("FakeChat", (), {"send_message": staticmethod(fake_send_message)})()
    fake_client = type("FakeClient", (), {
        "chats": type("FakeChats", (), {"create": staticmethod(lambda **k: fake_chat)})()
    })()
    monkeypatch.setattr(ai_main, "_gemini_client", fake_client)
    monkeypatch.setattr(ai_main, "tools", [])
    monkeypatch.setattr(ai_main, "refresh_actions", lambda: None)

    handoff_calls = []

    result_holder = {}

    def _run():
        try:
            ai_main.run_autonomous_mode(
                "test goal",
                max_steps=5,
                on_human_handoff=lambda reason: handoff_calls.append(reason),
            )
            result_holder["ok"] = True
        except Exception as e:
            result_holder["error"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=10)

    assert not t.is_alive(), "run_autonomous_mode never returned - human handoff pause/resume deadlocked"
    assert "error" not in result_holder, result_holder.get("error")
    assert handoff_calls == ["A CAPTCHA is on screen"]
    assert steps["n"] == 2, "should have taken a second step after resuming, not stopped at the handoff"


# ══════════════════════════════════════════════════════════════════
# _is_transient_network_error - classifying connectivity failures
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("exc", [
    ConnectionError("Connection refused"),
    TimeoutError("timed out"),
    OSError("[Errno -3] Temporary failure in name resolution"),
    Exception("HTTPSConnectionPool: Max retries exceeded with url: /v1/models"),
    Exception("503 Service Unavailable"),
    Exception("DeadlineExceeded: 504 Deadline Exceeded"),
    Exception("Failed to establish a new connection: [Errno 111] Connection refused"),
])
def test_is_transient_network_error_recognizes_connectivity_failures(exc):
    assert ai_main._is_transient_network_error(exc) is True


@pytest.mark.parametrize("exc", [
    ValueError("invalid argument: goal cannot be empty"),
    Exception("400 Bad Request: malformed content"),
    Exception("401 Unauthorized: invalid API key"),
    PermissionError("API key does not have access to this model"),
])
def test_is_transient_network_error_rejects_non_network_errors(exc):
    assert ai_main._is_transient_network_error(exc) is False


# ══════════════════════════════════════════════════════════════════
# _retry_send_message_after_network_error
# ══════════════════════════════════════════════════════════════════

def test_retry_send_message_succeeds_after_a_few_failures(monkeypatch):
    monkeypatch.setattr(ai_main.time, "sleep", lambda *a, **k: None)
    ai_main.actions.emergency_stop = False

    attempts = {"n": 0}

    class FakeResponse:
        text = "ok"

    def fake_send(parts):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("Connection refused")
        return FakeResponse()

    fake_chat = type("FakeChat", (), {"send_message": staticmethod(fake_send)})()
    retry_calls = []

    result = ai_main._retry_send_message_after_network_error(
        fake_chat, "prompt", "screen",
        on_network_retry=lambda attempt, wait: retry_calls.append((attempt, wait)),
    )

    assert result is not None
    assert result.text == "ok"
    assert attempts["n"] == 3
    assert retry_calls == [(1, 3), (2, 6), (3, 12)]  # backoff before each attempt, including the successful 3rd


def test_retry_send_message_stops_immediately_on_emergency_stop(monkeypatch):
    monkeypatch.setattr(ai_main.time, "sleep", lambda *a, **k: None)
    ai_main.actions.emergency_stop = True
    try:
        fake_chat = type("FakeChat", (), {"send_message": staticmethod(lambda parts: (_ for _ in ()).throw(AssertionError("should never be called")))})()
        result = ai_main._retry_send_message_after_network_error(fake_chat, "p", "s", on_network_retry=None)
        assert result is None
    finally:
        ai_main.actions.emergency_stop = False


def test_retry_send_message_gives_up_after_budget_exhausted(monkeypatch):
    monkeypatch.setattr(ai_main.time, "sleep", lambda *a, **k: None)
    ai_main.actions.emergency_stop = False
    # Simulate the retry budget elapsing after the first attempt's backoff
    # by making time.monotonic() jump straight past the deadline on the
    # second check, without needing to actually wait NETWORK_RETRY_BUDGET_SECONDS.
    calls = {"n": 0}
    real_monotonic = ai_main.time.monotonic

    def fake_monotonic():
        calls["n"] += 1
        return 0.0 if calls["n"] <= 2 else 10_000.0

    monkeypatch.setattr(ai_main.time, "monotonic", fake_monotonic)

    def always_fails(parts):
        raise ConnectionError("Connection refused")

    fake_chat = type("FakeChat", (), {"send_message": staticmethod(always_fails)})()
    result = ai_main._retry_send_message_after_network_error(fake_chat, "p", "s", on_network_retry=None)
    assert result is None


# ══════════════════════════════════════════════════════════════════
# run_autonomous_mode - end-to-end network error recovery
# ══════════════════════════════════════════════════════════════════

def test_run_autonomous_mode_recovers_from_a_transient_network_error(monkeypatch):
    """
    Step 1's send_message fails with a connectivity error, retries twice,
    then succeeds - step 2 then completes the goal normally. Regression
    test for the old behavior, where a network error just silently
    consumed a step from max_steps every time instead of actually
    retrying.
    """
    import threading
    import ai.actions as actions

    monkeypatch.setattr(ai_main, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_main, "get_max_automation_steps", lambda: 5)
    monkeypatch.setattr(ai_main, "reset_safety_limits", lambda: None)
    monkeypatch.setattr(ai_main, "load_file", lambda path: "instructions")
    monkeypatch.setattr(ai_main, "play_notification_sound", lambda *a, **k: None)
    monkeypatch.setattr(ai_main.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(ai_main, "ImageGrab", type("FakeGrab", (), {"grab": staticmethod(_fake_screen)}))
    monkeypatch.setattr(ai_main.pyautogui, "failSafeCheck", lambda: None)

    actions.emergency_stop = False
    actions.human_handoff_requested = False
    actions.single_fcn = False
    actions.fcn_name = False

    call_count = {"n": 0}
    network_failures_left = {"n": 2}
    logical_steps_completed = {"n": 0}

    class FakeResponse:
        text = ""

    def fake_send_message(parts):
        call_count["n"] += 1
        if network_failures_left["n"] > 0:
            network_failures_left["n"] -= 1
            raise ConnectionError("Connection refused")
        logical_steps_completed["n"] += 1
        if logical_steps_completed["n"] == 1:
            return FakeResponse()  # step 1 (after recovering): no tool call
        actions.fcn_name = True  # step 2: goal complete
        return FakeResponse()

    fake_chat = type("FakeChat", (), {"send_message": staticmethod(fake_send_message)})()
    fake_client = type("FakeClient", (), {
        "chats": type("FakeChats", (), {"create": staticmethod(lambda **k: fake_chat)})()
    })()
    monkeypatch.setattr(ai_main, "_gemini_client", fake_client)
    monkeypatch.setattr(ai_main, "tools", [])
    monkeypatch.setattr(ai_main, "refresh_actions", lambda: None)

    retry_calls = []
    result_holder = {}

    def _run():
        try:
            ai_main.run_autonomous_mode(
                "test goal",
                max_steps=5,
                on_network_retry=lambda attempt, wait: retry_calls.append((attempt, wait)),
            )
            result_holder["ok"] = True
        except Exception as e:
            result_holder["error"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=10)

    assert not t.is_alive(), "run_autonomous_mode never returned"
    assert "error" not in result_holder, result_holder.get("error")
    assert len(retry_calls) == 2  # two failed attempts before the retry succeeded
    # 3 calls to recover from the network error (1 initial fail + 2 retry
    # attempts) + 1 more for the second, successful "real" step.
    assert call_count["n"] == 4
    assert logical_steps_completed["n"] == 2


def test_run_autonomous_mode_drains_pending_screenshot_when_failsafe_stops_a_tool_call(monkeypatch):
    """
    Regression test for the reported bug: autonomous mode appeared to
    drop a screenshot into the chat "at the end" of a run. Root cause - a screenshot tool_screenshot() took mid-step, right before a
    FailSafeException aborted that same step, used to survive past
    run_autonomous_mode's return because that break path skipped the
    per-step pop_pending_screenshot_path() drain. The NEXT regular chat
    message's AIWorker would then pop it and show it as a bubble. The
    finally block must drain it regardless of how the loop exits.
    """
    import ai.actions as actions

    monkeypatch.setattr(ai_main, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_main, "get_max_automation_steps", lambda: 5)
    monkeypatch.setattr(ai_main, "reset_safety_limits", lambda: None)
    monkeypatch.setattr(ai_main, "load_file", lambda path: "instructions")
    monkeypatch.setattr(ai_main, "play_notification_sound", lambda *a, **k: None)
    monkeypatch.setattr(ai_main.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(ai_main, "ImageGrab", type("FakeGrab", (), {"grab": staticmethod(_fake_screen)}))
    monkeypatch.setattr(ai_main.pyautogui, "failSafeCheck", lambda: None)

    actions.emergency_stop = False
    actions.human_handoff_requested = False
    actions.single_fcn = False
    actions.fcn_name = False

    def fake_send_message(parts):
        # Simulate tool_screenshot() having run (and marked a screenshot
        # pending) as part of this step's tool call, immediately before
        # the fail-safe aborted mid-call.
        ai_main._pending_screenshot_path = "/fake/screenshot_from_this_step.png"
        raise ai_main.pyautogui.FailSafeException("mouse hit a corner")

    fake_chat = type("FakeChat", (), {"send_message": staticmethod(fake_send_message)})()
    fake_client = type("FakeClient", (), {
        "chats": type("FakeChats", (), {"create": staticmethod(lambda **k: fake_chat)})()
    })()
    monkeypatch.setattr(ai_main, "_gemini_client", fake_client)
    monkeypatch.setattr(ai_main, "tools", [])
    monkeypatch.setattr(ai_main, "refresh_actions", lambda: None)

    ai_main.run_autonomous_mode("test goal", max_steps=5)

    assert ai_main.pop_pending_screenshot_path() is None


def test_run_autonomous_mode_drains_pending_screenshot_even_when_it_raises(monkeypatch):
    """
    Same leak, different early-exit path: NetworkUnavailableError
    propagates OUT of run_autonomous_mode (see the test above this one
    in the file) rather than breaking the loop - the finally block has
    to drain the pending screenshot on that path too, not just the
    break-based ones.
    """
    import ai.actions as actions

    monkeypatch.setattr(ai_main, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_main, "get_max_automation_steps", lambda: 5)
    monkeypatch.setattr(ai_main, "reset_safety_limits", lambda: None)
    monkeypatch.setattr(ai_main, "load_file", lambda path: "instructions")
    monkeypatch.setattr(ai_main, "play_notification_sound", lambda *a, **k: None)
    monkeypatch.setattr(ai_main.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(ai_main, "ImageGrab", type("FakeGrab", (), {"grab": staticmethod(_fake_screen)}))
    monkeypatch.setattr(ai_main.pyautogui, "failSafeCheck", lambda: None)

    calls = {"n": 0}

    def fake_monotonic():
        calls["n"] += 1
        return 0.0 if calls["n"] <= 2 else 10_000.0

    monkeypatch.setattr(ai_main.time, "monotonic", fake_monotonic)

    actions.emergency_stop = False
    actions.human_handoff_requested = False
    actions.single_fcn = False
    actions.fcn_name = False
    ai_main._pending_screenshot_path = "/fake/screenshot_before_network_died.png"

    def always_fails(parts):
        raise ConnectionError("Connection refused")

    fake_chat = type("FakeChat", (), {"send_message": staticmethod(always_fails)})()
    fake_client = type("FakeClient", (), {
        "chats": type("FakeChats", (), {"create": staticmethod(lambda **k: fake_chat)})()
    })()
    monkeypatch.setattr(ai_main, "_gemini_client", fake_client)
    monkeypatch.setattr(ai_main, "tools", [])
    monkeypatch.setattr(ai_main, "refresh_actions", lambda: None)

    with pytest.raises(ai_main.NetworkUnavailableError):
        ai_main.run_autonomous_mode("test goal", max_steps=5)

    assert ai_main.pop_pending_screenshot_path() is None


def test_run_autonomous_mode_raises_network_unavailable_when_retries_exhausted(monkeypatch):
    """
    Regression test for the actual reported bug: a network drop that
    outlasts the retry window used to make run_autonomous_mode return
    normally, so MainWindow showed a false "Task completed" message.
    Raising NetworkUnavailableError instead routes it through
    AutonomousWorker's existing error signal (see that class) - accurate
    "lost connection" reporting instead.
    """
    import threading
    import ai.actions as actions

    monkeypatch.setattr(ai_main, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_main, "get_max_automation_steps", lambda: 5)
    monkeypatch.setattr(ai_main, "reset_safety_limits", lambda: None)
    monkeypatch.setattr(ai_main, "load_file", lambda path: "instructions")
    monkeypatch.setattr(ai_main, "play_notification_sound", lambda *a, **k: None)
    monkeypatch.setattr(ai_main.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(ai_main, "ImageGrab", type("FakeGrab", (), {"grab": staticmethod(_fake_screen)}))
    monkeypatch.setattr(ai_main.pyautogui, "failSafeCheck", lambda: None)

    calls = {"n": 0}

    def fake_monotonic():
        calls["n"] += 1
        return 0.0 if calls["n"] <= 2 else 10_000.0

    monkeypatch.setattr(ai_main.time, "monotonic", fake_monotonic)

    actions.emergency_stop = False
    actions.human_handoff_requested = False
    actions.single_fcn = False
    actions.fcn_name = False

    def always_fails(parts):
        raise ConnectionError("Connection refused")

    fake_chat = type("FakeChat", (), {"send_message": staticmethod(always_fails)})()
    fake_client = type("FakeClient", (), {
        "chats": type("FakeChats", (), {"create": staticmethod(lambda **k: fake_chat)})()
    })()
    monkeypatch.setattr(ai_main, "_gemini_client", fake_client)
    monkeypatch.setattr(ai_main, "tools", [])
    monkeypatch.setattr(ai_main, "refresh_actions", lambda: None)

    result_holder = {}

    def _run():
        try:
            ai_main.run_autonomous_mode("test goal", max_steps=5)
            result_holder["ok"] = True
        except Exception as e:
            result_holder["error"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=10)

    assert not t.is_alive()
    assert "error" in result_holder
    assert isinstance(result_holder["error"], ai_main.NetworkUnavailableError)


# ══════════════════════════════════════════════════════════════════
# _seed_instructions_file / load_instructions
# ══════════════════════════════════════════════════════════════════
# instructions.txt moved from BASE_PATH (next to the exe) to
# USER_DATA_PATH, because the [[UPDATE_START]] protocol WRITES to it and
# Program Files is not writable by a standard user - and because an app
# rewriting files in its own install directory is an antivirus heuristic
# (see docs/ANTIVIRUS.md). The copy under BASE_PATH is now the shipped
# default that seeds the writable one on first run.

def test_seed_copies_the_bundled_prompt_when_nothing_exists_yet(monkeypatch, tmp_path):
    bundled = tmp_path / "bundled" / "instructions.txt"
    bundled.parent.mkdir()
    bundled.write_text("SHIPPED PROMPT", encoding="utf-8")
    target = tmp_path / "userdata" / "instructions.txt"

    monkeypatch.setattr(ai_main, "BUNDLED_INSTRUCTIONS_PATH", str(bundled))
    monkeypatch.setattr(ai_main, "INSTRUCTIONS_PATH", str(target))

    ai_main._seed_instructions_file()

    assert target.read_text(encoding="utf-8") == "SHIPPED PROMPT"


def test_seed_never_overwrites_a_prompt_that_is_already_there(monkeypatch, tmp_path):
    """The whole point of moving the file is that what's written to it
    survives. Seeding over an existing one would throw away every
    preference the assistant had learned, on every launch."""
    bundled = tmp_path / "bundled" / "instructions.txt"
    bundled.parent.mkdir()
    bundled.write_text("SHIPPED PROMPT", encoding="utf-8")
    target = tmp_path / "userdata" / "instructions.txt"
    target.parent.mkdir()
    target.write_text("MY PROMPT with learned preferences", encoding="utf-8")

    monkeypatch.setattr(ai_main, "BUNDLED_INSTRUCTIONS_PATH", str(bundled))
    monkeypatch.setattr(ai_main, "INSTRUCTIONS_PATH", str(target))

    ai_main._seed_instructions_file()

    assert target.read_text(encoding="utf-8") == "MY PROMPT with learned preferences"


def test_seed_carries_an_old_installs_prompt_across_the_move(monkeypatch, tmp_path):
    """On upgrade from a build that still wrote to BASE_PATH, the file
    sitting there is the user's own prompt, not the shipped default.
    Same code path, and it must bring their preferences with it rather
    than resetting them."""
    old_location = tmp_path / "program_files" / "instructions.txt"
    old_location.parent.mkdir()
    old_location.write_text(
        "### PREFERENCES\n- The user prefers Edge.\n", encoding="utf-8"
    )
    target = tmp_path / "userdata" / "instructions.txt"

    monkeypatch.setattr(ai_main, "BUNDLED_INSTRUCTIONS_PATH", str(old_location))
    monkeypatch.setattr(ai_main, "INSTRUCTIONS_PATH", str(target))

    ai_main._seed_instructions_file()

    assert "The user prefers Edge." in target.read_text(encoding="utf-8")


def test_seed_is_a_no_op_when_running_from_source(monkeypatch, tmp_path):
    """From source, BASE_PATH and USER_DATA_PATH are the same directory,
    so both paths are the same file. The explicit equality guard makes
    that a stated no-op rather than one that happens to fall out of the
    exists() check below it - copying a file onto itself with
    open(src)/open(dst,'w') would truncate it to nothing first, and that
    should not depend on the ordering of a later branch."""
    same = tmp_path / "instructions.txt"
    same.write_text("THE ONLY COPY", encoding="utf-8")

    monkeypatch.setattr(ai_main, "BUNDLED_INSTRUCTIONS_PATH", str(same))
    monkeypatch.setattr(ai_main, "INSTRUCTIONS_PATH", str(same))

    ai_main._seed_instructions_file()

    assert same.read_text(encoding="utf-8") == "THE ONLY COPY"


def test_seed_survives_a_missing_bundled_copy(monkeypatch, tmp_path):
    """load_file() writes a default when the file is absent, so a build
    missing its bundled prompt still starts. Seeding must not raise on
    the way there."""
    monkeypatch.setattr(ai_main, "BUNDLED_INSTRUCTIONS_PATH", str(tmp_path / "gone.txt"))
    monkeypatch.setattr(ai_main, "INSTRUCTIONS_PATH", str(tmp_path / "userdata" / "i.txt"))

    ai_main._seed_instructions_file()  # must not raise


def test_load_instructions_seeds_before_reading(monkeypatch, tmp_path):
    """Order matters: load_file() creates a bare default when the file is
    missing, so a read that beat the seed would leave that default in
    place and the seed would then skip it. Going through one accessor is
    what keeps the two in the right order."""
    bundled = tmp_path / "bundled" / "instructions.txt"
    bundled.parent.mkdir()
    bundled.write_text("SHIPPED PROMPT", encoding="utf-8")
    target = tmp_path / "userdata" / "instructions.txt"

    monkeypatch.setattr(ai_main, "BUNDLED_INSTRUCTIONS_PATH", str(bundled))
    monkeypatch.setattr(ai_main, "INSTRUCTIONS_PATH", str(target))

    assert ai_main.load_instructions() == "SHIPPED PROMPT"


def test_an_autonomous_run_goes_on_after_a_one_step_tool(monkeypatch):
    """
    A flag left set by the chat (opening an app there sets single_fcn) used
    to end the next run after its first step as "Single action completed",
    and so did opening an app inside the run. Only the model saying the
    goal is reached (tool_name_action) ends it.
    """
    import ai.actions as actions

    monkeypatch.setattr(ai_main, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_main, "get_max_automation_steps", lambda: 5)
    monkeypatch.setattr(ai_main, "reset_safety_limits", lambda: None)
    monkeypatch.setattr(ai_main, "load_file", lambda path: "instructions")
    monkeypatch.setattr(ai_main, "play_notification_sound", lambda *a, **k: None)
    monkeypatch.setattr(ai_main.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(ai_main, "ImageGrab", type("FakeGrab", (), {"grab": staticmethod(_fake_screen)}))
    monkeypatch.setattr(ai_main.pyautogui, "failSafeCheck", lambda: None)
    monkeypatch.setattr(actions, "emergency_stop", False)
    monkeypatch.setattr(actions, "human_handoff_requested", False)
    monkeypatch.setattr(actions, "fcn_name", False)
    monkeypatch.setattr(actions, "single_fcn", True)        # left over from the chat

    steps = {"n": 0}

    class FakeResponse:
        text = ""

    def fake_send_message(parts):
        steps["n"] += 1
        if steps["n"] == 1:
            actions.single_fcn = True                          # opened Chrome
        elif steps["n"] == 3:
            actions.fcn_name = True                            # goal reached
        return FakeResponse()

    fake_chat = type("FakeChat", (), {"send_message": staticmethod(fake_send_message)})()
    fake_client = type("FakeClient", (), {
        "chats": type("FakeChats", (), {"create": staticmethod(lambda **k: fake_chat)})()
    })()
    monkeypatch.setattr(ai_main, "_gemini_client", fake_client)
    monkeypatch.setattr(ai_main, "tools", [])
    monkeypatch.setattr(ai_main, "refresh_actions", lambda: None)
    monkeypatch.setattr(ai_main, "_defer_post_run_work", lambda goal: None)

    result = ai_main.run_autonomous_mode("Open Chrome and go to news.ycombinator.com", max_steps=5)

    assert steps["n"] == 3 and result["outcome"] == "success" and result["steps_used"] == 3
    assert actions.single_fcn is False


def test_a_vision_click_on_kestrels_own_window_is_not_sent_through_it(monkeypatch):
    """The screenshot was taken just now: a point on Kestrel's window means
    the model picked something on Kestrel, and a click there would go to an
    app beneath that it never saw."""
    import ai.actions as actions
    import ai.model_client as model_client
    from ai import direct_input

    class FakeModels:
        @staticmethod
        def generate_content(**kwargs):
            return type("R", (), {"text": '[{"point": [500, 500], "label": "search bar"}]'})()

    monkeypatch.setattr(ai_main, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_main, "ImageGrab", type("FakeGrab", (), {"grab": staticmethod(_fake_screen)}))
    monkeypatch.setattr(model_client, "make_client", lambda: type("C", (), {"models": FakeModels})())
    monkeypatch.setattr(ai_main.pyautogui, "size", lambda: (1920, 1080))
    monkeypatch.setattr(actions, "emergency_stop", False)
    monkeypatch.setattr(direct_input, "kestrel_window_at", lambda x, y: True)
    monkeypatch.setattr(actions, "actions_list", [])
    clicks = []
    monkeypatch.setattr(direct_input, "click", lambda *a, **k: clicks.append(a))

    result = ai_main.get_position_object_and_click("search bar")

    assert result.startswith("Could not click search bar at (960, 540): Kestrel's own window is there")
    assert clicks == []
    assert actions.actions_list == []          # nothing recorded for a macro either


def test_a_chat_message_leaves_a_stop_pressed_while_another_automation_winds_down(monkeypatch):
    import threading
    import ai.actions as actions
    from ai import automation_lock

    held, release = threading.Event(), threading.Event()

    def mapping_run():
        with automation_lock.hold("a mapping run"):
            held.set()
            release.wait(5)

    thread = threading.Thread(target=mapping_run)
    thread.start()
    try:
        held.wait(5)
        actions.emergency_stop = True
        ai_main._clear_stop_for_new_message()
        assert actions.emergency_stop is True
    finally:
        release.set()
        thread.join(5)
    ai_main._clear_stop_for_new_message()
    assert actions.emergency_stop is False
