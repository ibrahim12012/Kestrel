# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

import sys
import os
import threading

if sys.platform == "win32":
    # Fixes: "qt.qpa.window: SetProcessDpiAwarenessContext() failed:
    # Access denied." - cosmetic, not a crash, but it clutters every
    # startup. Windows only lets a process's DPI awareness be set ONCE;
    # a frozen PyInstaller .exe's embedded manifest (or, unfrozen, a
    # Windows default for unmanifested processes) already sets it before
    # Qt gets a chance to, so Qt's own attempt is redundant and fails
    # with access-denied - even though the value it wanted
    # (Per-Monitor-V2, see the warning's own text) is usually exactly
    # what's already in effect. Passing dpiawareness explicitly via the
    # windows platform plugin's own option (documented at
    # https://doc.qt.io/qt-6/highdpi.html#configuring-windows) makes Qt
    # skip that redundant call instead of trying and failing. Must be
    # set before PyQt6 is imported - Qt reads platform options at
    # import/init time.
    os.environ.setdefault("QT_QPA_PLATFORM", "windows:dpiawareness=2")

from dotenv import load_dotenv
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import qInstallMessageHandler, QtMsgType
from app.main_window import MainWindow
import branding
from paths import BASE_PATH, USER_DATA_PATH, APP_VERSION
from ai.log import log
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
    # Redundant now that QT_QPA_PLATFORM sets dpiawareness explicitly
    # above, but kept here too in case Qt still emits it once on some
    # Windows configurations regardless.
    "SetProcessDpiAwarenessContext",
    # Traced to qtawesome's own internal icon-glyph rendering (this app
    # has no QFont(...)/.setPointSize(...) calls anywhere in its own
    # code - confirmed by direct search - so this isn't coming from
    # anything under app/ or ai/). qtawesome computes a QFont point size
    # for its icon font internally from the current DPI scale factor,
    # and on some DPI configurations that computation rounds down to an
    # invalid value. Purely cosmetic - icons still render correctly
    # and not something fixable from this side of a third-party
    # library's internals, so it's filtered here rather than left to
    # spam the console (including the new in-app Console window, which
    # mirrors everything that reaches here) on every icon repaint.
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

    # ── Warm the slow-to-start bits while the splash is up ──────────────
    # Two things used to be built lazily on first use and cost about two
    # seconds each, both of them in the worst possible place: between
    # someone asking for voice and voice responding.
    #
    #   prewarm_audio  loads PortAudio and enumerates the input devices,
    #                  which is most of the wait before the mic goes live
    #   prewarm_voice  builds the Gemini client (a .env read plus an OS
    #                  keyring lookup), which is the one-off part of the
    #                  wait before Read aloud starts playing
    #
    # A daemon thread, so neither can hold up the window appearing and
    # neither can keep the process alive on quit. Both swallow their own
    # errors: this is an optimisation, and a machine with no microphone
    # or no API key must still start normally and report that later, in
    # the places that know how to show it.
    threading.Thread(
        target=lambda: (ai_main.prewarm_audio(), ai_main.prewarm_voice()),
        daemon=True,
        name="prewarm",
    ).start()

    # ── One window, start to finish ─────────────────────────────────────
    # MainWindow's own constructor shows itself (on its splash page)
    # partway through, before doing its own heavier setup - see its
    # "Startup shell" section - so by the time MainWindow() returns here,
    # the window is already on screen and the splash page's minimum
    # display time (SplashPage.ensure_minimum_display_time) hasn't
    # necessarily elapsed yet. The name gate below runs in that SAME
    # window rather than a popup dialog: see MainWindow.run_name_gate /
    # show_main_content, and app/splash_screen.py for why this used to
    # be several windows in a row.
    window = MainWindow()

    # ── Assistant name gate ────────────────────────────────────────────
    # The only thing between launching and using the app. It never
    # blocks startup the way the old sign-in gate did: see
    # app/name_gate_widget.py.
    if not ai_main.is_assistant_name_configured():
        window.run_name_gate()

    window.show_main_content()

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
    # sits relative to the use.
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(3000, lambda: threading.Thread(target=_check_update, daemon=True).start())

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
