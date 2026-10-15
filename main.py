# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

import sys
import os
import threading
import time

# Taken before any of the heavy imports below, so the "[startup] ready"
# line in the Console reports what someone double-clicking the app
# actually waited for, imports included.
_STARTED_AT = time.perf_counter()

# One Kestrel at a time. Checked before Qt and the rest of the app are
# imported, so a second launch hands over to the running copy (which
# shows its window) and exits as soon as it has answered, without paying
# for a full startup. Two copies used to run
# side by side, each with the same global shortcuts and the same wake
# word, so every hotkey fired twice. See app/single_instance.py.
if __name__ == "__main__":
    # The DPI mode Qt is built for, before any import can set another:
    # see app/win_runtime.py and the note above the Qt imports below.
    from app import win_runtime as _win_runtime

    _win_runtime.use_per_monitor_v2()

    # First of all, somewhere to say what went wrong: Kestrel.exe has no
    # console, so without this a failure at startup left no trace at all.
    # %LOCALAPPDATA%\Kestrel\logs\crash.log, see app/crash_report.py.
    from paths import USER_DATA_PATH as _USER_DATA_PATH
    from app import crash_report

    crash_report.install(_USER_DATA_PATH)

    from app import single_instance

    if not single_instance.acquire():
        # Another copy holds the lock: ask it to show its window. If it
        # was on its way out (closing, or crashed and still held by
        # Windows Error Reporting), this launch takes over and starts
        # normally instead.
        _outcome = single_instance.hand_over()
        if _outcome == single_instance.INSTALLING:
            crash_report.show_fatal_error(
                "Setup is replacing Kestrel's files. Start Kestrel again once "
                "setup has finished.",
                heading="Kestrel is being updated.",
                details=False,
            )
        elif _outcome == single_instance.NOT_RESPONDING:
            # Exiting silently here would look exactly like the app
            # launching and dying.
            crash_report.record(
                "Another Kestrel is running but didn't answer",
                "This launch asked it to show its window, waited 20 seconds and "
                "exited. If no window appears, end Kestrel.exe in Task Manager "
                "and start Kestrel again.",
            )
            crash_report.show_fatal_error(
                "It didn't answer when asked to show its window. If no window "
                "appears, end Kestrel.exe in Task Manager (Details tab) and "
                "start Kestrel again.",
                heading="Kestrel is already running.",
            )
        if _outcome != single_instance.TOOK_OVER:
            sys.exit(0)

    # Kestrel.exe has no console, so any console program it starts
    # (cmd.exe, PowerShell, `ver`...) flashes a console window of its own.
    # From here on none of them does, whichever library starts it. See
    # ai/lazy_import.py.
    from ai.lazy_import import hide_console_windows_everywhere

    hide_console_windows_everywhere()
    crash_report.start_run_log()

    # Before Qt or any other C++ code is loaded: make sure the Microsoft
    # C++ runtime every DLL in the process shares is the newest copy on
    # the machine, not whichever one the DLL search happens to find
    # first. An old one crashes newer code with an access violation and
    # no message. Kestrel.exe has already done this in a runtime hook
    # (installer/pyi_rth_kestrel_runtime.py), which makes this a no-op
    # there; see app/win_runtime.py.
    from app import win_runtime

    win_runtime.pin_newest_cpp_runtime()

# DPI awareness is per-monitor v2, Qt's own default and best-tested mode,
# set at the top of the __main__ block above before anything is imported
# that could set another. pyautogui sets system-DPI aware when imported
# and uiautomation sets per-monitor v1; whichever call comes first wins.
# pyautogui loads after the window is up (ai/lazy_import.py), but
# uiautomation came in through the Mapping page's imports and the app ran
# in v1, where the window could end up drawn at the wrong scale (twice,
# after Alt+Tab). Kestrel.exe's manifest (installer/Kestrel.manifest)
# doesn't declare DPI awareness. The mode the process actually ends up in
# is in the "[startup] Environment" log line.

from dotenv import load_dotenv
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import qInstallMessageHandler, QtMsgType, QTimer
from app.main_window import MainWindow
import branding
from paths import BASE_PATH, USER_DATA_PATH, APP_VERSION
from ai.log import log, add_sink as _add_log_sink
from app import crash_report
_add_log_sink(crash_report.log_sink)     # every log line into logs/kestrel.log
import ai.main as ai_main

# Load .env before anything here reads it via os.getenv. ai/main.py
# calls load_dotenv(ENV_PATH) too, but lazily, the first time the AI
# pipeline is used, which is later than some of the startup path needs.
# Same ENV_PATH, kept in sync via USER_DATA_PATH.
ENV_PATH = os.path.join(USER_DATA_PATH, ".env")
load_dotenv(ENV_PATH)


# Substrings of genuinely benign, purely cosmetic Qt-internal messages
# filtered out entirely rather than routed through log() below. Kept to
# an explicit, narrow list (not a blanket "hide all Qt warnings") so a
# real Qt-level problem in the future still surfaces; this only silences
# messages already understood and expected to be harmless.
_BENIGN_QT_MESSAGE_SUBSTRINGS = (
    # Qt couldn't set the DPI awareness because something had already
    # (see the note above the imports). main.py sets the same mode Qt
    # wants first, so this only appears when something else got in before
    # that. The "[startup] Environment" line says which mode it is.
    "SetProcessDpiAwarenessContext",
    # Was traced to qtawesome's icon font, which computed a QFont point
    # size from the DPI scale and on some configurations rounded it down
    # to an invalid value. The app no longer uses qtawesome (app/icons.py
    # draws its own SVG icons), so this should not appear any more; it
    # stays filtered because it is cosmetic whatever emits it, and the
    # in-app Console mirrors everything that reaches here.
    "QFont::setPointSize",
)


def _qt_message_handler(msg_type, context, message: str) -> None:
    """
    Routes Qt's own internal qWarning/qCritical/qFatal output (things
    like the "does not have a property named X" QSS warnings, or a font
    resolving to an invalid point size) through the same timestamped,
    color-coded log() every other console line in this app already uses,
    instead of Qt printing its own unformatted, unmarked lines straight
    to stderr - so the console reads as one consistent stream instead of
    two different formats interleaved.
    """
    if any(needle in message for needle in _BENIGN_QT_MESSAGE_SUBSTRINGS):
        return
    level = "error" if msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg) else "warn"
    log(f"[qt] {message}", level=level)
    if msg_type == QtMsgType.QtFatalMsg:
        # Qt ends the process right after this returns, with no Python
        # stack and nothing on screen: this line is all that is left.
        crash_report.record("Qt fatal error", message)


qInstallMessageHandler(_qt_message_handler)


def _fix_windows_taskbar_icon() -> None:
    """
    Without this, Windows shows the Python interpreter's own icon in the
    taskbar instead of the app's - QWidget.setWindowIcon() alone doesn't
    fix it when running unfrozen (python main.py rather than a bundled
    .exe). Windows groups taskbar buttons and picks their icon by
    "Application User Model ID"; without one, a Python process gets
    grouped (and iconed) under the interpreter itself. Setting an
    explicit AppUserModelID tells Windows this is its own distinct app.
    No-op on non-Windows platforms.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(branding.WINDOWS_APP_ID)
    except Exception as e:
        print(f"[WARN] Could not set the Windows taskbar app ID: {e}")


def _require_windows() -> None:
    """
    Stop on any other platform with a sentence that says why, rather than
    letting it fail later on an import of uiautomation, pywin32, AppOpener
    or winsound and leave someone reading a traceback to work out that the
    app was never going to run.

    Windows-only is what this app is built out of, not a porting backlog
    item: see the README's "Windows only, by construction". KESTREL_ALLOW_
    ANY_PLATFORM exists so the portable parts can be poked at during
    development; nothing past that point is expected to work.
    """
    if sys.platform == "win32" or os.getenv("KESTREL_ALLOW_ANY_PLATFORM"):
        return
    sys.stderr.write(
        f"{branding.APP_NAME} runs on Windows only. It drives the desktop through\n"
        "Windows UI Automation, pywin32 and AppOpener, none of which exist on\n"
        f"{sys.platform}. See the README section \"Windows only, by construction\".\n"
    )
    sys.exit(1)


def _apply_language(app) -> None:
    """
    Load the interface language before any window is built, because
    nothing re-translates a widget after the fact.

    English unless someone picks otherwise in Settings. Following the OS
    locale sounds friendlier and is worse in practice: it hands a
    machine-translated interface to people who never asked for one, and
    the person most likely to be surprised is the one whose OS is in a
    language the catalogs only half cover. "Match my system" is still
    there in the picker for anyone who wants it.

    The layout direction is set from the same call, since an RTL
    language needs Qt told before the first widget exists.
    """
    from app import i18n
    chosen = ai_main.get_language()
    if chosen == i18n.FOLLOW_SYSTEM:
        chosen = i18n.system_language()
    if chosen and chosen != i18n.SOURCE_LANGUAGE:
        i18n.set_language(chosen)
    i18n.apply_layout_direction(app)


def _log_environment() -> None:
    """One line with what the app is running on, for kestrel.log: the
    first thing to compare when it works on one machine and not another."""
    import platform
    from PyQt6.QtCore import PYQT_VERSION_STR, qVersion
    from app import win_runtime

    how = "Kestrel.exe" if getattr(sys, "frozen", False) else "from source"
    # Not platform.platform(): on Windows it can run `ver` through cmd.exe
    # to find out, a process started just to write this line.
    try:
        major, minor, build = sys.getwindowsversion().platform_version[:3]
        windows = f"Windows {major}.{minor} build {build}"
    except (AttributeError, ValueError, TypeError):
        windows = sys.platform
    log(f"[startup] Environment: {windows}, Python {platform.python_version()}, "
        f"PyQt {PYQT_VERSION_STR}, Qt {qVersion()}, {how}, DPI awareness "
        f"{win_runtime.dpi_awareness()}.")
    log(f"[startup] C++ runtime: {win_runtime.summary}. Loaded: {win_runtime.loaded_runtimes()}.")


def main():
    _require_windows()

    # The console is otherwise blank until the first log() call fires
    # (which can be a few seconds in, once ai/main.py finishes lazily
    # initializing) - printing something immediately confirms the app
    # actually started, which "it just shows nothing" easily reads as a
    # hang before that first real log line ever appears.
    # Goes into ai/log.py's ring buffer, so the in-app Console (lazily
    # built on first open, well after this line runs) still replays it
    # see ConsoleWidget._replay_history(). This is the first thing anyone
    # opening the Console sees, before it settles into a regular scrolling
    # log, which is the point: confirms which build is actually running
    # without needing an About dialog for it.
    log(f"Welcome to {branding.APP_NAME} - build {APP_VERSION}")
    # GPL v3 section 5(d) asks an interactive program to show an
    # "Appropriate Legal Notice" when it starts. The console is where
    # this app's startup banner already lives, and it is mirrored into
    # the in-app Console, so it is where the notice goes.
    log(f"{branding.APP_NAME} comes with ABSOLUTELY NO WARRANTY. "
        "Free software under the GNU GPL v3; see LICENSE. "
        "Commercial licensing: see COMMERCIAL-LICENSE.md.")
    log(f"[startup] {branding.APP_NAME} is starting...")

    import ai.telemetry as telemetry
    telemetry.init_if_enabled()

    _fix_windows_taskbar_icon()

    app = QApplication(sys.argv)
    # Emitted by a normal quit, and also when Windows ends the session
    # (it then ends the process without app.exec() ever returning), so
    # neither counts as a crash at the next launch.
    app.aboutToQuit.connect(crash_report.closed_normally)
    _log_environment()
    app.setApplicationName(branding.APP_NAME)
    # Qt's QPA plugin appends applicationDisplayName to the end of every
    # window's title bar, regardless of that window's own windowTitle, so
    # leaving this at its default (== applicationName) would put the
    # product name back in the title bar even with windowTitle blanked.
    # The chrome shows no name on purpose: see branding.py.
    # applicationName itself stays set, for OS process identification.
    app.setApplicationDisplayName("")
    app.setQuitOnLastWindowClosed(False)  # stays alive in the tray
    app.setWindowIcon(QIcon(os.path.join(BASE_PATH, branding.ICON_PATH)))

    # Style global anti-aliasing
    app.setStyle("Fusion")

    _apply_language(app)

    # ── One window, start to finish ─────────────────────────────────────
    # MainWindow's own constructor shows itself (on its splash page)
    # partway through, before doing its own heavier setup - see its
    # "Startup shell" section - so by the time MainWindow() returns here,
    # the window is already on screen. There is no minimum splash time
    # any more (see SplashPage.ensure_minimum_display_time): the content
    # replaces it as soon as it exists. The name gate below runs in that SAME
    # window rather than a popup dialog: see MainWindow.run_name_gate /
    # show_main_content, and app/splash_screen.py for why this used to
    # be several windows in a row.
    window = MainWindow()
    log(f"[startup] Window ready in {time.perf_counter() - _STARTED_AT:.2f} s "
        "(from launch, imports included).")
    # A second launch (show yourself) and the installer (close, please)
    # can reach this copy from here on. See app/single_instance.py.
    window.listen_for_outside_requests()

    # ── Assistant name gate ────────────────────────────────────────────
    # The only thing between launching and using the app. It never
    # blocks startup the way the old sign-in gate did: see
    # app/name_gate_widget.py.
    if not ai_main.is_assistant_name_configured():
        window.run_name_gate()
        if window.quit_requested:
            # The installer asked this copy to close while the name gate
            # was up (see MainWindow._on_installer_quit_request).
            sys.exit(0)

    window.show_main_content()

    # ── Warm the slow-to-start bits once the window is up ───────────────
    # Things that are only needed once a feature is used, loaded ahead of
    # that first use so it does not pay for them:
    #
    #   prewarm_audio      loads PortAudio and enumerates the input
    #                      devices, most of the wait before the mic goes
    #                      live the first time
    #   prewarm_voice      builds the Gemini client (google-genai's
    #                      import, a .env read and an OS keyring lookup),
    #                      the one-off part of the wait before Read aloud
    #   prewarm_appopener  AppOpener's import, which runs cmd.exe and, on
    #                      the first launch of an install, PowerShell
    #   pyautogui          its import pulls in tkinter and PIL
    #
    # This used to start before the window was built, which made it
    # compete with building the window for the interpreter (Python runs
    # one thread at a time), so it slowed down the very thing it was
    # meant to stay out of the way of. Starting it after the first frame,
    # with a pause for the fade-in to finish, keeps it off that path.
    #
    # A daemon thread, so none of it can keep the process alive on quit.
    # Each step swallows its own errors: this is an optimisation, and a
    # machine with no microphone or no API key must still start normally
    # and report that later, in the places that know how to show it.
    def _prewarm() -> None:
        import ai.actions as actions
        from ai.lazy_import import pyautogui
        actions.prewarm_appopener()
        try:
            pyautogui._lazy_load()
        except Exception as e:
            log(f"[startup] pyautogui prewarm skipped: {e}", level="warn")
        ai_main.prewarm_audio()
        ai_main.prewarm_voice()

    QTimer.singleShot(
        1500,
        lambda: threading.Thread(target=_prewarm, daemon=True, name="prewarm").start(),
    )

    # Non-blocking update check - see ai/updater.py for why this only
    # checks and points at the release page instead of self-installing.
    def _check_update():
        import ai.updater as updater
        info = updater.check_for_update()
        if info and hasattr(window, "tray_icon"):
            window.tray_icon.showMessage(
                "Kestrel update available",
                f"Version {info['version']} is available - click to open the release page.",
            )
            log(f"[updater] Update available: {info['version']} - {info['url']}")

    # threading is imported at module level. A second `import threading`
    # HERE would rebind it as a local for the whole of main(), which
    # makes every earlier use of it in this function an UnboundLocalError
    # - Python decides a name is local at compile time, from the presence
    # of any binding anywhere in the body, not from where that binding
    # sits relative to the use. QTimer is imported at module level for
    # the same reason.
    QTimer.singleShot(3000, lambda: threading.Thread(target=_check_update, daemon=True).start())

    # From here an uncaught exception is logged and the app carries on,
    # instead of ending it with a message box (see app/crash_report.py).
    crash_report.startup_finished()
    if window.quit_requested:
        sys.exit(0)      # asked to close before the main loop could start
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        # Recorded in logs/crash.log and shown with the file's path, which
        # a windowed Kestrel.exe has no console to print. See
        # app/crash_report.py.
        crash_report.fatal(*sys.exc_info())
