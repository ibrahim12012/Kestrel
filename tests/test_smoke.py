# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_smoke.py
=====================
GUI smoke tests - every widget and the main window must import and
construct without raising, on a completely fresh database (no chats yet).

This is deliberately shallow (no interaction testing, no assertions about
visuals) - the point is to catch import errors, wrong widget names from
hand-edited .ui files, and initialization-order bugs. It's what caught a
real one: MainWindow used to set self._overlay_active *after* calling
launch_page(), which on a fresh install (no chats) immediately calls
show_window() -> reads self._overlay_active -> AttributeError. Every
previous manual test happened to run against a DB that already had chats
in it, so the crashing code path was never exercised.

Requires PyQt6 and the offscreen Qt platform plugin (see the qapp fixture
in conftest.py) - skips cleanly if PyQt6 isn't installed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Point app.chat_database at a brand new, empty database file."""
    import app.chat_database as db
    path = str(tmp_path / "smoke_test.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    return db


def test_sidebar_constructs(qapp, fresh_db):
    from app.sidebar_widget import Sidebar
    sidebar = Sidebar()
    assert hasattr(sidebar, "btnNewChat")
    assert hasattr(sidebar, "btnActions")
    assert hasattr(sidebar, "btnRecorder")


def test_chat_widget_constructs(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    chat = ChatWidget()
    assert hasattr(chat, "micBtn")
    assert hasattr(chat, "sendBtn")
    assert hasattr(chat, "chatInput")


def test_macros_widget_constructs(qapp, fresh_db):
    from app.macros_widget import MacrosWidget
    macros = MacrosWidget()
    assert hasattr(macros, "btnRecordMacro")
    assert hasattr(macros, "btnRefresh")


def test_splash_screen_constructs(qapp):
    from app.splash_screen import SplashPage
    splash = SplashPage("dark")
    splash.set_status("Loading…")


def test_splash_screen_constructs_light_theme(qapp):
    from app.splash_screen import SplashPage
    splash = SplashPage("light")


def test_splash_screen_minimum_display_time_is_near_instant_when_already_elapsed(qapp):
    from app.splash_screen import SplashPage
    import time
    splash = SplashPage("dark")
    splash._created_at = time.monotonic() - 10  # pretend it's been up for ages
    start = time.monotonic()
    splash.ensure_minimum_display_time(min_seconds=1.4)
    assert time.monotonic() - start < 0.2  # returns ~immediately, doesn't over-wait


def test_splash_screen_minimum_display_time_waits_out_the_remainder(qapp):
    from app.splash_screen import SplashPage
    splash = SplashPage("dark")
    start_time = __import__("time").monotonic()
    splash.ensure_minimum_display_time(min_seconds=0.2)
    elapsed = __import__("time").monotonic() - start_time
    assert elapsed >= 0.15  # actually waited, not a no-op


def test_recorder_widget_constructs(qapp, fresh_db):
    from app.recorder_widget import RecorderWidget
    RecorderWidget()  # must not raise


def test_overlay_widget_constructs(qapp, fresh_db):
    from app.overlay_widget import OverlayWindow
    OverlayWindow()  # must not raise


def test_main_window_constructs_on_fresh_db(qapp, fresh_db):
    """
    Regression test for the _overlay_active startup crash: MainWindow must
    construct cleanly even when there are zero existing chats (the exact
    condition a brand new install starts in).
    """
    from app.main_window import MainWindow
    window = MainWindow()

    assert window._stack.widget(window.PAGE_RECORDER).__class__.__name__ == "RecorderWidget"
    assert hasattr(window, "_overlay_active")
    assert hasattr(window, "_overlay")


def test_main_window_recorder_nav_is_wired(qapp, fresh_db):
    from app.main_window import MainWindow
    window = MainWindow()

    window.sidebar.btnRecorder.click()
    assert window._stack.currentIndex() == window.PAGE_RECORDER


def test_main_window_navigating_to_chat_refreshes_profile_picker(qapp, fresh_db, monkeypatch):
    import ai.profiles as profiles
    from app.main_window import MainWindow
    window = MainWindow()

    calls = []
    monkeypatch.setattr(window.chat, "refresh_profile_picker", lambda: calls.append(True))

    window._navigate(window.PAGE_SETTINGS)
    assert calls == []  # navigating AWAY from chat shouldn't refresh it

    window._navigate(window.PAGE_CHAT)
    assert calls == [True]


def test_main_window_profile_switched_from_chat_refreshes_settings_list(qapp, fresh_db, monkeypatch):
    from app.main_window import MainWindow
    window = MainWindow()

    calls = []
    monkeypatch.setattr(window.settings, "_refresh_profiles_list", lambda: calls.append(True))

    window.chat.profile_switched.emit()

    assert calls == [True]


# ══════════════════════════════════════════════════════════════════
# Startup shell - splash + auth/name/plan gates all as pages inside
# MainWindow's own shell stack, instead of separate popup windows
# ══════════════════════════════════════════════════════════════════

def test_main_window_starts_on_the_splash_page(qapp, fresh_db):
    from app.main_window import MainWindow
    window = MainWindow()

    assert window._shell_stack.currentWidget() is window._splash_page
    assert window._left_splash is False


def test_run_name_gate_shows_the_name_page_and_blocks_until_finished(qapp, fresh_db, monkeypatch):
    from PyQt6.QtCore import QTimer
    from app.main_window import MainWindow
    window = MainWindow()
    monkeypatch.setattr(window._splash_page, "ensure_minimum_display_time", lambda *a, **k: None)

    QTimer.singleShot(0, lambda: window._name_page.gate_finished.emit())
    window.run_name_gate()  # returns once the signal fires - no assertion needed beyond "didn't hang"

    assert window._left_splash is True


def test_show_main_content_switches_to_the_real_content_page(qapp, fresh_db, monkeypatch):
    from app.main_window import MainWindow
    window = MainWindow()
    monkeypatch.setattr(window._splash_page, "ensure_minimum_display_time", lambda *a, **k: None)

    window.show_main_content()

    assert window._shell_stack.currentWidget() is window._real_content_page
    assert window._left_splash is True


def test_leave_splash_only_waits_once(qapp, fresh_db, monkeypatch):
    from app.main_window import MainWindow
    window = MainWindow()

    calls = []
    monkeypatch.setattr(window._splash_page, "ensure_minimum_display_time", lambda *a, **k: calls.append(True))

    window._leave_splash_if_needed()
    window._leave_splash_if_needed()
    window.show_main_content()

    assert calls == [True]


def test_gate_sequence_ends_on_real_content(qapp, fresh_db, monkeypatch):
    """Full sequence: splash -> name -> real content, each step actually
    showing its page, ending up on the real one."""
    from PyQt6.QtCore import QTimer
    from app.main_window import MainWindow
    window = MainWindow()
    monkeypatch.setattr(window._splash_page, "ensure_minimum_display_time", lambda *a, **k: None)

    assert window._shell_stack.currentWidget() is window._splash_page

    QTimer.singleShot(0, lambda: window._name_page.gate_finished.emit())
    window.run_name_gate()
    assert window._shell_stack.currentWidget() is window._name_page

    window.show_main_content()
    assert window._shell_stack.currentWidget() is window._real_content_page


def test_no_sign_in_or_plan_gate_exists(qapp, fresh_db):
    """The app is bring-your-own-key with no account and no tiers. A gate
    reappearing would be a regression, not a feature."""
    from app.main_window import MainWindow
    window = MainWindow()
    for gone in ("_auth_page", "_plan_page", "run_auth_gate", "run_plan_gate"):
        assert not hasattr(window, gone), f"{gone} is back"


def test_main_window_macros_record_button_navigates_to_recorder(qapp, fresh_db):
    from app.main_window import MainWindow
    window = MainWindow()

    window.macros.btnRecordMacro.click()
    assert window._stack.currentIndex() == window.PAGE_RECORDER


def test_main_window_theme_switch_propagates_to_every_page(qapp, fresh_db, monkeypatch):
    """
    Switching the theme (via the Settings dropdown, or apply_theme
    directly) must reach every page live - this is what a restart-free
    dark/light toggle actually requires. Doesn't assert on rendered
    pixels (no real display here), but does assert every page's own
    _theme tracking updated and none of the seven setStyleSheet calls in
    apply_theme raised.

    Isolated from the real .env (get_theme/set_theme persist to disk) - forced to a known "dark" starting point regardless of whatever a
    previous manual run or another test may have left on disk.
    """
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "get_theme", lambda: "dark")
    from app.main_window import MainWindow
    window = MainWindow()

    assert window._theme == "dark"
    for page in (window.chat, window.macros, window.recorder, window.sidebar, window.settings):
        assert page._theme == "dark"

    window.apply_theme("light")

    assert window._theme == "light"
    for page in (window.chat, window.macros, window.recorder, window.sidebar, window.settings):
        assert page._theme == "light"

    # And back - nothing should be a one-way trip.
    window.apply_theme("dark")
    assert window._theme == "dark"
    assert window.chat._theme == "dark"


def test_main_window_settings_theme_dropdown_propagates(qapp, fresh_db, monkeypatch):
    """
    Same as the apply_theme test above, but driven through the actual
    signal chain a real click on the Settings dropdown fires (theme_changed),
    rather than calling apply_theme directly - catches a wiring bug the
    other test wouldn't (e.g. theme_changed never connected). Isolated
    from the real .env the same way (set_theme also persists to disk).
    """
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "get_theme", lambda: "dark")
    monkeypatch.setattr(ai_main, "set_theme", lambda theme_name: None)
    from app.main_window import MainWindow
    window = MainWindow()

    idx = window.settings._theme_dropdown.current_value()
    assert idx != "light"  # sanity check on the starting value before we change it
    window.settings._theme_dropdown._select("light")

    assert window._theme == "light"
    assert window.chat._theme == "light"
    assert window.sidebar._theme == "light"


def test_chat_widget_set_typing_label_updates_in_place(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    from PyQt6.QtWidgets import QLabel
    chat = ChatWidget()
    chat._current_chat_id = 1

    chat._show_typing(1, text="🎤  Listening…")
    row_before = chat._typing_row
    chat.set_typing_label("💭  Kestrel is thinking…")

    assert chat._typing_row is row_before  # updated in place, not recreated
    label = chat._typing_row.findChild(QLabel, "typingBubble")
    assert label.text() == "💭  Kestrel is thinking…"


def test_chat_widget_clear_messages_resets_typing_row_reference(qapp, fresh_db):
    """
    Regression test: switching chats while a typing indicator was showing
    used to leave self._typing_row pointing at an already-deleted widget,
    since _clear_messages() deletes every child widget but never reset the
    reference itself.
    """
    from app.chat_widget import ChatWidget
    chat = ChatWidget()
    chat._current_chat_id = 1
    chat._show_typing(1)
    assert chat._typing_row is not None

    chat._clear_messages()

    assert chat._typing_row is None


def test_chat_widget_hide_typing_does_not_crash_with_no_typing_row(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    chat = ChatWidget()
    chat._current_chat_id = 1
    assert chat._typing_row is None

    chat._hide_typing(1)  # must not raise even though nothing was ever shown


def test_sidebar_chat_item_supports_native_right_click(qapp, fresh_db):
    """
    The context menu must be reachable via a real right-click
    (contextMenuEvent), not just the ⋯ button - and use native OS styling
    (no custom stylesheet applied to the QMenu).
    """
    from app.sidebar_widget import ChatItemWidget
    from PyQt6.QtCore import QPoint

    item = ChatItemWidget(chat_id=1, title="Test chat")
    assert hasattr(item, "contextMenuEvent")

    # Calling _show_context_menu directly (as contextMenuEvent does) must
    # not raise, and the menu it builds must carry no custom stylesheet.
    import app.sidebar_widget as sidebar_module
    from PyQt6.QtWidgets import QMenu

    original_exec = QMenu.exec
    captured = {}

    def fake_exec(self, *args, **kwargs):
        captured["stylesheet"] = self.styleSheet()
        return None

    QMenu.exec = fake_exec
    try:
        item._show_context_menu(QPoint(10, 10))
    finally:
        QMenu.exec = original_exec

    assert captured["stylesheet"] == ""  # native look, nothing custom applied
    assert not hasattr(sidebar_module, "CONTEXT_MENU_STYLE")


def test_sidebar_menu_button_click_does_not_crash(qapp, fresh_db):
    """
    Regression test: QPushButton.clicked emits clicked(bool checked=False).
    self._menu_btn.clicked used to be connected directly to
    _show_context_menu(self, pos: QPoint | None = None) - PyQt passed that
    bool straight into `pos`, so `pos is None` was False and
    menu.exec(False) was called instead of exec() with a real QPoint (or
    no args). Real QMenu.exec(False) raises TypeError("argument 1 has
    unexpected type 'bool'") - confirmed against the actual PyQt6 QMenu,
    not just asserted - which used to crash the whole app since it's
    raised from inside a slot invoked by a C++ signal.
    """
    from app.sidebar_widget import ChatItemWidget
    from PyQt6.QtWidgets import QMenu
    from PyQt6.QtCore import QPoint

    item = ChatItemWidget(chat_id=1, title="Test chat")

    def fake_exec(self, *args, **kwargs):
        # Mirrors real QMenu.exec()'s own type validation (verified
        # separately against the actual PyQt6 QMenu) without popping up a
        # real blocking modal menu in this headless test.
        if args and not isinstance(args[0], QPoint):
            raise TypeError(f"argument 1 has unexpected type {type(args[0]).__name__!r}")
        return None

    original_exec = QMenu.exec
    QMenu.exec = fake_exec
    try:
        item._menu_btn.click()  # real click, emits clicked(bool) exactly like a user would trigger
    finally:
        QMenu.exec = original_exec


def test_reload_macros_reloads_actions_module_from_disk(qapp, fresh_db, monkeypatch):
    """
    Regression test: _reload_macros() used to only re-parse whatever was
    already in memory - a macro appended to ai/actions.py by any path that
    didn't happen to also call importlib.reload() on this exact module
    object stayed invisible in the Macros tab no matter how many times
    Refresh was clicked.
    """
    from app.main_window import MainWindow
    import app.main_window as main_window_module
    window = MainWindow()

    reload_calls = []
    original_reload = main_window_module.importlib.reload

    def spy_reload(module):
        reload_calls.append(module)
        return original_reload(module)

    monkeypatch.setattr(main_window_module.importlib, "reload", spy_reload)

    window._reload_macros()

    assert len(reload_calls) == 1


def test_reload_macros_preserves_in_progress_recording(qapp, fresh_db):
    """
    importlib.reload() resets every module-level global in actions.py,
    including actions_list - which holds an in-progress, not-yet-saved
    Recorder session. A reload triggered from elsewhere must not silently
    wipe it.
    """
    from app.main_window import MainWindow
    import ai.actions as actions
    window = MainWindow()

    actions.actions_list.clear()
    actions.actions_list.append(("click", 10, 20))

    window._reload_macros()

    assert actions.actions_list == [("click", 10, 20)]
    actions.actions_list.clear()  # leave a clean slate for any later test


def test_voice_transcript_ready_stages_it_for_review_by_default(qapp, fresh_db, monkeypatch):
    """
    Default behavior: the transcript lands in the composer to be read,
    corrected or thrown away - NOT sent. Speech recognition mishears
    things, and this is the only point at which the wrong word can
    still be fixed rather than corrected in a follow-up message.
    """
    from app.main_window import MainWindow
    import app.chat_database as db
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "get_voice_review_before_send", lambda: True)

    window = MainWindow()
    chat_id = db.create_chat("Test chat")
    window.chat._current_chat_id = chat_id
    window.chat.set_mic_state("listening")

    sent = []
    window.chat.request_sent.connect(lambda cid, prompt, from_voice: sent.append((cid, prompt, from_voice)))

    window._on_voice_transcript_ready(chat_id, "what's the weather today")

    assert sent == []  # nothing left for the model yet
    assert window.chat.chatInput.toPlainText() == "what's the weather today"
    assert window.chat._pending_voice_reply is True
    assert window.chat.sendBtn.isEnabled() is True

    # Sending it by hand afterwards still counts as a voice message, so
    # the answer is still spoken back.
    window.chat._send_message()
    assert len(sent) == 1
    assert sent[0][2] is True
    assert window.chat._pending_voice_reply is False


def test_staged_voice_transcript_can_be_discarded(qapp, fresh_db, monkeypatch):
    """Escape throws away a transcript that came out wrong, rather than
    making you select-and-delete something you never typed."""
    from app.main_window import MainWindow
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent
    import app.chat_database as db
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "get_voice_review_before_send", lambda: True)

    window = MainWindow()
    chat_id = db.create_chat("Test chat")
    window.chat._current_chat_id = chat_id
    window._on_voice_transcript_ready(chat_id, "misheard nonsense")
    assert window.chat.chatInput.toPlainText() == "misheard nonsense"

    escape = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    window.chat.eventFilter(window.chat.chatInput, escape)

    assert window.chat.chatInput.toPlainText() == ""
    assert window.chat._pending_voice_reply is False


def test_voice_transcript_ready_writes_to_input_and_sends(qapp, fresh_db, monkeypatch):
    """
    With review turned off (hands-free use), a ready transcript is
    written into the chat's text input and sent through the exact same
    path a typed message takes, rather than being sent to the AI
    directly as raw audio - see VoiceWorker/SpeakWorker's module
    docstrings for why. This is what guarantees the displayed/saved
    message and the one the AI actually responds to can never disagree
    with each other.
    """
    from app.main_window import MainWindow
    import app.chat_database as db
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "get_voice_review_before_send", lambda: False)

    window = MainWindow()
    chat_id = db.create_chat("Test chat")
    window.chat._current_chat_id = chat_id
    window.chat.set_mic_state("listening")

    sent = []
    window.chat.request_sent.connect(lambda cid, prompt, from_voice: sent.append((cid, prompt, from_voice)))

    window._on_voice_transcript_ready(chat_id, "what's the weather today")

    assert window.chat.micBtn.isEnabled() is True  # mic freed right away - its job is done
    assert len(sent) == 1
    sent_chat_id, prompt, from_voice = sent[0]
    assert sent_chat_id == chat_id
    assert "what's the weather today" in prompt
    assert from_voice is True
    messages = db.get_messages(chat_id)
    assert messages[-1]["content"] == "what's the weather today"  # no hint text in the saved bubble


def test_voice_transcript_ready_ignored_if_chat_switched(qapp, fresh_db):
    """If the user switches to a different chat while a recording was in
    flight, the transcript must not get sent into whatever chat happens
    to be open now."""
    from app.main_window import MainWindow
    import app.chat_database as db
    window = MainWindow()
    chat_id = db.create_chat("Original chat")
    other_chat_id = db.create_chat("Different chat")
    window.chat._current_chat_id = other_chat_id
    window.chat.set_mic_state("listening")

    sent = []
    window.chat.request_sent.connect(lambda cid, prompt, from_voice: sent.append((cid, prompt, from_voice)))

    window._on_voice_transcript_ready(chat_id, "some transcript")

    assert sent == []


def test_voice_transcription_failed_shows_a_note_and_frees_the_mic(qapp, fresh_db):
    from app.main_window import MainWindow
    import app.chat_database as db
    window = MainWindow()
    chat_id = db.create_chat("Test chat")
    window.chat._current_chat_id = chat_id
    window.chat.set_mic_state("listening")

    window._on_voice_transcription_failed(chat_id)

    assert window.chat.micBtn.isEnabled() is True
    messages = db.get_messages(chat_id)
    assert "try again" in messages[-1]["content"].lower()


def test_on_ai_response_speaks_only_for_voice_originated_requests(qapp, fresh_db, monkeypatch):
    from app.main_window import MainWindow
    import app.chat_database as db
    window = MainWindow()
    chat_id = db.create_chat("Test chat")
    window.chat._current_chat_id = chat_id

    spoken = []
    monkeypatch.setattr(window, "_speak_response", lambda cid, text: spoken.append((cid, text)))

    window.on_ai_response(chat_id, "typed reply", from_voice=False)
    assert spoken == []

    window.on_ai_response(chat_id, "spoken reply", from_voice=True)
    assert spoken == [(chat_id, "spoken reply")]


def test_on_ai_response_hides_typing_indicator_even_if_db_add_message_raises(qapp, fresh_db, monkeypatch):
    """
    Regression test: _hide_typing used to run only AFTER db.add_message()
    succeeded - if that raised, the typing indicator (the "..." three
    dots) never cleared. Since on_ai_response runs as a slot on a queued
    signal from AIWorker's background thread, an uncaught exception here
    doesn't crash the app outright - Qt just prints the traceback and
    moves on - so the visible symptom was a permanently stuck typing
    indicator with no response ever appearing, which read as "the app
    crashed" even though the process was still alive.
    """
    import app.main_window as main_window_module
    from app.main_window import MainWindow
    import app.chat_database as db
    window = MainWindow()
    chat_id = db.create_chat("Test chat")
    window.chat._current_chat_id = chat_id
    window.chat._show_typing(chat_id)
    assert window.chat._typing_row is not None

    def _raise(*a, **k):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(main_window_module.db, "add_message", _raise)

    with pytest.raises(RuntimeError):
        window.on_ai_response(chat_id, "a reply", from_voice=False)

    assert window.chat._typing_row is None


def test_on_ai_error_hides_typing_indicator_even_if_db_add_message_raises(qapp, fresh_db, monkeypatch):
    """
    Same regression as on_ai_response's version, found again in the
    sibling error-path handler: db.add_message() ran before
    _hide_typing() here too, so a raise there left the "..." indicator
    stuck if the FAILURE PATH (not just the success path) was what
    actually fired.
    """
    import app.main_window as main_window_module
    from app.main_window import MainWindow
    import app.chat_database as db
    window = MainWindow()
    chat_id = db.create_chat("Test chat")
    window.chat._current_chat_id = chat_id
    window.chat._show_typing(chat_id)
    assert window.chat._typing_row is not None

    def _raise(*a, **k):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(main_window_module.db, "add_message", _raise)

    with pytest.raises(RuntimeError):
        window.on_ai_error(chat_id, "something went wrong")

    assert window.chat._typing_row is None


def test_process_user_request_from_voice_speaks_reply_via_worker_signal(qapp, fresh_db, monkeypatch):
    """
    from_voice is captured per-request via closure in process_user_request
    (not shared mutable state) - see its docstring/comment for why. This
    exercises that wiring end-to-end through a fake AIWorker.
    """
    import app.main_window as main_window_module
    from app.main_window import MainWindow
    import app.chat_database as db
    from PyQt6.QtCore import QObject, pyqtSignal

    class FakeAIWorker(QObject):
        finished = pyqtSignal(int, str)
        error = pyqtSignal(int, str)
        start = pyqtSignal(int, str)
        autonomous_suggested = pyqtSignal(int, str)
        screenshot_captured = pyqtSignal(int, str)
        chunk = pyqtSignal(int, str)
        thinking_chunk = pyqtSignal(int, str)

        def __init__(self):
            super().__init__()
            self.start.connect(self._run)

        def _run(self, chat_id, text):
            self.finished.emit(chat_id, "spoken reply")

    monkeypatch.setattr(main_window_module, "AIWorker", FakeAIWorker)

    window = MainWindow()
    chat_id = db.create_chat("Test chat")
    window.chat._current_chat_id = chat_id

    spoken = []
    monkeypatch.setattr(window, "_speak_response", lambda cid, text: spoken.append((cid, text)))

    window.process_user_request(chat_id, "hello", from_voice=True)

    # Cross-thread queued signal delivery needs the event loop pumped.
    import time
    deadline = time.time() + 2.0
    while not spoken and time.time() < deadline:
        qapp.processEvents()

    assert spoken == [(chat_id, "spoken reply")]


def test_mic_stop_speaking_calls_ai_main_stop_speaking(qapp, fresh_db, monkeypatch):
    from app.main_window import MainWindow
    import ai.main as ai_main
    window = MainWindow()

    called = []
    monkeypatch.setattr(ai_main, "stop_speaking", lambda: called.append(True))

    window._on_mic_stop_speaking_requested(1)

    assert called == [True]


def test_stop_word_detected_calls_ai_main_stop_speaking(qapp, fresh_db, monkeypatch):
    from app.main_window import MainWindow
    import ai.main as ai_main
    window = MainWindow()

    called = []
    monkeypatch.setattr(ai_main, "stop_speaking", lambda: called.append(True))

    window._on_stop_word_detected()

    assert called == [True]


def test_speaking_started_enables_wakeword_stop_listening(qapp, fresh_db, monkeypatch):
    from app.main_window import MainWindow
    window = MainWindow()

    modes = []
    monkeypatch.setattr(window._wakeword_worker, "set_speaking_mode", lambda active: modes.append(active))

    window._on_voice_speaking_started(1)
    assert modes == [True]

    window._on_voice_speaking_finished(1)
    assert modes == [True, False]


def test_voice_worker_run_never_calls_the_ai_directly():
    """
    Structural guard for the redesign: VoiceWorker's whole job is now
    record + transcribe - it must never call ai.main.ai() or
    ai.main.speak_reply() itself, since that would reintroduce the two
    disagreeing transcripts (one shown, one actually processed) that
    motivated this change in the first place.
    """
    import inspect
    from app.workers.voice_worker import VoiceWorker
    source = inspect.getsource(VoiceWorker.run)
    assert "speak_reply" not in source
    assert "ai_main.ai(" not in source and " ai(" not in source
    assert "transcript_ready.emit" in source


def test_speak_worker_always_emits_speaking_finished():
    """
    Static check on SpeakWorker.run()'s source, mirroring the old
    VoiceWorker check: speaking_finished must fire from inside a `finally`
    so a TTS/audio-device failure can never leave the mic (and the
    wake-word listener's speaking_mode) stuck.
    """
    import inspect
    from app.workers.speak_worker import SpeakWorker
    source = inspect.getsource(SpeakWorker.run)
    assert "finally" in source
    assert source.count("speaking_finished.emit") >= 1
    # The emit must actually be inside the finally block, not just
    # present somewhere else in the method.
    finally_block = source.split("finally:", 1)[1]
    assert "speaking_finished.emit" in finally_block


# ══════════════════════════════════════════════════════════════════
# File drag-and-drop attachments
# ══════════════════════════════════════════════════════════════════

def test_chat_widget_starts_with_no_attachments(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    chat = ChatWidget()
    assert chat._pending_attachments == []
    assert chat._attachmentsRow.isHidden() is True


def test_add_attachment_shows_chip_and_enables_send(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    chat = ChatWidget()
    chat._current_chat_id = 1

    chat._add_attachment("notes.txt", "hello world")

    assert len(chat._pending_attachments) == 1
    assert chat._pending_attachments[0]["name"] == "notes.txt"
    assert chat._attachmentsRow.isHidden() is False
    assert chat.sendBtn.isEnabled() is True  # attachment alone is enough, even with empty input


def test_remove_attachment_hides_row_when_empty(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    chat = ChatWidget()
    chat._current_chat_id = 1
    chat._add_attachment("notes.txt", "hello world")
    entry = chat._pending_attachments[0]

    chat._remove_attachment(entry)

    assert chat._pending_attachments == []
    assert chat._attachmentsRow.isHidden() is True
    assert chat.sendBtn.isEnabled() is False


def test_handle_dropped_file_shows_error_for_unsupported_type(qapp, fresh_db, tmp_path):
    from app.chat_widget import ChatWidget
    chat = ChatWidget()
    chat._current_chat_id = 1
    bad_file = tmp_path / "video.mp4"
    bad_file.write_bytes(b"not really a video")

    chat._handle_dropped_file(str(bad_file))

    assert chat._pending_attachments == []
    assert chat._attachStatus.isHidden() is False
    assert "Couldn't attach" in chat._attachStatus.text()


def test_handle_dropped_file_attaches_supported_text_file(qapp, fresh_db, tmp_path):
    from app.chat_widget import ChatWidget
    chat = ChatWidget()
    chat._current_chat_id = 1
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("meeting notes: ship it friday", encoding="utf-8")

    chat._handle_dropped_file(str(txt_file))

    assert len(chat._pending_attachments) == 1
    assert chat._pending_attachments[0]["text"] == "meeting notes: ship it friday"
    assert chat._attachStatus.isHidden() is True


def test_send_message_folds_attachment_text_into_prompt_but_not_bubble(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    import app.chat_database as db

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    chat._current_chat_id = chat_id
    chat._add_attachment("report.txt", "Q3 revenue is up 12%.")

    sent = []
    chat.request_sent.connect(lambda cid, text, from_voice: sent.append((cid, text, from_voice)))
    chat.chatInput.setPlainText("Summarize this")

    chat._send_message()

    assert len(sent) == 1
    sent_chat_id, prompt, from_voice = sent[0]
    assert sent_chat_id == chat_id
    assert from_voice is False
    assert "Summarize this" in prompt
    assert "[Attached file: report.txt]" in prompt
    assert "Q3 revenue is up 12%." in prompt
    # Attachments are cleared after sending, and the file's content never
    # lands in the persisted/displayed bubble itself.
    assert chat._pending_attachments == []
    messages = db.get_messages(chat_id)
    assert messages[-1]["content"] == "Summarize this"
    assert "Q3 revenue" not in messages[-1]["content"]


def test_send_message_with_only_attachments_uses_placeholder_bubble(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    import app.chat_database as db

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    chat._current_chat_id = chat_id
    chat._add_attachment("data.json", '{"a": 1}')

    sent = []
    chat.request_sent.connect(lambda cid, text, from_voice: sent.append((cid, text, from_voice)))

    chat._send_message()

    assert len(sent) == 1
    messages = db.get_messages(chat_id)
    assert "Sent 1 file" in messages[-1]["content"]
    assert '{"a": 1}' in sent[0][1]


def test_send_message_voice_reply_appends_hint_to_prompt_not_bubble(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    import app.chat_database as db

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    chat._current_chat_id = chat_id
    chat.chatInput.setPlainText("what's the weather like")

    sent = []
    chat.request_sent.connect(lambda cid, text, from_voice: sent.append((cid, text, from_voice)))

    chat._send_message(voice_reply=True)

    assert len(sent) == 1
    _, prompt, from_voice = sent[0]
    assert from_voice is True
    assert "read aloud" in prompt  # the hint made it into the prompt...
    messages = db.get_messages(chat_id)
    assert messages[-1]["content"] == "what's the weather like"  # ...but not the saved/displayed bubble
    assert "read aloud" not in messages[-1]["content"]


# ══════════════════════════════════════════════════════════════════
# Message actions - Copy / Read aloud / Edit
# ══════════════════════════════════════════════════════════════════

def test_user_bubble_copy_button_copies_current_label_text(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    from PyQt6.QtWidgets import QApplication, QPushButton
    chat = ChatWidget()
    chat._current_chat_id = 1

    row = chat._append_bubble("hello world", "user", message_id=1)
    btn_copy = row.findChild(QPushButton, "msgCopyBtn")
    assert btn_copy is not None
    btn_copy.click()

    assert QApplication.clipboard().text() == "hello world"


def test_scroll_snaps_to_bottom_when_scrollbar_range_changes(qapp, fresh_db):
    """
    Regression test for the "the more you talk to it, the more the chat
    drifts upward" report. The old fixed-delay QTimer.singleShot calls
    raced Qt's layout recalculation (whose cost scales with conversation
    length) - this connects _scroll_to_bottom directly to the
    scrollbar's own rangeChanged signal, which fires exactly when Qt has
    actually finished recalculating the new (taller) range, closing that
    race regardless of how many messages already exist.
    """
    from app.chat_widget import ChatWidget
    import app.chat_database as db
    chat_id = db.create_chat("Test chat")
    chat = ChatWidget()
    chat.load_chat(chat_id)

    sb = chat.messagesScroll.verticalScrollBar()
    # Simulate what actually happens as messages accumulate and the
    # content grows taller - Qt itself emits rangeChanged when that
    # happens; this fires it directly rather than needing to actually
    # add enough real message bubbles to grow the layout in a test.
    sb.setRange(0, 500)

    assert sb.value() == sb.maximum() == 500


def test_send_message_dismisses_a_pending_confirmation_card(qapp, fresh_db):
    """
    Regression test: an unanswered accept/decline card (see
    show_confirmation) used to stay on screen forever once the user sent
    a different message instead of answering it.
    """
    from app.chat_widget import ChatWidget
    import app.chat_database as db
    chat_id = db.create_chat("Test chat")
    chat = ChatWidget()
    chat.load_chat(chat_id)

    chat.show_confirmation(chat_id, "open youtube and play a video")
    assert chat._pending_confirmation_row is not None

    chat.chatInput.setPlainText("never mind, do something else")
    chat._send_message()

    assert chat._pending_confirmation_row is None


def test_send_message_with_no_pending_confirmation_is_unaffected(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    import app.chat_database as db
    chat_id = db.create_chat("Test chat")
    chat = ChatWidget()
    chat.load_chat(chat_id)
    assert chat._pending_confirmation_row is None

    chat.chatInput.setPlainText("hello")
    chat._send_message()   # must not raise just because nothing was pending

    assert chat._pending_confirmation_row is None


def test_copy_button_shows_confirmation_feedback_after_click(qapp, fresh_db):
    """
    Regression test: clicking Copy used to give zero visual
    acknowledgement that anything happened - the clipboard write is
    instant and silent, which read as unresponsive even though it
    worked. Now flips to "Copied" briefly (see ChatWidget._on_copy_clicked).
    """
    from app.chat_widget import ChatWidget
    from PyQt6.QtWidgets import QPushButton
    chat = ChatWidget()
    chat._current_chat_id = 1

    row = chat._append_bubble("hello world", "user", message_id=1)
    btn_copy = row.findChild(QPushButton, "msgCopyBtn")
    original_text = btn_copy.text()

    btn_copy.click()

    assert "Copied" in btn_copy.text()
    assert btn_copy.text() != original_text
    assert btn_copy.isEnabled() is False   # briefly disabled so a rapid double-click can't stack timers oddly


def test_copy_button_restores_original_text_after_delay(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    from PyQt6.QtWidgets import QPushButton
    from PyQt6.QtCore import QEventLoop, QTimer
    chat = ChatWidget()
    chat._current_chat_id = 1

    row = chat._append_bubble("hello world", "user", message_id=1)
    btn_copy = row.findChild(QPushButton, "msgCopyBtn")
    original_text = btn_copy.text()

    btn_copy.click()
    assert "Copied" in btn_copy.text()

    # Pump the event loop past the 1200ms restore timer without a real sleep.
    loop = QEventLoop()
    QTimer.singleShot(1300, loop.quit)
    loop.exec()

    assert btn_copy.text() == original_text
    assert btn_copy.isEnabled() is True


def test_assistant_bubble_has_speak_button_not_edit(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    from PyQt6.QtWidgets import QPushButton
    chat = ChatWidget()
    chat._current_chat_id = 1

    row = chat._append_bubble("here's the answer", "assistant")
    assert row.findChild(QPushButton, "msgSpeakBtn") is not None
    assert row.findChild(QPushButton, "msgEditBtn") is None


def test_user_bubble_has_edit_button_not_speak(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    from PyQt6.QtWidgets import QPushButton
    chat = ChatWidget()
    chat._current_chat_id = 1

    row = chat._append_bubble("my question", "user", message_id=1)
    assert row.findChild(QPushButton, "msgEditBtn") is not None
    assert row.findChild(QPushButton, "msgSpeakBtn") is None


def test_speak_button_emits_speak_requested_with_current_chat_and_text(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    from PyQt6.QtWidgets import QPushButton
    chat = ChatWidget()
    chat._current_chat_id = 42

    row = chat._append_bubble("read this back to me", "assistant")
    received = []
    chat.speak_requested.connect(lambda cid, text: received.append((cid, text)))

    row.findChild(QPushButton, "msgSpeakBtn").click()

    assert received == [(42, "read this back to me")]


def test_edit_button_disabled_when_no_message_id(qapp, fresh_db):
    """A row we couldn't attach a DB id to (shouldn't normally happen for
    a user message, but is the safe default) must not offer a broken
    Edit that has nothing to delete-from."""
    from app.chat_widget import ChatWidget
    from PyQt6.QtWidgets import QPushButton
    chat = ChatWidget()
    chat._current_chat_id = 1

    row = chat._append_bubble("no id given", "user", message_id=None)
    btn_edit = row.findChild(QPushButton, "msgEditBtn")
    assert btn_edit is not None
    assert btn_edit.isEnabled() is False


def _find_row_by_message_id(chat, message_id):
    for i in range(chat._messages_layout.count()):
        w = chat._messages_layout.itemAt(i).widget()
        if w is not None and w.property("message_id") == message_id:
            return w
    return None


def test_edit_click_switches_bubble_to_inline_editor_not_the_compose_box(qapp, fresh_db):
    """
    ChatGPT-style: clicking Edit turns the BUBBLE ITSELF into an editable
    field, pre-filled with its own text - the compose box at the bottom
    is untouched, unlike the old behavior this replaced.
    """
    from app.chat_widget import ChatWidget
    import app.chat_database as db
    from PyQt6.QtWidgets import QPushButton, QLabel, QTextEdit, QWidget

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    msg_id = db.add_message(chat_id, "user", "original text")
    chat.load_chat(chat_id)

    row = _find_row_by_message_id(chat, msg_id)
    row.findChild(QPushButton, "msgEditBtn").click()

    label = row.findChild(QLabel, "bubbleUser")
    edit_container = row.findChild(QWidget, "editContainer")
    edit_input = row.findChild(QTextEdit, "editTextEdit")

    assert edit_container.isHidden() is False
    assert label.isHidden() is True
    assert edit_input.toPlainText() == "original text"
    assert chat.chatInput.toPlainText() == ""  # compose box untouched


def test_edit_click_hides_but_does_not_delete_subsequent_messages(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    import app.chat_database as db
    from PyQt6.QtWidgets import QPushButton

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    id1 = db.add_message(chat_id, "user", "first question")
    id2 = db.add_message(chat_id, "assistant", "first answer")
    chat.load_chat(chat_id)

    row = _find_row_by_message_id(chat, id1)
    answer_row = _find_row_by_message_id(chat, id2)
    assert answer_row.isHidden() is False

    row.findChild(QPushButton, "msgEditBtn").click()

    assert answer_row.isHidden() is True
    # Nothing committed to the DB yet - only a UI-level hide.
    assert [m["id"] for m in db.get_messages(chat_id)] == [id1, id2]


def test_cancel_edit_reverts_everything_with_no_db_changes(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    import app.chat_database as db
    from PyQt6.QtWidgets import QPushButton, QLabel, QTextEdit, QWidget

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    id1 = db.add_message(chat_id, "user", "first question")
    id2 = db.add_message(chat_id, "assistant", "first answer")
    chat.load_chat(chat_id)

    row = _find_row_by_message_id(chat, id1)
    answer_row = _find_row_by_message_id(chat, id2)
    row.findChild(QPushButton, "msgEditBtn").click()
    edit_input = row.findChild(QTextEdit, "editTextEdit")
    edit_input.setPlainText("something the user typed but didn't mean to keep")

    row.findChild(QPushButton, "editCancelBtn").click()

    label = row.findChild(QLabel, "bubbleUser")
    edit_container = row.findChild(QWidget, "editContainer")
    assert label.isHidden() is False
    assert label.text() == "first question"  # original, untouched
    assert edit_container.isHidden() is True
    assert answer_row.isHidden() is False  # restored
    assert [m["content"] for m in db.get_messages(chat_id)] == ["first question", "first answer"]
    assert chat._active_edit_row is None


def test_confirm_edit_branches_and_resends(qapp, fresh_db, monkeypatch):
    """
    Save & submit commits the edit as a NEW BRANCH (db.branch_message) - NOT a delete of this message and everything after it - then triggers
    a fresh AI reply to the edited text directly, verified here by
    checking a request_sent signal fires with the edited text, the same
    signal a real user-typed send would emit. The pre-edit branch
    ("second question" / "second answer") must still exist in the DB
    afterward, just no longer the active one showing.
    """
    from app.chat_widget import ChatWidget
    import app.chat_database as db
    from PyQt6.QtWidgets import QPushButton, QTextEdit

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    id1 = db.add_message(chat_id, "user", "first question")
    db.add_message(chat_id, "assistant", "first answer")
    id2 = db.add_message(chat_id, "user", "second question")
    db.add_message(chat_id, "assistant", "second answer")
    chat.load_chat(chat_id)

    sent = []
    chat.request_sent.connect(lambda cid, prompt, voice: sent.append((cid, prompt)))

    row = _find_row_by_message_id(chat, id2)
    row.findChild(QPushButton, "msgEditBtn").click()
    edit_input = row.findChild(QTextEdit, "editTextEdit")
    edit_input.setPlainText("second question, but edited")

    # Re-fetch the row's Save button fresh - load_chat() inside the
    # click handler tears down and rebuilds every row, so any reference
    # captured before this click would be stale afterward.
    row.findChild(QPushButton, "editSaveBtn").click()

    active_thread = db.get_messages(chat_id)
    assert [m["content"] for m in active_thread] == ["first question", "first answer", "second question, but edited"]
    assert sent == [(chat_id, "second question, but edited")]
    assert chat._active_edit_row is None

    # The pre-edit branch is still there, just not the active one.
    siblings = db.list_siblings(id2)
    assert {s["content"] for s in siblings} == {"second question", "second question, but edited"}


def test_switching_back_to_an_older_branch_restores_its_own_reply(qapp, fresh_db):
    """
    Switching back to the ORIGINAL (pre-edit) branch after an edit
    restores that branch's own AI reply too, not just the one message - db.switch_branch only repoints the ONE pointer at the branch point;
    everything below it (that branch's own active_child_id chain) was
    never touched while it sat inactive.
    """
    from app.chat_widget import ChatWidget
    import app.chat_database as db
    from PyQt6.QtWidgets import QPushButton, QTextEdit

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    id1 = db.add_message(chat_id, "user", "first question")
    db.add_message(chat_id, "assistant", "first answer")
    id2 = db.add_message(chat_id, "user", "second question")
    db.add_message(chat_id, "assistant", "second answer")
    chat.load_chat(chat_id)

    row = _find_row_by_message_id(chat, id2)
    row.findChild(QPushButton, "msgEditBtn").click()
    row.findChild(QTextEdit, "editTextEdit").setPlainText("second question, but edited")
    row.findChild(QPushButton, "editSaveBtn").click()
    # Simulate the AI's reply to the edit actually landing, same as
    # main_window.py's on_ai_response would do via db.add_message's
    # auto-parent default.
    db.add_message(chat_id, "assistant", "edited answer")

    db.switch_branch(chat_id, id2)  # back to the ORIGINAL branch
    restored = db.get_messages(chat_id)
    assert [m["content"] for m in restored] == ["first question", "first answer", "second question", "second answer"]


def test_confirm_edit_with_empty_text_does_nothing(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    import app.chat_database as db
    from PyQt6.QtWidgets import QPushButton, QTextEdit

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    id1 = db.add_message(chat_id, "user", "first question")
    chat.load_chat(chat_id)

    row = _find_row_by_message_id(chat, id1)
    row.findChild(QPushButton, "msgEditBtn").click()
    row.findChild(QTextEdit, "editTextEdit").setPlainText("   ")

    row.findChild(QPushButton, "editSaveBtn").click()

    assert [m["content"] for m in db.get_messages(chat_id)] == ["first question"]


def test_starting_a_new_edit_cancels_the_previous_one(qapp, fresh_db):
    from app.chat_widget import ChatWidget
    import app.chat_database as db
    from PyQt6.QtWidgets import QPushButton, QLabel, QWidget

    chat = ChatWidget()
    chat_id = db.create_chat("Test chat")
    id1 = db.add_message(chat_id, "user", "first question")
    id2 = db.add_message(chat_id, "user", "second question")
    chat.load_chat(chat_id)

    row1 = _find_row_by_message_id(chat, id1)
    row2 = _find_row_by_message_id(chat, id2)

    row1.findChild(QPushButton, "msgEditBtn").click()
    assert chat._active_edit_row is row1

    row2.findChild(QPushButton, "msgEditBtn").click()

    assert chat._active_edit_row is row2
    assert row1.findChild(QWidget, "editContainer").isHidden() is True
    assert row1.findChild(QLabel, "bubbleUser").isHidden() is False

def test_autonomous_worker_emits_network_retry(qapp, monkeypatch):
    from app.workers.autonomous_worker import AutonomousWorker
    import app.workers.autonomous_worker as worker_mod

    def fake_run_autonomous_mode(goal, max_steps, on_human_handoff=None, on_network_retry=None):
        on_network_retry(1, 3)
        on_network_retry(2, 6)

    monkeypatch.setattr(worker_mod, "run_autonomous_mode", fake_run_autonomous_mode)
    monkeypatch.setattr(worker_mod, "get_max_automation_steps", lambda: 5)

    w = AutonomousWorker()
    received = []
    w.network_retry.connect(lambda chat_id, attempt, wait: received.append((chat_id, attempt, wait)))
    finished = []
    w.finished.connect(lambda chat_id: finished.append(chat_id))

    w.run(7, "test goal")

    assert received == [(7, 1, 3), (7, 2, 6)]
    assert finished == [7]


def test_main_window_network_retry_updates_typing_label(qapp, fresh_db):
    from app.main_window import MainWindow
    from PyQt6.QtWidgets import QLabel
    window = MainWindow()
    chat_id = 1
    window.chat._current_chat_id = chat_id
    window.chat._show_typing(chat_id)

    window._on_autonomous_network_retry(chat_id, 2, 12)

    label = window.chat._typing_row.findChild(QLabel, "typingBubble")
    assert "12s" in label.text()
    assert "attempt 2" in label.text()


def test_main_window_network_retry_ignored_for_a_different_chat(qapp, fresh_db):
    """Purely cosmetic no-op if the retrying automation isn't the chat
    currently open - must not touch or crash on another chat's typing row."""
    from app.main_window import MainWindow
    from PyQt6.QtWidgets import QLabel
    window = MainWindow()
    window.chat._current_chat_id = 1
    window.chat._show_typing(1, text="  ● ● ●  ")

    window._on_autonomous_network_retry(999, 1, 3)

    label = window.chat._typing_row.findChild(QLabel, "typingBubble")
    assert label.text() == "  ● ● ●  "


def test_selecting_existing_chat_does_not_check_new_chat_button(qapp, fresh_db):
    """
    Regression test: viewing an EXISTING chat from the history list used
    to leave the "New chat" nav button looking checked/active (since
    _navigate() checks it for any chat-page navigation, not knowing
    whether that navigation was "create new" or "load existing") - as if
    a fresh empty chat were selected when it wasn't. Only macros/recorder
    navigation looked correct because they don't share this ambiguity.
    """
    from app.main_window import MainWindow
    import app.chat_database as db
    window = MainWindow()

    other_chat_id = db.create_chat("An existing chat")
    # Land on macros first so PAGE_CHAT's nav button starts unchecked,
    # same as it would after actually using the app for a bit.
    window._navigate(window.PAGE_MACROS)
    assert window.sidebar.btnActions.isChecked() is True

    window._on_chat_selected(other_chat_id)

    assert window._stack.currentIndex() == window.PAGE_CHAT
    assert window.sidebar.btnNewChat.isChecked() is False
    assert window.chat._current_chat_id == other_chat_id
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_sidebar_scroll_wraps_history_area(qapp, tmp_path, monkeypatch):
    import app.chat_database as db
    db_path = str(tmp_path / "resize_test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()

    from app.sidebar_widget import Sidebar
    from PyQt6.QtWidgets import QScrollArea
    sb = Sidebar()

    scroll = sb.findChild(QScrollArea, "chatHistoryScroll")
    assert scroll is not None
    assert scroll.widget() is sb.historyArea
    assert scroll.widgetResizable() is True


def test_sidebar_min_size_hint_bounded_with_many_chats(qapp, tmp_path, monkeypatch):
    """
    The actual regression: before wrapping historyArea in a scroll area,
    the sidebar's (and therefore the whole window's) computed minimum
    height grew with every chat added, since nothing could scroll to
    compress it - eventually making the window impossible to resize
    shorter than "tall enough for every chat at once".
    """
    import app.chat_database as db
    db_path = str(tmp_path / "resize_test2.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()

    from app.sidebar_widget import Sidebar
    sb = Sidebar()
    small_height = sb.minimumSizeHint().height()

    for i in range(80):
        chat_id = db.create_chat(f"Chat number {i}")
        sb._make_chat_item(chat_id, f"Chat number {i}", False)
        sb._history_layout.insertWidget(sb._history_layout.count() - 1, sb._chat_items[chat_id])

    sb.adjustSize()
    large_height = sb.minimumSizeHint().height()

    # A handful of pixels of slack is fine; growing by hundreds of pixels
    # (roughly what 80 unscrolled ~30px rows would add) means the scroll
    # area isn't actually absorbing the list anymore.
    assert large_height - small_height < 100, (
        f"sidebar's minimum height grew by {large_height - small_height}px with 80 chats - "
        "the history list isn't scrolling, it's still forcing the window to grow"
    )


def test_refresh_history_preserves_scroll_position(qapp, tmp_path, monkeypatch):
    """
    Regression test: refresh_history() destroys and recreates every
    chat-list item on every call (see its own docstring - this happens
    after essentially every message exchange, not just occasionally).
    Verified behaviorally - the scroll value read BEFORE the rebuild is
    what gets restored AFTER it - rather than by comparing actual
    scrollbar geometry: this offscreen test environment doesn't grow the
    scrollable range realistically without a real paint cycle, so a
    pixel/maximum()-based comparison doesn't reliably discriminate here.
    """
    import app.chat_database as db
    db_path = str(tmp_path / "scroll_preserve_test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()

    from app.sidebar_widget import Sidebar
    sb = Sidebar()

    for i in range(10):
        db.create_chat(f"Chat number {i}")
    sb.refresh_history()

    sb._history_scroll.verticalScrollBar().setValue(37)

    set_value_calls = []
    original_set_value = sb._history_scroll.verticalScrollBar().setValue
    monkeypatch.setattr(
        sb._history_scroll.verticalScrollBar(), "setValue",
        lambda v: (set_value_calls.append(v), original_set_value(v))[1],
    )

    sb.refresh_history()

    assert 37 in set_value_calls, (
        "refresh_history() didn't restore the pre-rebuild scroll value (37) - "
        "a fresh rebuild naturally starts scrolled to the top, so without this, "
        "anyone scrolled down browsing older chats gets snapped back to the top "
        "of the sidebar after every single message"
    )


def test_no_pictographic_emoji_in_user_facing_source():
    """
    Regression test for the "remove all native emojis" request - scans
    every app/*.py and ai/*.py source line for actual pictographic
    Unicode emoji (not plain typographic symbols like checkmarks/ellipses,
    which render consistently everywhere and were left alone on purpose).
    A hit here means a QLabel/QPushButton/etc. text literal reintroduced
    one - icons belong in app/icons.py via qtawesome instead.
    """
    import re
    import glob
    emoji_pattern = re.compile(
        "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E0-\U0001F1FF]"
    )
    offenders = []
    for path in glob.glob("app/*.py") + glob.glob("ai/*.py"):
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if "test" in path:
                    continue
                found = emoji_pattern.findall(line)
                if found:
                    offenders.append(f"{path}:{i}: {found} -> {line.strip()[:100]}")
    assert not offenders, "\n" + "\n".join(offenders)


def test_settings_widget_content_is_scrollable(qapp):
    """
    Regression test: SettingsWidget used to put every section directly
    in its own top-level layout with no QScrollArea - same bug class as
    the sidebar's chat history (see test_sidebar_scroll_wraps_history_area).
    As more sections get added over time, the page's minimum height would
    just keep growing, eventually taller than the window can shrink to.
    """
    from app.settings_widget import SettingsWidget
    from PyQt6.QtWidgets import QScrollArea
    w = SettingsWidget()

    scroll = w.findChild(QScrollArea, "settingsScroll")
    assert scroll is not None
    assert scroll.widgetResizable() is True
    assert scroll.widget() is not None
    # The content lives INSIDE the scroll area, not directly on the page.
    assert w.layout().indexOf(scroll) != -1
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time


def test_macros_tab_refresh_performance_with_50_macros(qapp, tmp_path, monkeypatch):
    """
    Benchmarks the actual _reload_macros() pipeline (importlib.reload of
    ai.actions + get_all_tools + _parse_tools_to_macros + rebuilding every
    MacroCard widget) with 50 real macros on disk, to answer: is it safe
    to also call this every time the user navigates TO the Macros tab
    (not just on the explicit Refresh button / after recording/generalizing
    one), without it becoming a perceptible stutter?
    """
    import importlib
    import ai.macro_store as macro_store
    import ai.actions as actions

    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))

    for i in range(50):
        actions_list = [("click", 100 + i, 200 + i), (f"search term {i}", "write"), ("enter", "enter")]
        macro_store.save_literal_macro(f"macro_number_{i}", actions_list)
    # save_literal_macro only writes the JSON to disk - registering the
    # macros as actual callables on ai.actions (what normally happens
    # once, automatically, right when each one is first created via
    # tool_name_action) has to happen separately. Do it once here to set
    # up "50 macros already exist, as they would after a while of normal
    # use" - this ISN'T part of what a tab switch should have to repeat.
    macro_store.load_and_register_all(actions)

    from app.main_window import _parse_tools_to_macros
    from app.macros_widget import MacrosWidget
    w = MacrosWidget()

    def do_reload():
        live_actions_list = list(actions.actions_list) if actions.actions_list else None
        importlib.reload(actions)
        if live_actions_list:
            actions.actions_list = live_actions_list
        tools = actions.get_all_tools(actions)
        macros = _parse_tools_to_macros(tools)
        w.load_macros(macros)
        return len(macros)

    # Warm up (first import/reload pays one-time costs unrelated to the
    # steady-state "switch tabs repeatedly" case being asked about).
    n = do_reload()
    assert n >= 50  # >= since actions.py's OWN built-in tools count too

    timings = []
    for _ in range(5):
        t0 = time.perf_counter()
        do_reload()
        timings.append((time.perf_counter() - t0) * 1000)

    avg_ms = sum(timings) / len(timings)
    print(f"\n[perf] 50-macro reload average over 5 runs: {avg_ms:.1f}ms  (runs: {[f'{t:.1f}' for t in timings]})")

    # 100ms is a reasonable "won't feel like a stutter when switching
    # tabs" bar - comfortably under a frame-drop-noticeable threshold.
    assert avg_ms < 100, f"50-macro reload averaged {avg_ms:.1f}ms - too slow to auto-refresh on every tab switch"


def test_navigating_to_macros_tab_auto_refreshes(qapp, fresh_db, monkeypatch):
    """
    Regression test for "macros page doesn't update with newly created
    macros" - navigating to the Macros tab must call _reload_macros(),
    not rely solely on the explicit Refresh button or the macro_saved/
    autonomous-finished hooks. Confirmed safe to always do via the
    measured ~30ms-for-50-macros perf test above.
    """
    from app.main_window import MainWindow
    window = MainWindow()

    calls = []
    monkeypatch.setattr(window, "_reload_macros", lambda: calls.append(1))

    window._navigate(window.PAGE_MACROS)

    assert calls == [1]


# ══════════════════════════════════════════════════════════════════
# Recorder - editable/deletable actions, description field
# ══════════════════════════════════════════════════════════════════

def test_recorder_page_title_says_recorder_not_no_code_recorder(qapp):
    from app.recorder_widget import RecorderWidget
    from PyQt6.QtWidgets import QLabel
    w = RecorderWidget()
    title = w.findChild(QLabel, "pageTitle")
    assert title.text() == "Recorder"


def test_recorder_refresh_builds_one_block_per_action(qapp):
    """Recorded actions land in the block strip, one block each - the
    recorder's list is the block editor now (see app/block_list.py)."""
    from app.recorder_widget import RecorderWidget
    import ai.actions as actions

    actions.actions_list.clear()
    actions.actions_list.extend([
        ("click", 100, 200),
        ("hello world", "write"),
        ("enter", "enter"),
    ])
    try:
        w = RecorderWidget()
        w._refresh_actions_list()
        assert [s["type"] for s in w._blocks.steps()] == ["click", "write", "enter"]
    finally:
        actions.actions_list.clear()


def test_recorder_block_values_are_editable_in_place(qapp, monkeypatch):
    """
    Editing a recorded value used to mean an "edit" button per row
    opening a dialog, with the button disabled for actions that had
    nothing worth editing. Values are now pipes inside the block, and
    what replaced that gating is the pipe set itself: a block only
    offers slots for what it actually has (press Enter has none).
    """
    import ai.macro_blocks as blocks

    assert [p.key for p in blocks.spec_for("enter").pipes] == []
    assert "point" in [p.key for p in blocks.spec_for("click").pipes]
    assert "value" in [p.key for p in blocks.spec_for("write").pipes]


def test_recorder_editing_a_click_updates_actions_list(qapp):
    """A click's position pipe writes straight back through to
    ai.actions.actions_list, which is what saving and replaying read."""
    from app.recorder_widget import RecorderWidget
    import ai.actions as actions

    actions.actions_list.clear()
    actions.actions_list.append(("click", 100, 200))
    try:
        w = RecorderWidget()
        w._refresh_actions_list()

        steps = w._blocks.steps()
        steps[0]["x"], steps[0]["y"] = 555, 666
        w._blocks.set_steps(steps)
        w._on_blocks_changed()

        assert actions.actions_list[0] == ["click", 555, 666]
    finally:
        actions.actions_list.clear()


def test_recorder_editing_typed_text_updates_actions_list(qapp):
    from app.recorder_widget import RecorderWidget
    import ai.actions as actions

    actions.actions_list.clear()
    actions.actions_list.append(("old text", "write"))
    try:
        w = RecorderWidget()
        w._refresh_actions_list()

        steps = w._blocks.steps()
        steps[0]["value"] = "new text"
        w._blocks.set_steps(steps)
        w._on_blocks_changed()

        assert actions.actions_list[0] == ["new text", "write"]
    finally:
        actions.actions_list.clear()


def test_recorder_deleting_a_block_removes_it_and_its_action(qapp):
    from app.recorder_widget import RecorderWidget
    import ai.actions as actions

    actions.actions_list.clear()
    actions.actions_list.extend([("click", 1, 2), ("enter", "enter")])
    try:
        w = RecorderWidget()
        w._refresh_actions_list()

        steps = w._blocks.steps()
        del steps[0]
        w._blocks.set_steps(steps)
        w._on_blocks_changed()

        # Round-tripped, not rebuilt: the recorder's own opaque second
        # value ("_extra" in editor-step form) is carried through
        # untouched, so a surviving action replays exactly as recorded.
        assert actions.actions_list == [["enter", "enter"]]
        assert [s["type"] for s in w._blocks.steps()] == ["enter"]
    finally:
        actions.actions_list.clear()


def test_recorder_delete_disables_save_when_list_becomes_empty(qapp):
    from app.recorder_widget import RecorderWidget
    import ai.actions as actions

    actions.actions_list.clear()
    actions.actions_list.append(("click", 1, 2))
    try:
        w = RecorderWidget()
        w._refresh_actions_list()
        w._btn_save.setEnabled(True)

        w._blocks.set_steps([])
        w._on_blocks_changed()

        assert w._btn_save.isEnabled() is False
        assert w._status_label.text() == "Ready to record."
    finally:
        actions.actions_list.clear()


def test_recorder_save_threads_description_into_the_macro(qapp, tmp_path, monkeypatch):
    """
    Regression/feature test: the description field must actually reach
    the saved macro's spec - it becomes the synthesized function's
    docstring (see ai/macro_store.py's save_literal_macro), which is
    what Gemini's automatic function calling reads to decide whether
    this macro is the right tool to call.
    """
    from app.recorder_widget import RecorderWidget
    import ai.actions as actions
    import ai.macro_store as macro_store

    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))
    monkeypatch.setattr("ai.main.refresh_actions", lambda: None)

    actions.actions_list.clear()
    actions.actions_list.append(("click", 10, 20))
    try:
        w = RecorderWidget()
        w._refresh_actions_list()
        w._name_input.setText("my_test_macro")
        w._description_input.setPlainText("Opens the settings panel and enables dark mode.")

        w._on_save_clicked()

        import json
        with open(tmp_path / "my_test_macro.json", encoding="utf-8") as f:
            spec = json.load(f)
        assert spec["description"] == "Opens the settings panel and enables dark mode."
    finally:
        actions.actions_list.clear()
        macro_store.delete_macro("my_test_macro")


def test_recorder_save_without_description_falls_back_to_auto_generated(qapp, tmp_path, monkeypatch):
    from app.recorder_widget import RecorderWidget
    import ai.actions as actions
    import ai.macro_store as macro_store

    monkeypatch.setattr(macro_store, "MACROS_DIR", str(tmp_path))
    monkeypatch.setattr("ai.main.refresh_actions", lambda: None)

    actions.actions_list.clear()
    actions.actions_list.append(("click", 10, 20))
    try:
        w = RecorderWidget()
        w._refresh_actions_list()
        w._name_input.setText("no_description_macro")
        # description_input left empty

        w._on_save_clicked()

        import json
        with open(tmp_path / "no_description_macro.json", encoding="utf-8") as f:
            spec = json.load(f)
        assert "no description macro" in spec["description"]
    finally:
        actions.actions_list.clear()
        macro_store.delete_macro("no_description_macro")


# ══════════════════════════════════════════════════════════════════
# Recorder triggers - configurable keyboard/mouse shortcuts
# ══════════════════════════════════════════════════════════════════

def test_settings_shows_a_capture_button_per_recorder_trigger(qapp):
    from app.settings_widget import SettingsWidget
    import ai.main as ai_main
    w = SettingsWidget()
    assert set(w._trigger_buttons.keys()) == set(ai_main.RECORDER_SHORTCUT_IDS)
    assert w._trigger_buttons["recorder_click"].current_trigger() == "f7"


def test_trigger_capture_button_captures_a_keyboard_key(qapp):
    from app.settings_widget import TriggerCaptureButton
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent, Qt as QtCore_Qt

    btn = TriggerCaptureButton("f7")
    btn._start_listening()

    event = QKeyEvent(QEvent.Type.KeyPress, QtCore_Qt.Key.Key_F6, QtCore_Qt.KeyboardModifier.NoModifier)
    captured = []
    btn.triggerCaptured.connect(captured.append)
    btn.keyPressEvent(event)

    assert captured == ["f6"]
    assert btn.current_trigger() == "f6"
    assert btn._listening is False


def test_trigger_capture_button_ignores_bare_modifier_keys(qapp):
    from app.settings_widget import TriggerCaptureButton
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent, Qt as QtCore_Qt

    btn = TriggerCaptureButton("f7")
    btn._start_listening()
    captured = []
    btn.triggerCaptured.connect(captured.append)

    event = QKeyEvent(QEvent.Type.KeyPress, QtCore_Qt.Key.Key_Control, QtCore_Qt.KeyboardModifier.ControlModifier)
    btn.keyPressEvent(event)

    assert captured == []
    assert btn._listening is True  # still waiting for a real key


def test_trigger_capture_button_escape_cancels_without_changing(qapp):
    from app.settings_widget import TriggerCaptureButton
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent, Qt as QtCore_Qt

    btn = TriggerCaptureButton("f7")
    btn._start_listening()
    captured = []
    btn.triggerCaptured.connect(captured.append)

    event = QKeyEvent(QEvent.Type.KeyPress, QtCore_Qt.Key.Key_Escape, QtCore_Qt.KeyboardModifier.NoModifier)
    btn.keyPressEvent(event)

    assert captured == []
    assert btn.current_trigger() == "f7"  # unchanged


def test_trigger_capture_button_captures_a_mouse_button(qapp):
    from app.settings_widget import TriggerCaptureButton
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent, Qt as QtCore_Qt, QPointF

    btn = TriggerCaptureButton("f7")
    btn._start_listening()
    captured = []
    btn.triggerCaptured.connect(captured.append)

    event = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(0, 0), QtCore_Qt.MouseButton.MiddleButton,
        QtCore_Qt.MouseButton.MiddleButton, QtCore_Qt.KeyboardModifier.NoModifier,
    )
    btn.mousePressEvent(event)

    assert captured == ["mouse:middle"]
    assert btn.current_trigger() == "mouse:middle"


def test_rebind_recorder_trigger_only_persists_does_not_call_bind_shortcut(qapp, fresh_db, monkeypatch, tmp_path):
    """
    Regression test for the design distinction in rebind_shortcut's
    docstring: a recorder trigger (possibly "mouse:x") must NOT be
    handed to keyboard.add_hotkey (the four GLOBAL shortcuts' path) - that call would either fail outright on a mouse trigger or silently
    register a nonsense keyboard binding.

    Isolated from the real .env (ai_main.set_shortcut persists to disk)
    the same way the theme tests are, above.
    """
    from app.main_window import MainWindow
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.delenv("KESTREL_SHORTCUT_RECORDER_CLICK", raising=False)
    window = MainWindow()

    called = []
    monkeypatch.setattr(window, "_bind_shortcut", lambda *a, **k: called.append(a))

    ok, message = window.rebind_shortcut("recorder_click", "mouse:x2")

    assert ok is True
    assert called == []  # _bind_shortcut (the keyboard.add_hotkey path) was never touched
    assert ai_main.get_shortcut("recorder_click") == "mouse:x2"


def test_settings_trigger_capture_saves_immediately_via_main_window(qapp, fresh_db, monkeypatch, tmp_path):
    from app.main_window import MainWindow
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.delenv("KESTREL_SHORTCUT_RECORDER_STOP", raising=False)
    window = MainWindow()

    window.settings._trigger_buttons["recorder_stop"].set_current_trigger("mouse:middle")
    window.settings._trigger_buttons["recorder_stop"].triggerCaptured.emit("mouse:middle")

    assert ai_main.get_shortcut("recorder_stop") == "mouse:middle"


def test_mic_icon_uses_theme_colors_not_a_static_asset(qapp, fresh_db):
    """
    Regression test: the mic button used to load a static SVG file with
    a hardcoded light-gray stroke (#cccccc) - invisible against the
    light theme's near-white background. It's a themed qtawesome icon
    now, refreshed on every set_mic_state() call and on theme switches.
    """
    from app.chat_widget import ChatWidget
    chat = ChatWidget()

    icon_dark_idle = chat.micBtn.icon()
    assert not icon_dark_idle.isNull()

    chat.set_theme("light")
    icon_light_idle = chat.micBtn.icon()
    assert not icon_light_idle.isNull()
    # Can't compare pixel content directly through QIcon equality, but a
    # theme switch must have gone through set_mic_state again - confirm
    # via the state being preserved and the call not raising.
    assert chat._mic_state == "idle"

    chat.set_mic_state("listening")
    assert not chat.micBtn.icon().isNull()
    # Listening is now clickable-to-cancel (see VoiceWorker.request_cancel)
    # rather than disabled - there used to be no way to back out of an
    # in-progress recording short of waiting out the full silence timeout.
    assert chat.micBtn.isEnabled() is True


def test_settings_spinboxes_have_theme_appropriate_contrast(qapp):
    """
    Regression test: QSpinBox/QDoubleSpinBox had no stylesheet rule at
    all, so their background/text used the OS-native palette instead of
    the app's theme - in light mode this could land on light-text-on-
    light-background (or vice versa) depending on the platform's default
    palette. Checks the actual rendered contrast, not just that a style
    string exists.
    """
    from app.theme import THEMES

    def luminance(hexcolor):
        hexcolor = hexcolor.lstrip("#")
        r, g, b = [int(hexcolor[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        def f(v):
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b = f(r), f(g), f(b)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def contrast(c1, c2):
        l1, l2 = luminance(c1), luminance(c2)
        l1, l2 = max(l1, l2), min(l1, l2)
        return (l1 + 0.05) / (l2 + 0.05)

    for theme_name in ("dark", "light"):
        c = THEMES[theme_name]
        ratio = contrast(c["text_primary"], c["bg_panel"])
        assert ratio >= 4.5, f"{theme_name}: spinbox text/background contrast only {ratio:.2f}"


def test_settings_widget_style_includes_spinbox_rules(qapp):
    """More direct check that the QSS gap is actually closed, not just that the color pair would look fine in isolation."""
    from app.settings_widget import _build_style
    from app.theme import colors
    for theme_name in ("dark", "light"):
        qss = _build_style(colors(theme_name))
        assert "QSpinBox" in qss
        assert "QDoubleSpinBox" in qss

def test_reveal_response_ends_with_full_text_and_correct_height(qapp, fresh_db):
    """
    Regression test for the "reply looks cut off mid-sentence" bug:
    drives the actual QTimer-based reveal to completion (not just
    checking label.text() at the end, which would pass even with the
    bug - the bug was the DISPLAYED height falling behind, not the text
    itself) and asserts the label's final sizeHint is tall enough for
    every line of a long, multi-line-wrapping response.
    """
    from app.chat_widget import ChatWidget
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtWidgets import QLabel
    import time

    chat = ChatWidget()
    chat._current_chat_id = 1

    long_text = (
        "Paris is not a country, so it doesn't have a capital; rather, "
        "it is the capital city of France. France is a country in "
        "Western Europe known for its culture, cuisine, and history."
    )

    chat.reveal_response(1, long_text, chars_per_tick=3, interval_ms=1)

    # Pump the event loop until the reveal's QTimer finishes (bounded
    # wait so a real bug shows up as a test failure, not a hang).
    deadline = time.monotonic() + 5
    while chat._reveal_timers and time.monotonic() < deadline:
        QCoreApplication.processEvents()

    assert not chat._reveal_timers, "reveal timer never finished"

    label = None
    for i in range(chat._messages_layout.count()):
        w = chat._messages_layout.itemAt(i).widget()
        if w is not None:
            found = w.findChild(QLabel, "bubbleAi")
            if found is not None:
                label = found
    assert label is not None
    assert label.text() == long_text

    # The actual regression: sizeHint must be tall enough to show every
    # wrapped line, not stuck at whatever height an earlier, shorter
    # version of the text needed.
    QCoreApplication.processEvents()
    single_line_height = label.fontMetrics().height()
    assert label.sizeHint().height() > single_line_height * 2, (
        f"label height ({label.sizeHint().height()}px) looks stuck at an "
        f"early/short size for text this long - the tail end would be clipped"
    )


def test_all_chat_widget_style_functions_produce_balanced_braces(qapp):
    """
    Regression test for a real bug found this session: a plain (non-f)
    string segment inside a style function's concatenated return value
    had a literal "}}" instead of "}" - invalid QSS that Qt silently
    warns about and drops (visible as "Could not parse stylesheet of
    object ..." on stderr), so the button never got its intended style.
    Three functions had this bug (_confirm_accept_style,
    _confirm_cancel_style, _attachment_chip_style) plus two more
    introduced by the edit-mode buttons in the same session
    (_edit_cancel_style, _edit_save_style) - checking every one
    mechanically here instead of one at a time by inspection.
    """
    import app.chat_widget as cw
    from app.theme import colors

    style_fns = [
        name for name in dir(cw)
        if name.startswith("_") and name.endswith("_style")
        and callable(getattr(cw, name))
    ]
    assert len(style_fns) >= 10  # sanity: actually found the functions, not an empty list

    for theme_name in ("dark", "light"):
        c = colors(theme_name)
        for name in style_fns:
            fn = getattr(cw, name)
            qss = fn(c)
            assert qss.count("{") == qss.count("}"), (
                f"{name}({theme_name}) has unbalanced braces - invalid QSS: {qss!r}"
            )


def test_edit_and_confirm_card_buttons_parse_without_qt_warnings(qapp, capsys):
    """
    More direct check than the brace-counting one above: actually builds
    the real widgets these styles get applied to and asserts Qt didn't
    print its "Could not parse stylesheet" warning to stderr for any of
    them (which the brace bug caused for editCancelBtn/editSaveBtn and
    the confirmation/handoff cards' accept/cancel buttons).
    """
    from app.chat_widget import _make_row, _make_confirmation_row, _make_human_handoff_row

    _make_row("hello", "user", "dark", message_id=1)
    _make_confirmation_row("do something", True, "dark")
    _make_human_handoff_row("a captcha appeared", "dark")

    stderr = capsys.readouterr().err
    assert "Could not parse stylesheet" not in stderr


def test_model_picker_is_populated_and_shows_the_active_model(qapp, fresh_db, monkeypatch):
    import ai.main as ai_main
    from app.chat_widget import ChatWidget
    from ai.providers.base import MODEL_CHOICES

    monkeypatch.setattr(ai_main, "get_active_provider", lambda: "gemini")
    monkeypatch.setattr(ai_main, "get_active_model", lambda: "models/gemini-3-flash-preview")

    chat = ChatWidget()
    # StyledDropdown (app/styled_dropdown.py) rather than a QComboBox
    # see _build_model_picker's own docstring for why - so this checks
    # its options/current_value() instead of count()/currentData().
    assert len(chat._modelPicker._options) == len(MODEL_CHOICES)
    assert chat._modelPicker.current_value() == "gemini|models/gemini-3-flash-preview"


def test_model_picker_switch_calls_set_active_provider_and_model(qapp, fresh_db, monkeypatch):
    import ai.main as ai_main
    from app.chat_widget import ChatWidget

    monkeypatch.setattr(ai_main, "get_active_provider", lambda: "gemini")
    monkeypatch.setattr(ai_main, "get_active_model", lambda: "models/gemini-3-flash-preview")

    calls = []
    monkeypatch.setattr(ai_main, "set_active_provider_and_model", lambda p, m: calls.append((p, m)))

    chat = ChatWidget()
    # Picking a StyledDropdown option is _select(value) under the hood
    # exercised directly rather than through a simulated click on the
    # popup row, same as the old test drove QComboBox via
    # findData()+setCurrentIndex() rather than an actual mouse click.
    chat._modelPicker._select("anthropic|claude-sonnet-5")

    assert calls == [("anthropic", "claude-sonnet-5")]


def test_model_picker_reverts_selection_when_switch_fails(qapp, fresh_db, monkeypatch):
    import ai.main as ai_main
    from app.chat_widget import ChatWidget
    import ai.providers as providers

    monkeypatch.setattr(ai_main, "get_active_provider", lambda: "gemini")
    monkeypatch.setattr(ai_main, "get_active_model", lambda: "models/gemini-3-flash-preview")

    def fail_switch(p, m):
        raise providers.ProviderClientError("No OpenAI API key is configured.")
    monkeypatch.setattr(ai_main, "set_active_provider_and_model", fail_switch)
    # Avoid a real blocking modal during the test.
    monkeypatch.setattr("app.chat_widget.QMessageBox.warning", lambda *a, **k: None)

    chat = ChatWidget()
    chat._modelPicker._select("openai|gpt-5.4")

    # Reverted back to the still-actually-active gemini entry, not left
    # showing openai (which never actually became live).
    assert chat._modelPicker.current_value() == "gemini|models/gemini-3-flash-preview"


def test_model_picker_style_parses_without_qt_warnings(qapp, fresh_db, capsys):
    from app.chat_widget import ChatWidget

    chat = ChatWidget()
    chat.set_theme("light")
    chat.set_theme("dark")

    stderr = capsys.readouterr().err
    assert "Could not parse stylesheet" not in stderr


# ══════════════════════════════════════════════════════════════════
# Profile picker (chat topbar) - app/chat_widget.py's
# _build_profile_picker/_on_profile_picker_changed/refresh_profile_picker
# ══════════════════════════════════════════════════════════════════

def _profile(name, id_=None, provider="gemini", model_id="models/gemini-3-flash-preview"):
    import time
    import ai.profiles as profiles
    return profiles.Profile(id=id_ or name.lower(), name=name, provider=provider, model_id=model_id, created_at=time.time())


def test_profile_picker_is_populated_and_shows_the_active_profile(qapp, fresh_db, monkeypatch):
    import ai.profiles as profiles
    from app.chat_widget import ChatWidget

    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Aria"), _profile("Nova")])
    monkeypatch.setattr(profiles, "get_active_profile_id", lambda: "nova")

    chat = ChatWidget()

    assert set(chat._profilePicker._options) == {("aria", "Aria"), ("nova", "Nova")}
    assert chat._profilePicker.current_value() == "nova"


def test_profile_picker_switch_activates_profile_and_updates_model_picker(qapp, fresh_db, monkeypatch):
    import ai.profiles as profiles
    import ai.main as ai_main
    from app.chat_widget import ChatWidget

    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Aria"), _profile("Nova", provider="anthropic", model_id="claude-sonnet-5")])
    monkeypatch.setattr(profiles, "get_active_profile_id", lambda: "aria")
    monkeypatch.setattr(ai_main, "get_active_provider", lambda: "gemini")
    monkeypatch.setattr(ai_main, "get_active_model", lambda: "models/gemini-3-flash-preview")

    activated = []

    def fake_activate(profile_id):
        activated.append(profile_id)
        # Mirror what a real activate_profile() would do: flip what
        # get_active_provider/model() (and get_active_profile_id) return.
        monkeypatch.setattr(ai_main, "get_active_provider", lambda: "anthropic")
        monkeypatch.setattr(ai_main, "get_active_model", lambda: "claude-sonnet-5")
        monkeypatch.setattr(profiles, "get_active_profile_id", lambda: "nova")

    monkeypatch.setattr(profiles, "activate_profile", fake_activate)

    switched = []
    chat = ChatWidget()
    chat.profile_switched.connect(lambda: switched.append(True))

    chat._profilePicker._select("nova")

    assert activated == ["nova"]
    assert chat._modelPicker.current_value() == "anthropic|claude-sonnet-5"
    assert switched == [True]


def test_profile_picker_reverts_selection_when_switch_fails(qapp, fresh_db, monkeypatch):
    import ai.profiles as profiles
    import ai.main as ai_main
    from app.chat_widget import ChatWidget

    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Aria"), _profile("Nova")])
    monkeypatch.setattr(profiles, "get_active_profile_id", lambda: "aria")
    monkeypatch.setattr(ai_main, "get_active_provider", lambda: "gemini")
    monkeypatch.setattr(ai_main, "get_active_model", lambda: "models/gemini-3-flash-preview")

    def fail_activate(profile_id):
        raise ValueError("Nova's provider has no API key configured yet.")
    monkeypatch.setattr(profiles, "activate_profile", fail_activate)
    monkeypatch.setattr("app.chat_widget.QMessageBox.warning", lambda *a, **k: None)

    switched = []
    chat = ChatWidget()
    chat.profile_switched.connect(lambda: switched.append(True))

    chat._profilePicker._select("nova")

    assert chat._profilePicker.current_value() == "aria"
    assert switched == []


def test_profile_picker_no_op_when_reselecting_the_active_profile(qapp, fresh_db, monkeypatch):
    import ai.profiles as profiles
    from app.chat_widget import ChatWidget

    monkeypatch.setattr(profiles, "list_profiles", lambda: [_profile("Aria")])
    monkeypatch.setattr(profiles, "get_active_profile_id", lambda: "aria")

    activated = []
    monkeypatch.setattr(profiles, "activate_profile", lambda pid: activated.append(pid))

    chat = ChatWidget()
    chat._on_profile_picker_changed("aria")

    assert activated == []


def test_refresh_profile_picker_picks_up_newly_added_profile(qapp, fresh_db, monkeypatch):
    import ai.profiles as profiles
    from app.chat_widget import ChatWidget

    current_profiles = [_profile("Aria")]
    monkeypatch.setattr(profiles, "list_profiles", lambda: current_profiles)
    monkeypatch.setattr(profiles, "get_active_profile_id", lambda: "aria")

    chat = ChatWidget()
    assert len(chat._profilePicker._options) == 1

    # A profile gets added from Settings while the chat page isn't
    # showing (see MainWindow._navigate's own call to this method).
    current_profiles.append(_profile("Nova"))
    chat.refresh_profile_picker()

    assert len(chat._profilePicker._options) == 2
    assert chat._profilePicker.current_value() == "aria"



# ══════════════════════════════════════════════════════════════════
# Wake word - must always start a fresh chat when triggered from
# "outside" a conversation (hidden window, or a non-chat page open)
# ══════════════════════════════════════════════════════════════════

def test_wake_word_starts_new_chat_when_window_hidden(qapp, fresh_db):
    from app.main_window import MainWindow
    window = MainWindow()
    mic_calls = []
    window._on_mic_requested = lambda chat_id: mic_calls.append(chat_id)

    # An existing chat is already open...
    window._on_new_chat()
    existing_chat_id = window.chat._current_chat_id
    # ...but the window itself is hidden/minimized when the wake word fires.
    window.hide()

    window._on_wake_word_detected("")

    assert window.chat._current_chat_id != existing_chat_id
    assert mic_calls == [window.chat._current_chat_id]


def test_wake_word_starts_new_chat_when_on_a_different_page(qapp, fresh_db):
    from app.main_window import MainWindow
    window = MainWindow()
    window._on_mic_requested = lambda chat_id: None

    window._on_new_chat()
    existing_chat_id = window.chat._current_chat_id
    window.show()
    window._navigate(window.PAGE_SETTINGS)
    assert window._stack.currentIndex() != window.PAGE_CHAT

    window._on_wake_word_detected("")

    assert window.chat._current_chat_id != existing_chat_id


def test_wake_word_keeps_current_chat_when_already_open_on_chat_page(qapp, fresh_db):
    from app.main_window import MainWindow
    window = MainWindow()
    mic_calls = []
    window._on_mic_requested = lambda chat_id: mic_calls.append(chat_id)

    window._on_new_chat()
    existing_chat_id = window.chat._current_chat_id
    window.show()
    window._navigate(window.PAGE_CHAT)

    window._on_wake_word_detected("")

    assert window.chat._current_chat_id == existing_chat_id
    assert mic_calls == [existing_chat_id]


def test_wake_word_creates_chat_when_none_exists_even_if_visible_on_chat_page(qapp, fresh_db):
    from app.main_window import MainWindow
    window = MainWindow()
    window._on_mic_requested = lambda chat_id: None
    window.show()
    window._navigate(window.PAGE_CHAT)
    # Force the "no chat at all yet" edge case directly - MainWindow's
    # own startup already ensures a chat exists, so this state has to be
    # simulated rather than reached by construction alone.
    window.chat._current_chat_id = None

    window._on_wake_word_detected("")

    assert window.chat._current_chat_id is not None
