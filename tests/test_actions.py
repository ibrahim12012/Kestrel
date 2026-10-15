# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_actions.py
======================
Tests for ai/actions.py.

Covers the parts that are pure logic (file I/O, tool discovery, macro
playback dispatch) - not the actual OS-level effects of pyautogui/keyboard,
which are stubbed out in conftest.py.
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.actions as actions  # noqa: E402  (must come after the sys.path tweak)


# ══════════════════════════════════════════════════════════════════
# load_file / save_file
# ══════════════════════════════════════════════════════════════════

def test_load_file_creates_default_when_missing(tmp_path):
    target = tmp_path / "instructions.txt"
    assert not target.exists()

    content = actions.load_file(str(target))

    assert target.exists()
    assert "IDENTITY & ROLE" in content
    assert "[[UPDATE_START]]" in content


def test_load_file_reads_existing_content_without_overwriting(tmp_path):
    target = tmp_path / "instructions.txt"
    target.write_text("custom instructions", encoding="utf-8")

    content = actions.load_file(str(target))

    assert content == "custom instructions"


def test_save_file_writes_content(tmp_path):
    target = tmp_path / "out.txt"

    actions.save_file(str(target), "hello world")

    assert target.read_text(encoding="utf-8") == "hello world"


def test_save_file_creates_parent_directories(tmp_path):
    target = tmp_path / "nested" / "dir" / "out.txt"

    actions.save_file(str(target), "content")

    assert target.exists()
    assert target.read_text(encoding="utf-8") == "content"


def test_save_file_overwrites_existing_content(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("old", encoding="utf-8")

    actions.save_file(str(target), "new")

    assert target.read_text(encoding="utf-8") == "new"


# ══════════════════════════════════════════════════════════════════
# get_all_tools - auto-discovery of tool_* functions
# ══════════════════════════════════════════════════════════════════

def test_get_all_tools_only_returns_tool_prefixed_functions():
    tools = actions.get_all_tools(actions)
    names = {t.__name__ for t in tools}

    # Known tool_* functions must all be present
    for expected in (
        "tool_open_application",
        "tool_close_application",
        "tool_write_and_enter",
        "tool_enter",
        "tool_new_page",
        "tool_wait",
        "tool_name_action",
    ):
        assert expected in names

    # Non tool_* functions must never leak in
    for not_expected in ("load_file", "save_file", "get_all_tools", "play_actions"):
        assert not_expected not in names


def test_get_all_tools_only_scans_the_given_module():
    """A function imported into a module from elsewhere shouldn't count as one of its tools."""
    import types
    fake_module = types.ModuleType("fake_module_with_borrowed_tool")
    fake_module.tool_borrowed = actions.tool_wait  # defined in ai.actions, not fake_module

    tools = actions.get_all_tools(fake_module)
    assert tools == []


# ══════════════════════════════════════════════════════════════════
# play_actions - macro playback dispatch
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def clicks(monkeypatch):
    """direct_input.click (ai/direct_input.py, the clicks that don't take
    the user's mouse), recorded instead of made."""
    made = []

    def fake_click(x, y, button="left"):
        made.append((x, y, button))
        return actions.direct_input.Outcome(True, "invoke")

    monkeypatch.setattr(actions.direct_input, "click", fake_click)
    return made


def test_play_actions_dispatches_click(clicks, mock_pyautogui):
    actions.play_actions([("click", 100, 200)])

    assert clicks == [(100, 200, "left")]
    mock_pyautogui.moveTo.assert_not_called()      # the user's mouse is left alone


def test_play_actions_dispatches_right_click(clicks):
    actions.play_actions([("right_click", 100, 200)])

    assert clicks == [(100, 200, "right")]


def test_a_click_that_cannot_be_made_stops_the_macro(monkeypatch, mock_keyboard):
    """The steps after a click were recorded for the screen it leads to;
    typing them somewhere else is worse than stopping."""
    monkeypatch.setattr(actions.direct_input, "click",
                        lambda x, y, button="left": actions.direct_input.Outcome(False, "mouse", note="no mouse"))

    actions.play_actions([("click", 1, 2), ("enter", "enter")])

    mock_keyboard.press_and_release.assert_not_called()


def test_a_block_macro_stops_at_a_click_that_cannot_be_made(monkeypatch, mock_keyboard):
    monkeypatch.setattr(actions, "_get_action_delay", lambda: 0.0)
    monkeypatch.setattr(actions.direct_input, "click",
                        lambda x, y, button="left": actions.direct_input.Outcome(False, "mouse", note="no mouse"))

    actions.play_actions([{"type": "click", "x": 1, "y": 2}, {"type": "enter"}])

    mock_keyboard.press_and_release.assert_not_called()


def test_play_actions_dispatches_enter(mock_keyboard):
    actions.play_actions([("enter", "enter")])

    mock_keyboard.press_and_release.assert_called_once_with("enter")


def test_play_actions_dispatches_new_tab(mock_pyautogui):
    actions.play_actions([("new_page", "ctrl+t")])

    mock_pyautogui.hotkey.assert_called_once_with("ctrl", "t")


def test_play_actions_dispatches_write_plain_text(mock_pyautogui):
    actions.play_actions([("hi", "write")])

    # Each non-punctuation character is typed individually
    calls = [c.args[0] for c in mock_pyautogui.write.call_args_list]
    assert calls == ["h", "i"]


def test_play_actions_dispatches_write_with_punctuation_via_clipboard(mock_pyautogui, monkeypatch):
    copied = []
    monkeypatch.setattr(actions.pyperclip, "copy", lambda text: copied.append(text))

    actions.play_actions([("a!", "write")])

    assert copied == ["!"]
    # 'a' typed normally, '!' pasted via ctrl+v
    assert mock_pyautogui.write.call_args_list[0].args[0] == "a"
    mock_pyautogui.hotkey.assert_any_call("ctrl", "v")


def test_play_actions_continues_after_a_failing_action(monkeypatch, mock_keyboard):
    """One bad action shouldn't stop the rest of the macro from playing."""
    def _raise(x, y, button="left"):
        raise RuntimeError("boom")

    monkeypatch.setattr(actions.direct_input, "click", _raise)

    actions.play_actions([
        ("click", 1, 2),     # will raise, should be caught and logged
        ("enter", "enter"),  # should still run
    ])

    mock_keyboard.press_and_release.assert_called_once_with("enter")


def test_play_actions_ignores_unrecognized_action_shape(mock_pyautogui, mock_keyboard):
    # A tuple that matches no known pattern should just be skipped, not crash
    actions.play_actions([("mystery", "action", "shape")])

    mock_pyautogui.click.assert_not_called()
    mock_keyboard.press_and_release.assert_not_called()


# ══════════════════════════════════════════════════════════════════
# _sanitize_macro_name - guards tool_name_action against bad names
# ══════════════════════════════════════════════════════════════════

def test_sanitize_macro_name_keeps_clean_snake_case_untouched():
    assert actions._sanitize_macro_name("open_youtube_lofi") == "open_youtube_lofi"


def test_sanitize_macro_name_strips_accents_and_lowercases():
    assert actions._sanitize_macro_name("Écouter Café") == "ecouter_cafe"


def test_sanitize_macro_name_collapses_punctuation_and_spaces():
    assert actions._sanitize_macro_name("send email!! (gmail)") == "send_email_gmail"


def test_sanitize_macro_name_handles_empty_string():
    name = actions._sanitize_macro_name("")
    assert name == "macro"
    assert name.isidentifier()


def test_sanitize_macro_name_handles_leading_digit():
    name = actions._sanitize_macro_name("123_go")
    assert name[0].isalpha()
    assert name.isidentifier()


def test_sanitize_macro_name_avoids_python_keywords():
    name = actions._sanitize_macro_name("class")
    assert not __import__("keyword").iskeyword(name)
    assert name.isidentifier()


def test_sanitize_macro_name_result_is_always_a_valid_identifier():
    for raw in ["", "   ", "1", "class", "déjà-vu!!!", "a" * 200, "___"]:
        name = actions._sanitize_macro_name(raw)
        assert name.isidentifier(), f"{raw!r} -> {name!r} is not a valid identifier"


def test_tool_name_action_sanitizes_before_writing(tmp_path, monkeypatch):
    import ai.macro_store as macro_store
    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))
    monkeypatch.setattr(macro_store, "load_and_register_all", lambda module: 0)
    monkeypatch.setattr(actions, "actions_list", [("click", 1, 1)])
    monkeypatch.setattr(actions, "fcn_name", False)

    actions.tool_name_action("Écouter du Café !!")

    assert (tmp_path / "ecouter_du_cafe.json").is_file()


# ══════════════════════════════════════════════════════════════════
# tool_name_action - macro saving (JSON record under ai/macros/, see
# ai.macro_store; redirected to a tmp dir so tests never touch the
# real macro store)
# ══════════════════════════════════════════════════════════════════

def test_tool_name_action_replaces_existing_macro(tmp_path, monkeypatch):
    import ai.macro_store as macro_store
    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))
    monkeypatch.setattr(macro_store, "load_and_register_all", lambda module: 0)
    macro_store.save_literal_macro("existing_macro", [("click", 1, 1)])

    monkeypatch.setattr(actions, "actions_list", [("click", 9, 9), ("enter", "enter")])
    monkeypatch.setattr(actions, "fcn_name", False)

    actions.tool_name_action("existing_macro")

    spec = macro_store.load_spec("existing_macro")
    assert spec["recorded_actions"] == [["click", 9, 9], ["enter", "enter"]]
    assert actions.actions_list == []
    assert actions.fcn_name is True


def test_tool_name_action_saves_new_macro(tmp_path, monkeypatch):
    import ai.macro_store as macro_store
    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))
    monkeypatch.setattr(macro_store, "load_and_register_all", lambda module: 0)

    monkeypatch.setattr(actions, "actions_list", [("click", 10, 20), ("enter", "enter")])
    monkeypatch.setattr(actions, "fcn_name", False)

    actions.tool_name_action("my_new_macro")

    spec = macro_store.load_spec("my_new_macro")
    assert spec is not None
    assert spec["kind"] == "literal"
    assert spec["recorded_actions"] == [["click", 10, 20], ["enter", "enter"]]
    assert actions.actions_list == []
    assert actions.fcn_name is True


def test_tool_name_action_tracks_last_saved_state_for_the_generalizer(tmp_path, monkeypatch):
    import ai.macro_store as macro_store
    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))
    monkeypatch.setattr(macro_store, "load_and_register_all", lambda module: 0)

    monkeypatch.setattr(actions, "actions_list", [("click", 10, 20)])
    monkeypatch.setattr(actions, "fcn_name", False)

    actions.tool_name_action("tracked_macro")

    assert actions.last_saved_name == "tracked_macro"
    assert actions.last_saved_actions == [("click", 10, 20)]


# ══════════════════════════════════════════════════════════════════
# tool_delete_macro
# ══════════════════════════════════════════════════════════════════

def test_tool_delete_macro_removes_existing_macro(tmp_path, monkeypatch):
    import ai.macro_store as macro_store
    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))
    macro_store.save_literal_macro("old_broken_macro", [("click", 1, 1)])

    dropped = []
    monkeypatch.setattr(actions, "single_fcn", False)

    class FakeAiMain:
        # The live tool list is updated incrementally now, not by
        # rebuilding it: refresh_actions() re-read and re-exec'd every
        # surviving macro from disk to achieve the removal of one.
        def drop_macro_tool(self, name):
            dropped.append(name)

    # `import ai.main as ai_main` can resolve through EITHER the `ai`
    # package's own `main` attribute or the sys.modules entry, depending
    # on whether the submodule was already imported when the statement
    # runs. Patching only the package attribute left the real module
    # reachable through the other route, which is why this test was
    # intermittently red. Patch both.
    import ai as ai_pkg
    fake = FakeAiMain()
    monkeypatch.setattr(ai_pkg, "main", fake)
    monkeypatch.setitem(sys.modules, "ai.main", fake)

    result = actions.tool_delete_macro("old_broken_macro")

    assert "Deleted macro tool_old_broken_macro" in result
    assert not macro_store.macro_exists("old_broken_macro")
    assert dropped == ["old_broken_macro"]


def test_tool_delete_macro_fuzzy_matches_name(tmp_path, monkeypatch):
    import ai.macro_store as macro_store
    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))
    macro_store.save_literal_macro("open_youtube_lofi", [("click", 1, 1)])

    dropped = []
    import ai as ai_pkg
    fake = type("M", (), {"drop_macro_tool": lambda self, n: dropped.append(n)})()
    monkeypatch.setattr(ai_pkg, "main", fake)      # see the test above
    monkeypatch.setitem(sys.modules, "ai.main", fake)

    result = actions.tool_delete_macro("tool_open_youtube_lof")  # slightly off + prefixed

    assert "Deleted macro tool_open_youtube_lofi" in result
    # The RESOLVED name is what gets dropped, not the approximate one the
    # caller passed. Handing the fuzzy string through would leave the
    # real tool_open_youtube_lofi function live in the namespace after
    # its file was deleted - callable, and backed by nothing.
    assert dropped == ["open_youtube_lofi"]


def test_tool_delete_macro_reports_when_nothing_matches(tmp_path, monkeypatch):
    import ai.macro_store as macro_store
    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))

    result = actions.tool_delete_macro("totally_unrelated_name")

    assert "No saved macro matching" in result


# ══════════════════════════════════════════════════════════════════
# click_at_target_or_position / _type_text - helpers used by both
# play_actions() and macros generated by ai/macro_generalizer.py
# ══════════════════════════════════════════════════════════════════

def test_type_text_plain_characters(mock_pyautogui):
    actions._type_text("hi")
    calls = [c.args[0] for c in mock_pyautogui.write.call_args_list]
    assert calls == ["h", "i"]


def test_type_text_punctuation_via_clipboard(mock_pyautogui, monkeypatch):
    copied = []
    monkeypatch.setattr(actions.pyperclip, "copy", lambda text: copied.append(text))

    actions._type_text("a!")

    assert copied == ["!"]
    assert mock_pyautogui.write.call_args_list[0].args[0] == "a"
    mock_pyautogui.hotkey.assert_any_call("ctrl", "v")


def test_type_text_presses_delete_after_typing_to_clear_autocomplete(mock_pyautogui, mock_keyboard):
    """
    Regression test: typing "youtube" into an address bar can leave a
    browser-suggested autocomplete URL selected right after it - if the
    next macro step presses Enter, THAT gets submitted instead of the
    literal text. Delete clears a trailing selection like that (and is a
    no-op with nothing selected), so it must always follow typing.
    """
    actions._type_text("youtube")
    mock_keyboard.press_and_release.assert_called_once_with("delete")


def test_type_text_delete_comes_after_all_characters_are_typed(mock_pyautogui, mock_keyboard):
    call_order = []
    mock_pyautogui.write.side_effect = lambda ch: call_order.append(("write", ch))
    mock_keyboard.press_and_release.side_effect = lambda key: call_order.append(("press", key))

    actions._type_text("hi")

    assert call_order == [("write", "h"), ("write", "i"), ("press", "delete")]


def test_type_text_typed_straight_into_a_background_window_skips_the_keyboard(
        monkeypatch, mock_pyautogui, mock_keyboard):
    monkeypatch.setattr(actions.direct_input, "type_text",
                        lambda text, window=None: actions.direct_input.Outcome(True, "text message"))
    monkeypatch.setattr(actions.direct_input, "press_key",
                        lambda key, window=None: actions.direct_input.Outcome(True, "window message"))

    actions._type_text("hi")

    mock_pyautogui.write.assert_not_called()
    mock_keyboard.press_and_release.assert_not_called()


def test_line_breaks_and_tabs_are_pressed_as_keys(mock_pyautogui, mock_keyboard):
    """The way pyautogui typed them: "\n" submits a field, "\t" moves on."""
    order = []
    mock_pyautogui.write.side_effect = lambda ch: order.append(ch)
    mock_keyboard.press_and_release.side_effect = lambda key: order.append(f"<{key}>")

    actions._type_text("ab\tc\r\nd")

    assert order == ["a", "b", "<tab>", "c", "<enter>", "d", "<delete>"]


def test_typing_stops_when_its_window_cannot_be_reached(monkeypatch, mock_pyautogui, mock_keyboard):
    monkeypatch.setattr(actions.direct_input, "type_text",
                        lambda text, window=None: actions.direct_input.Outcome(False, "blocked"))

    actions._type_text("secret")

    mock_pyautogui.write.assert_not_called()
    mock_keyboard.press_and_release.assert_not_called()


def test_a_key_combination_is_not_sent_when_stopped(monkeypatch, mock_keyboard):
    monkeypatch.setattr(actions, "emergency_stop", True)
    actions._press_hotkey("ctrl+t")
    mock_keyboard.press_and_release.assert_not_called()


def test_a_generalized_macro_stops_at_a_click_that_cannot_be_made(monkeypatch):
    """Its later steps were made for the screen that click leads to."""
    import types as pytypes
    fake_ai_main = pytypes.SimpleNamespace(
        get_position_object_and_click=lambda target, button="left": "Could not locate 'x': nope"
    )
    monkeypatch.setitem(sys.modules, "ai.main", fake_ai_main)
    monkeypatch.setattr(sys.modules["ai"], "main", fake_ai_main, raising=False)
    monkeypatch.setattr(actions.direct_input, "click",
                        lambda x, y, button="left": actions.direct_input.Outcome(False, "mouse", note="no mouse"))

    with pytest.raises(actions.MacroStopped):
        actions.click_at_target_or_position("search bar", 10, 20)


def test_a_generalized_macro_stops_when_its_target_was_found_but_not_clicked(monkeypatch):
    import types as pytypes
    fake_ai_main = pytypes.SimpleNamespace(
        get_position_object_and_click=lambda target, button="left": "Could not click x at (1, 2): no mouse."
    )
    monkeypatch.setitem(sys.modules, "ai.main", fake_ai_main)
    monkeypatch.setattr(sys.modules["ai"], "main", fake_ai_main, raising=False)

    with pytest.raises(actions.MacroStopped):
        actions.click_at_target_or_position("search bar", 10, 20)


def test_enter_is_released(mock_keyboard):
    """keyboard.press() held Enter down for good."""
    actions.tool_enter()
    mock_keyboard.press_and_release.assert_called_once_with("enter")
    mock_keyboard.press.assert_not_called()


def test_a_key_combination_brings_the_ai_window_forward_first(monkeypatch, mock_keyboard):
    order = []
    monkeypatch.setattr(actions.direct_input, "keyboard_window",
                        lambda window=None: order.append("forward") or actions.direct_input.Outcome(False, "keyboard"))
    mock_keyboard.press_and_release.side_effect = lambda combo: order.append(combo)

    assert actions._press_hotkey("ctrl+t")

    assert order == ["forward", "ctrl+t"]


def test_a_windows_key_combination_is_for_windows_whatever_is_in_front(monkeypatch, mock_keyboard):
    """Win+R opens Run from any window, Kestrel's own included."""
    monkeypatch.setattr(actions.direct_input, "keyboard_window",
                        lambda window=None: actions.direct_input.Outcome(False, "blocked", note="Kestrel's own"))

    assert actions._press_hotkey("win+r")
    mock_keyboard.press_and_release.assert_called_once_with("win+r")
    assert not actions._press_hotkey("ctrl+v")


# ── Typing that can't finish ────────────────────────────────────────────

@pytest.fixture
def keyboard_until(monkeypatch):
    """The window being typed into stays in front for `n` more keys."""
    def setup(n):
        left = {"keys": n}

        def reaches(window):
            left["keys"] -= 1
            return left["keys"] >= 0

        monkeypatch.setattr(actions.direct_input, "keyboard_reaches", reaches)
    return setup


def test_typing_stops_at_the_key_where_the_window_left_the_front(mock_pyautogui, mock_keyboard, keyboard_until):
    """Checked before every key, so nothing more lands in the user's
    window."""
    keyboard_until(3)

    typed = actions._type_text("Remove-Item -Recurse C:\\temp")

    assert not typed and typed.started and "no longer the one in front" in typed.note
    assert [c.args[0] for c in mock_pyautogui.write.call_args_list] == ["R", "e", "m"]
    mock_keyboard.press_and_release.assert_not_called()            # no Delete either


def test_write_and_enter_does_not_press_enter_after_cut_off_typing(mock_pyautogui, mock_keyboard, keyboard_until):
    """A command cut short would run as it is."""
    keyboard_until(5)
    recorded = len(actions.actions_list)

    result = actions.tool_write_and_enter("Remove-Item -Recurse C:\\temp")

    assert "partway" in result and "Enter was not pressed" in result
    mock_keyboard.press_and_release.assert_not_called()
    assert len(actions.actions_list) == recorded                     # nothing recorded for a macro


def test_write_and_enter_says_when_nothing_was_typed(monkeypatch, mock_pyautogui, mock_keyboard):
    monkeypatch.setattr(actions, "single_fcn", False)
    monkeypatch.setattr(actions.direct_input, "type_text",
                        lambda text, window=None: actions.direct_input.Outcome(
                            False, "blocked", note="its window couldn't be brought to the front"))

    result = actions.tool_write_and_enter("hello")

    assert result.startswith("Nothing was typed") and "brought to the front" in result
    mock_keyboard.press_and_release.assert_not_called()
    assert actions.single_fcn is False                              # the run goes on, the AI was told


def test_enter_says_when_it_was_not_pressed(monkeypatch, mock_keyboard):
    monkeypatch.setattr(actions.direct_input, "press_key",
                        lambda key, window=None: actions.direct_input.Outcome(
                            False, "blocked", note="Kestrel's own window is in front"))

    assert "not pressed" in actions.tool_enter()
    mock_keyboard.press_and_release.assert_not_called()


def test_typing_that_stops_before_its_first_key_says_nothing_was_typed(mock_pyautogui, mock_keyboard,
                                                                     keyboard_until):
    keyboard_until(0)

    result = actions.tool_write_and_enter("hello")

    assert result.startswith("Nothing was typed")


def test_keys_that_can_open_a_window_are_followed(monkeypatch, mock_keyboard):
    """Win+R's Run box, Ctrl+N's window, the app Enter opened a file in:
    the next keys go there (ai/direct_input.py's follow_keys)."""
    followed = []
    monkeypatch.setattr(actions.direct_input, "follow_keys",
                        lambda before, system=False, switch=False: followed.append(system))

    actions._press_key("enter")
    actions._press_key("tab")
    actions._press_hotkey("win+r")
    actions._press_hotkey("ctrl+n")

    assert followed == [False, True, False]


def test_new_page_says_whether_the_tab_opened(monkeypatch, mock_pyautogui):
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)
    assert actions.tool_new_page() == "Opened a new tab."
    monkeypatch.setattr(actions.direct_input, "keyboard_window",
                        lambda window=None: actions.direct_input.Outcome(False, "blocked", note="no window"))
    assert actions.tool_new_page() == "No new tab was opened: no window."


def test_the_ai_can_press_a_shortcut(monkeypatch, mock_keyboard):
    """Win+R when the user asks for it; recorded for a macro as a block."""
    monkeypatch.setattr(actions, "single_fcn", False)
    monkeypatch.setattr(actions, "actions_list", [])

    assert actions.tool_press_keys("Win + R") == "Pressed win+r."
    assert actions.tool_press_keys("escape") == "Pressed escape."

    assert [c.args[0] for c in mock_keyboard.press_and_release.call_args_list] == ["win+r", "escape"]
    assert actions.actions_list == [{"type": "hotkey", "keys": "win+r"}, {"type": "hotkey", "keys": "escape"}]
    assert actions.single_fcn is True


def test_closing_shortcuts_are_asked_about_first(monkeypatch, mock_keyboard):
    asked = []
    monkeypatch.setattr(actions.risk_gate, "require_approval", lambda text: asked.append(text) or False)

    result = actions.tool_press_keys("alt+f4")

    assert result.startswith("Refused") and asked
    mock_keyboard.press_and_release.assert_not_called()


@pytest.mark.parametrize("keys", ["shift+del", "control+w", "left ctrl+w", "Alt + F4", "ctrl+right shift+w"])
def test_closing_shortcuts_are_asked_about_however_they_are_written(monkeypatch, mock_keyboard, keys):
    asked = []
    monkeypatch.setattr(actions.risk_gate, "require_approval", lambda text: asked.append(text) or False)

    assert actions.tool_press_keys(keys).startswith("Refused") and asked
    mock_keyboard.press_and_release.assert_not_called()


@pytest.mark.parametrize("keys", ["alt+f4, enter", "tab, ctrl+w"])
def test_a_closing_shortcut_among_several_presses_is_asked_about(monkeypatch, mock_keyboard, keys):
    """keyboard reads "a, b" as two presses in a row."""
    asked = []
    monkeypatch.setattr(actions.risk_gate, "require_approval", lambda text: asked.append(text) or False)

    assert actions.tool_press_keys(keys).startswith("Refused") and asked
    mock_keyboard.press_and_release.assert_not_called()


def test_a_closing_shortcut_in_the_layouts_own_key_names_is_asked_about(monkeypatch, mock_keyboard):
    """keyboard accepts the names of the layout in use: on a French one,
    "maj+suppr" is Shift+Delete. Compared by scan code."""
    codes = {"alt": (56, 541), "f4": (62,), "ctrl": (29, 3613), "left ctrl": (29,), "w": (17,), "q": (16,),
             "shift": (42, 54), "maj": (42,), "delete": (83,), "suppr": (83,), "enter": (28,), "a": (30,)}

    def parse_hotkey(combo):
        return tuple(tuple(codes[name.strip()] for name in step.split("+")) for step in combo.split(","))

    mock_keyboard.parse_hotkey.side_effect = parse_hotkey
    monkeypatch.setattr(actions, "_confirm_scan_codes", None)
    asked = []
    monkeypatch.setattr(actions.risk_gate, "require_approval", lambda text: asked.append(text) or False)

    assert actions.tool_press_keys("maj+suppr").startswith("Refused")
    assert len(asked) == 1
    assert actions._needs_confirming("ctrl+a") is False
    mock_keyboard.press_and_release.assert_not_called()


def test_a_shortcut_that_cant_reach_its_window_says_so(monkeypatch, mock_keyboard):
    monkeypatch.setattr(actions.direct_input, "keyboard_window",
                        lambda window=None: actions.direct_input.Outcome(False, "blocked", note="no window"))

    assert actions.tool_press_keys("ctrl+a") == "ctrl+a was not pressed: no window."


def test_no_delete_outside_a_text_field(monkeypatch, mock_pyautogui, mock_keyboard):
    """In a file list a Delete would send the selected file to the Recycle
    Bin."""
    monkeypatch.setattr(actions.direct_input, "focus_takes_text", lambda: False)

    assert actions._type_text("report")

    mock_keyboard.press_and_release.assert_not_called()


def test_combinations_that_stay_in_their_window_arent_followed(monkeypatch, mock_keyboard):
    followed = []
    monkeypatch.setattr(actions.direct_input, "follow_keys",
                        lambda before, system=False, switch=False: followed.append((system, switch)))

    actions._press_hotkey("ctrl+c")
    actions._press_hotkey("Ctrl+Shift+Z")
    actions._press_hotkey("ctrl+n")
    actions._press_hotkey("alt+tab")

    assert followed == [(False, False), (False, True)]


def test_alt_tab_goes_through_when_typing_is_held_back(monkeypatch, mock_keyboard):
    """It is for Windows itself, like Win+R."""
    monkeypatch.setattr(actions.direct_input, "keyboard_window",
                        lambda window=None: actions.direct_input.Outcome(False, "blocked", note="closed"))

    assert actions._press_hotkey("alt+tab")
    mock_keyboard.press_and_release.assert_called_once_with("alt+tab")


def test_no_delete_after_text_that_ends_with_enter(mock_pyautogui, mock_keyboard):
    """That Enter may have closed a Save As dialog; a Delete after it would
    land on the file selected in the window behind."""
    pressed = []
    mock_keyboard.press_and_release.side_effect = lambda key: pressed.append(key)

    assert actions._type_text("report.txt\n")

    assert pressed == ["enter"]


def test_a_macro_stops_where_its_typing_could_not_go_in(monkeypatch, mock_keyboard):
    """Block macros, and recordings: the steps after it expect the text."""
    monkeypatch.setattr(actions, "_type_text", lambda text: actions.Typed(False, "the window left the front"))
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)

    actions.play_actions([{"type": "write", "value": "hello"}, {"type": "enter"}])
    actions.play_actions([("hello", "write"), ("enter", "enter")])

    mock_keyboard.press_and_release.assert_not_called()


def test_click_at_target_or_position_uses_vision_when_it_succeeds(monkeypatch, mock_pyautogui):
    import types as pytypes
    fake_ai_main = pytypes.SimpleNamespace(
        get_position_object_and_click=lambda target, button="left": f"Clicked {target}"
    )
    # `import ai.main as ai_main` (used inside click_at_target_or_position) resolves
    # via the parent package's attribute, not sys.modules['ai.main'] alone - patch both.
    monkeypatch.setitem(sys.modules, "ai.main", fake_ai_main)
    monkeypatch.setattr(sys.modules["ai"], "main", fake_ai_main, raising=False)

    result = actions.click_at_target_or_position("search bar", 10, 20)

    assert result == "Clicked search bar"
    mock_pyautogui.click.assert_not_called()  # vision path succeeded, no fallback needed


def test_click_at_target_or_position_passes_button_through_to_vision(monkeypatch, mock_pyautogui):
    import types as pytypes
    received = {}

    def fake_click(target, button="left"):
        received["button"] = button
        return f"Right-clicked {target}"

    fake_ai_main = pytypes.SimpleNamespace(get_position_object_and_click=fake_click)
    monkeypatch.setitem(sys.modules, "ai.main", fake_ai_main)
    monkeypatch.setattr(sys.modules["ai"], "main", fake_ai_main, raising=False)

    result = actions.click_at_target_or_position("this file", 10, 20, button="right")

    assert received["button"] == "right"
    assert result == "Right-clicked this file"


def test_a_generalized_macro_clicks_its_recorded_spot_when_kestrel_covers_the_target(monkeypatch, clicks):
    """Kestrel's window over the target hides it from the screenshot. A
    recorded macro clicks its spot through Kestrel's window; so does a
    generalized one, rather than stopping."""
    import types as pytypes
    fake_ai_main = pytypes.SimpleNamespace(
        get_position_object_and_click=lambda target, button="left": (
            "Could not click search bar at (960, 540): Kestrel's own window is there, not the app.")
    )
    monkeypatch.setitem(sys.modules, "ai.main", fake_ai_main)
    monkeypatch.setattr(sys.modules["ai"], "main", fake_ai_main, raising=False)

    assert actions.click_at_target_or_position("search bar", 10, 20) == "Clicked fallback position (10, 20)"
    assert clicks == [(10, 20, "left")]


def test_click_at_target_or_position_falls_back_with_right_button(monkeypatch, clicks):
    import types as pytypes
    fake_ai_main = pytypes.SimpleNamespace(
        get_position_object_and_click=lambda target, button="left": "Could not locate 'x': nope"
    )
    monkeypatch.setitem(sys.modules, "ai.main", fake_ai_main)
    monkeypatch.setattr(sys.modules["ai"], "main", fake_ai_main, raising=False)

    actions.click_at_target_or_position("this file", 10, 20, button="right")

    assert clicks == [(10, 20, "right")]


def test_click_at_target_or_position_falls_back_on_vision_failure(monkeypatch, clicks):
    import types as pytypes
    fake_ai_main = pytypes.SimpleNamespace(
        get_position_object_and_click=lambda target, button="left": "Could not locate 'search bar': nope"
    )
    monkeypatch.setitem(sys.modules, "ai.main", fake_ai_main)
    monkeypatch.setattr(sys.modules["ai"], "main", fake_ai_main, raising=False)

    result = actions.click_at_target_or_position("search bar", 111, 222)

    assert clicks == [(111, 222, "left")]
    assert "111" in result and "222" in result


def test_click_at_target_or_position_falls_back_on_exception(monkeypatch, clicks):
    import types as pytypes

    def _raise(target, button="left"):
        raise RuntimeError("vision backend unavailable")

    fake_ai_main = pytypes.SimpleNamespace(get_position_object_and_click=_raise)
    monkeypatch.setitem(sys.modules, "ai.main", fake_ai_main)
    monkeypatch.setattr(sys.modules["ai"], "main", fake_ai_main, raising=False)

    actions.click_at_target_or_position("search bar", 5, 6)

    assert clicks == [(5, 6, "left")]


# ══════════════════════════════════════════════════════════════════
# tool_open_application / _find_running_app_windows / _focus_window
# bring an already-running app to the foreground instead of relaunching it
# ══════════════════════════════════════════════════════════════════

class _FakeProc:
    def __init__(self, pid, name):
        self.info = {"pid": pid, "name": name}


def test_find_running_app_windows_returns_empty_without_psutil(monkeypatch):
    monkeypatch.setattr(actions, "psutil", None)
    assert actions._find_running_app_windows("chrome") == []


def test_find_running_app_windows_returns_empty_without_win32gui(monkeypatch):
    monkeypatch.setattr(actions, "psutil", object())  # anything non-None
    monkeypatch.setattr(actions, "win32gui", None)
    assert actions._find_running_app_windows("chrome") == []


def test_find_running_app_windows_matches_process_name_loosely(monkeypatch):
    fake_psutil = type("P", (), {})()
    fake_psutil.process_iter = lambda attrs: [
        _FakeProc(111, "chrome.exe"),
        _FakeProc(222, "notepad.exe"),
    ]
    monkeypatch.setattr(actions, "psutil", fake_psutil)

    fake_win32gui = type("W", (), {})()

    def fake_enum_windows(callback, extra):
        callback(1001, extra)  # belongs to pid 111 (chrome) per fake below
        callback(1002, extra)  # belongs to pid 222 (notepad)

    fake_win32gui.EnumWindows = fake_enum_windows
    fake_win32gui.IsWindowVisible = lambda hwnd: True
    fake_win32gui.GetWindowText = lambda hwnd: "Some Window"
    monkeypatch.setattr(actions, "win32gui", fake_win32gui)

    fake_win32process = type("WP", (), {})()
    fake_win32process.GetWindowThreadProcessId = lambda hwnd: (0, 111 if hwnd == 1001 else 222)
    monkeypatch.setattr(actions, "win32process", fake_win32process)

    result = actions._find_running_app_windows("chrome")
    assert result == [1001]


def test_find_running_app_windows_ignores_invisible_or_untitled_windows(monkeypatch):
    fake_psutil = type("P", (), {})()
    fake_psutil.process_iter = lambda attrs: [_FakeProc(111, "chrome.exe")]
    monkeypatch.setattr(actions, "psutil", fake_psutil)

    fake_win32gui = type("W", (), {})()
    fake_win32gui.EnumWindows = lambda callback, extra: callback(1001, extra)
    fake_win32gui.IsWindowVisible = lambda hwnd: False  # hidden window
    fake_win32gui.GetWindowText = lambda hwnd: "Some Window"
    monkeypatch.setattr(actions, "win32gui", fake_win32gui)

    fake_win32process = type("WP", (), {})()
    fake_win32process.GetWindowThreadProcessId = lambda hwnd: (0, 111)
    monkeypatch.setattr(actions, "win32process", fake_win32process)

    assert actions._find_running_app_windows("chrome") == []


def test_focus_window_calls_set_foreground_window(monkeypatch):
    calls = []

    fake_win32gui = type("W", (), {})()
    fake_win32gui.ShowWindow = lambda hwnd, flag: calls.append(("show", hwnd, flag))
    fake_win32gui.GetForegroundWindow = lambda: 999
    fake_win32gui.SetForegroundWindow = lambda hwnd: calls.append(("set_fg", hwnd))
    monkeypatch.setattr(actions, "win32gui", fake_win32gui)

    fake_win32process = type("WP", (), {})()
    fake_win32process.GetWindowThreadProcessId = lambda hwnd: (0, 42)
    fake_win32process.AttachThreadInput = lambda *a: calls.append(("attach", a))
    monkeypatch.setattr(actions, "win32process", fake_win32process)

    fake_win32api = type("WA", (), {})()
    fake_win32api.GetCurrentThreadId = lambda: 1
    monkeypatch.setattr(actions, "win32api", fake_win32api)

    monkeypatch.setattr(actions, "win32con", type("WC", (), {"SW_MAXIMIZE": 3})())

    monkeypatch.setattr(actions.direct_input, "_target_hwnd", None)

    result = actions._focus_window(1001)

    assert result is True
    assert ("set_fg", 1001) in calls
    assert ("show", 1001, 3) in calls
    # What the AI types next goes to the app it just brought up.
    assert actions.direct_input._target_hwnd == 1001


def test_focus_window_returns_false_on_exception(monkeypatch):
    fake_win32gui = type("W", (), {})()

    def _raise(hwnd, flag):
        raise RuntimeError("no such window")

    fake_win32gui.ShowWindow = _raise
    monkeypatch.setattr(actions, "win32gui", fake_win32gui)

    assert actions._focus_window(1001) is False


def test_normalize_app_name_resolves_edge():
    assert actions._normalize_app_name("edge") == "msedge"
    assert actions._normalize_app_name("Edge") == "msedge"
    assert actions._normalize_app_name("Microsoft Edge") == "msedge"
    assert actions._normalize_app_name("  EDGE  ") == "msedge"


def test_normalize_app_name_passes_through_unlisted_apps():
    assert actions._normalize_app_name("chrome") == "chrome"
    assert actions._normalize_app_name("spotify") == "spotify"
    assert actions._normalize_app_name("some random app") == "some random app"


def test_tool_open_application_uses_resolved_name_for_appopener(monkeypatch):
    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: [])
    monkeypatch.setattr(actions, "_wait_for_window_and_maximize", lambda name: True)
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "single_fcn", False)

    called = []
    monkeypatch.setattr(actions, "appopen", lambda *a, **k: called.append(a) or "opened")

    actions.tool_open_application("edge")

    assert called == [("msedge",)]  # not "edge" - that's exactly what AppOpener couldn't find


def test_tool_open_application_uses_resolved_name_for_process_matching(monkeypatch):
    seen = []
    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: seen.append(name) or [])
    monkeypatch.setattr(actions, "_wait_for_window_and_maximize", lambda name: True)
    monkeypatch.setattr(actions, "appopen", lambda *a, **k: "opened")
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "single_fcn", False)

    actions.tool_open_application("Microsoft Edge")

    assert seen == ["msedge"]


def test_tool_open_application_respects_low_risk_action_limit(monkeypatch):
    # Regression test: tool_open_application used to never call the
    # limiter at all, so a "low risk actions / session" cap configured
    # in Settings only ever throttled tool_run_command - asking Kestrel
    # to open a 4th app after a limit of 3 would just open it anyway.
    from ai.safety import RiskLevel
    actions.safety.limiter.set_limit(RiskLevel.LOW, 3)

    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: [])
    monkeypatch.setattr(actions, "_wait_for_window_and_maximize", lambda name: True)
    monkeypatch.setattr(actions, "appopen", lambda *a, **k: "opened")
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "single_fcn", False)

    results = [actions.tool_open_application(f"app{i}") for i in range(4)]

    assert "SAFETY LIMIT REACHED" not in results[0]
    assert "SAFETY LIMIT REACHED" not in results[1]
    assert "SAFETY LIMIT REACHED" not in results[2]
    assert "SAFETY LIMIT REACHED" in results[3]


def test_tool_open_application_focuses_instead_of_relaunching(monkeypatch):
    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: [1001])
    monkeypatch.setattr(actions, "_focus_window", lambda hwnd: True)

    called = []
    monkeypatch.setattr(actions, "appopen", lambda *a, **k: called.append((a, k)))
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "single_fcn", False)

    result = actions.tool_open_application("chrome")

    assert called == []  # never relaunched
    assert "already running" in result


def test_is_browser_recognizes_known_browsers():
    assert actions._is_browser("chrome") is True
    assert actions._is_browser("msedge") is True
    assert actions._is_browser("firefox") is True
    assert actions._is_browser("CHROME") is True
    assert actions._is_browser("notepad") is False
    assert actions._is_browser("spotify") is False


def test_tool_open_application_opens_fresh_tab_for_already_running_browser(monkeypatch):
    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: [1001])
    monkeypatch.setattr(actions, "_focus_window", lambda hwnd: True)
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "single_fcn", False)

    fresh_tab_calls = []
    monkeypatch.setattr(actions, "_open_fresh_browser_tab", lambda: fresh_tab_calls.append(1) or True)

    called = []
    monkeypatch.setattr(actions, "appopen", lambda *a, **k: called.append((a, k)))

    result = actions.tool_open_application("edge")  # resolves to msedge, a recognized browser

    assert called == []  # never relaunched
    assert fresh_tab_calls == [1]
    assert "new tab" in result
    assert "existing tabs" in result


def test_tool_open_application_does_not_open_fresh_tab_for_non_browser(monkeypatch):
    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: [1001])
    monkeypatch.setattr(actions, "_focus_window", lambda hwnd: True)
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "single_fcn", False)

    fresh_tab_calls = []
    monkeypatch.setattr(actions, "_open_fresh_browser_tab", lambda: fresh_tab_calls.append(1) or True)

    result = actions.tool_open_application("spotify")

    assert fresh_tab_calls == []  # not a browser, never attempted
    assert "new tab" not in result


def test_tool_open_application_falls_back_to_focus_message_if_fresh_tab_fails(monkeypatch):
    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: [1001])
    monkeypatch.setattr(actions, "_focus_window", lambda hwnd: True)
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "single_fcn", False)
    monkeypatch.setattr(actions, "_open_fresh_browser_tab", lambda: False)  # Ctrl+T attempt failed

    result = actions.tool_open_application("chrome")

    assert "brought it to the foreground" in result  # fell back to the normal focus message


def test_open_fresh_browser_tab_sends_ctrl_t(monkeypatch, mock_pyautogui):
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)

    result = actions._open_fresh_browser_tab()

    assert result is True
    mock_pyautogui.hotkey.assert_called_once_with("ctrl", "t")


def test_open_fresh_browser_tab_returns_false_on_exception(monkeypatch, mock_pyautogui):
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)
    mock_pyautogui.hotkey.side_effect = RuntimeError("boom")

    assert actions._open_fresh_browser_tab() is False


def test_tool_open_application_launches_when_not_running(monkeypatch):
    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: [])

    called = []
    monkeypatch.setattr(actions, "appopen", lambda *a, **k: called.append((a, k)) or "opened")
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "single_fcn", False)
    monkeypatch.setattr(actions, "_wait_for_window_and_maximize", lambda name: True)

    result = actions.tool_open_application("chrome")

    assert len(called) == 1
    assert result == "opened"


def test_tool_open_application_maximizes_after_launching(monkeypatch):
    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: [])
    monkeypatch.setattr(actions, "appopen", lambda *a, **k: "opened")
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "single_fcn", False)

    maximize_calls = []
    monkeypatch.setattr(
        actions, "_wait_for_window_and_maximize",
        lambda name: maximize_calls.append(name) or True,
    )

    actions.tool_open_application("chrome")

    assert maximize_calls == ["chrome"]


def test_wait_for_window_and_maximize_returns_false_without_deps(monkeypatch):
    monkeypatch.setattr(actions, "psutil", None)
    assert actions._wait_for_window_and_maximize("chrome", timeout=0.1, interval=0.05) is False


def test_wait_for_window_and_maximize_polls_until_window_appears(monkeypatch):
    monkeypatch.setattr(actions, "psutil", object())
    monkeypatch.setattr(actions, "win32gui", object())  # just needs to be non-None here

    attempts = {"count": 0}

    def fake_find(name):
        attempts["count"] += 1
        return [1001] if attempts["count"] >= 3 else []

    monkeypatch.setattr(actions, "_find_running_app_windows", fake_find)
    monkeypatch.setattr(actions, "_focus_window", lambda hwnd: hwnd == 1001)
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)  # skip real waiting

    result = actions._wait_for_window_and_maximize("chrome", timeout=5.0, interval=0.01)

    assert result is True
    assert attempts["count"] == 3


def test_wait_for_window_and_maximize_gives_up_after_timeout(monkeypatch):
    monkeypatch.setattr(actions, "psutil", object())
    monkeypatch.setattr(actions, "win32gui", object())
    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: [])
    monkeypatch.setattr(actions.time, "sleep", lambda s: None)

    result = actions._wait_for_window_and_maximize("chrome", timeout=0.05, interval=0.01)

    assert result is False


def test_tool_open_application_launches_when_focus_fails(monkeypatch):
    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: [1001])
    monkeypatch.setattr(actions, "_focus_window", lambda hwnd: False)  # focus attempt failed

    called = []
    monkeypatch.setattr(actions, "appopen", lambda *a, **k: called.append((a, k)) or "opened")
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "single_fcn", False)

    result = actions.tool_open_application("chrome")

    assert len(called) == 1  # fell back to relaunch/refocus via AppOpener
    assert result == "opened"


# ══════════════════════════════════════════════════════════════════
# Native Windows apps - language-independent launch, bypassing AppOpener
# ══════════════════════════════════════════════════════════════════

def test_launch_native_app_recognizes_known_exe_apps():
    result = actions._launch_native_app("notepad")
    assert result is not None
    process_name, _launch = result
    assert process_name == "notepad"


def test_launch_native_app_recognizes_uri_apps():
    result = actions._launch_native_app("settings")
    assert result is not None
    process_name, _launch = result
    assert process_name is None  # nothing reliable to focus for a URI launch


def test_launch_native_app_returns_none_for_third_party_apps():
    assert actions._launch_native_app("spotify") is None
    assert actions._launch_native_app("chrome") is None
    assert actions._launch_native_app("discord") is None


def test_tool_open_application_bypasses_appopener_for_native_apps(monkeypatch):
    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: [])
    monkeypatch.setattr(actions, "_wait_for_window_and_maximize", lambda name: True)
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "single_fcn", False)

    appopen_called = []
    monkeypatch.setattr(actions, "appopen", lambda *a, **k: appopen_called.append(a) or "opened")

    popen_called = []
    monkeypatch.setattr(actions.subprocess, "Popen", lambda args, **k: popen_called.append(args))

    result = actions.tool_open_application("notepad")

    assert appopen_called == []  # AppOpener's own (localized) name search never ran
    assert popen_called == [["notepad.exe"]]
    assert "Opened notepad" in result


def test_tool_open_application_still_uses_appopener_for_non_native_apps(monkeypatch):
    monkeypatch.setattr(actions, "_find_running_app_windows", lambda name: [])
    monkeypatch.setattr(actions, "_wait_for_window_and_maximize", lambda name: True)
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "single_fcn", False)

    appopen_called = []
    monkeypatch.setattr(actions, "appopen", lambda *a, **k: appopen_called.append(a) or "opened")

    actions.tool_open_application("spotify")

    assert appopen_called == [("spotify",)]


# ══════════════════════════════════════════════════════════════════
# tool_run_command - allow-list, blocked commands, no shell=True
# ══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_safety_limiter():
    from ai.safety import limiter
    limiter.reset()
    yield
    limiter.reset()


def test_tool_run_command_refuses_command_not_on_allow_list(monkeypatch):
    monkeypatch.setattr(actions, "emergency_stop", False)
    result = actions.tool_run_command("format C:")
    assert "never allowed" in result

    result = actions.tool_run_command("some_totally_unknown_binary --flag")
    assert "isn't on the allowed command list" in result


def test_tool_run_command_refuses_shell_metacharacters(monkeypatch):
    for dangerous in ("dir & del *", "type a.txt | more", "echo hi > secrets.txt"):
        result = actions.tool_run_command(dangerous)
        assert "refused" in result


def test_tool_run_command_runs_allowed_command_without_shell(monkeypatch):
    calls = []

    class FakeCompleted:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return FakeCompleted()

    monkeypatch.setattr(actions.subprocess, "run", fake_run)

    result = actions.tool_run_command("git status")

    assert calls, "subprocess.run should have been called"
    args, kwargs = calls[0]
    assert args == ["cmd.exe", "/c", "git", "status"]
    assert kwargs.get("shell") is False
    assert "succeeded" in result


def test_tool_run_command_respects_high_risk_action_limit(monkeypatch):
    from ai.safety import RiskLevel
    actions.safety.limiter.set_limit(RiskLevel.HIGH, 1)
    monkeypatch.setattr(actions.risk_gate, "require_approval", lambda desc: True)

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(actions.subprocess, "run", lambda *a, **k: FakeCompleted())

    first = actions.tool_run_command("del somefile.txt")
    assert "succeeded" in first

    second = actions.tool_run_command("del anotherfile.txt")
    assert "SAFETY LIMIT REACHED" in second


def test_tool_run_command_refuses_when_emergency_stop_active(monkeypatch):
    monkeypatch.setattr(actions, "emergency_stop", True)
    result = actions.tool_run_command("dir")
    assert "emergency stop is active" in result


# ══════════════════════════════════════════════════════════════════
# tool_close_application - no shell=True, respects safety limiter
# ══════════════════════════════════════════════════════════════════

def test_tool_close_application_uses_argv_list_not_shell_string(monkeypatch):
    monkeypatch.setattr(actions, "_get_running_processes", lambda: ["notepad.exe"])
    monkeypatch.setattr(actions, "single_fcn", False)
    monkeypatch.setattr(actions.risk_gate, "require_approval", lambda desc: True)

    calls = []

    class FakeCompleted:
        returncode = 0

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return FakeCompleted()

    monkeypatch.setattr(actions.subprocess, "run", fake_run)

    actions.tool_close_application("notepad")

    assert calls
    args, kwargs = calls[0]
    assert args == ["taskkill", "/IM", "notepad.exe", "/F"]
    assert kwargs.get("shell") is False


def test_tool_close_application_respects_high_risk_action_limit(monkeypatch):
    from ai.safety import RiskLevel
    actions.safety.limiter.set_limit(RiskLevel.HIGH, 0)
    monkeypatch.setattr(actions, "_get_running_processes", lambda: ["notepad.exe"])
    monkeypatch.setattr(actions, "single_fcn", False)
    monkeypatch.setattr(actions.risk_gate, "require_approval", lambda desc: True)

    result = actions.tool_close_application("notepad")

    assert "SAFETY LIMIT REACHED" in result


# ══════════════════════════════════════════════════════════════════
# HIGH-risk human-approval gate (ai/risk_gate.py)
# ══════════════════════════════════════════════════════════════════

def test_tool_run_command_refused_when_high_risk_denied(monkeypatch):
    monkeypatch.setattr(actions.risk_gate, "require_approval", lambda desc: False)
    result = actions.tool_run_command("del somefile.txt")
    assert "not approved by the human" in result


def test_tool_run_command_low_and_medium_risk_skip_the_gate(monkeypatch):
    """A LOW/MEDIUM-risk command must never even ask for approval - only
    HIGH-risk ones should reach ai.risk_gate.require_approval()."""
    calls = []
    monkeypatch.setattr(actions.risk_gate, "require_approval", lambda desc: calls.append(desc) or True)

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(actions.subprocess, "run", lambda *a, **k: FakeCompleted())
    actions.tool_run_command("dir")
    assert calls == []


def test_tool_close_application_refused_when_high_risk_denied(monkeypatch):
    monkeypatch.setattr(actions, "_get_running_processes", lambda: ["notepad.exe"])
    monkeypatch.setattr(actions.risk_gate, "require_approval", lambda desc: False)
    result = actions.tool_close_application("notepad")
    assert "not approved by the human" in result


def test_risk_gate_fails_closed_with_no_handler_connected():
    """The actual default in ai/risk_gate.py - no UI connected means
    HIGH-risk actions are denied, never silently allowed."""
    import ai.risk_gate as risk_gate
    assert risk_gate.require_approval("anything") is False


# ══════════════════════════════════════════════════════════════════
# Human handoff (2-tier automation pipeline)
# ══════════════════════════════════════════════════════════════════

def test_tool_request_human_handoff_sets_state_and_clears_event():
    actions.human_handoff_event.set()  # simulate a stale set from a previous run
    actions.human_handoff_requested = False
    actions.human_handoff_reason = None

    result = actions.tool_request_human_handoff("A CAPTCHA is blocking the form.")

    assert actions.human_handoff_requested is True
    assert actions.human_handoff_reason == "A CAPTCHA is blocking the form."
    assert actions.human_handoff_event.is_set() is False
    assert "Waiting for the human" in result


def test_resume_human_handoff_clears_state_and_sets_event():
    actions.human_handoff_requested = True
    actions.human_handoff_reason = "2FA code needed."
    actions.human_handoff_event.clear()

    actions.resume_human_handoff()

    assert actions.human_handoff_requested is False
    assert actions.human_handoff_reason is None
    assert actions.human_handoff_event.is_set() is True


# ══════════════════════════════════════════════════════════════════
# tool_screenshot - must not falsely signal "goal complete"
# ══════════════════════════════════════════════════════════════════

def test_tool_screenshot_does_not_set_single_fcn(monkeypatch):
    # single_fcn is what run_autonomous_mode reads to decide a run is
    # done (see ai/main.py). tool_screenshot() is the AI looking at the
    # screen, not finishing the task - it must leave single_fcn alone,
    # or an autonomous run would stop dead the moment the model called
    # it mid-task.
    monkeypatch.setattr(actions, "single_fcn", False)
    fake_ai_main = types.SimpleNamespace(trigger_screenshot=lambda: "/tmp/shot.png")
    monkeypatch.setitem(sys.modules, "ai.main", fake_ai_main)

    result = actions.tool_screenshot()

    assert actions.single_fcn is False
    assert "Screenshot taken" in result


# ══════════════════════════════════════════════════════════════════
# tool_read_file
# ══════════════════════════════════════════════════════════════════

def test_tool_read_file_returns_formatted_attachment_block(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello from disk", encoding="utf-8")

    result = actions.tool_read_file(str(f))

    assert result == "[Attached file: notes.txt]\nhello from disk"


def test_tool_read_file_reports_missing_file():
    result = actions.tool_read_file(str("/nonexistent/path/ghost.txt"))
    assert "Couldn't read" in result
    assert "not found" in result.lower()


def test_tool_read_file_reports_unsupported_type(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"binary")

    result = actions.tool_read_file(str(f))

    assert "Couldn't read" in result
    assert "Unsupported file type" in result


def test_tool_read_file_rejects_empty_path():
    result = actions.tool_read_file("")
    assert "no path given" in result


# ══════════════════════════════════════════════════════════════════
# tool_merge_files_to_pdf
# ══════════════════════════════════════════════════════════════════

def test_tool_merge_files_to_pdf_rejects_empty_paths():
    result = actions.tool_merge_files_to_pdf([], "merged")
    assert "no files given" in result


def test_tool_merge_files_to_pdf_rejects_empty_output_filename(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    result = actions.tool_merge_files_to_pdf([str(f)], "")
    assert "no output filename given" in result


def test_tool_merge_files_to_pdf_success_reports_path_and_opens(tmp_path, monkeypatch):
    import ai.file_generation as file_generation
    out_dir = tmp_path / "Kestrel Files"
    out_dir.mkdir()
    monkeypatch.setattr(file_generation, "generated_files_dir", lambda: str(out_dir))

    opened = []
    monkeypatch.setattr(actions.os, "startfile", lambda p: opened.append(p), raising=False)

    src = tmp_path / "notes.txt"
    src.write_text("merge me", encoding="utf-8")

    result = actions.tool_merge_files_to_pdf([str(src)], "combined")

    assert "Merged PDF saved to" in result
    assert result.endswith("and opened.")
    assert len(opened) == 1


def test_tool_merge_files_to_pdf_reports_skipped_files(tmp_path, monkeypatch):
    import ai.file_generation as file_generation
    out_dir = tmp_path / "Kestrel Files"
    out_dir.mkdir()
    monkeypatch.setattr(file_generation, "generated_files_dir", lambda: str(out_dir))
    monkeypatch.setattr(actions.os, "startfile", lambda p: None, raising=False)

    src = tmp_path / "notes.txt"
    src.write_text("merge me", encoding="utf-8")
    missing = str(tmp_path / "ghost.png")

    result = actions.tool_merge_files_to_pdf([str(src), missing], "combined")

    assert "Merged PDF saved to" in result
    assert "Skipped:" in result
    assert "ghost.png" in result


def test_tool_merge_files_to_pdf_reports_total_failure(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"nope")

    result = actions.tool_merge_files_to_pdf([str(video)], "combined")

    assert "Couldn't merge files" in result


def test_tool_merge_files_to_pdf_respects_open_after_false(tmp_path, monkeypatch):
    import ai.file_generation as file_generation
    out_dir = tmp_path / "Kestrel Files"
    out_dir.mkdir()
    monkeypatch.setattr(file_generation, "generated_files_dir", lambda: str(out_dir))

    opened = []
    monkeypatch.setattr(actions.os, "startfile", lambda p: opened.append(p), raising=False)

    src = tmp_path / "notes.txt"
    src.write_text("merge me", encoding="utf-8")

    result = actions.tool_merge_files_to_pdf([str(src)], "combined", open_after=False)

    assert result.rstrip(".").endswith("combined.pdf") or "Merged PDF saved to" in result
    assert opened == []
    assert not result.endswith("and opened.")
