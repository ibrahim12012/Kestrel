# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_main_qt_message_handler.py
========================================
Tests for main.py's Qt message handler - routes Qt's own internal
qWarning/qCritical output through ai.log.log() instead of printing raw,
unformatted lines to stderr, while filtering a short, explicit list of
known-benign, purely cosmetic messages (SetProcessDpiAwarenessContext,
qtawesome's QFont::setPointSize) rather than a blanket "hide all Qt
warnings".
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main as main_module


def test_benign_dpi_message_is_filtered(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "log", lambda message, level="info": calls.append((message, level)))

    main_module._qt_message_handler(
        main_module.QtMsgType.QtWarningMsg, None,
        "qt.qpa.window: SetProcessDpiAwarenessContext() failed: Access denied.",
    )

    assert calls == []


def test_benign_qfont_message_is_filtered(monkeypatch):
    """
    Traced to qtawesome's internal icon-glyph rendering (this app has no
    QFont(...)/.setPointSize(...) calls of its own) - purely cosmetic,
    not fixable from this side of a third-party library, filtered here
    so it doesn't spam the console (or the in-app Console window, which
    mirrors everything ai.log.log() prints) on every icon repaint.
    """
    calls = []
    monkeypatch.setattr(main_module, "log", lambda message, level="info": calls.append((message, level)))

    main_module._qt_message_handler(
        main_module.QtMsgType.QtWarningMsg, None,
        "QFont::setPointSize: Point size <= 0 (-1), must be greater than 0",
    )

    assert calls == []


def test_a_real_qt_warning_is_routed_through_log(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "log", lambda message, level="info": calls.append((message, level)))

    main_module._qt_message_handler(
        main_module.QtMsgType.QtWarningMsg, None,
        'QPushButton(0x123, name="menuBtn")  does not have a property named  "alignment"',
    )

    assert len(calls) == 1
    message, level = calls[0]
    assert "does not have a property named" in message
    assert message.startswith("[qt] ")
    assert level == "warn"


def test_a_qt_critical_message_is_routed_as_error_level(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "log", lambda message, level="info": calls.append((message, level)))

    main_module._qt_message_handler(main_module.QtMsgType.QtCriticalMsg, None, "something is seriously wrong")

    assert calls == [("[qt] something is seriously wrong", "error")]


def test_a_qt_fatal_message_is_routed_as_error_level(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "log", lambda message, level="info": calls.append((message, level)))

    main_module._qt_message_handler(main_module.QtMsgType.QtFatalMsg, None, "fatal")

    assert calls == [("[qt] fatal", "error")]
