# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/telemetry.py
================
Crash reporting only - no usage analytics, no content/prompt capture,
nothing about what the user asked Kestrel to do. Off by default; a user
has to explicitly opt in from Settings (get/set_telemetry_enabled in
ai/main.py), which is what GDPR/CCPA require for anything beyond
strictly-necessary data collection.

Uses Sentry (sentry-sdk) if it's installed and a DSN is configured
(SENTRY_DSN in .env) - see docs/TELEMETRY_SETUP.md for getting a DSN.
Both are optional: no DSN means telemetry silently stays off even if the
user opts in, same guarded-optional pattern as the rest of this codebase
(pywin32, uiautomation, keyring, ...).
"""

import os
import sys

from ai.log import log

try:
    import sentry_sdk
    _SENTRY_AVAILABLE = True
except ImportError:
    _SENTRY_AVAILABLE = False

_initialized = False


def _dsn() -> "str | None":
    return os.getenv("SENTRY_DSN") or None


def is_configured() -> bool:
    """Whether crash reporting COULD run at all - sentry-sdk installed
    and a DSN present - regardless of the user's opt-in choice. Used to
    decide whether Settings even shows the toggle."""
    return _SENTRY_AVAILABLE and bool(_dsn())


def init_if_enabled() -> None:
    """Call once at startup, after ai.main has loaded .env. No-op unless
    the user has already opted in AND telemetry is configured."""
    import ai.main as ai_main
    if ai_main.get_telemetry_enabled():
        set_enabled(True)


def set_enabled(enabled: bool) -> None:
    """
    Called immediately when the user flips the Settings toggle, and once
    at startup if they'd already opted in previously. Sentry's own SDK
    has no clean "turn off after init" - the pragmatic, honest way to
    make disabling take full effect is to say so and let a restart fully
    unload it, same as the API-key-changes-need-a-fresh-client pattern
    elsewhere in this app.
    """
    global _initialized
    if not enabled:
        log("[telemetry] Crash reporting disabled. Fully stops after a restart.")
        return
    if not is_configured():
        log("[telemetry] Crash reporting requested but not available (sentry-sdk not "
            "installed or SENTRY_DSN not set) - staying off.", level="warn")
        return
    if _initialized:
        return

    sentry_sdk.init(
        dsn=_dsn(),
        # Crash reporting, not performance/session tooling - keep both off
        # so nothing beyond unhandled-exception payloads is ever sent.
        traces_sample_rate=0.0,
        profiles_sample_rate=0.0,
        send_default_pii=False,
        release=_release_string(),
    )
    _initialized = True
    log("[telemetry] Crash reporting enabled (opt-in).")

    sys.excepthook = _make_excepthook(sys.excepthook)


def _release_string() -> str:
    from paths import APP_VERSION
    return f"kestrel@{APP_VERSION}"


def _make_excepthook(previous):
    def _hook(exc_type, exc_value, exc_tb):
        try:
            sentry_sdk.capture_exception(exc_value)
        except Exception:
            pass  # never let telemetry itself crash the crash handler
        previous(exc_type, exc_value, exc_tb)
    return _hook
