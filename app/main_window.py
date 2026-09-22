# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

import inspect
import importlib
import os
import sys
import time
from PyQt6 import uic
from PyQt6.QtWidgets import (
    QMainWindow, QSystemTrayIcon,
    QMenu, QApplication, QStackedWidget, QMessageBox, QGraphicsOpacityEffect,
    QDialog, QWidget, QVBoxLayout,
)
from PyQt6.QtCore import (
    Qt, QEvent, QSize, pyqtSlot, QThread, QTimer, QMetaObject, QEventLoop,
    QPropertyAnimation, QEasingCurve,
)
from PyQt6.QtGui import QIcon, QAction

from app.sidebar_widget import Sidebar
from app.chat_widget import ChatWidget
import app.chat_widget as chat_widget
from app.macros_widget import MacrosWidget
from app.recorder_widget import RecorderWidget
from app.settings_widget import SettingsWidget
from app.mapping_widget import MappingWidget
from app.overlay_widget import OverlayWindow, VoiceStatusOverlay
import app.chat_database as db

from app.workers.ai_worker import AIWorker
from app.workers.voice_worker import VoiceWorker
from app.workers.speak_worker import SpeakWorker
from app.workers.wakeword_worker import WakeWordWorker
from app.workers.autonomous_worker import AutonomousWorker
from app.workers.macro_run_worker import MacroRunWorker
import ai.actions as actions
import ai.main as ai_main
import ai.risk_gate as risk_gate
import ai.profiles as profiles
import app.theme as theme
import app.icons as icons
from ai.log import log as print

from paths import BASE_PATH
import branding
from app.i18n import tr
root_path = BASE_PATH


def _tray_menu_style(c: dict) -> str:
    """The system-tray menu, in the app's own palette rather than the
    OS's - see _setup_tray for why that matters for its icons."""
    return f"""
QMenu {{
    background-color: {c['bg_elevated']};
    border: 1px solid {c['border']};
    border-radius: 8px;
    padding: 4px;
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
}}
QMenu::item {{ padding: 7px 24px 7px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background-color: {c['bg_hover_strong']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QMenu::icon {{ padding-left: 8px; }}
"""


def _confirm_box_style(c: dict) -> str:
    """A QMessageBox in the app's palette. Used for the high-risk
    approval prompt, which blocks a worker thread waiting on an answer
    and should not be the one dialog that looks like it came from a
    different application."""
    return f"""
QMessageBox {{
    background-color: {c['bg_panel']};
    color: {c['text_primary']};
    font-family: "Segoe UI";
}}
QMessageBox QLabel {{
    color: {c['text_primary']};
    font-family: "Segoe UI";
    font-size: 12px;
    background: transparent;
}}
QPushButton {{
    background-color: {c['bg_elevated']};
    color: {c['text_secondary']};
    border: 1px solid {c['border']};
    border-radius: 6px;
    font-family: "Segoe UI";
    font-size: 12px;
    padding: 6px 18px;
    min-width: 72px;
}}
QPushButton:hover {{ background-color: {c['bg_hover']}; color: {c['text_primary']}; }}
QPushButton:default {{
    background-color: rgba({c['accent_rgb']},0.18);
    border-color: {c['accent']};
    color: {c['text_primary']};
}}
"""


def _build_window_chrome_style(c: dict) -> str:
    """
    Regenerates the small set of local styleSheet properties app/ui/main_window.ui
    bakes onto specific widgets (root QMainWindow, centralwidget, sidebar_container,
    chat_container) - same reasoning as chat_widget._build_chrome_style. Applied to
    each of those SPECIFIC objects individually (not just self.setStyleSheet on the
    root), since each one has its own separately-set local stylesheet in the .ui and
    a widget's own stylesheet wins over a cascaded ancestor rule for itself.
    """
    return f"QMainWindow {{ background-color: {c['bg_base']}; }}"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _coerce_macro_params(func, params: dict) -> dict:
    """
    The Macros page's parameter inputs are plain QLineEdit widgets, so
    `params` values are always strings. Convert each one to the target
    function's real annotated type (int/float/bool/str) before calling
    it - every tool_* happens to take only str parameters today, so this
    is a
    no-op in practice, but it keeps a future int/float/bool tool parameter
    from silently breaking instead of just working.
    """
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return params

    coerced = {}
    for pname, raw_value in params.items():
        param = sig.parameters.get(pname)
        annotation = param.annotation if param else inspect.Parameter.empty

        if annotation is bool:
            coerced[pname] = str(raw_value).strip().lower() in ("1", "true", "yes", "oui", "on")
        elif annotation is int:
            coerced[pname] = int(raw_value)
        elif annotation is float:
            coerced[pname] = float(raw_value)
        else:
            coerced[pname] = raw_value  # str, or no/unknown annotation - pass through

    return coerced


def _parse_tools_to_macros(tools: list) -> list[dict]:
    """
    Converts the list of functions returned by get_all_tools() into a
    list of dicts compatible with MacrosWidget.load_macros().

    For each function:
      - id          : function name
      - name        : readable name (underscores → spaces, capitalized)
      - badge       : "ai" for a genuine saved macro, "starter" for a
                       hand-written built-in function that ships with
                       the app
      - deletable   : False for "starter" macros - MacroCard hides both
                       its trash icon AND its edit (pencil) icon entirely
                       for these (see macros_widget.py). tool_delete_macro()
                       itself already refuses to delete anything that isn't
                       a real saved macro (macro_store.resolve_macro_name
                       only matches against JSON records), and there's no
                       JSON spec file for the editor to open either - so
                       showing either enabled icon when it would silently
                       do nothing (or, worse, error out) was the actually
                       confusing part, not the missing feature itself.
      - description : docstring (first line) if available
      - params      : signature parameters (excluding *args/**kwargs)
    """
    from ai import macro_store
    saved_macro_names = set(macro_store.list_macro_names())

    macros = []
    for fn in tools:
        fn_name  = fn.__name__                          # e.g. "tool_close_application"
        readable = fn_name.replace("tool_", "").replace("_", " ").strip().capitalize()
        macro_name = fn_name[len("tool_"):] if fn_name.startswith("tool_") else fn_name
        is_saved_macro = macro_name in saved_macro_names

        # Docstring
        doc   = inspect.getdoc(fn) or ""
        desc  = doc.split("\n")[0] if doc else ""

        # Parameters
        try:
            sig    = inspect.signature(fn)
            params = []
            for pname, param in sig.parameters.items():
                if param.kind in (
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                ):
                    continue
                # Type hint → str
                annotation = param.annotation
                if annotation is inspect.Parameter.empty:
                    type_str = "str"
                else:
                    type_str = getattr(annotation, "__name__", str(annotation))

                # Placeholder = default value, if any
                default = param.default
                placeholder = "" if default is inspect.Parameter.empty else str(default)

                params.append({
                    "name":        pname,
                    "type":        type_str,
                    "placeholder": placeholder,
                })
        except (ValueError, TypeError):
            params = []

        macros.append({
            "id":          fn_name,
            "name":        readable,
            # Every saved macro used to be badged "AI", including ones
            # the person recorded themselves - which is most of them.
            # The spec says who made it now (macro_store.created_by):
            # a recording is the user's, and only a macro the
            # generalizer authored is the AI's.
            "badge":       (
                macro_store.created_by(macro_store.load_spec(macro_name))
                if is_saved_macro else "starter"
            ),
            "deletable":   is_saved_macro,
            "description": desc,
            "params":      params,
        })

    return macros


# ── MainWindow ─────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    # Pages (indices in the QStackedWidget)
    PAGE_CHAT     = 0
    PAGE_MACROS   = 1
    PAGE_RECORDER = 2
    PAGE_SETTINGS = 3
    PAGE_MAPPING  = 4

    def __init__(self):
        super().__init__()

        # Keeps a strong reference to every QThread currently running,
        # regardless of which single-purpose attribute (self._macro_thread,
        # self._voice_thread, etc.) also points at it. Those attributes get
        # overwritten the next time the SAME kind of action fires (e.g. a
        # second macro run started before the first one's thread finished)
        # once overwritten, the old QThread object had no Python reference
        # left anywhere else, so it got garbage-collected mid-run, which is
        # exactly what "QThread: Destroyed while thread '' is still running"
        # is: Qt's C++ side still had work in flight when the wrapper object
        # backing it disappeared. Doing "multiple things at once" is exactly
        # what triggers two starts of the same handler to overlap. Entries
        # are added right before .start() and removed by _untrack_thread,
        # connected to the thread's own `finished` signal, so this never
        # grows unbounded.
        self._active_threads: list[QThread] = []

        # Code-enforced approval gate for HIGH-risk actions (see
        # ai/risk_gate.py) - connected here, once, so it's live for the
        # rest of the app's life regardless of which page/thread a
        # tool_run_command / tool_close_application call comes from.
        risk_gate.connect_handler(self._on_risk_approval_needed)

        # Initialize the DB
        db.init_db()

        uic.loadUi(fr"{root_path}/app/ui/main_window.ui", self)

        self._theme = ai_main.get_theme()
        self._apply_window_chrome_theme(self._theme)

        # The .ui file's own windowIcon property points at a *relative*
        # path ("../../assets/icon/..."), which Qt resolves against the
        # process's current working directory - not against the .ui file's
        # location or BASE_PATH. That only happens to work when the app is
        # launched with the project root as CWD; from a shortcut, a
        # different "Start in" folder, or the frozen .exe launched some
        # other way, the relative path resolves to nothing, silently
        # falling back to Qt/Windows' generic default icon in the taskbar.
        # Overriding it here with the same absolute path main.py already
        # uses (via BASE_PATH) makes it launch-location-independent.
        self.setWindowIcon(QIcon(os.path.join(BASE_PATH, branding.ICON_PATH)))
        # A real title, which is what the taskbar tooltip, the alt-tab
        # switcher and Task Manager's Apps group all read. This was a
        # single space for a while, to keep the product name from being
        # printed across the native title bar - but the result was that
        # every one of those OS surfaces fell back to the process
        # filename instead, so the window announced itself as
        # "Kestrel.exe" rather than as Kestrel.
        #
        # There is no longer a native title bar to print it into (see
        # window_chrome below), so the title is free to be a real one:
        # visible to the OS, nowhere in the app's own chrome.
        # applicationDisplayName stays empty in main.py so Qt does not
        # append it and make this read "Kestrel - Kestrel".
        self.setWindowTitle(branding.WINDOW_TITLE)

        self._apply_default_geometry()

        # ── Client-side decorations ───────────────────────────────────────
        # Frameless, with our own title bar and controls, because the
        # native frame draws a grey caption in the OS palette directly
        # above a window that is otherwise entirely ours. Everything the
        # frame used to do - drag, resize, Snap, double-click to
        # maximize - is preserved via the platform's own move/resize,
        # not reimplemented; see app/window_chrome.py.
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        # Rounded corners, applied once the native window exists -
        # winId() has to be real before DWM will take an attribute for
        # it, and it is not until show(). See _apply_rounded_corners.

        # ── Startup shell (splash + name gate + real content) ──────────────
        # Wraps the .ui file's own centralwidget (the sidebar+pages layout
        # everything below this block builds into) as one page inside a
        # QStackedWidget, alongside a splash page and the name gate, so
        # opening the app is always exactly ONE OS-level window from the
        # very first frame (this splash page, shown below) through to the
        # real app, rather than a popup window for each step. See
        # app/splash_screen.py's own docstring for the fuller history.
        #
        # self.show() + processEvents() right here - BEFORE the heavy
        # widget construction below (Sidebar/ChatWidget/MacrosWidget/etc.,
        # the wake-word thread, the system tray) - is what actually gets
        # this splash page painted on screen before that work runs, while
        # __init__ itself stays a single synchronous call that returns a
        # fully-built MainWindow either way (same contract every existing
        # caller - including the test suite - already depends on).
        from app.splash_screen import SplashPage
        from app.name_gate_widget import NameGateWidget

        real_content = self.centralWidget()
        self._shell_stack = QStackedWidget()
        self._real_content_page = real_content
        self._splash_page = SplashPage(self._theme)
        self._name_page = NameGateWidget()
        for page in (self._splash_page, self._name_page, real_content):
            self._shell_stack.addWidget(page)
        self._shell_stack.setCurrentWidget(self._splash_page)

        # The title bar sits ABOVE the shell stack rather than inside it,
        # so it spans the whole window and stays put across every page -
        # splash, name gate and the real app. A frameless window with no
        # controls during startup would be one nobody could move or
        # close while it was loading.
        from app.window_chrome import TitleBar, WindowChrome

        shell_host = QWidget()
        shell_layout = QVBoxLayout(shell_host)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        self._title_bar = TitleBar(self, self._theme)
        self._title_bar.always_on_top_toggled.connect(self._set_always_on_top)
        shell_layout.addWidget(self._title_bar)
        shell_layout.addWidget(self._shell_stack, 1)

        self.setCentralWidget(shell_host)
        self._chrome = WindowChrome(self, self._title_bar)
        self._left_splash = False  # see _leave_splash_if_needed

        # Deliberately NOT calling QApplication.processEvents() here too
        # (only .show()) - pumping the event queue this early, still
        # mid-constructor, turned out to intermittently abort the process
        # under repeated MainWindow() construction (e.g. the test suite,
        # which builds many in a row): pending signals/timers can fire
        # against a window that isn't fully built yet. It doesn't need to
        # anyway - _leave_splash_if_needed's own wait (called from
        # run_name_gate/show_main_content,
        # ALWAYS the very next thing that happens once this constructor
        # returns) runs a real QEventLoop for a meaningful stretch on its
        # own, which is what actually gets this splash page painted
        # on-screen for real before anything past it is ever shown.
        self.show()
        # The native window exists from here, so DWM will accept an
        # attribute for it. Before show(), winId() is not a usable
        # HWND and the call is silently ignored.
        self._apply_rounded_corners()

        # ── Main widgets ───────────────────────────────────────────────────────
        self.sidebar  = Sidebar()
        self.chat     = ChatWidget()
        self.macros   = MacrosWidget()
        self.recorder = RecorderWidget()
        self.settings = SettingsWidget()

        # QStackedWidget to switch between pages
        self._stack = QStackedWidget()
        self._stack.insertWidget(self.PAGE_CHAT,     self.chat)
        self._stack.insertWidget(self.PAGE_MACROS,   self.macros)
        self._stack.insertWidget(self.PAGE_RECORDER, self.recorder)
        self._stack.insertWidget(self.PAGE_SETTINGS, self.settings)
        self._stack.setCurrentIndex(self.PAGE_CHAT)

        self.sidebar_container.layout().addWidget(self.sidebar)
        self.chat_container.layout().addWidget(self._stack)

        self._connect_page_signals()

        # ── Overlay window ─────────────────────────────────────────────────────
        # Must exist before launch_page() below: on a fresh install with no
        # chats yet, launch_page() -> _on_new_chat() -> show_window() reads
        # self._overlay_active immediately, and used to crash with an
        # AttributeError because this was previously initialized *after*
        # launch_page() was called.
        self._overlay = OverlayWindow()
        self._voice_status_overlay = VoiceStatusOverlay()
        self._overlay_active: bool = False
        self._console = None  # lazily created - see _toggle_console()
        self.mapping = None  # lazily created - see _navigate()

        # Created eagerly (not lazily, unlike self._console) - it has to
        # register its ai.discovery.events callbacks before the FIRST
        # mapping run, and there's no click-driven moment (like opening
        # the console from the tray menu) to hook that registration into
        # otherwise. A mapping run can start from a tool call at any
        # point once the app is up.
        from app.mapping_monitor import MappingMonitor
        self._mapping_monitor = MappingMonitor()

        # ── Initial state ──────────────────────────────────────────────────────
        self._always_on_top: bool = False
        self._pending_autonomous_goal: tuple[int, str] | None = None
        # The chat an autonomous run is happening in, or None when
        # nothing is running. See _start_autonomous_mode.
        self._automation_chat_id: int | None = None

        self.launch_page()

        # ── System tray ────────────────────────────────────────────────────────
        self._tray_icon_path     = f"{root_path}/{branding.ICON_PATH}"
        # Was Red-transparent-1000.png: a flashing red icon reads as a
        # system warning, not "Kestrel is working". The two marks differ
        # only in colour, which is readable at a glance in a taskbar full
        # of monochrome icons without looking like an alert.
        self._tray_icon_alt_path = f"{root_path}/{branding.ICON_ALT_PATH}"
        self._setup_tray(self._tray_icon_path)
        self._tray_blink_state = True
        self._tray_blink_timer = QTimer(self)
        self._tray_blink_timer.timeout.connect(self._tray_blink_tick)

        # ── Initial macro load ─────────────────────────────────────────────────
        self._reload_macros()

        # ── Wake word ──────────────────────────────────────────────────────────
        self._wakeword_worker = WakeWordWorker()
        self._wakeword_worker.wake_word_detected.connect(self._on_wake_word_detected)
        self._wakeword_worker.confirmation_word_detected.connect(self._on_confirmation_word_detected)
        self._wakeword_worker.stop_word_detected.connect(self._on_stop_word_detected)
        self._wakeword_worker.error.connect(self._on_wake_word_error)
        self._wakeword_worker.ready.connect(self._on_wake_word_ready)
        if ai_main.get_wake_word_enabled():
            self._start_wake_word_listening()

        # ── Global keyboard shortcuts ──────────────────────────────────────────
        self.register_global_shortcuts()

    # ── Startup shell (splash + name gate) ──────────────────────────────────

    def _leave_splash_if_needed(self) -> None:
        """
        Applies the splash's artificial minimum-display-time wait (see
        SplashPage.ensure_minimum_display_time) exactly once, right
        before the FIRST transition away from it - whichever of
        run_name_gate/show_main_content happens to be called first.
        Calling it more than once would be harmless
        (ensure_minimum_display_time no-ops once its own window has
        already elapsed) but there's no reason to check twice.
        """
        if not self._left_splash:
            self._splash_page.ensure_minimum_display_time()
            self._left_splash = True

    def set_gate_status(self, text: str) -> None:
        """Updates the splash page's status line, for whatever startup
        work is still happening before the name gate or the real content
        is shown."""
        self._splash_page.set_status(text)

    def _switch_shell_page(self, page) -> None:
        """
        Switches the shell stack to `page` with a brief crossfade
        instead of the hard, instant cut QStackedWidget.setCurrentWidget
        does on its own - splash -> name -> real content are all one
        single OS-level window (see this class's own "Startup shell"
        section / splash_screen.py's docstring), but without this it was
        one window that JUMP-CUT between completely different-looking
        pages, which didn't read as
        "seamless" even though it technically never opened a second
        window. QStackedWidget hides the outgoing page immediately when
        the current widget changes (no built-in crossfade support), so
        this only fades the INCOMING page in - a fade-out of the
        outgoing page isn't visible anyway since it's hidden the instant
        `setCurrentWidget` below runs.

        Keeps the QPropertyAnimation alive on `self` for its own
        duration - a local variable would otherwise be garbage collected
        as soon as this method returns, killing the animation after
        frame one.
        """
        if self._shell_stack.currentWidget() is page:
            return
        self._shell_stack.setCurrentWidget(page)

        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", page)
        anim.setDuration(320)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        # Drop the effect once the fade finishes - leaving a
        # QGraphicsOpacityEffect permanently attached costs an extra
        # off-screen composite pass on every repaint of that page for
        # the rest of the run, for zero visual benefit once opacity==1.
        anim.finished.connect(lambda: page.setGraphicsEffect(None))
        self._shell_fade_anim = anim  # see docstring - keeps it alive
        anim.start()

    def _wait_for_gate(self, page, signal) -> "bool | tuple":
        """
        Shows `page` as the current shell page and blocks - via a local
        QEventLoop, which keeps the window painting/responding, NOT a
        plain blocking call - until `signal` fires once. Returns
        whatever value(s) the signal carried (or True if it carries
        none), same as the old QDialog.exec()==Accepted pattern this
        replaces, just without a second top-level window to do it in.
        """
        self._leave_splash_if_needed()
        page.reset_for_display()
        self._switch_shell_page(page)

        loop = QEventLoop()
        result = []

        def _capture(*args):
            result.extend(args if args else [True])
            loop.quit()

        signal.connect(_capture)
        loop.exec()
        signal.disconnect(_capture)
        return result[0] if len(result) == 1 else tuple(result)

    def run_name_gate(self) -> None:
        """Shows the "name your assistant" page and blocks until Continue
        is clicked. It cannot fail: see app/name_gate_widget.py."""
        self._wait_for_gate(self._name_page, self._name_page.gate_finished)

    def show_main_content(self) -> None:
        """Switches the shell to the real app content - the last step of
        startup (see main.py). Splash and every gate page stay alive
        (not deleted) but are never shown again for the rest of this run.

        Bug fix: ChatWidget (and its topbar profile picker) is built as
        part of this same shell BEFORE run_name_gate() ever runs (see
        name_gate_widget.py's own docstring) - so its picker's options
        reflect whatever ai.profiles looked like at THAT moment, not
        whatever the name gate may have since changed it to. _navigate(PAGE_CHAT) is normally what
        refreshes the picker on every return to the chat page, but the
        chat page is already the inner stack's default-selected page at
        construction time - nothing ever calls _navigate for it on this
        very first reveal, since there's no earlier page to navigate
        FROM. Without this, the picker (and the "Ask <name>…" style
        copy _refresh_assistant_name_copy covers) stayed showing
        whatever name was configured (or "Kestrel", if none was) right up
        until the user manually left the chat page and came back - which
        a support report described as "it only updates once I
        [do something unrelated, elsewhere,] first."
        """
        self._leave_splash_if_needed()
        self.chat.refresh_profile_picker()
        self._switch_shell_page(self._real_content_page)

    # ── Navigation ─────────────────────────────────────────────────────────────

    def _navigate(self, page_index: int) -> None:
        """Shows the requested page in the QStackedWidget."""
        if page_index == self.PAGE_CHAT:
            # Profiles can be added/renamed/removed from Settings while
            # the chat page isn't showing - refresh on every return to
            # it rather than wiring a dedicated signal for each of those
            # (see ChatWidget.refresh_profile_picker's own docstring).
            self.chat.refresh_profile_picker()

        if page_index == self.PAGE_SETTINGS:
            # Bug fix: Settings' own profile list (app/settings_widget.py's
            # _build_profiles_section) is only rebuilt reactively from ITS
            # OWN controls (a rename typed there, switching/adding/deleting
            # a profile) - it has no idea the name gate (app/name_gate_
            # widget.py, first run only) may have renamed the active
            # profile in the meantime, since it never touches Settings
            # at all. Left alone,
            # Settings kept showing the pre-name-gate name (almost always
            # still "Kestrel") until the user happened to rename/switch/add
            # a profile from Settings itself - even though the chat page
            # and the wake word had already picked up the real name (see
            # show_main_content's own bug-fix comment for the identical
            # issue on the chat page, fixed the same way: refresh on
            # every arrival instead of only reacting to Settings' own
            # controls).
            self.settings._refresh_profiles_list()
            self.settings._refresh_wake_word_copy()

        if page_index == self.PAGE_MAPPING and self.mapping is None:
            # Lazily created, same reasoning as self._console: most
            # sessions never visit this page, and - unlike
            # self._mapping_monitor, which genuinely needs to be
            # watching from app start to catch a mapping run triggered
            # from ANYWHERE (including chat) - this page only needs its
            # own ai.discovery.events registration active while it's
            # actually in use, so there's no reason to pay for it (or
            # the registration itself) on every single app launch.
            self.mapping = MappingWidget()
            self._stack.insertWidget(self.PAGE_MAPPING, self.mapping)

        self._stack.setCurrentIndex(page_index)

        # Syncs the checked state of the sidebar's nav buttons
        nav_buttons = {
            self.PAGE_CHAT:     self.sidebar.btnNewChat,
            self.PAGE_MACROS:   self.sidebar.btnActions,
            self.PAGE_RECORDER: self.sidebar.btnRecorder,
            self.PAGE_MAPPING:  self.sidebar.btnMapping,
        }
        for idx, btn in nav_buttons.items():
            btn.setChecked(idx == page_index)

        if page_index == self.PAGE_MACROS:
            # Auto-refresh on every visit rather than relying solely on
            # the explicit Refresh button / the macro_saved and
            # autonomous-finished hooks - those cover the common paths
            # but not every way a macro can appear (hand-edited JSON,
            # another Kestrel window, etc.), and measured at ~30ms for 50
            # macros (tests/test_smoke.py's perf test), it's cheap enough
            # to just always do.
            self._reload_macros()
        elif page_index == self.PAGE_RECORDER:
            # Rebinding a shortcut in Settings used to leave this page's
            # subtitle showing the OLD keys until the app was relaunched
            # - it was only ever refreshed at construction time and when
            # a new recording started. Cheap (one string rebuild), so
            # just always do it on every visit, same reasoning as the
            # Macros auto-refresh above.
            self.recorder.refresh_shortcuts_description()

    # ── Macros ─────────────────────────────────────────────────────────────────

    def _reload_macros(self) -> None:
        """Reloads ai.actions FROM DISK, then updates the cards.

        Without the explicit reload here, this method only re-read what
        was already in the module's memory - a macro added to the
        ai/actions.py file through another path (manual generation,
        autonomous mode on another thread, hand-editing...) stayed
        invisible in the Macros tab even after clicking Refresh, since
        nothing forced Python to re-read the file.
        """
        if self.recorder._controller.is_recording:
            # importlib.reload() re-executes ai/actions.py's entire
            # module body while RecorderController's global keyboard/mouse
            # hooks are still live and actively appending to
            # actions.actions_list from OS-level callback threads
            # racing a hook firing mid-reload against the module being
            # torn down and rebuilt attribute-by-attribute risks
            # corrupting or losing the in-progress recording (this is
            # the "issue with macro creation" report: simply navigating
            # to the Macros tab, or editing an already-recorded action,
            # while still recording could trigger exactly this). Skip
            # the reload until recording actually stops - the cards just
            # stay as they were for now; _on_stopped()/macro_saved
            # already call _reload_macros() again once it's safe.
            print("[Macro] Skipping reload - a recording is in progress.")
            return

        # importlib.reload() resets every module-level global in actions.py
        # back to its initial value - including actions_list, which holds
        # whatever the Recorder has captured so far in an in-progress,
        # not-yet-saved session. Preserve it across the reload so a reload
        # triggered from elsewhere (autonomous mode finishing a different
        # task, for instance) can never silently wipe an active recording.
        live_actions_list = list(actions.actions_list) if actions.actions_list else None
        importlib.reload(actions)
        if live_actions_list:
            actions.actions_list = live_actions_list

        self._refresh_macro_cards()

    def _refresh_macro_cards(self) -> None:
        """
        Rebuild the Macros page from what is already in ai.actions'
        namespace. No module reload, no disk access beyond the one
        directory listing _parse_tools_to_macros needs to tell a saved
        macro from a built-in tool.

        Split out of _reload_macros so a caller that has just changed the
        namespace itself - deleting a macro, say - can update the cards
        without also re-reading ai/actions.py from disk to discover a
        change it made a moment ago.
        """
        tools = actions.get_all_tools(actions)
        macros = _parse_tools_to_macros(tools)
        self.macros.load_macros(macros)

    def _track_thread(self, thread: QThread) -> None:
        """
        Register a just-started QThread so it stays alive until it actually
        finishes, independent of whatever single self._x_thread attribute
        also references it - see self._active_threads' docstring in
        __init__ for why that attribute alone isn't safe once two of the
        same kind of action can overlap.
        """
        self._active_threads.append(thread)
        thread.finished.connect(lambda: self._untrack_thread(thread))

    def _untrack_thread(self, thread: QThread) -> None:
        try:
            self._active_threads.remove(thread)
        except ValueError:
            pass  # already removed - harmless, can happen if finished fires twice

    @pyqtSlot(str, dict)
    def _on_macro_run(self, macro_id: str, params: dict) -> None:
        """
        Called when the user clicks Run on a macro. Runs it in a
        background thread (see MacroRunWorker) rather than straight on
        this one - macros click things, type text, and sleep for real
        seconds between steps, none of which can happen on the GUI
        thread without freezing the entire window for the macro's whole
        duration.
        """
        if not self._confirm_screen_match(macro_id):
            return
        print(f"[Macro] Run: {macro_id}", input_data=params)

        self._macro_thread = QThread()
        self._macro_worker = MacroRunWorker()
        self._macro_worker.moveToThread(self._macro_thread)
        self._macro_thread.started.connect(lambda: self._macro_worker.start.emit(macro_id, params))
        self._macro_worker.finished.connect(self._on_macro_run_finished)
        self._macro_worker.error.connect(self._on_macro_run_error)
        self._macro_worker.finished.connect(self._macro_thread.quit)
        self._macro_worker.finished.connect(self._macro_worker.deleteLater)
        self._macro_worker.error.connect(self._macro_thread.quit)
        self._macro_worker.error.connect(self._macro_worker.deleteLater)
        self._macro_thread.finished.connect(self._macro_thread.deleteLater)
        self._track_thread(self._macro_thread)
        self._macro_thread.start()

    def _confirm_screen_match(self, macro_id: str) -> bool:
        """
        A recorded macro's clicks are absolute pixel coordinates, so on a
        screen of a different size or scaling they land somewhere else
        entirely. False means the user saw the warning and backed out.
        Macros recorded before screens were tracked have nothing to
        compare against and run without a prompt.
        """
        import ai.macro_store as macro_store
        name = macro_id[len("tool_"):] if macro_id.startswith("tool_") else macro_id
        warning = actions.screen_mismatch(macro_store.screen_of(macro_store.load_spec(name)))
        if not warning:
            return True
        print(f"[Macro] Screen mismatch for {macro_id}: {warning}", level="warn")
        answer = QMessageBox.warning(
            self,
            "Different screen",
            f"{warning}\n\nRun it anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_macro_run_finished(self, macro_id: str, result: str) -> None:
        print(f"[Macro] {macro_id} finished", input_data=result or None)

    def _on_macro_run_error(self, macro_id: str, message: str) -> None:
        print(f"[Macro] {macro_id} failed: {message}", level="error")

    def _on_macro_delete_requested(self, macro_id: str, display_name: str) -> None:
        """
        macro_id is the raw tool_* function name (macro_store's
        `resolve_macro_name` fuzzy-matches this fine, but it's already
        exact here since it came straight from the loaded macro's own
        id - no ambiguity to resolve).

        Runs on the GUI thread, which is now actually true rather than
        aspirational. The old comment here claimed deletion was "just a
        file removal, fast enough to run right here" - the os.remove was,
        but everything wrapped around it was not: this method resolved
        the name, then tool_delete_macro resolved it AGAIN, then
        refresh_actions reloaded ai.actions and re-read, re-parsed and
        re-exec'd every surviving macro from disk, then built a fresh
        provider chat session, and then _reload_macros reloaded
        ai.actions a SECOND time. Deleting one macro out of N cost four
        directory listings, N file reads, N compiles, two module reloads
        and a provider round trip, with the window frozen throughout.

        Now: one resolve, one os.remove, one delattr, and a cheap
        in-memory tool-list rebuild. The chat session is marked stale and
        rebuilt before the next message instead (ai.main.drop_macro_tool).
        """
        from ai import macro_store

        # Read the spec before it goes, so Undo has something to put
        # back. Without this the only way back from a mis-click is
        # re-recording the whole macro. The resolve is done once, here,
        # and the resolved name handed to the delete - rather than each
        # of them listing the directory to work it out separately.
        resolved = macro_store.resolve_macro_name(macro_id)
        backup = macro_store.load_spec(resolved) if resolved else None

        result = actions.tool_delete_macro(resolved or macro_id)
        print(f"[Macro] Delete: {macro_id}", input_data=result)
        # Refresh the cards from what is already in memory. The module
        # reload _reload_macros does is for picking up changes made to
        # ai/actions.py on disk by some other path; a deletion we just
        # performed ourselves is already reflected in the namespace.
        self._refresh_macro_cards()

        if backup is not None:
            self.macros.offer_undo(
                f'Deleted "{display_name}".',
                lambda spec=backup: self._restore_macro(spec),
            )

    def _restore_macro(self, spec: dict) -> None:
        from ai import macro_store
        try:
            macro_store.restore_macro(spec)
        except (OSError, KeyError) as e:
            QMessageBox.warning(self, "Couldn't undo", f"The macro could not be put back: {e}")
            return
        ai_main.refresh_actions()
        print(f"[Macro] Restored: {spec.get('name')}")
        self._reload_macros()

    def _on_macro_edit_requested(self, macro_id: str, display_name: str) -> None:
        """
        Opens the manual step editor (app/macro_editor_dialog.py) for an
        already-saved macro - reorder/tweak/insert/remove a step without
        re-recording the whole thing from scratch, previously the only
        fix path once a macro's recorded clicks/targets drifted from the
        UI it was learned against.

        macro_id is the raw tool_* function name, same as
        _on_macro_delete_requested above - resolve_macro_name fuzzy-
        matches it down to the bare saved-macro name the editor/
        macro_store actually key off of.
        """
        from ai import macro_store
        from app.macro_editor_dialog import MacroEditorDialog

        resolved = macro_store.resolve_macro_name(macro_id)
        if resolved is None:
            QMessageBox.warning(
                self, "Macro not found",
                f'"{display_name}" doesn\'t have a saved macro file to edit '
                f" - only macros you've recorded/learned can be edited, not "
                f"the app's built-in ones.",
            )
            return

        dlg = MacroEditorDialog(resolved, theme_name=ai_main.get_theme(), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._reload_macros()

    # ── Always on top ─────────────────────── ───────────────────────────────────

    @pyqtSlot(bool)
    def _set_always_on_top(self, enabled: bool) -> None:
        """
        Pin or unpin the window.

        This used to be driven by a pin button in the sidebar's header
        strip, which put a window-management control inside the app's
        own content. It now lives where the OS puts it: the title bar's
        right-click menu (app/window_chrome.py), alongside restore and
        minimize. The global shortcut goes through here too, so there is
        no button to find and click programmatically any more.
        """
        self._always_on_top = enabled
        flags = self.windowFlags()
        if enabled:
            self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        # setWindowFlags() destroys and recreates the native window handle
        # under the hood - on Windows that new handle no longer carries the
        # WM_SETICON value from the constructor's setWindowIcon() call,
        # so the taskbar icon silently reverts to Qt/Windows' generic
        # default right after toggling this. Same underlying cause as
        # that constructor comment already flags for the .ui file's
        # relative-path icon; reapplying it here, after show() has
        # recreated the handle, is the fix.
        #
        # FramelessWindowHint survives the mask above (it only clears
        # WindowStaysOnTopHint), so the window does not grow a native
        # frame back on the way through.
        self.show()
        self.setWindowIcon(QIcon(os.path.join(BASE_PATH, branding.ICON_PATH)))
        # setWindowFlags recreated the native window, and the corner
        # preference was set on the old HWND - same reason the icon has
        # to be re-applied here.
        self._apply_rounded_corners()
        if hasattr(self, "_chrome"):
            self._chrome.sync()

    # ── System tray ────────────────────────────────────────────────────────────

    def _setup_tray(self, icon_path: str | None) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        icon = QIcon(icon_path) if icon_path else QIcon()
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip(branding.APP_NAME)

        tray_menu = QMenu()
        self._tray_menu = tray_menu
        # Kept so a theme switch can re-tint them. A QIcon is a baked
        # bitmap - no stylesheet can recolour one after the fact.
        self._tray_actions: dict = {}
        c = theme.colors(self._theme)
        # The menu had no stylesheet at all, so it rendered in the OS's
        # own chrome - light, on Windows - while its icons were tinted
        # text_primary from the app theme. In the dark theme that is
        # #f2f2f2 icons on a white menu: invisible. Styling the menu
        # from the same palette as the icons is what makes the pair
        # agree, in either theme.
        tray_menu.setStyleSheet(_tray_menu_style(c))

        show_action    = QAction(tr("Show / Hide"), self)
        show_action.setIcon(icons.icon("window", c))
        self._tray_actions["window"] = show_action
        show_action.triggered.connect(self._toggle_window)

        new_chat_action = QAction(tr("New chat"), self)
        new_chat_action.setIcon(icons.icon("new-chat", c))
        self._tray_actions["new-chat"] = new_chat_action
        new_chat_action.triggered.connect(self._on_new_chat)

        console_action = QAction(tr("Console"), self)
        console_action.setIcon(icons.icon("terminal", c))
        self._tray_actions["terminal"] = console_action
        console_action.triggered.connect(self._toggle_console)

        quit_action = QAction(tr("Quit"), self)
        quit_action.setIcon(icons.icon("quit", c))
        self._tray_actions["quit"] = quit_action
        quit_action.triggered.connect(self._quit_app)

        tray_menu.addAction(show_action)
        tray_menu.addAction(new_chat_action)
        tray_menu.addAction(console_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    # ── Tray blink ─────────────────────────────────────────────────────────────

    def start_tray_blink(self, interval_ms: int = 900) -> None:
        self._tray_blink_state = True
        self._tray_blink_timer.start(interval_ms)

    def stop_tray_blink(self) -> None:
        self._tray_blink_timer.stop()
        self._tray_blink_state = True
        if hasattr(self, "tray_icon"):
            self.tray_icon.setIcon(QIcon(self._tray_icon_path))

    def _tray_blink_tick(self) -> None:
        if not hasattr(self, "tray_icon"):
            return
        icon_path = self._tray_icon_path if self._tray_blink_state else self._tray_icon_alt_path
        self.tray_icon.setIcon(QIcon(icon_path))
        self._tray_blink_state = not self._tray_blink_state

    # ── Overlay ────────────────────────────────────────────────────────────────

    def _toggle_overlay(self) -> None:
        if self._overlay_active:
            self.deactivate_overlay()
        else:
            self.activate_overlay()

    def activate_overlay(self, blink_border: bool = True, blink_tray: bool = True) -> None:
        self._overlay_active = True
        self._overlay.cover_screen()
        self._overlay.activate(blink=blink_border)
        if blink_tray:
            self.start_tray_blink()

    def deactivate_overlay(self) -> None:
        self._overlay_active = False
        self._overlay.deactivate()
        self.stop_tray_blink()

    # ── Window management ──────────────────────────────────────────────────────

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if self._overlay_active:
            self._overlay.cover_screen()
            self._overlay.raise_()

    @pyqtSlot()
    def _toggle_window(self) -> None:
        if self.isVisible():
            self.hide()
            if self._overlay_active:
                self._overlay.hide()
        else:
            self.show_window()
            if self._overlay_active:
                self._overlay.show()

    def _toggle_console(self) -> None:
        """
        Shows the console window, creating it lazily on first use (most
        sessions never open it, so there's no reason to pay for a
        QTextBrowser + ai.log sink registration on every app launch).
        Re-shown/raised on subsequent toggles rather than recreated -
        ConsoleWidget registers exactly one ai.log sink per instance
        (see its closeEvent), so reusing the same instance is what keeps
        that at exactly one registration for the app's whole lifetime.
        """
        if self._console is None:
            from app.console_widget import ConsoleWidget
            self._console = ConsoleWidget(self)
        if self._console.isVisible():
            self._console.hide()
        else:
            self._console.show()
            self._console.raise_()
            self._console.activateWindow()

    @pyqtSlot()
    def _toggle_always_on_top(self) -> None:
        self._set_always_on_top(not self._always_on_top)

    @pyqtSlot()
    def _trigger_screenshot_shortcut(self) -> None:
        """
        Stages the capture as an attachment chip in the composer - same
        treatment an image dragged onto the input gets (thumbnail,
        name, an X to remove it before sending), rather than the
        read-only preview bubble this used to post straight into the
        transcript. That bubble looked like a message someone had sent,
        couldn't be removed once taken, and gave no way to check WHAT
        had been captured before it went to the model.

        Falls back to the old transcript bubble when the chip can't be
        staged (unreadable file, or no chat open yet) so a screenshot
        never silently vanishes - the capture itself is already in
        ai.main's hands either way.
        """
        path = ai_main.trigger_screenshot()
        if not path:
            return  # capture itself still succeeded (for the AI); just no preview file

        if self.chat._current_chat_id is not None and self.chat.attach_screenshot(path):
            # The chip now owns this image (it'll go out via
            # queue_chat_images with the next message) - drop ai.main's
            # own copy so the model doesn't receive it twice.
            ai_main.discard_pending_screenshot()
            return

        chat_id = self._get_or_create_target_chat_id()
        marker = f"[[SCREENSHOT:{path}]]"
        db.add_message(chat_id, "assistant", marker)
        if self.chat._current_chat_id == chat_id:
            self.chat._append_bubble(text=marker, role="assistant")
        self.update_history(chat_id)

    def _get_or_create_target_chat_id(self) -> int:
        """
        Returns the chat to drop a background-triggered message into (like
        the screenshot shortcut's preview) - the currently open one if
        there is one, otherwise a freshly created one. Deliberately does
        NOT call show_window()/_navigate() the way _on_new_chat() does: the
        screenshot shortcut is meant to work while some other app has
        focus, and forcing Kestrel to the foreground right then would be
        exactly the disruption a background shortcut should avoid. If a
        new chat had to be created, it simply waits in the sidebar until
        the user opens it themselves.
        """
        if self.chat._current_chat_id is not None:
            return self.chat._current_chat_id
        chat_id = db.create_chat("New chat")
        self.update_history(active_chat_id=chat_id)
        return chat_id

    @pyqtSlot()
    def _trigger_stop_automation(self) -> None:
        """
        Global-shortcut counterpart to the PyAutoGUI corner fail-safe -
        the fail-safe alone wasn't reliable as the ONLY way to stop a
        running automation (it depends on physically reaching a screen
        corner while Kestrel is mid-action, which can be awkward or slow
        mid-macro), so this gives an always-available keyboard shortcut
        that sets the exact same stop flag instantly.

        The confirmation is a screen-level toast, and the chat note goes
        to the chat the automation actually STARTED in.

        It used to call _get_or_create_target_chat_id(), which returns
        whichever chat happens to be open right now. Automations run for
        minutes and people switch chats while they wait, so "Automation
        stopped" landed in an unrelated conversation - and when no chat
        was open at all it created a brand new one purely to hold the
        note. Meanwhile the chat that ran the automation showed nothing.
        """
        ai_main.stop_automation_now()

        # Screen-level, because the stop shortcut is global: the window
        # is usually not even focused when this fires, and a bubble in a
        # background window is not confirmation of anything.
        self._show_toast(tr("Automation stopped"))

        chat_id = self._automation_chat_id
        if chat_id is None:
            # Nothing was running, or it finished already. The toast is
            # the whole response; there is no conversation this belongs
            # to, and inventing one is what the old code did.
            return

        note = "[[SYSTEM:stop|Automation stopped (stop shortcut)]]"
        db.add_message(chat_id, "assistant", note)
        if self.chat._current_chat_id == chat_id:
            self.chat._append_bubble(text=note, role="assistant")
        self.update_history(chat_id)

    def _show_toast(self, message: str, seconds: float = 3.0) -> None:
        """
        A brief status pill at the top of the screen, over whatever is
        focused.

        Reuses the voice status overlay, which is already a click-through
        always-on-top desktop widget - exactly the shape needed, and it
        avoids a second floating-window implementation. It has no
        auto-dismiss of its own, so the timer is here.
        """
        try:
            self._voice_status_overlay.show_status(message)
            QTimer.singleShot(
                int(seconds * 1000), self._voice_status_overlay.hide_status
            )
        except Exception as e:
            print(f"[toast] Couldn't show {message!r}: {e}", level="warn")

    # ── Language ─────────────────────────────────────────────────────────────

    def apply_language(self, code: str) -> None:
        """
        Switch language without restarting.

        tr() resolves once, when a widget is built, so swapping the
        catalog on its own changes nothing already on screen - which is
        why this used to say "Saved. Restart to see it applied." The
        pages therefore get rebuilt against the new catalog.

        The splash page is shown over it while that happens. Not
        decoration: rebuilding five page widgets takes long enough to
        see, and without it the window sits half-rebuilt with a mix of
        both languages on screen. It also covers the one case that
        genuinely needs a repaint of everything - switching to or from
        Arabic flips the whole layout direction.
        """
        import app.i18n as i18n

        if code == i18n.current_language():
            return

        ai_main.set_language(code)
        i18n.set_language(code)
        i18n.apply_layout_direction(QApplication.instance())

        # The splash was built at startup with whatever theme was active
        # then. Switch theme, then language, and it reappears in the old
        # one - so rebuild it in the current theme before showing it.
        self._splash_page.set_status(tr("Applying language…"))
        self._switch_shell_page(self._splash_page)
        QApplication.processEvents()

        started = time.monotonic()
        try:
            self._rebuild_pages()
        finally:
            # A floor of 0.45s, not 1.4. The rebuild itself takes a few
            # hundred milliseconds, and the two crossfades either side
            # are 320ms each, so a 1.4s floor on top made the whole
            # switch run to well over two seconds of staring at a
            # progress bar for something that had already finished.
            #
            # The floor exists only so the splash does not flash past on
            # a fast machine; it does not need to be long to do that.
            # QEventLoop rather than time.sleep so the window keeps
            # painting while it waits.
            remaining = 0.45 - (time.monotonic() - started)
            if remaining > 0:
                loop = QEventLoop()
                QTimer.singleShot(int(remaining * 1000), loop.quit)
                loop.exec()
            self._switch_shell_page(self._real_content_page)

    def _rebuild_pages(self) -> None:
        """
        Tear down and rebuild every page widget against the current
        catalog, restoring what the user was looking at.

        Rebuilding rather than walking the widget tree re-translating
        labels: a page's strings are not all in labels. They are in
        tooltips, placeholder text, menu actions, table headers, dialog
        titles and strings baked into stylesheets, and a retranslate pass
        that misses one leaves a single stale word in the old language
        for the rest of the session. Rebuilding cannot miss any.
        """
        open_chat = self.chat._current_chat_id
        page_index = self._stack.currentIndex()

        for widget in (self.chat, self.macros, self.recorder, self.settings):
            self._stack.removeWidget(widget)
            widget.deleteLater()
        self.sidebar.setParent(None)
        self.sidebar.deleteLater()
        if self.mapping is not None:
            self._stack.removeWidget(self.mapping)
            self.mapping.deleteLater()
            self.mapping = None          # rebuilt lazily by _navigate

        self.sidebar = Sidebar()
        self.chat = ChatWidget()
        self.macros = MacrosWidget()
        self.recorder = RecorderWidget()
        self.settings = SettingsWidget()

        self._stack.insertWidget(self.PAGE_CHAT, self.chat)
        self._stack.insertWidget(self.PAGE_MACROS, self.macros)
        self._stack.insertWidget(self.PAGE_RECORDER, self.recorder)
        self._stack.insertWidget(self.PAGE_SETTINGS, self.settings)
        self.sidebar_container.layout().addWidget(self.sidebar)

        # Every signal was connected to the widgets that were just
        # destroyed, so the new ones are inert until this runs again.
        self._connect_page_signals()
        self.apply_theme(self._theme)
        self._reload_macros()

        if open_chat is not None:
            # The same path the sidebar uses to open an existing chat,
            # so the rebuilt page comes back showing what it showed
            # before rather than empty.
            self._on_chat_selected(open_chat)
        else:
            self.update_history()
        self._stack.setCurrentIndex(min(page_index, self.PAGE_SETTINGS))

    def _connect_page_signals(self) -> None:
        """
        Wire every signal from the five page widgets to this window.

        Extracted from __init__ so a language switch can rebuild the
        pages and reconnect them (see _rebuild_pages). A connection is
        to a specific widget instance, so freshly built pages arrive
        completely inert - clicking anything does nothing at all - and
        the failure is silent.
        """
        # ── Sidebar → navigation connections ───────────────────────────────────
        self.sidebar.new_chat_requested.connect(self._on_new_chat)
        self.sidebar.chat_selected.connect(self._on_chat_selected)
        self.sidebar.chat_deleted.connect(self.launch_page)
        self.sidebar.chat_renamed.connect(self.load_new_title)

        # Sidebar navigation buttons
        self.sidebar.btnNewChat.clicked.connect(lambda: self._navigate(self.PAGE_CHAT))
        self.sidebar.btnActions.clicked.connect(lambda: self._navigate(self.PAGE_MACROS))
        self.sidebar.btnRecorder.clicked.connect(lambda: self._navigate(self.PAGE_RECORDER))
        self.sidebar.btnMapping.clicked.connect(lambda: self._navigate(self.PAGE_MAPPING))
        self.sidebar.btnSettings.clicked.connect(lambda: self._navigate(self.PAGE_SETTINGS))
        # chatSearchInput (the sidebar's own chat-history filter box) is
        # self-contained in Sidebar itself - nothing for MainWindow to
        # wire up. It used to be a toggle button here too (btnSearch);
        # see Sidebar.__init__'s own comment on why that's gone.

        # ── Connexions chat ────────────────────────────────────────────────────
        self.chat.chat_title_changed.connect(self._on_chat_title_changed)
        self.chat.new_message_sent.connect(self.update_history)
        self.chat.request_sent.connect(self.process_user_request)
        self.chat.mic_requested.connect(self._on_mic_requested)
        self.chat.mic_stop_speaking_requested.connect(self._on_mic_stop_speaking_requested)
        self.chat.mic_cancel_requested.connect(self._on_mic_cancel_requested)
        self.chat.confirmation_response.connect(self._on_confirmation_response)
        self.chat.human_handoff_response.connect(self._on_human_handoff_response)
        self.chat.speak_requested.connect(self._speak_response)
        self.chat.stop_read_aloud_requested.connect(self._on_stop_read_aloud_requested)
        self.chat.regenerate_requested.connect(self._on_regenerate_requested)
        self.chat.profile_switched.connect(self._on_profile_switched_from_chat)

        # ── Connexions macros ──────────────────────────────────────────────────
        self.macros.macro_run_requested.connect(self._on_macro_run)
        self.macros.macro_delete_requested.connect(self._on_macro_delete_requested)
        self.macros.macro_edit_requested.connect(self._on_macro_edit_requested)
        # Imported macros are registered by the Macros page itself
        # (refresh_actions), but the CARDS still come from here - same
        # reload the recorder triggers after saving one.
        self.macros.macro_imported.connect(self._reload_macros)
        self.macros.btnRefresh.clicked.connect(self._reload_macros)
        self.macros.btnRecordMacro.clicked.connect(lambda: self._navigate(self.PAGE_RECORDER))

        # ── Connexions recorder ─────────────────────────────────────────────────
        self.recorder.macro_saved.connect(self._reload_macros)

        # ── Connexions settings ─────────────────────────────────────────────────
        # These lived outside this method, in __init__ alongside the
        # wake-word worker's connections. That meant _rebuild_pages built
        # a fresh SettingsWidget and never reconnected it: changing the
        # language once worked, and every change after that did nothing
        # at all, because the signal was still wired to the page that had
        # been destroyed.
        self.settings.wake_word_toggled.connect(self._on_wake_word_setting_toggled)
        self.settings.assistant_name_changed.connect(self._on_assistant_name_changed)
        self.settings.shortcut_change_requested.connect(self._on_shortcut_change_requested)
        self.settings.theme_changed.connect(self.apply_theme)
        self.settings.language_changed.connect(self.apply_language)


    def _apply_tray_theme(self, theme_name: str) -> None:
        """
        Re-tint the tray menu and its icons.

        Built once at startup and never revisited, so after a theme
        switch the menu kept the palette it was born with. The icons are
        the part that actually breaks: they are baked bitmaps, so a
        stylesheet cannot recolour them and they have to be rebuilt.
        """
        if not hasattr(self, "_tray_menu"):
            return
        c = theme.colors(theme_name)
        try:
            self._tray_menu.setStyleSheet(_tray_menu_style(c))
            for name, action in getattr(self, "_tray_actions", {}).items():
                action.setIcon(icons.icon(name, c))
        except RuntimeError:
            return          # the tray was torn down (quit in progress)

    def _bind_escape_abort(self) -> None:
        """
        Register Escape as an abort for a running automation.

        Uses a plain hook rather than add_hotkey with suppression, so
        Escape keeps working normally everywhere else - suppressing it
        globally would break dialogs, menus and full-screen exits in
        every other application on the machine for as long as Kestrel
        runs, which is a far worse bargain than the convenience is
        worth.
        """
        try:
            keyboard.on_press_key("esc", self._abort_on_escape, suppress=False)
            print("[shortcut] Esc will stop a running automation.")
        except Exception as e:
            # Non-fatal: Ctrl+Alt+X and the corner fail-safe both still
            # work, and this is the third route to the same flag.
            print(f"[shortcut] Couldn't bind Esc to abort: {e}", level="warn")

    def _abort_on_escape(self, _event=None) -> None:
        """
        Stop the automation, but only if one is running.

        The guard is what makes a bare Escape acceptable as a global
        hook. With nothing running this returns immediately and the key
        press goes on to whatever the user was actually doing; the toast
        and the chat note only happen when there was something to stop.
        """
        if not self._automation_is_running():
            return
        QMetaObject.invokeMethod(
            self, "_trigger_stop_automation", Qt.ConnectionType.QueuedConnection
        )

    def _automation_is_running(self) -> bool:
        """Whether anything is currently driving the machine: an
        autonomous run, a macro, or a UI mapping pass."""
        if self._automation_chat_id is not None:
            return True
        for attr in ("_macro_thread", "_mapping_thread"):
            thread = getattr(self, attr, None)
            try:
                if thread is not None and thread.isRunning():
                    return True
            except RuntimeError:
                continue          # the QThread was already deleted
        return False

    def register_global_shortcuts(self) -> None:
        """
        Binds every configured global shortcut via the `keyboard` library
        (toggle_window, always_on_top, screenshot, stop_automation). Called
        once at startup (see main.py). Safe to call again later -
        rebind_shortcut() below is what Settings actually uses to change
        one without restarting the app.
        """
        self._bound_shortcuts: dict[str, str] = {}
        bindings = (
            ("toggle_window", self._toggle_window),
            ("always_on_top", self._toggle_always_on_top),
            ("screenshot", self._trigger_screenshot_shortcut),
            ("stop_automation", self._trigger_stop_automation),
        )
        # Esc as a second, unconfigurable route to the same stop.
        #
        # Not in the bindings table above because it is deliberately not
        # rebindable and deliberately not a combo: when an automation is
        # driving the mouse and typing into another application, the
        # thing anyone reaches for is Escape, and expecting them to
        # remember Ctrl+Alt+X while their cursor is being moved for them
        # is the wrong bet. It only does anything while something is
        # actually running (see _abort_on_escape), so it does not
        # interfere with Escape in other applications the rest of the
        # time.
        self._bind_escape_abort()
        # Every result is logged, and each binding is isolated from the
        # others. _bind_shortcut reports failure by RETURNING (False,
        # why) - which this used to discard, so a shortcut that never
        # registered (combo already held by another app, an unparseable
        # combo, the keyboard hook unavailable) looked exactly like one
        # that did: nothing in the console, nothing in the UI, it just
        # didn't respond. "All the shortcuts stopped working" is not a
        # thing that should have to be diagnosed by guesswork.
        for shortcut_id, callback in bindings:
            combo = ai_main.get_shortcut(shortcut_id)
            try:
                ok, message = self._bind_shortcut(shortcut_id, combo, callback)
            except Exception as e:  # noqa: BLE001 - one bad binding must not skip the rest
                ok, message = False, f"{type(e).__name__}: {e}"
            if ok:
                print(f"[shortcuts] {shortcut_id} → {combo or '(disabled)'}")
            else:
                print(f"[shortcuts] {shortcut_id} FAILED to bind to {combo!r}: {message}")

    def _bind_shortcut(self, shortcut_id: str, combo: str, callback) -> tuple[bool, str]:
        """Binds one shortcut, unbinding whatever this shortcut_id was
        previously bound to first. Returns (success, message)."""
        import keyboard

        old_combo = self._bound_shortcuts.get(shortcut_id)
        if old_combo:
            try:
                keyboard.remove_hotkey(old_combo)
            except (KeyError, ValueError):
                pass  # wasn't actually bound (e.g. registration had failed) - fine

        if not combo:
            self._bound_shortcuts.pop(shortcut_id, None)
            return True, "Shortcut disabled."

        try:
            keyboard.add_hotkey(
                combo,
                lambda: QMetaObject.invokeMethod(self, callback.__name__, Qt.ConnectionType.QueuedConnection),
            )
            self._bound_shortcuts[shortcut_id] = combo
            return True, f"Shortcut active: {combo}"
        except Exception as e:
            # Restore the previous binding rather than leaving the app with
            # no hotkey at all after a failed rebind attempt.
            if old_combo:
                self._bind_shortcut(shortcut_id, old_combo, callback)
            return False, f"Invalid combination, or already used by another app: {e}"

    def rebind_shortcut(self, shortcut_id: str, combo: str) -> tuple[bool, str]:
        """
        Called from the Settings page when the user saves a new key combo
        or (for recorder_* ids) captures a new trigger. Persists it (so it
        survives a restart) and, for the four app-level global shortcuts,
        also applies it immediately via the `keyboard` library.

        Recorder triggers (ai_main.RECORDER_SHORTCUT_IDS) are NOT bound
        here at all - they're not app-level global hotkeys, they're read
        directly by RecorderController.start() each time recording
        begins (see that class), and can be a mouse button, which
        keyboard.add_hotkey has no way to express anyway. Persisting is
        everything needed for those.
        """
        if shortcut_id in ai_main.RECORDER_SHORTCUT_IDS:
            ai_main.set_shortcut(shortcut_id, combo)
            return True, f"Trigger set: {combo}"

        callback = {
            "toggle_window": self._toggle_window,
            "always_on_top": self._toggle_always_on_top,
            "screenshot":    self._trigger_screenshot_shortcut,
            "stop_automation": self._trigger_stop_automation,
        }.get(shortcut_id)
        if callback is None:
            return False, "Unknown shortcut."
        ok, message = self._bind_shortcut(shortcut_id, combo, callback)
        if ok:
            ai_main.set_shortcut(shortcut_id, combo)
        return ok, message

    def _on_shortcut_change_requested(self, shortcut_id: str, combo: str) -> None:
        ok, message = self.rebind_shortcut(shortcut_id, combo)
        self.settings.set_shortcut_status(shortcut_id, message, ok=ok)

    # ── Theme ───────────────────────────────────────────────────────────────────

    def _apply_window_chrome_theme(self, theme_name: str) -> None:
        """Re-skins the few widgets app/ui/main_window.ui sets a LOCAL styleSheet on directly - see _build_window_chrome_style's docstring for why each needs its own call rather than one setStyleSheet on self."""
        c = theme.colors(theme_name)
        self.setStyleSheet(_build_window_chrome_style(c))
        self.centralwidget.setStyleSheet(f"background-color: {c['bg_base']};")
        self.sidebar_container.setStyleSheet(
            f"background-color: {c['bg_base']}; border-right: 1px solid {c['bg_input']};"
        )
        self.chat_container.setStyleSheet(f"background-color: {c['bg_base']};")
        # Built after this method's first call, in the constructor, so a
        # theme applied before the chrome exists must not crash.
        if hasattr(self, "_chrome"):
            self._chrome.set_theme(theme_name)

    # ── Default geometry ─────────────────────────────────────────────────────

    # 16:10, which is the shape the sidebar-plus-conversation layout
    # actually wants. The .ui file carries the same numbers as its
    # design-time geometry; these are what get applied at runtime.
    DEFAULT_WIDTH = 1280
    DEFAULT_HEIGHT = 800

    # Never take more than this share of the screen when the preferred
    # size does not fit. Leaves room for the taskbar to stay reachable
    # and for the window to read as a window rather than as the desktop.
    MAX_SCREEN_FRACTION = 0.92

    # Below this the three-column-ish layout stops working: the 260px
    # sidebar plus a conversation column plus its margins.
    MIN_WIDTH = 900
    MIN_HEIGHT = 560

    def _apply_default_geometry(self) -> None:
        """
        Size the window to 1280x800, or to whatever fraction of the
        screen actually fits, and centre it.

        The .ui file's design-time geometry was applied verbatim, which
        is fine on a 1080p display and wrong on anything shorter. On a
        1366x768 laptop - still the second most common desktop
        resolution - an 800px-tall window is taller than the 728px of
        usable height once the taskbar is accounted for, so it opened
        with its bottom edge, and therefore the chat input, underneath
        the taskbar or off-screen entirely. That reads as "excessive
        vertical height" because from the user's side that is exactly
        what it is.

        availableGeometry, not geometry: it already excludes the taskbar
        and any other reserved edge, which is the whole difficulty here.
        """
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:                       # headless; nothing to fit to
            self.resize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
            return

        available = screen.availableGeometry()
        width = min(self.DEFAULT_WIDTH, int(available.width() * self.MAX_SCREEN_FRACTION))
        height = min(self.DEFAULT_HEIGHT, int(available.height() * self.MAX_SCREEN_FRACTION))

        # A floor as well as a ceiling. On a very small screen the
        # fraction above could produce something unusable, and a window
        # slightly larger than the screen is a better failure than one
        # too narrow to lay out.
        width = max(width, self.MIN_WIDTH)
        height = max(height, self.MIN_HEIGHT)

        self.setMinimumSize(
            min(self.MIN_WIDTH, width), min(self.MIN_HEIGHT, height)
        )
        self.resize(width, height)
        self.move(
            available.x() + (available.width() - width) // 2,
            available.y() + (available.height() - height) // 2,
        )

    # ── Frameless window plumbing ────────────────────────────────────────────

    def resizeEvent(self, event) -> None:
        """Keep the invisible resize grips pinned to the new edges."""
        super().resizeEvent(event)
        if hasattr(self, "_chrome"):
            self._chrome.reposition_grips()

    def _apply_rounded_corners(self) -> None:
        """
        Ask Windows to round the window's corners itself.

        The first attempt at this used setMask with a rounded path, and
        the corners came out visibly stair-stepped. A window mask is a
        one-bit region: a pixel is either in the window or out of it,
        with no partial coverage, so a curve through it cannot be
        antialiased. It is the wrong tool for a rounded corner and there
        is no way to make it look clean.

        DWMWA_WINDOW_CORNER_PREFERENCE is what Windows 11 uses for its
        own windows. The compositor draws the rounding, so it is
        properly antialiased, it matches the radius every other window
        on the system uses, and the drop shadow survives - all three of
        which the mask lost.

        On Windows 10 the attribute does not exist and the call fails
        harmlessly, leaving square corners. That is the right outcome
        rather than a fallback worth writing: Windows 10's own windows
        have square corners, so the app matches its platform either way.

        Nothing to do on maximize any more either. The compositor
        already drops the rounding when a window fills the screen, which
        is the behaviour the mask had to special-case by hand.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2

            preference = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(int(self.winId())),
                ctypes.c_uint(DWMWA_WINDOW_CORNER_PREFERENCE),
                ctypes.byref(preference),
                ctypes.sizeof(preference),
            )
        except Exception as e:
            # Windows 10, or dwmapi unavailable. Square corners, which
            # is what that platform looks like anyway.
            log_debug(f"[chrome] Native corner rounding unavailable: {e}")

    def changeEvent(self, event) -> None:
        """
        Track maximize state the window did not hear about from its own
        button: Aero Snap, Win+Up, double-clicking the bar, or the
        taskbar's own restore. Without this the glyph would say
        "maximize" on an already-maximized window, and the resize grips
        would stay live along edges that no longer have anywhere to go.
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if hasattr(self, "_chrome"):
                self._chrome.sync()

    def apply_theme(self, theme_name: str) -> None:
        """
        Propagates a theme switch to every child widget/page. The
        SettingsWidget itself already re-skinned and persisted (see
        SettingsWidget._on_theme_combo_changed) before this fires - this
        just fans the change out to everything else, so switching themes
        takes effect everywhere at once, live, no restart needed.
        """
        self._theme = theme_name
        self._apply_window_chrome_theme(theme_name)
        self.sidebar.set_theme(theme_name)
        self.chat.set_theme(theme_name)
        self.macros.set_theme(theme_name)
        self.recorder.set_theme(theme_name)
        if self.mapping is not None:
            self.mapping.set_theme(theme_name)
        # No guard. It used to skip this when settings._theme already
        # matched, which _on_theme_combo_changed always made true before
        # emitting - so this never ran. set_theme is idempotent; calling
        # it twice costs one stylesheet rebuild and is the safe side to
        # err on.
        self.settings.set_theme(theme_name)
        self._apply_tray_theme(theme_name)

    def closeEvent(self, event: QEvent) -> None:
        if hasattr(self, "tray_icon") and self.tray_icon.isVisible():
            event.ignore()
            self.hide()
            if self._overlay_active:
                self._overlay.hide()
        else:
            event.accept()

    # ── Business logic ─────────────────────────────────────────────────────────

    def update_history(self, active_chat_id: int):
        self.sidebar.refresh_history(active_chat_id)

    def launch_page(self):
        chats = db.get_all_chats()
        if chats:
            self._on_chat_selected(chats[0]["id"])
        else:
            self._on_new_chat()

    def _on_new_chat(self) -> None:
        self.show_window()
        self._navigate(self.PAGE_CHAT)
        chat_id = db.create_chat("New chat")
        self.update_history(active_chat_id=chat_id)
        self.chat.start_new_chat(chat_id)

    def _on_chat_selected(self, chat_id: int):
        self._navigate(self.PAGE_CHAT)
        # _navigate() marks btnNewChat as the "active" nav button for
        # ANY chat-page navigation (it doesn't know why it's happening)
        # right here that's wrong: this is loading an EXISTING chat, not
        # creating one, so "New chat" shouldn't look selected. The
        # chat's own row in the history list is the correct active
        # indicator (set_active_chat, right below), not this button.
        self.sidebar.btnNewChat.setChecked(False)
        self.sidebar.set_active_chat(chat_id)
        self.chat.load_chat(chat_id)

    def load_new_title(self, new_title: str):
        self.chat.load_title(title=new_title)

    def _on_chat_title_changed(self, chat_id: int, title: str):
        self.sidebar.update_chat_title_in_list(chat_id, title)
        self.update_history(active_chat_id=chat_id)

    def _quit_app(self) -> None:
        self.deactivate_overlay()
        if hasattr(self, "tray_icon"):
            self.tray_icon.hide()
        QApplication.quit()

    # ── Logique IA ─────────────────────────────────────────────────────────────

    def process_user_request(self, chat_id, text, from_voice: bool = False):
        self.chat._show_typing(chat_id)

        self.thread = QThread()
        self.worker = AIWorker()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(lambda: self.worker.start.emit(chat_id, text))

        self._toggle_overlay()
        # from_voice is captured here (per-request, via closure) rather
        # than tracked as shared mutable state - a second request for a
        # different chat could otherwise start before this one's signals
        # finish, which a single shared "is this voice" flag can't
        # distinguish between.
        self.worker.finished.connect(lambda cid, resp: self.on_ai_response(cid, resp, from_voice=from_voice))
        self.worker.error.connect(self.on_ai_error)
        self.worker.autonomous_suggested.connect(
            lambda cid, goal: self._on_autonomous_suggested(cid, goal, from_voice=from_voice)
        )
        self.worker.screenshot_captured.connect(self._on_screenshot_captured)
        self.worker.terminal_output_captured.connect(self._on_terminal_output_captured)
        self.worker.file_output_captured.connect(self._on_file_output_captured)
        # Only fires when Settings > Stream responses is on (see
        # AIWorker.run) - begins the streaming bubble on the first
        # chunk for this chat, then just appends after that.
        self.worker.chunk.connect(self._on_ai_chunk)
        # Only fires when streaming AND Settings > Extended thinking
        # are both on, for a model that actually produces any reasoning
        # text (Claude, Gemini, and GPT-5 reasoning models all do - see
        # AIWorker.thinking_chunk).
        self.worker.thinking_chunk.connect(self._on_ai_thinking_chunk)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.error.connect(self.thread.quit)
        self.worker.error.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self._track_thread(self.thread)
        self.thread.start()

    def _on_ai_chunk(self, chat_id: int, delta: str) -> None:
        if not self.chat.is_streaming_response(chat_id):
            # First chunk for this chat_id - open the streaming bubble
            # now that we know a real reply is actually on its way
            # (rather than up front in process_user_request, which
            # would open an empty bubble even for a request that errors
            # out before the provider ever sends anything back).
            self.chat.begin_streaming_response(chat_id)
        self.chat.append_stream_chunk(chat_id, delta)

    def _on_ai_thinking_chunk(self, chat_id: int, delta: str) -> None:
        """
        Same lazy-open idea as _on_ai_chunk, but for the "Thinking…"
        section above the reply bubble - see
        ChatWidget.begin_thinking_block. Every provider that produces
        thinking text sends it before any answer text for that same
        turn, so in practice this opens the block first and
        _on_ai_chunk's begin_streaming_response only runs once the
        model moves on to its actual answer - but each guards its own
        lazy-open independently rather than assuming that ordering.
        """
        if not self.chat.is_streaming_thinking(chat_id):
            self.chat.begin_thinking_block(chat_id)
        self.chat.append_thinking_chunk(chat_id, delta)

    def _on_screenshot_captured(self, chat_id: int, path: str) -> None:
        """
        Drops a screenshot thumbnail bubble into the chat for a screenshot
        the AI itself took mid-conversation (tool_screenshot), the same
        way the global screenshot shortcut already does - so it's a
        permanent part of the message history (persisted to the DB, shown
        inline like any other message), not a transient toast that
        disappears. Emitted before `finished` (see AIWorker.run), so it
        lands ahead of the assistant's text reply about it.

        Regular chat mode ONLY (self.worker) - NOT wired to
        self._auto_worker. During autonomous mode, tool_screenshot() is
        just Kestrel looking at the screen mid-task; run_autonomous_mode
        drains and discards it rather than surfacing it here (see the
        comment above the pop_pending_screenshot_path() call in that
        loop). Showing it as a chat bubble would just spam a thumbnail
        for every "check the screen" step of an automation run - the
        thumbnail treatment is for the manual/global-shortcut screenshot
        a human takes to hand Kestrel context, which is what this handler
        is actually for.
        """
        marker = f"[[SCREENSHOT:{path}]]"
        db.add_message(chat_id, "assistant", marker)
        if self.chat._current_chat_id == chat_id:
            self.chat._append_bubble(text=marker, role="assistant")
        self.update_history(chat_id)

    def _on_terminal_output_captured(self, chat_id: int, command: str, output: str, success: bool) -> None:
        """
        Drops a terminal-styled block into the chat for a command the AI
        actually ran via tool_run_command mid-conversation, showing the
        real command + its real combined stdout/stderr - not just
        whatever the model chose to paraphrase about it in its text
        reply. Same ordering/persistence treatment as
        _on_screenshot_captured right above (permanent DB history, not a
        transient toast; emitted before `finished` so it lands ahead of
        the assistant's own reply) - see that method's docstring for why.

        Regular chat mode ONLY (self.worker) - NOT wired to
        self._auto_worker, same reasoning as the screenshot handler:
        during autonomous mode a command is just Kestrel working through a
        task step by step, not a human asking to see a terminal in a
        live chat. run_autonomous_mode already drains (and discards) any
        queued terminal output of its own for exactly this reason - see
        the pop_pending_terminal_output() calls in that loop.
        """
        marker = chat_widget.encode_terminal_marker(command, output, success)
        db.add_message(chat_id, "assistant", marker)
        if self.chat._current_chat_id == chat_id:
            self.chat._append_bubble(text=marker, role="assistant")
        self.update_history(chat_id)

    def _on_file_output_captured(self, chat_id: int, path: str, action: str) -> None:
        """
        Drops a "Show in folder" card into the chat for a file the AI
        just wrote via tool_generate_file / tool_merge_files_to_pdf, so
        the person can jump straight to it in File Explorer instead of
        hunting for the path in the assistant's reply. Same
        ordering/persistence treatment as _on_terminal_output_captured
        (stored in the DB as a marker so it survives a chat reload;
        emitted before `finished` so it lands ahead of the text reply),
        and the same regular-chat-only scope: run_autonomous_mode drains
        and discards queued file entries rather than surfacing them here.
        """
        marker = chat_widget.encode_file_marker(path, action)
        db.add_message(chat_id, "assistant", marker)
        if self.chat._current_chat_id == chat_id:
            self.chat._append_bubble(text=marker, role="assistant")
        self.update_history(chat_id)

    def on_ai_response(self, chat_id, response, from_voice: bool = False):
        """
        Regression note: _hide_typing used to run only AFTER
        db.add_message() succeeded - if that raised (a DB lock, an
        unexpected value in `response`, ...), the typing indicator never
        got cleared at all. Since this runs as a slot on a queued signal
        (from AIWorker's background thread), an uncaught exception here
        doesn't crash the app outright - Qt just prints the traceback
        and moves on - so the visible symptom was a permanently stuck
        "..." indicator with no further response ever appearing, which
        reads as "it crashed" even though the process was still alive.
        Hiding it FIRST, unconditionally, is what guarantees that no
        matter what happens below, the indicator is already gone - same
        visual ordering as before for the normal success case,
        just no longer gated on db.add_message succeeding first.
        """
        self.chat._hide_typing(chat_id)
        new_id = db.add_message(chat_id, "assistant", response)
        if self.chat.is_streaming_thinking(chat_id):
            # Whether or not any actual answer text ever streamed
            # (finish_streaming_response below only runs for the
            # latter) - a thinking block left open with no matching
            # answer would otherwise stay stuck on "Thinking…" forever.
            self.chat.finish_thinking_block(chat_id)
        if self.chat.is_streaming_response(chat_id):
            # A stream was actually live for this reply - correct the
            # already-growing bubble to the authoritative final text
            # instead of animating a second, redundant typewriter
            # reveal on top of what already streamed in.
            self.chat.finish_streaming_response(chat_id, response)
        else:
            # Streaming was off (or errored out before a single chunk
            # arrived) - fall back to the local typewriter effect, same
            # as before streaming existed at all.
            self.chat.reveal_response(chat_id, response)
        # The bubble reveal_response/finish_streaming_response just
        # built was constructed before this reply had a DB id (neither
        # knows it yet at that point - see attach_message_id's own
        # docstring) - stamp it on now so its Regenerate button (and
        # any earlier reply's losing leaf status) resolve correctly.
        self.chat.attach_message_id(chat_id, new_id)
        self._toggle_overlay()
        if from_voice:
            # Explicitly the voice-mode path, which is what turns on the
            # mic-button Stop affordance, the overlay and the "listen for
            # 'stop'" mode. Read aloud reaches _speak_response without
            # this flag and gets none of them.
            self._speak_response(chat_id, response, from_voice=True)

    def on_ai_error(self, chat_id, error):
        """Same fix as on_ai_response: hide the typing indicator FIRST, unconditionally - this had the identical ordering bug (db.add_message could raise before _hide_typing ever ran), just on the error path instead of the success one."""
        self.chat._hide_typing(chat_id)
        db.add_message(chat_id, "assistant", error)
        if self.chat.is_streaming_thinking(chat_id):
            self.chat.finish_thinking_block(chat_id)
        if self.chat.is_streaming_response(chat_id):
            # Extremely unlikely (ai_stream() catches its own provider
            # errors and returns them as normal response text, same as
            # ai()) but cheap to guard: a stream bubble left open by a
            # genuinely unexpected exception shouldn't linger stuck
            # mid-reply with no error text ever reaching it. Corrects
            # the already-open bubble in place instead of also opening
            # a second one below.
            self.chat.finish_streaming_response(chat_id, f"AI error: {error}")
        elif self.chat._current_chat_id == chat_id:
            self.chat._append_bubble(role="assistant", text=f"AI error: {error}")
        self._toggle_overlay()

    # ── Regenerating an assistant reply ────────────────────────────────────────

    def _on_regenerate_requested(self, chat_id: int, message_id: int) -> None:
        """
        Re-asks the AI for a fresh reply to the same preceding user
        message that `message_id` (the chat's CURRENT last message - the
        only thing ChatWidget ever offers this for, see
        _wire_message_actions' is_leaf check) already answered. The
        new reply is persisted as a SIBLING of the old one (an explicit
        parent_id passed to db.add_message - see chat_database.py's
        branching-model comment) rather than a reply after it: the old
        reply stays in the DB as a switchable-back-to branch via the
        same ‹ › selector edited user messages already get, exactly
        like db.branch_message already does for those - that function
        just isn't reusable here as-is since its new content isn't
        known yet at click-time, only once the AI actually answers.

        Deliberately doesn't reuse process_user_request's live
        streaming/typewriter wiring: those animate a reply being
        APPENDED to the chat, but this one REPLACES an existing bubble
        at its position (the old reply stops being the active branch
        the moment the new one is persisted) - a brief typing
        indicator during the wait, then a full chat.load_chat() once
        the new reply lands, is simpler and correct where reusing the
        append-only streaming path wouldn't be.
        """
        original = db.get_message(message_id)
        if original is None or original["parent_id"] is None:
            return  # an assistant reply always has a parent user message under this model - fail soft if that's somehow not true
        parent = db.get_message(original["parent_id"])
        if parent is None:
            return

        self.chat._show_typing(chat_id)

        thread = QThread()
        worker = AIWorker()
        worker.moveToThread(thread)
        thread.started.connect(lambda: worker.start.emit(chat_id, parent["content"]))
        worker.finished.connect(lambda cid, resp: self._on_regenerate_finished(cid, resp, original["parent_id"]))
        worker.error.connect(self._on_regenerate_error)
        # Regenerating never triggers autonomous-mode suggestions or a
        # fresh screenshot capture the way a normal turn can - those
        # signals exist on AIWorker for process_user_request's benefit,
        # not left unconnected here, they're simply not relevant to
        # "re-ask the same question again".
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(thread.quit)
        worker.error.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._track_thread(thread)
        thread.start()

    def _on_regenerate_finished(self, chat_id: int, response: str, parent_id: int) -> None:
        self.chat._hide_typing(chat_id)
        db.add_message(chat_id, "assistant", response, parent_id=parent_id)
        if self.chat._current_chat_id == chat_id:
            self.chat.load_chat(chat_id)
        self.update_history(chat_id)

    def _on_regenerate_error(self, chat_id: int, error: str) -> None:
        """
        Doesn't touch the branching tree at all - the old reply is
        still the active one (regeneration never got far enough to
        persist a replacement), so there's no sibling to create. Just
        surfaces the error as a normal new turn after it, same as
        on_ai_error's own non-streaming fallback, and leaves the user
        free to try Regenerate again.
        """
        self.chat._hide_typing(chat_id)
        db.add_message(chat_id, "assistant", f"AI error: {error}")
        if self.chat._current_chat_id == chat_id:
            self.chat._append_bubble(role="assistant", text=f"AI error: {error}")
        self.update_history(chat_id)

    # ── Autonomous mode (suggested by the AI, confirmed by the user) ──────────

    def _on_autonomous_suggested(self, chat_id: int, goal: str, from_voice: bool = False) -> None:
        # Inline card in the chat itself (not a modal popup) so the user
        # can see exactly what's about to run without leaving the
        # conversation. `from_voice` - set only when the request that
        # produced this suggestion itself came through voice mode - also
        # switches the wake-word listener from "Kestrel" over to just
        # "accept"/"refuse" for as long as this card is pending, so a
        # voice-originated request can be answered by voice too, with no
        # need to touch the mouse or repeat the wake word.
        self._pending_autonomous_goal = (chat_id, goal)
        # Confirmation listening is on whenever a card is pending, not
        # only for suggestions that themselves came from voice.
        #
        # Gating it on from_voice meant the common case - type a request,
        # get a suggestion - could not be answered by voice at all, which
        # is the "oral commands don't work" report. The listener is
        # already running either way, the card is already on screen, and
        # there is no reason the answer has to arrive by the same channel
        # the question did. It costs nothing: set_confirmation_mode is a
        # flag, and it is cleared the moment the card is answered by any
        # means.
        self._wakeword_worker.set_confirmation_mode(True)
        self.chat.show_confirmation(chat_id, goal, voice_hint=True)

    def _on_risk_approval_needed(self, description: str, out: list) -> None:
        """
        Connected to ai.risk_gate's bridge via BlockingQueuedConnection
        - runs on this (the UI) thread, blocking whichever background
        worker thread called ai.risk_gate.require_approval() until this
        returns. A real modal dialog (not an inline chat card, unlike
        the autonomous-mode confirmation above) since this can fire
        while any page is open, not just the chat page, and needs to be
        impossible to miss for a HIGH-risk action.
        """
        box = QMessageBox(self)
        # The one dialog in the app that blocks a worker thread waiting
        # on a yes, for an action that deletes or closes something - and
        # the only one that was left in Qt's default chrome.
        box.setStyleSheet(_confirm_box_style(theme.colors(self._theme)))
        box.setWindowTitle(tr("Approve this action?"))
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(description)
        box.setInformativeText(
            f"{ai_main.get_assistant_name()} flagged this as a high-risk action (deletes, closes an app, "
            "or otherwise changes something that isn't easily undone)."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        answer = box.exec()
        out[0] = (answer == QMessageBox.StandardButton.Yes)

    def _on_confirmation_response(self, chat_id: int, accepted: bool) -> None:
        # Reached either by clicking a button on the card, or by
        # WakeWordWorker hearing "accept"/"refuse" (see
        # _on_confirmation_word_detected) - both funnel through
        # ChatWidget.confirmation_response, so this is the one place that
        # actually starts (or drops) the suggested task.
        self._wakeword_worker.set_confirmation_mode(False)
        pending = self._pending_autonomous_goal
        self._pending_autonomous_goal = None
        if accepted and pending is not None and pending[0] == chat_id:
            self._start_autonomous_mode(chat_id, pending[1])

    def _on_confirmation_word_detected(self, accepted: bool) -> None:
        applied = self.chat.respond_to_pending_confirmation(accepted)
        if not applied:
            # Heard "accept"/"refuse" but nothing was actually pending
            # (e.g. the card was already answered by click a moment
            # earlier) - just stop listening for those words, nothing
            # else to do.
            self._wakeword_worker.set_confirmation_mode(False)

    def _start_autonomous_mode(self, chat_id: int, goal: str) -> None:
        # Which chat this run belongs to, for as long as it runs. The
        # stop shortcut fires from anywhere, often minutes later and
        # often with a different chat open, and without this it had no
        # way to find the conversation the automation actually came from
        # - so it wrote "Automation stopped" into whatever was on screen.
        self._automation_chat_id = chat_id
        self.chat._show_typing(chat_id)
        self._toggle_overlay()

        self._auto_thread = QThread()
        self._auto_worker = AutonomousWorker()
        self._auto_worker.moveToThread(self._auto_thread)
        self._auto_thread.started.connect(lambda: self._auto_worker.start.emit(chat_id, goal))

        self._auto_worker.finished.connect(self._on_autonomous_finished)
        self._auto_worker.error.connect(self._on_autonomous_error)
        self._auto_worker.human_handoff_needed.connect(self._on_human_handoff_needed)
        self._auto_worker.network_retry.connect(self._on_autonomous_network_retry)
        self._auto_worker.finished.connect(self._auto_thread.quit)
        self._auto_worker.finished.connect(self._auto_worker.deleteLater)
        self._auto_worker.error.connect(self._auto_thread.quit)
        self._auto_worker.error.connect(self._auto_worker.deleteLater)
        self._auto_thread.finished.connect(self._auto_thread.deleteLater)
        self._track_thread(self._auto_thread)
        self._auto_thread.start()

    def _on_human_handoff_needed(self, chat_id: int, reason: str) -> None:
        """
        Tier 2 of the automation pipeline: run_autonomous_mode paused
        because the model recognized a CAPTCHA/MFA/verification challenge
        (see ai.actions.tool_request_human_handoff) and is waiting for a
        human. Shows a "Continue" card in the chat, played a sound
        already (ai_main.play_notification_sound, from inside
        run_autonomous_mode) - this just handles the visible side.
        """
        note = f"{ai_main.get_assistant_name()} needs you: {reason}"
        db.add_message(chat_id, "assistant", note)
        # Always recorded, even if this isn't the chat currently open
        # otherwise switching to it later would show the note above as
        # inert history text with no way to actually resume the run
        # (see ChatWidget._pending_handoff_chat_id).
        self.chat.note_pending_human_handoff(chat_id, reason)
        if self.chat._current_chat_id == chat_id:
            self.chat.show_human_handoff(chat_id, reason)
        self.update_history(chat_id)

    def _on_human_handoff_response(self, chat_id: int) -> None:
        ai_main.confirm_human_handoff_resolved()

    def _on_autonomous_network_retry(self, chat_id: int, attempt: int, wait_seconds: int) -> None:
        """
        A step's Gemini call failed with a connectivity-shaped error and
        run_autonomous_mode is retrying with backoff instead of just
        burning through max_steps (see ai.main._retry_send_message_after_network_error).
        Updates the typing indicator's text in place - already showing
        for the whole autonomous run (see _start_autonomous_mode) - so
        the run visibly looks like it's reconnecting rather than stalled.
        Purely cosmetic if this isn't the currently-open chat.
        """
        if self.chat._current_chat_id == chat_id:
            self.chat.set_typing_label(f"Connection lost - retrying in {wait_seconds}s (attempt {attempt})…")

    def _on_autonomous_finished(self, chat_id: int, outcome: dict = None) -> None:
        """
        Report what the run actually did.

        This said "Task completed in autonomous mode." unconditionally,
        which was wrong for every run that did not complete: one that was
        stopped, one that burned its whole step budget without finishing,
        one that gave up after three steps with no response from the
        model. run_autonomous_mode knew which of those happened the whole
        time - it computed an `outcome` on every exit path and then
        returned None, so none of it reached here.
        """
        self._automation_chat_id = None
        self.chat._hide_typing(chat_id)
        self.chat.clear_pending_human_handoff(chat_id)
        # The suggestion card that started this run has served its
        # purpose; leaving it behind puts a stale prompt in the
        # transcript under the work it produced.
        self.chat.clear_confirmation_card(chat_id)
        self._toggle_overlay()

        outcome = outcome or {}
        message = outcome.get("summary") or "Finished in autonomous mode."

        # A toast, not a chat message. Completion is a status report
        # about a background job, not something the assistant said - and
        # writing it into the transcript meant every autonomous run left
        # a permanent "Task completed" bubble sitting in the
        # conversation, cluttering it and, worse, becoming part of the
        # history the model reads back on the next turn.
        #
        # Nothing is written to the database. If the run produced
        # something worth keeping - a macro - that macro is the artifact,
        # and it is on the Macros page.
        self._show_toast(message, seconds=5.0)
        self._refresh_macro_cards()

    def _on_autonomous_error(self, chat_id: int, error: str) -> None:
        self._automation_chat_id = None
        self.chat._hide_typing(chat_id)
        self.chat.clear_confirmation_card(chat_id)
        self.chat.clear_pending_human_handoff(chat_id)
        self._toggle_overlay()
        message = f"Error in autonomous mode: {error}"
        db.add_message(chat_id, "assistant", message)
        if self.chat._current_chat_id == chat_id:
            self.chat._append_bubble(role="assistant", text=message)

    # ── Voice assistant (mic button in the input bar) ─────────────────────────
    #
    # Flow: mic click → VoiceWorker records+transcribes only → the
    # transcript is written into the chat's text input and sent through
    # the exact same path a typed message takes (ChatWidget._send_message,
    # voice_reply=True) → the normal AIWorker produces the reply →
    # on_ai_response(from_voice=True) hands it to SpeakWorker for TTS.
    # See voice_worker.py and speak_worker.py for why the two are split.

    def _on_mic_requested(self, chat_id: int) -> None:
        # Reachable from two independent places - the mic button
        # (ChatWidget._on_mic_clicked, guarded by its own _mic_state) and
        # wake-word detection (_on_wake_word_detected, which calls this
        # directly and has NO knowledge of the button's state machine).
        # Without a guard *here*, a wake-word trigger arriving while a
        # manual recording is already underway (plausible - wake-word
        # detection has its own processing lag) silently overwrites
        # self._voice_thread/_voice_worker, orphaning the first session:
        # no longer cancellable, and if it later captures speech, its
        # transcript_ready can still fire and get sent into whatever chat
        # happens to be active by then. One guard here covers both
        # callers instead of relying on each caller to coordinate.
        #
        # isRunning() itself can raise RuntimeError: once a previous
        # voice thread finishes, its `finished` signal fires
        # deleteLater(), which eventually destroys the underlying C++
        # QThread - but self._voice_thread (the Python attribute) is
        # never reset to None when that happens, so it keeps pointing at
        # a now-dead wrapper. Calling .isRunning() on that raises
        # RuntimeError rather than returning False. Left uncaught, that
        # exception fires INSIDE _on_wake_word_detected before
        # set_mic_state("listening")/the actual capture flow ever starts
        # - which means _reset_after_voice_capture() (the only place that
        # calls self._wakeword_worker.resume()) never runs either,
        # leaving the wake-word listener paused forever after exactly one
        # crash like this. Treating a deleted wrapper as "not running"
        # and clearing the stale attribute so future calls don't even
        # need to hit this except path - fixes both.
        voice_thread = getattr(self, "_voice_thread", None)
        if voice_thread is not None:
            try:
                still_running = voice_thread.isRunning()
            except RuntimeError:
                still_running = False
                self._voice_thread = None
            if still_running:
                return

        self._wakeword_worker.pause()
        # "preparing", not "listening" yet - the microphone isn't
        # actually capturing until VoiceWorker's own listening_started
        # signal arrives (see _on_voice_listening_started), which now
        # only fires once mic setup + ambient-noise calibration are
        # done. Jumping straight to "listening" here (the old behavior)
        # invited the person to start talking during that setup window,
        # before anything was actually being recorded - see
        # voice_worker.py's own comment on this same fix.
        self.chat.set_mic_state("preparing")
        self._toggle_overlay()

        self._voice_thread = QThread()
        self._voice_worker = VoiceWorker()
        self._voice_worker.wakeword_worker = self._wakeword_worker
        self._voice_worker.moveToThread(self._voice_thread)
        self._voice_thread.started.connect(lambda: self._voice_worker.start.emit(chat_id))

        self._voice_worker.listening_started.connect(self._on_voice_listening_started)
        self._voice_worker.transcript_ready.connect(self._on_voice_transcript_ready)
        self._voice_worker.no_speech_detected.connect(self._on_voice_no_speech)
        self._voice_worker.transcription_failed.connect(self._on_voice_transcription_failed)
        self._voice_worker.unavailable.connect(self._on_voice_unavailable)
        self._voice_worker.cancelled.connect(self._on_voice_cancelled)

        # VoiceWorker's job (record + transcribe) is fully done by the
        # time any of these fire - safe to tear the thread down right
        # away, unlike the old design where it also blocked on TTS
        # playback afterward.
        for sig in (
            self._voice_worker.transcript_ready, self._voice_worker.no_speech_detected,
            self._voice_worker.transcription_failed, self._voice_worker.unavailable,
            self._voice_worker.cancelled,
        ):
            sig.connect(self._voice_thread.quit)
            sig.connect(self._voice_worker.deleteLater)
        self._voice_thread.finished.connect(self._voice_thread.deleteLater)
        self._track_thread(self._voice_thread)
        self._voice_thread.start()

    def _on_voice_listening_started(self, chat_id: int) -> None:
        # Only NOW is the microphone actually capturing (see
        # voice_worker.py's own comment) - this is what flips the mic
        # button from "preparing" over to the red "Listening… click to
        # cancel" state, instead of that happening the instant the
        # button was clicked.
        self.chat.set_mic_state("listening")
        self._voice_status_overlay.show_status("Listening…")

    def _reset_after_voice_capture(self, chat_id: int) -> None:
        self.chat.set_mic_state("idle")
        self._wakeword_worker.resume()
        self._voice_status_overlay.hide_status()
        self._toggle_overlay()

    def _on_voice_transcript_ready(self, chat_id: int, transcript: str) -> None:
        """
        The one and only transcript of what was said. By default it
        lands in the chat's input box for review - speech recognition
        mishears things, and this is the only moment the wrong word can
        still be fixed rather than corrected in a follow-up message.
        Press Enter to send it, edit it first, or press Escape to throw
        it away (see ChatWidget.stage_voice_transcript).

        Settings > "Review voice messages before sending" turns that
        off for genuinely hands-free use, in which case it's sent the
        instant transcription finishes, exactly as it always was. Either
        path marks the message voice-originated (ChatWidget's
        voice_reply flag), so the answer still comes back spoken.
        """
        self._reset_after_voice_capture(chat_id)
        if self.chat._current_chat_id != chat_id:
            return  # the user switched chats while this was recording
        if ai_main.get_voice_review_before_send():
            self.chat.stage_voice_transcript(transcript)
            return
        self.chat.chatInput.setPlainText(transcript)
        self.chat._send_message(voice_reply=True)

    def _on_voice_no_speech(self, chat_id: int) -> None:
        self._reset_after_voice_capture(chat_id)

    def _on_mic_cancel_requested(self, chat_id: int) -> None:
        """
        The user clicked the mic again while it was listening, to back
        out of the recording - see VoiceWorker.request_cancel's
        docstring for why this is now possible at all (it wasn't:
        clicking while "listening" used to be a no-op, with the button
        disabled, forcing the full silence timeout to be waited out).
        """
        if hasattr(self, "_voice_worker") and self._voice_worker is not None:
            self._voice_worker.request_cancel()

    def _on_voice_cancelled(self, chat_id: int) -> None:
        self._reset_after_voice_capture(chat_id)

    def _on_voice_transcription_failed(self, chat_id: int) -> None:
        self._reset_after_voice_capture(chat_id)
        note = "Couldn't quite catch that - try again, or type your message instead."
        db.add_message(chat_id, "assistant", note)
        if self.chat._current_chat_id == chat_id:
            self.chat._append_bubble(text=note, role="assistant")
        self.update_history(chat_id)

    def _on_voice_unavailable(self, chat_id: int, reason: str) -> None:
        self._reset_after_voice_capture(chat_id)
        QMessageBox.information(self, "Voice assistant", reason)

    # ── Speaking the reply back (SpeakWorker) ───────────────────────────────

    def _speak_response(self, chat_id: int, text: str, from_voice: bool = False) -> None:
        """
        Say `text` aloud on a worker thread.

        `from_voice` is what separates the two features that both end up
        here. A voice-mode turn is a conversation: the mic button becomes
        Stop, the "Speaking…" overlay appears, and the wake-word listener
        switches to listening for "stop" instead of the wake word. Read
        aloud is not a conversation - somebody pressed a speaker icon on
        a message - and none of that applies to it.

        Both used to get all of it, because this method took no argument
        saying which it was. Pressing Read aloud turned the mic button
        red and repurposed it as Stop, so you could not start a voice
        recording while a message was being read; it popped up an overlay
        telling you to say "stop"; and it put the wake-word listener into
        speaking mode, where it ignores its own wake word entirely. For
        the length of any Read aloud, the assistant could not be woken by
        name.
        """
        self._speak_thread = QThread()
        self._speak_worker = SpeakWorker()
        self._speak_worker.moveToThread(self._speak_thread)
        self._speak_thread.started.connect(lambda: self._speak_worker.start.emit(chat_id, text))

        if from_voice:
            self._speak_worker.speaking_started.connect(self._on_voice_speaking_started)
            self._speak_worker.speaking_finished.connect(self._on_voice_speaking_finished)
        else:
            self._speak_worker.speaking_started.connect(self._on_read_aloud_started)
            self._speak_worker.speaking_finished.connect(self._on_read_aloud_finished)

        self._speak_worker.speaking_finished.connect(self._speak_thread.quit)
        self._speak_worker.speaking_finished.connect(self._speak_worker.deleteLater)
        self._speak_thread.finished.connect(self._speak_thread.deleteLater)
        self._track_thread(self._speak_thread)
        self._speak_thread.start()

    # ── Voice mode speaking (a conversational turn) ─────────────────────────

    def _on_voice_speaking_started(self, chat_id: int) -> None:
        self.chat.set_mic_state("speaking")
        self._voice_status_overlay.show_status('Speaking… click the mic or say "stop" to interrupt')
        # Reuses the wake-word listener's mic access (already free - the
        # mic isn't needed again until a NEW recording starts) to also
        # listen for "stop"/"arrête" while Kestrel talks - see
        # WakeWordWorker.set_speaking_mode. Only works if wake-word
        # listening is enabled in Settings; the mic-button click-to-stop
        # below always works regardless.
        self._wakeword_worker.set_speaking_mode(True)

    def _on_voice_speaking_finished(self, chat_id: int) -> None:
        self.chat.set_mic_state("idle")
        self._voice_status_overlay.hide_status()
        self._wakeword_worker.set_speaking_mode(False)

    # ── Read aloud (a button on one message) ────────────────────────────────

    def _on_read_aloud_started(self, chat_id: int) -> None:
        """
        Deliberately almost nothing. Read aloud touches no voice-mode
        state: the mic button stays free to start a recording, the wake
        word stays armed, and no overlay appears. Stopping it is the
        speaker button on the message itself, and starting another one
        replaces it (ai.main._begin_speech).
        """
        self.chat.set_speaking_message(chat_id, True)

    def _on_read_aloud_finished(self, chat_id: int) -> None:
        self.chat.set_speaking_message(chat_id, False)

    def _on_stop_read_aloud_requested(self, chat_id: int) -> None:
        """Read aloud's own Stop, from clicking the speaker button on the
        message already being read. Separate slot from the mic button's
        stop purely so the two features do not share an entry point
        again - the underlying call is the same one."""
        ai_main.stop_speaking()

    # ── Stopping ────────────────────────────────────────────────────────────

    def _on_mic_stop_speaking_requested(self, chat_id: int) -> None:
        # The actual "interrupt voice mode" affordance: stops Kestrel mid-
        # reply instead of forcing the user to wait it out. This just
        # supersedes the utterance's generation inside ai_main (the loop
        # is still running on the SpeakWorker thread) - no new thread is
        # started. The worker's own `finally` block still runs afterward
        # and fires speaking_finished as normal.
        #
        # Only reachable while the mic button is in "speaking" state,
        # which only a voice-mode turn now puts it in - so this can no
        # longer stop a Read aloud by surprise.
        ai_main.stop_speaking()

    def _on_stop_word_detected(self) -> None:
        # Voice-triggered counterpart to the click above - see
        # WakeWordWorker.set_speaking_mode / STOP_WORD_STEMS. The
        # listener only reaches this branch while _speaking_mode is set,
        # which again is voice mode only: saying "stop" near the machine
        # no longer cuts off a message you asked to have read to you.
        ai_main.stop_speaking()

    # ── Wake word ──────────────────────────────────────────────────────────────

    def _start_wake_word_listening(self) -> None:
        # start() now kicks off setup on a background thread and returns
        # immediately (see WakeWordWorker.start()'s own docstring for why
        # - this used to block the GUI thread for however long PyAudio's
        # device enumeration + the 0.5s calibration took, often a few
        # real seconds). So this can no longer just check the return
        # value to know it's ready - "Listening" now only gets set once
        # the `ready` signal actually arrives (_on_wake_word_ready);
        # meanwhile this shows an honest in-between state instead of
        # claiming it's already listening before it actually is.
        started = self._wakeword_worker.start()
        if started:
            self.settings.set_wake_word_status("Starting…", ok=True)

    def _on_wake_word_ready(self) -> None:
        # Lists every configured profile's name, not a single fixed
        # word - most installs only have the one (migrated) profile, so
        # this reads exactly as it used to for them ("say « Kestrel » to
        # talk"), just built from ai.profiles instead of a hardcoded
        # string.
        names = " / ".join(f"« {p.name} »" for p in profiles.list_profiles())
        self.settings.set_wake_word_status(f"Listening - say {names} to talk.", ok=True)

    def _on_assistant_name_changed(self, new_name: str) -> None:
        """
        Renaming the ACTIVE profile from Settings (app.settings_widget's
        profile list) also renames it in ai.profiles - see
        ai.profiles.rename_profile, which Settings itself calls. The wake
        word already reads profile names fresh from ai.profiles on every
        heard phrase (match_wake_word), so a rename takes effect there on
        the very next thing said, nothing to refresh.

        The chat page's own topbar is a different story - its profile
        picker (a StyledDropdown) shows whatever label it was BUILT or
        last refresh_profile_picker()'d with, not something re-derived
        fresh on every read; same for the static copy (the mic tooltip)
        _refresh_assistant_name_copy handles. Left alone, renaming from
        Settings while already sitting on the chat page (not switching
        pages, which would otherwise refresh it on the way back in via
        _navigate) left BOTH stale on screen - the picker still showing
        the pre-rename name right next to the now-correct copy beside it
        - until leaving the chat page and returning. Bug fix: this used
        to call chat._refresh_assistant_name_copy() directly, covering
        only the copy half of that; refresh_profile_picker() covers both
        (it calls _refresh_assistant_name_copy() itself - see its own
        docstring) so nothing here can drift out of sync with the other
        again.
        """
        self.chat.refresh_profile_picker()

    def _on_profile_switched_from_chat(self) -> None:
        """
        The chat page's own profile picker (app/chat_widget.py's
        topbar) just switched the active profile. Settings' profile
        list (app/settings_widget.py's _build_profiles_section) marks
        the active one with a ● and only refreshes reactively from its
        OWN controls - without this, switching profile from chat would
        leave that page showing the PREVIOUS profile as active until
        something else there happened to trigger a refresh.
        """
        self.settings._refresh_profiles_list()

    def _on_wake_word_setting_toggled(self, enabled: bool) -> None:
        if enabled:
            self._start_wake_word_listening()
        else:
            self._wakeword_worker.stop()
            self.settings.set_wake_word_status("Disabled.", ok=True)

    def _on_wake_word_detected(self, profile_id: str = "") -> None:
        self._wakeword_worker.pause()

        # "Outside of a chat" - the window was hidden/minimized, or it
        # was open but showing a different page (Macros/Settings/etc.)
        # means the user wasn't already in the middle of a conversation
        # when they said the wake word. Captured BEFORE show_window()/
        # _navigate() below, both of which are about to make this look
        # "already in a chat" regardless of what was true a moment ago.
        was_outside_chat = (not self.isVisible()) or (self._stack.currentIndex() != self.PAGE_CHAT)

        self.show_window()

        # Route to whichever profile's name was heard - a no-op switch
        # if it's already the active one (activate_profile still runs,
        # but ai_main.set_assistant_name/set_active_provider_and_model
        # are themselves no-ops when nothing actually changed). Missing
        # profile_id (e.g. a stale/disconnected signal) or a profile
        # whose provider has no API key configured yet both fail safe:
        # keep talking to whatever WAS already active rather than
        # blocking the voice turn entirely over a routing hiccup.
        if profile_id:
            try:
                profiles.activate_profile(profile_id)
            except Exception as e:
                print(f"[MainWindow] Couldn't switch to profile {profile_id!r}: {e}")

        # Being invoked "cold" from outside any open conversation should
        # feel like starting something new, not silently resuming
        # whatever chat happened to be left open last - even if one
        # already exists, per was_outside_chat above.
        if was_outside_chat or self.chat._current_chat_id is None:
            self._on_new_chat()

        chat_id = self.chat._current_chat_id
        if chat_id is not None:
            self._on_mic_requested(chat_id)
        else:
            # No chat could be created - don't leave the listener stuck paused.
            self._wakeword_worker.resume()

    def _on_wake_word_error(self, reason: str) -> None:
        self.settings.set_wake_word_status(reason, ok=False)
