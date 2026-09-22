# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
app/console_widget.py
=======================
A live console window that mirrors everything ai.log.log() prints,
styled as a dark developer console instead of a raw terminal window -
see ai/log.py's add_sink()/remove_sink() for how it receives lines
without hijacking sys.stdout (which other code may also be reading from
or redirecting).

Line classification (_classify - a "tagFor()" equivalent):
    - input : the user's own message, routed through ai.log.log_input()
      (called from chat_widget.py's send path - see that function's
      call to it) rather than log(), since input lines have no
      timestamp/level (see the DOM difference in _input_html vs
      _line_html) and are visually distinct regardless of content.
    - tool  : message starts with a bracketed [tag]. The vast majority
      of this app's log() calls already use this convention
      ("[tool_name_action] ...", "[Macro] ...", "[autonomous] ...").
    - reply : message (after stripping any leading [tag]) starts with
      "<assistant name>:" - ai/main.py's own direct-reply prints,
      using whatever name is currently configured via
      ai.main.get_assistant_name()/set_assistant_name().
    - idle  : the literal "Waiting..." (ai/actions.py's screenshot-wait
      loop) - the one deliberately idle-styled state in this app today.
    - plain : everything else.
"""

import html
import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout, QWidget

import branding
import ai.main as ai_main
from ai.log import add_sink, get_history, remove_sink
from app.smooth_scroll import install_smooth_scroll

# Fixed dark palette - deliberately NOT tied to app/theme.py's
# light/dark tokens. A developer console keeping its own consistent
# scheme regardless of the app's current theme is standard (same reason
# most terminal emulators don't re-theme themselves when their host OS
# switches appearance).
_CSS = """
.console-line { white-space: pre-wrap; margin: 0; padding: 2px 10px; }
.input-line {
    background-color: rgba(124, 106, 255, 0.12);
    border-left: 3px solid #7c6aff;
    padding-left: 8px;
}
.arrow { color: #7c6aff; margin-right: 6px; }
.log-stamp { color: #5c5c5c; }
.tag-tool { color: #6fc3ff; }
.tag-reply { color: #a99bff; }
.tag-idle { color: #5c5c5c; }
.tag-plain { color: #f2f2f2; }
"""

_LEADING_TAG_RE = re.compile(r"^\[[^\]]+\]\s*")


def _classify(message: str) -> str:
    """Pure classification logic - kept separate from any Qt/rendering so it's trivially unit-testable."""
    stripped = _LEADING_TAG_RE.sub("", message, count=1).lstrip()
    if stripped.startswith(f"{ai_main.get_assistant_name()}:"):
        return "reply"
    if message.strip() == "Waiting...":
        return "idle"
    if _LEADING_TAG_RE.match(message):
        return "tool"
    return "plain"


def _line_html(ts: str, level: str, message: str) -> str:
    tag = _classify(message)
    return (
        f'<div class="console-line">'
        f'<span class="log-stamp">{html.escape(ts)} {html.escape(level.upper()):<5}</span> '
        f'<span class="tag-{tag}">{html.escape(message)}</span>'
        f"</div>"
    )


def _input_html(text: str) -> str:
    return f'<div class="console-line input-line"><span class="arrow">\u203a</span>{html.escape(text)}</div>'


class ConsoleWidget(QDialog):
    """
    A standalone window (not a page in the main QStackedWidget - a
    console is conventionally something toggled in and out alongside
    whatever else is on screen, not something that replaces it) showing
    every ai.log.log() call live, plus the user's own chat input lines
    via log_input().
    """

    # ts, level, message - the thread-safe hand-off. log() can be (and
    # very often is) called from a background thread (autonomous mode,
    # macro playback, voice) - emitting a signal from there is safe;
    # touching self._view directly from there would not be. Qt
    # auto-queues the connected slot call onto this widget's own thread
    # (the GUI thread) whenever emit() happens from a different one.
    _line_ready = pyqtSignal(str, str, str)
    _input_ready = pyqtSignal(str)

    def __init__(self, parent: "QWidget | None" = None):
        super().__init__(parent)
        self.setWindowTitle(f"{branding.APP_NAME} Console")
        self.setMinimumSize(560, 420)
        self.setStyleSheet("QDialog { background-color: #121212; }")

        self._view = QTextBrowser(self)
        self._view.setObjectName("consoleView")
        self._view.setStyleSheet(
            "QTextBrowser#consoleView { background-color: #121212; color: #f2f2f2; border: none; }"
        )
        self._view.document().setDefaultStyleSheet(_CSS)
        self._view.setReadOnly(True)
        self._view.setOpenExternalLinks(False)

        font = QFont("JetBrains Mono")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self._view.setFont(font)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        # Eased wheel scrolling instead of Qt's default instant per-tick
        # jump - see app/smooth_scroll.py. append()'s own auto-scroll-to-
        # bottom for new log lines sets the scrollbar directly and is
        # unaffected - this only intercepts user-initiated wheel events.
        install_smooth_scroll(self._view)

        self._line_ready.connect(self._append_line)
        self._input_ready.connect(self.log_input)
        self._replay_history()
        add_sink(self._on_log)

    def _replay_history(self) -> None:
        """
        Fills the console with everything ai.log.log()/log_input()
        already recorded before this widget existed - it's only built
        lazily, the first time someone opens it from the tray menu, so
        without this it opened to a blank window no matter how much had
        already happened (app startup, tool calls, an entire autonomous
        run) - a sink only receives calls made AFTER add_sink()
        registers it, and this widget didn't exist yet to register one.
        """
        for ts, level, message, _input_data in get_history():
            if level == "input":
                self.log_input(message)
            else:
                self._append_line(ts, level, message)

    # -- ai.log sink --------------------------------------------------

    def _on_log(self, ts: "str | None", level: str, message: str, input_data) -> None:
        # ai.log.log_input() (see chat_widget.py's send path) notifies
        # sinks with level="input" and no timestamp - route those to
        # their own signal/rendering instead of the regular tool/reply
        # classification path.
        if level == "input":
            self._input_ready.emit(message)
        else:
            self._line_ready.emit(ts, level, message)

    def _append_line(self, ts: str, level: str, message: str) -> None:
        self._view.append(_line_html(ts, level, message))
        self._scroll_to_bottom()

    # -- user input lines ----------------------------------------------

    def log_input(self, text: str) -> None:
        """
        Appends a user-input-styled line directly. Normally reached via
        the ai.log.log_input() -> sink -> _input_ready signal path above
        (so chat_widget.py never needs a reference to this widget), but
        exposed as a plain method too since that's the more convenient
        call shape for tests and any other direct caller.
        """
        self._view.append(_input_html(text))
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # -- lifecycle ------------------------------------------------------

    def _unregister_sink(self) -> None:
        remove_sink(self._on_log)

    def closeEvent(self, event) -> None:
        # Closing the window (vs the whole app) must stop feeding it
        # otherwise every log() call keeps paying for a signal emit into
        # a hidden widget, and, worse, add_sink() would silently pile up
        # duplicate registrations across repeated show/hide cycles if a
        # new ConsoleWidget were ever created instead of reused.
        # closeEvent isn't reliably delivered to a widget that was never
        # actually shown (notably under tests, running the offscreen Qt
        # platform) - tests should call _unregister_sink() directly
        # rather than relying on .close() alone; a stale registration
        # left pointing at a since-garbage-collected widget segfaults
        # the next time log()/log_input() fires.
        self._unregister_sink()
        super().closeEvent(event)
