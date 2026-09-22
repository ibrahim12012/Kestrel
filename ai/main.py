# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/main.py
==========
Core AI backend for Kestrel.

Contains:
  - ia()                  → main chatbot function (one-turn response)
  - run_autonomous_mode() → multi-step screen-watching AI execution
  - No_code()             → manual macro recorder (keyboard hotkeys)
  - refresh_actions()     → reload actions module + rebuild chat session
  - get_position_object_and_click() → vision-based element locator
  - speak_reply()       → text-to-speech via Gemini TTS
  - run_voice_assistant() → hands-free voice loop (mic in / speech out)

This file is imported by main.py (the UI entry point).
The UI calls ia() and run_autonomous_mode() in background threads.
All print() output is captured and shown in the Console panel automatically.

Voice mode (speech_recognition + winsound) is optional: if those packages
or a microphone/Windows audio backend aren't available, everything else
in this module still works - only run_voice_assistant() will refuse to start.
"""

import os
import time
import threading
import importlib
import wave
import re
import io
import concurrent.futures

from google import genai
from google.genai import types
from PIL import ImageGrab, ImageChops, Image
from dotenv import load_dotenv
import pyautogui
import keyboard

# ── Optional voice-mode dependencies ───────────────────────────────
# speech_recognition needs a working microphone + PyAudio; winsound is
# Windows-only. Neither should block the rest of the module (text chat,
# autonomous mode, recorder) from working when they're missing.
try:
    import speech_recognition as sr
except ImportError:
    sr = None

try:
    import winsound
except ImportError:
    winsound = None

import ai.actions as actions
from ai.actions import (
    get_all_tools,
    load_file,
    save_file,
)

# ── Path setup ────────────────────────────────────────────────────
import branding
from paths import BASE_PATH, USER_DATA_PATH
from ai.log import log as print
from ai.log import log_debug
rel_path = os.path.join(BASE_PATH, "ai")

# ── File paths ────────────────────────────────────────────────────
# instructions.txt is the one system prompt the app WRITES back to: the
# [[UPDATE_START]] protocol appends a learned preference to it (see
# _append_preference and _process_full_response). So it lives under
# USER_DATA_PATH like everything else that's written, not next to the
# executable, for the two reasons the .env comment below already gives
# plus a third:
#
#   1. BASE_PATH is Program Files on a real install. Not writable.
#   2. An upgrade replaces the install directory, which would silently
#      wipe every preference the assistant had learned.
#   3. An application that rewrites files inside its own install
#      directory at runtime is a behavioural heuristic antivirus engines
#      score, and the content being written here is model-authored text.
#      Kestrel already looks like a RAT to a scanner on the strength of
#      what it legitimately does (see docs/ANTIVIRUS.md); self-modifying
#      its program directory on top of that is a fight not worth having
#      for something that was in the wrong place anyway.
#
# The copy under BASE_PATH is the shipped default, read-only, and seeds
# this one on first run (see _migrate_instructions below), the same
# one-time-copy pattern .env and chats.db already use.
BUNDLED_INSTRUCTIONS_PATH = os.path.join(rel_path, "instructions.txt")
INSTRUCTIONS_PATH       = os.path.join(USER_DATA_PATH, "ai", "instructions.txt")
# Read-only, never written back, so these two stay with the program.
AUTO_INSTRUCTIONS_PATH  = os.path.join(rel_path, "autonomous_instructions.txt")
# .env and screenshots are WRITTEN at runtime (settings saves, API key,
# every screenshot tool call) - they belong under USER_DATA_PATH, not
# BASE_PATH, for the exact reason paths.py's own docstring already spells
# out for macros/discovery trees: BASE_PATH is very commonly Program
# Files once actually installed (the whole point of adding an installer),
# which a standard non-admin user can't write to. This was the one thing
# that DIDN'T get updated when that pattern was set up elsewhere - every
# setting (API key, theme, wake word, shortcuts, screenshot quality) and
# every screenshot the AI takes would have silently failed to save the
# moment this was properly installed rather than just unzipped somewhere
# writable, with no visible error (same silent-failure shape paths.py
# already documents for macros - function-tool exceptions get swallowed
# by the SDK's automatic function calling).
ENV_PATH                = os.path.join(USER_DATA_PATH, ".env")
SCREENSHOTS_DIR         = os.path.join(USER_DATA_PATH, "ai", "screenshots")


def load_instructions() -> str:
    """
    Read the main system prompt, seeding it from the bundled copy first
    if this install has never written one.

    Every reader goes through this rather than load_file(INSTRUCTIONS_PATH)
    directly, because the order matters: load_file creates a bare default
    when the file is missing, so a read that happened before the seed ran
    would leave that default in place and the seed would then find a file
    already there and skip. One accessor is the only way to keep the two
    in the right order without depending on which entry point happens to
    run first.
    """
    _seed_instructions_file()
    return load_file(INSTRUCTIONS_PATH)


# ── Hotkey config ─────────────────────────────────────────────────
SHORTCUT_SCREEN       = "a+y"        # Hold A + press Y to capture screenshot (text/terminal mode)
SHORTCUT_SCREEN_VOICE = "ctrl+alt+s" # Screenshot hotkey while the voice assistant is running
SHORTCUT_VOICE_TALK   = "f8"         # Push-to-talk key in the voice loop

# ── Global state ──────────────────────────────────────────────────
screenshot = None
# Always a real google-genai client, regardless of which provider the
# main `chat` below is currently switched to - autonomous mode, voice
# mode, and the vision tool all stay on Gemini specifically (see
# ai/providers/__init__.py's module docstring for why: neither OpenAI
# nor Anthropic's manual tool-calling loop nor their audio-input story
# has been exercised for those particular call sites yet). Built once,
# lazily, by _ensure_gemini_client().
_gemini_client = None
chat       = None  # a ai.providers.base.ChatSession - provider-switchable, see get_active_provider()
tools      = []

# True when `tools` has changed since `chat` was built, so the session
# needs rebuilding before the next message goes out. Lets a macro
# deletion update the tool list immediately while deferring the
# expensive provider-session construction to the moment it matters -
# see drop_macro_tool / _ensure_chat_session_fresh.
_tools_stale = False

# ── Initialization guard ──────────────────────────────────────────
_initialized = False


# ── Settings (.env read/write) ──────────────────────────────────────
# Backs the Settings page: bring-your-own API key + the wake word toggle.
# The wake word toggle and assistant name persist as plain KEY=VALUE
# lines in .env (nothing secret about either). The API key is the one
# Settings value actually worth encrypting at rest - see
# _get_stored_api_key()/_set_stored_api_key() below - so it lives in
# the OS's native credential store via `keyring` instead.

try:
    import keyring
    _KEYRING_AVAILABLE = True
except ImportError:
    # A machine without a working keyring backend should not crash the
    # app on import, just fall back to the plaintext .env storage below.
    _KEYRING_AVAILABLE = False

_KEYRING_SERVICE_NAME = branding.APP_SLUG
_KEYRING_API_KEY_ACCOUNT = "gemini_api_key"


def _read_env_file() -> dict:
    values = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                values[k.strip()] = v.strip()
    return values


def _write_env_value(key: str, value: str) -> None:
    """Upsert one KEY=value line in .env - every other line is left untouched."""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()

    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} ="):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}={value}\n")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _get_stored_api_key() -> str:
    """
    Reads whatever Gemini API key is persisted on disk/in the OS
    credential store, NOT including any os.environ override (see
    get_api_key() below for the full resolution order). Prefers
    keyring; falls back to the legacy plaintext .env value if keyring
    is unavailable on this machine, or if that's simply where an older
    build of this app already left one before this migration.
    """
    if _KEYRING_AVAILABLE:
        try:
            key = keyring.get_password(_KEYRING_SERVICE_NAME, _KEYRING_API_KEY_ACCOUNT)
            if key:
                return key
        except Exception as e:
            print(f"[ai] keyring read failed, falling back to .env: {e}")
    return _read_env_file().get("GEMINI_API_KEY", "")


def _set_stored_api_key(new_key: str) -> None:
    """Persists new_key to the keyring, migrating away from any legacy
    plaintext .env value in the process - see save_api_key()."""
    if _KEYRING_AVAILABLE:
        try:
            keyring.set_password(_KEYRING_SERVICE_NAME, _KEYRING_API_KEY_ACCOUNT, new_key)
            # Migration: once a key lives in the encrypted keyring,
            # don't also leave a plaintext copy sitting in .env from
            # before this feature existed.
            if "GEMINI_API_KEY" in _read_env_file():
                _write_env_value("GEMINI_API_KEY", "")
            return
        except Exception as e:
            print(f"[ai] keyring write failed, falling back to .env: {e}")
    _write_env_value("GEMINI_API_KEY", new_key)


def get_api_key() -> str:
    """
    Resolves the actual key to use for a Gemini request: an explicit
    os.environ override if one is already set (mainly a test/CI path,
    or a key saved earlier this run - see save_api_key()), otherwise
    whatever's stored via _get_stored_api_key() (keyring, falling back
    to legacy plaintext .env). Every Gemini-calling code path in this
    app (ai/main.py itself, ai/macro_generalizer.py, ai/model_client.py's
    own default) should call this rather than os.getenv("GEMINI_API_KEY")
    directly - unlike .env values, a keyring-stored key is never
    automatically loaded into os.environ at startup the way
    load_dotenv() does, so reading the environment alone would miss it.
    """
    return os.getenv("GEMINI_API_KEY") or _get_stored_api_key()


def has_api_key() -> bool:
    return bool(get_api_key())


def get_api_key_masked() -> str:
    """Masked view of the currently configured key ('' if none set) - safe to show in the UI."""
    key = get_api_key()
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return key[:4] + "•" * (len(key) - 8) + key[-4:]


def save_api_key(new_key: str) -> None:
    """
    Persists a new Gemini API key to the OS keyring (see
    _set_stored_api_key()) and applies it immediately - no app restart
    needed. Rebuilds _gemini_client (used by autonomous mode, voice,
    and vision regardless of which provider the main chat is on - see
    that global's own comment) unconditionally, since all three of
    those always need a fresh Gemini client on a Gemini key change.
    Only also rebuilds the main `chat` if Gemini happens to be the
    active provider right now - see save_api_key_for() below, which
    this delegates to.
    """
    save_api_key_for("gemini", new_key)


# ── Generalized per-provider key storage ─────────────────────────────
# Everything above this point is the original Gemini-only key storage,
# kept as-is (every existing call site - the vision tool, macro
# generalizer, model_client.py's own default - still calls get_api_key()
# and gets exactly the same behavior it always did). The functions below
# generalize the SAME underlying mechanism (keyring, falling back to
# legacy plaintext .env) to OpenAI/Anthropic too, for anyone who wants
# to use a provider other than Gemini - see
# ai/providers/__init__.py's _make_openai_client/_make_anthropic_client,
# which call get_api_key_for() rather than duplicating this storage
# logic a second and third time.

_PROVIDER_KEYRING_ACCOUNTS = {
    "gemini": _KEYRING_API_KEY_ACCOUNT, "openai": "openai_api_key",
    "anthropic": "anthropic_api_key", "local": "local_api_key",
}
_PROVIDER_ENV_VARS = {
    "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY", "local": "LOCAL_API_KEY",
}


def _get_stored_api_key_for(provider: str) -> str:
    account = _PROVIDER_KEYRING_ACCOUNTS[provider]
    env_var = _PROVIDER_ENV_VARS[provider]
    if _KEYRING_AVAILABLE:
        # Names the app has shipped under are tried after the current
        # one, so a rename does not quietly lose someone their keys.
        for service in (_KEYRING_SERVICE_NAME, *branding.LEGACY_APP_SLUGS):
            try:
                key = keyring.get_password(service, account)
            except Exception as e:
                print(f"[ai] keyring read failed for {provider} under {service!r}: {e}")
                continue
            if key:
                if service != _KEYRING_SERVICE_NAME:
                    _rehome_key(account, key)
                return key
    return _read_env_file().get(env_var, "")


def _rehome_key(account: str, key: str) -> None:
    """Copy a key found under an old service name across to the current
    one, so the fallback above only ever runs once per key."""
    try:
        keyring.set_password(_KEYRING_SERVICE_NAME, account, key)
    except Exception as e:
        print(f"[ai] Could not move {account} to {_KEYRING_SERVICE_NAME}: {e}")


def _set_stored_api_key_for(provider: str, new_key: str) -> None:
    account = _PROVIDER_KEYRING_ACCOUNTS[provider]
    env_var = _PROVIDER_ENV_VARS[provider]
    if _KEYRING_AVAILABLE:
        try:
            keyring.set_password(_KEYRING_SERVICE_NAME, account, new_key)
            if env_var in _read_env_file():
                _write_env_value(env_var, "")
            return
        except Exception as e:
            print(f"[ai] keyring write failed for {provider}, falling back to .env: {e}")
    _write_env_value(env_var, new_key)


def get_api_key_for(provider: str) -> str:
    """Generalized get_api_key() - see that function's docstring for
    the resolution order (os.environ override, then keyring/.env)."""
    return os.getenv(_PROVIDER_ENV_VARS[provider]) or _get_stored_api_key_for(provider)


def get_api_key_masked_for(provider: str) -> str:
    key = get_api_key_for(provider)
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return key[:4] + "•" * (len(key) - 8) + key[-4:]


def save_api_key_for(provider: str, new_key: str) -> None:
    """
    Persists new_key for `provider` and applies it immediately.
    Rebuilding what depends on it is split in two, deliberately:

      - Gemini specifically always forces _gemini_client to rebuild
        (None it out here; _ensure_gemini_client() lazily rebuilds on
        next use) - autonomous mode, voice, and vision need a fresh
        client the moment this key changes, regardless of whatever the
        main chat's active provider currently is.
      - The main `chat` only gets rebuilt if `provider` is the
        currently ACTIVE one - changing an inactive provider's key
        (e.g. editing your OpenAI key while actively chatting with
        Claude) shouldn't silently drop the live conversation. When it
        IS the active provider, ai.providers.switch_provider() rebuilds
        with the same model while carrying the text history over,
        rather than a hard reset that would lose it.
    """
    global chat, _gemini_client
    new_key = new_key.strip()
    _set_stored_api_key_for(provider, new_key)
    os.environ[_PROVIDER_ENV_VARS[provider]] = new_key

    if provider == "gemini":
        _gemini_client = None

    if _initialized and provider == get_active_provider():
        import ai.providers as providers
        system_instructions = _render_instructions(load_instructions())
        chat = providers.switch_provider(chat, provider, get_active_model(), tools, str(system_instructions))


def get_active_provider() -> str:
    """'gemini' | 'openai' | 'anthropic' - which provider the main chat
    (ai() below) currently talks to. Defaults to 'gemini' so an install
    that's never touched this new setting behaves exactly as it always
    did."""
    return os.getenv("KESTREL_ACTIVE_PROVIDER") or _read_env_file().get("KESTREL_ACTIVE_PROVIDER") or "gemini"


def get_active_model() -> str:
    """The active provider's selected model id. Falls back to that
    provider's curated default (ai.providers.base.default_model_for)
    if none has been explicitly picked yet, or if a stale model id from
    a PREVIOUS provider is sitting in .env (set_active_provider_and_model
    always writes both together, so this only happens from manual .env
    editing)."""
    from ai.providers.base import MODEL_CHOICES, default_model_for
    provider = get_active_provider()
    stored = os.getenv("KESTREL_ACTIVE_MODEL") or _read_env_file().get("KESTREL_ACTIVE_MODEL")
    if stored and any(c.provider == provider and c.model_id == stored for c in MODEL_CHOICES):
        return stored
    return default_model_for(provider)


def set_active_provider_and_model(provider: str, model: str) -> None:
    """
    Called by the in-chat model-switcher (app/chat_widget.py) to change
    what the main chat talks to, mid-conversation. Persists the choice
    (so it survives a restart) and, if a chat is already live, rebuilds
    it immediately via ai.providers.switch_provider() - carrying the
    text history over rather than starting blank. If nothing's been
    initialized yet, the next _ensure_initialized() call just picks
    this up naturally from .env.
    """
    global chat
    _write_env_value("KESTREL_ACTIVE_PROVIDER", provider)
    _write_env_value("KESTREL_ACTIVE_MODEL", model)
    os.environ["KESTREL_ACTIVE_PROVIDER"] = provider
    os.environ["KESTREL_ACTIVE_MODEL"] = model

    if _initialized:
        import ai.providers as providers
        system_instructions = _render_instructions(load_instructions())
        try:
            chat = providers.switch_provider(chat, provider, model, tools, str(system_instructions))
        except providers.ProviderClientError as e:
            # e.g. switching to a BYO-key provider with no key saved yet
            # - leave the OLD chat session live rather than losing it,
            # and let the caller (chat_widget.py) surface e to the user.
            print(f"[AI] Could not switch to {provider}/{model}: {e}")
            raise


# ── Local model server (Ollama / LM Studio / llama.cpp / vLLM / ...) ──
# Unlike Gemini/OpenAI/Anthropic's model ids (a short, stable, curated
# list - see ai.providers.base.MODEL_CHOICES), a local model's name is
# whatever the user has pulled/loaded into whichever runtime they're
# running, so there's no sensible fixed list to offer instead of free
# text. These two settings - persisted the same .env-backed way as
# every other setting in this module - are what the "local" provider
# actually connects to; see ai.providers._make_local_client() and
# ai.providers.create_chat_session()'s "local" branch for how they get
# used at chat-session build time.

DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"  # Ollama's own OpenAI-compatible endpoint
DEFAULT_LOCAL_MODEL_NAME = "llama3.2"


def get_local_base_url() -> str:
    """Base URL of the local/LAN OpenAI-compatible server - e.g.
    Ollama's http://localhost:11434/v1, LM Studio's
    http://localhost:1234/v1, or a llama.cpp `server`/vLLM instance at
    whatever host:port it's bound to. Always has a value (falls back to
    Ollama's default) so a first-time switch to "Local" has something
    to try rather than failing with "no URL configured" before the user
    has touched Settings at all."""
    val = os.getenv("KESTREL_LOCAL_BASE_URL")
    if val is None:
        val = _read_env_file().get("KESTREL_LOCAL_BASE_URL", "")
    return val.strip() or DEFAULT_LOCAL_BASE_URL


def set_local_base_url(new_url: str) -> None:
    """Persists the local server URL and, if "local" is the active
    provider right now, rebuilds the chat session against it
    immediately - same pattern as save_api_key_for()."""
    global chat
    new_url = new_url.strip() or DEFAULT_LOCAL_BASE_URL
    _write_env_value("KESTREL_LOCAL_BASE_URL", new_url)
    os.environ["KESTREL_LOCAL_BASE_URL"] = new_url
    if _initialized and get_active_provider() == "local":
        import ai.providers as providers
        system_instructions = _render_instructions(load_instructions())
        chat = providers.switch_provider(chat, "local", get_active_model(), tools, str(system_instructions))


def get_local_model_name() -> str:
    """The exact model name/tag to send the local server - e.g.
    "llama3.2", "qwen2.5-coder:7b", or whatever a `ollama pull` (or
    LM Studio's model picker) actually loaded. Always has a value for
    the same reason get_local_base_url() does."""
    val = os.getenv("KESTREL_LOCAL_MODEL")
    if val is None:
        val = _read_env_file().get("KESTREL_LOCAL_MODEL", "")
    return val.strip() or DEFAULT_LOCAL_MODEL_NAME


def set_local_model_name(new_model: str) -> None:
    """Persists the local model name and, if "local" is the active
    provider right now, rebuilds the chat session against it
    immediately - same pattern as save_api_key_for()."""
    global chat
    new_model = new_model.strip() or DEFAULT_LOCAL_MODEL_NAME
    _write_env_value("KESTREL_LOCAL_MODEL", new_model)
    os.environ["KESTREL_LOCAL_MODEL"] = new_model
    if _initialized and get_active_provider() == "local":
        import ai.providers as providers
        system_instructions = _render_instructions(load_instructions())
        chat = providers.switch_provider(chat, "local", get_active_model(), tools, str(system_instructions))


def get_wake_word_enabled() -> bool:
    val = os.getenv("KESTREL_WAKE_WORD_ENABLED")
    if val is None:
        val = _read_env_file().get("KESTREL_WAKE_WORD_ENABLED", "0")
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def set_wake_word_enabled(enabled: bool) -> None:
    value = "1" if enabled else "0"
    _write_env_value("KESTREL_WAKE_WORD_ENABLED", value)
    os.environ["KESTREL_WAKE_WORD_ENABLED"] = value


def get_streaming_enabled() -> bool:
    """
    Whether chat replies stream in token-by-token via each provider's
    own real network streaming (ai.main.ai_stream / send_message_stream)
    instead of arriving as one finished reply. Defaults ON - unlike
    wake word (privacy-sensitive: an always-listening mic) or telemetry
    (sends data off-device), streaming is a pure display preference
    with no such downside, so it doesn't need an opt-IN default.
    """
    val = os.getenv("KESTREL_STREAMING_ENABLED")
    if val is None:
        val = _read_env_file().get("KESTREL_STREAMING_ENABLED", "1")
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def set_streaming_enabled(enabled: bool) -> None:
    value = "1" if enabled else "0"
    _write_env_value("KESTREL_STREAMING_ENABLED", value)
    os.environ["KESTREL_STREAMING_ENABLED"] = value


def get_generated_files_dir() -> str:
    """
    Where tool_generate_file / tool_merge_files_to_pdf write the files
    they make for the user. Empty means "wherever
    ai.file_generation.default_generated_files_dir() says", which is
    Documents\\Kestrel Files - the location this was hardcoded to before
    it was configurable.

    Worth being configurable because these are the user's OWN documents,
    not app data: someone who keeps their work on a second drive, in a
    synced folder, or in a per-project directory shouldn't have to go
    fetch each generated file out of Documents afterwards.
    """
    val = os.getenv("KESTREL_GENERATED_FILES_DIR")
    if val is None:
        val = _read_env_file().get("KESTREL_GENERATED_FILES_DIR", "")
    return str(val).strip()


def set_generated_files_dir(path: str) -> None:
    """Persists the output folder. An empty string resets to the
    default rather than being stored as a broken empty path."""
    path = (path or "").strip()
    _write_env_value("KESTREL_GENERATED_FILES_DIR", path)
    os.environ["KESTREL_GENERATED_FILES_DIR"] = path


def get_voice_review_before_send() -> bool:
    """
    Whether a finished voice transcript lands in the chat's input box
    for you to read, edit or delete before it goes (default), or is
    sent the instant transcription finishes.

    Defaults ON: speech recognition mishears things, and a transcript
    that's already been sent can only be fixed by sending a correction
    after it. The off state is still worth keeping for genuinely
    hands-free use - talking to the app from across the room, where
    having to reach for Enter defeats the point.
    """
    val = os.getenv("KESTREL_VOICE_REVIEW_BEFORE_SEND")
    if val is None:
        val = _read_env_file().get("KESTREL_VOICE_REVIEW_BEFORE_SEND", "1")
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def set_voice_review_before_send(enabled: bool) -> None:
    value = "1" if enabled else "0"
    _write_env_value("KESTREL_VOICE_REVIEW_BEFORE_SEND", value)
    os.environ["KESTREL_VOICE_REVIEW_BEFORE_SEND"] = value


def get_extended_thinking_enabled() -> bool:
    """
    Whether Claude should think out loud before answering - the same
    "extended thinking" feature/toggle claude.ai itself exposes,
    currently wired up only for ai.providers.anthropic_provider (see
    that module's own docstring). Defaults OFF: unlike streaming (a
    pure display preference), this genuinely costs extra output
    tokens and latency on every single message, on top of only ever
    doing anything when the active model is a Claude one - so, same
    as wake word/telemetry, it needs an opt-IN default rather than an
    opt-out one.
    """
    val = os.getenv("KESTREL_EXTENDED_THINKING_ENABLED")
    if val is None:
        val = _read_env_file().get("KESTREL_EXTENDED_THINKING_ENABLED", "0")
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def set_extended_thinking_enabled(enabled: bool) -> None:
    value = "1" if enabled else "0"
    _write_env_value("KESTREL_EXTENDED_THINKING_ENABLED", value)
    os.environ["KESTREL_EXTENDED_THINKING_ENABLED"] = value


# ── Assistant name ────────────────────────────────────────────────
# The single source of truth for what the assistant calls itself
# read by everything that speaks *as* or *about* the assistant: the
# system prompt sent to Gemini (via _render_instructions below), the
# wake word listener (app/workers/wakeword_worker.py derives its
# match stems from this instead of a hardcoded name), the Settings
# page's wake-word copy, and the handful of user-facing status/dialog
# strings in app/ that refer to the assistant by name. Deliberately
# separate from the product name (see branding.py): renaming who you
# talk to should not rebrand the application, and naming them the same
# thing is why the assistant used to read as having no name at all.
DEFAULT_ASSISTANT_NAME = branding.ASSISTANT_DEFAULT_NAME

# ── Conversation titles ───────────────────────────────────────────────

# A title is a label in a list, so it earns one cheap call and no more.
TITLE_MAX_CHARS = 48
_TITLE_MODEL = "gemini-3-flash-preview"
_TITLE_PROMPT = (
    "Give this conversation a title from the user's opening message.\n\n"
    "Rules:\n"
    "- Name what they are asking about, not what they said. "
    '"How do I get Excel to stop rounding my part numbers" becomes '
    '"Excel rounding part numbers", not "How do I get Excel to".\n'
    "- At most 6 words, no trailing period, no surrounding quotes.\n"
    "- Same language as the message.\n"
    "- Nonsense, keyboard mashing or a bare test string becomes a title "
    "describing what it IS, not a copy of it: \"Testing output\", "
    "\"Keyboard mash\". Never echo the gibberish back.\n"
    "- A greeting or something with no subject becomes: New chat\n\n"
    "Message:\n{message}\n\nTitle:"
)


_VOWELS = set("aeiouy\u00e0\u00e2\u00e4\u00e9\u00e8\u00ea\u00eb\u00ee\u00ef\u00f4\u00f6\u00f9\u00fb\u00fc")


def looks_like_gibberish(text: str) -> bool:
    """
    Whether a message is a keyboard mash rather than words.

    Strict on purpose, because the one caller that matters is the
    fallback title: getting this wrong turns a real message nobody can
    reach the model for into "New chat". Two signals, both of which a
    mash has and language does not:

      - four or more of the same character in a row ("aazeeeeeeee")
      - a long token that is almost all consonants ("asdfgh")

    "hello" and "kubernetes" are not gibberish by either test, which is
    the point. Whether a title is worth a model call is a different and
    much looser question; see suggest_chat_title.
    """
    text = " ".join((text or "").split())
    if not text:
        return True

    previous, run = "", 1
    for char in text.lower():
        run = run + 1 if char == previous else 1
        if run >= 4:
            return True
        previous = char

    letters = [c for c in text.lower() if c.isalpha()]
    if len(text.split()) == 1 and len(letters) >= 5:
        vowels = sum(1 for c in letters if c in _VOWELS)
        if vowels / len(letters) < 0.2:
            return True
    return False


def fallback_chat_title(message: str) -> str:
    """The first message, truncated. Used immediately on send, and kept
    if the model is slow, unreachable or unhelpful."""
    text = " ".join((message or "").split())
    if not text:
        return "New chat"
    if looks_like_gibberish(text):
        # Better a neutral label than a row in the sidebar reading
        # "aazeeeeeeeeeeeee" forever.
        return "New chat"
    if len(text) <= TITLE_MAX_CHARS:
        return text
    return text[:TITLE_MAX_CHARS].rstrip() + "\u2026"


def suggest_chat_title(message: str) -> str:
    """
    A title describing what the opening message is ASKING, which is not
    the same as its first forty characters: a long question truncates to
    its preamble, and a pasted error truncates to a stack frame.

    Never raises. Returns the fallback on any failure, so a chat is never
    left without a title because the network was down.
    """
    text = " ".join((message or "").split())
    if not text:
        return "New chat"
    # Short AND made of several real words is already a usable label, so
    # it is not worth a call. Length alone is not the test: a single
    # token is short and still says nothing, which is how
    # "aazeeeeeeeeeeeee" ended up as a chat title.
    # Several real words and short enough to read as a label already, so
    # not worth a call. A single token is short and still says nothing,
    # which is how "aazeeeeeeeeeeeee" ended up as a chat title, so that
    # goes to the model however short it is.
    if len(text) <= 24 and len(text.split()) >= 2 and not looks_like_gibberish(text):
        return fallback_chat_title(text)

    try:
        import ai.model_client as model_client
        client = model_client.make_client()
        result = client.models.generate_content(
            model=_TITLE_MODEL,
            contents=_TITLE_PROMPT.format(message=text[:2000]),
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=24,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        title = (result.text or "").strip()
    except Exception as e:
        print(f"[title] Falling back to the truncated message: {e}")
        return fallback_chat_title(text)

    return _clean_title(title) or fallback_chat_title(text)


def _clean_title(title: str) -> str:
    """Strip what a model tends to wrap a title in, and refuse anything
    that came back as a sentence rather than a label."""
    raw = title or ""
    # Checked before whitespace is collapsed: a model that answers with a
    # preamble and then the title puts a newline between them, and
    # collapsing first would hide exactly the thing being looked for.
    if "\n" in raw.strip():
        return ""
    title = " ".join(raw.split())
    title = title.strip("\"'\u201c\u201d\u00ab\u00bb ")
    # A model that ignored the format usually says "Title: ..." first.
    for prefix in ("Title:", "title:"):
        if title.startswith(prefix):
            title = title[len(prefix):].strip()
    title = title.rstrip(".")
    if not title or len(title) > TITLE_MAX_CHARS * 2:
        return ""
    return title[:TITLE_MAX_CHARS].rstrip()



def get_assistant_name() -> str:
    val = os.getenv("KESTREL_ASSISTANT_NAME")
    if val is None:
        val = _read_env_file().get("KESTREL_ASSISTANT_NAME", "")
    val = str(val).strip()
    return val or DEFAULT_ASSISTANT_NAME


DEFAULT_WEBSITE_URL = branding.DEFAULT_WEBSITE_URL


def get_website_url() -> str:
    """Base URL of the project's site. Overridable via
    KESTREL_WEBSITE_URL for a fork serving its own."""
    val = os.getenv("KESTREL_WEBSITE_URL")
    if val is None:
        val = _read_env_file().get("KESTREL_WEBSITE_URL", "")
    return str(val).strip() or DEFAULT_WEBSITE_URL


def is_assistant_name_configured() -> bool:
    """
    True once a name has ever been explicitly saved via
    set_assistant_name() - including a first-run screen calling it to
    accept the default by name (see app/name_gate_widget.py) - as
    opposed to get_assistant_name() merely falling back to
    DEFAULT_ASSISTANT_NAME because nothing has been configured yet.
    main.py uses this, before anything else is built, to decide
    whether that first-run screen needs to show at all.
    """
    return "KESTREL_ASSISTANT_NAME" in os.environ or "KESTREL_ASSISTANT_NAME" in _read_env_file()


def set_assistant_name(new_name: str, rebuild: bool = True) -> None:
    """
    Persists a new assistant name to .env and applies it immediately.
    Rebuilds the live chat session (same reason save_api_key() resets
    state: the old system_instruction - with the old name baked in -
    belongs to a chat session that's already been created) via
    refresh_actions(), which is a no-op cost-wise if the AI pipeline
    hasn't been touched yet this run.

    rebuild=False skips that refresh_actions() call - persisting the
    name (and updating os.environ, so get_assistant_name() reflects it
    immediately) without rebuilding the chat session at all. Used
    exclusively by ai.profiles.activate_profile(), which always makes a
    set_active_provider_and_model() call of its own right after this -
    and THAT call already does its own full chat-session rebuild
    (providers.switch_provider(), with the new name already in effect
    for its system-prompt render). Without rebuild=False, a profile
    switch used to pay for two full rebuilds back to back - this one's
    refresh_actions() (which also reloads every macro from disk, on top
    of building a session that's about to be discarded) immediately
    followed by set_active_provider_and_model's own - doubling the
    visible delay for no benefit, since the first rebuilt session never
    got used for anything. Every OTHER caller (a plain rename from
    Settings, the first-run name gate) still wants the default
    rebuild=True: nothing else follows them up with a
    set_active_provider_and_model() call of its own.
    """
    new_name = new_name.strip()
    if not new_name:
        return
    _write_env_value("KESTREL_ASSISTANT_NAME", new_name)
    os.environ["KESTREL_ASSISTANT_NAME"] = new_name
    if _initialized and rebuild:
        refresh_actions()


def _render_instructions(raw: str) -> str:
    """
    Substitutes the {{ASSISTANT_NAME}} placeholder used throughout the
    ai/*_instructions.txt files with the currently configured assistant
    name, then appends the ACTIVE PROFILE's own custom instructions (see
    ai.profiles.Profile.system_prompt), if it has any - every profile
    still gets this same shared base first, a custom prompt only ever
    adds to it, never replaces it, so a profile only needs to describe
    what makes IT different rather than restate every baseline
    behavior/safety rule from scratch. Callers that build a
    system_instruction for the model should always go through this
    rather than using load_file(...) directly.

    Deliberately NOT used by the preference-update path (see
    "[[UPDATE_START]]" handling below), which loads/saves those same
    files raw - the placeholder must survive on disk so it keeps
    resolving to whatever name is configured at the time, rather than
    being permanently baked in as whatever name happened to be active
    the first time a preference was appended.
    """
    rendered = raw.replace("{{ASSISTANT_NAME}}", get_assistant_name())
    custom_prompt = _active_profile_prompt()
    if custom_prompt:
        rendered = f"{rendered}\n\n{custom_prompt}"
    return rendered


def _active_profile_prompt() -> str:
    """
    The active AI profile's own extra instructions, or "" if it has
    none set (the common case - most profiles never need one) or if
    ai.profiles itself can't be read for any reason (a corrupt
    profiles.json shouldn't take down every system-prompt render along
    with it - falling back to "no extra prompt" is the safe default,
    identical to how this app behaved before profiles existed at all).
    """
    try:
        import ai.profiles as profiles
        active = profiles.get_active_profile()
        return (active.system_prompt or "").strip() if active else ""
    except Exception as e:
        print(f"[ai.main] Couldn't read the active profile's custom prompt: {e}")
        return ""


# ── Theme (dark / light) ─────────────────────────────────────────────
VALID_THEMES = ("dark", "light")
DEFAULT_THEME = "dark"  # unchanged look for anyone upgrading - opt into light


def get_theme() -> str:
    val = os.getenv("KESTREL_THEME")
    if val is None:
        val = _read_env_file().get("KESTREL_THEME", DEFAULT_THEME)
    val = str(val).strip().lower()
    return val if val in VALID_THEMES else DEFAULT_THEME


def set_theme(theme: str) -> None:
    theme = str(theme).strip().lower()
    if theme not in VALID_THEMES:
        theme = DEFAULT_THEME
    _write_env_value("KESTREL_THEME", theme)
    os.environ["KESTREL_THEME"] = theme


# ── Interface language ────────────────────────────────────────────────
# Empty means "follow the OS". The catalogs themselves live in
# app/locale/; see app/i18n.py.

def get_language() -> str:
    val = os.getenv("KESTREL_LANGUAGE")
    if val is None:
        val = _read_env_file().get("KESTREL_LANGUAGE", "")
    return str(val).strip().lower()


def set_language(code: str) -> None:
    code = str(code).strip().lower()
    _write_env_value("KESTREL_LANGUAGE", code)
    os.environ["KESTREL_LANGUAGE"] = code


# ── Autonomous-mode screenshot optimization ──────────────────────────
# Every step of autonomous mode grabs a full screenshot and sends it to
# Gemini - by far the single biggest recurring token cost in the app
# (see SCREENSHOT_QUALITY_PRESETS' docstring below for the actual levers
# and why). Configurable rather than hardcoded, since the right tradeoff
# depends on the task: fine detail (small text, precise target icons)
# genuinely needs more resolution than e.g. "click the next big button".
SCREENSHOT_QUALITY_PRESETS = {
    # max_side: the longer of the CAPTURED IMAGE's own width/height (PIL's
    # screen.width/screen.height) is capped at this many pixels, aspect
    # ratio preserved - NOT pyautogui.size(). Those two can genuinely
    # differ (DPI scaling - see get_position_object_and_click's own
    # coordinate-conversion comment above for a case where mixing them up
    # already caused a real bug), and resizing off the wrong one would
    # either under- or over-shoot the intended reduction depending on the
    # display's scale factor.
    #
    # jpeg_quality: None means "pass the PIL Image straight to the SDK as-
    # is" (original, lossless, whatever the SDK's own default encoding
    # is). A number re-encodes to actual JPEG bytes at that quality
    # ourselves before sending - this is the one part of this table that's
    # a real, verifiable, LOCALLY-provable byte-size reduction (see
    # ai/dev_tools/screenshot_quality_check.py); resizing is the part
    # that's documented to reduce Gemini's image TOKEN count specifically
    # (Gemini's vision encoder tokenizes images by resolution/tile count,
    # not by file size), so resizing is the lever that actually matters
    # for the cost this setting exists to address - JPEG quality mostly
    # just trims transfer bytes/latency on top of that.
    "original": {"max_side": None, "jpeg_quality": None},
    "balanced": {"max_side": 1120, "jpeg_quality": 85},
    "compact":  {"max_side": 768,  "jpeg_quality": 70},
    # Below "compact" - graduated down to 144px for empirically finding
    # where the model actually stops being able to do the task, since
    # that point depends on what's on screen (small text/icons need more
    # resolution than "click the big button") and isn't something to
    # guess at from here. Try each on the actual task and check two
    # things per step: the Console's "[autonomous] Screenshot tokens
    # this step (<preset>)" line (the real token cost), and whether
    # clicks still land correctly - once accuracy visibly degrades,
    # that's the floor for that particular task, not a fixed number.
    "tiny":     {"max_side": 512, "jpeg_quality": 60},
    "minimal":  {"max_side": 384, "jpeg_quality": 55},
    "extreme":  {"max_side": 256, "jpeg_quality": 50},
    "floor":    {"max_side": 144, "jpeg_quality": 45},
}
DEFAULT_SCREENSHOT_QUALITY = "balanced"


def get_autonomous_screenshot_quality() -> str:
    val = os.getenv("KESTREL_AUTONOMOUS_SCREENSHOT_QUALITY")
    if val is None:
        val = _read_env_file().get("KESTREL_AUTONOMOUS_SCREENSHOT_QUALITY", DEFAULT_SCREENSHOT_QUALITY)
    val = str(val).strip().lower()
    return val if val in SCREENSHOT_QUALITY_PRESETS else DEFAULT_SCREENSHOT_QUALITY


def set_autonomous_screenshot_quality(quality: str) -> None:
    quality = str(quality).strip().lower()
    if quality not in SCREENSHOT_QUALITY_PRESETS:
        quality = DEFAULT_SCREENSHOT_QUALITY
    _write_env_value("KESTREL_AUTONOMOUS_SCREENSHOT_QUALITY", quality)
    os.environ["KESTREL_AUTONOMOUS_SCREENSHOT_QUALITY"] = quality


def prepare_autonomous_screenshot(screen, quality: str | None = None):
    """
    Returns whatever should actually be handed to send_message([prompt,
    THIS]) in place of the raw `screen` PIL Image - either that same
    Image unchanged ("original"), or a resized + re-encoded
    types.Part.from_bytes for anything else.

    Sizing is computed from `screen.width`/`screen.height` (the actual
    captured image's own pixel dimensions) - deliberately NOT from
    pyautogui.size() (the logical/DPI-scaled screen size Windows reports
    for mouse coordinates). Those two can differ under display scaling;
    this function's whole job is shrinking the IMAGE that's about to be
    sent, so it has to measure the image, not a separate, possibly-
    different number that happens to also describe "the screen".
    """
    quality = quality or get_autonomous_screenshot_quality()
    preset = SCREENSHOT_QUALITY_PRESETS.get(quality, SCREENSHOT_QUALITY_PRESETS[DEFAULT_SCREENSHOT_QUALITY])

    if preset["max_side"] is None and preset["jpeg_quality"] is None:
        return screen  # "original" - unchanged, whatever the SDK's own default encoding is

    img = screen
    max_side = preset["max_side"]
    if max_side is not None:
        longest = max(img.width, img.height)
        if longest > max_side:
            scale = max_side / longest
            new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
            img = img.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=preset["jpeg_quality"])
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")


# Global keyboard shortcuts (system-wide, work even when Kestrel isn't
# focused - registered via the `keyboard` library). Everything else
# keyboard-related in the app (Enter to send, Shift+Enter for a newline)
# is a fixed in-window convention, not a rebindable global shortcut.
DEFAULT_SHORTCUTS = {
    "toggle_window": "ctrl+shift+space",
    "always_on_top": "ctrl+alt+p",
    "screenshot":    SHORTCUT_SCREEN_VOICE,  # same default as before ("ctrl+alt+s")
    "stop_automation": "ctrl+alt+x",
    # Recorder triggers (app/workers/recorder_worker.py) - unlike the four
    # above, these aren't bound as app-level global hotkeys via
    # MainWindow.register_global_shortcuts(); RecorderController reads
    # them directly with get_shortcut() each time recording starts. Can
    # be a keyboard combo (same format as the others) OR a mouse button,
    # written as "mouse:<button>" - "mouse:left", "mouse:right",
    # "mouse:middle", "mouse:x", "mouse:x2" (back/forward side buttons).
    "recorder_right_click":   "f6",
    "recorder_click":         "f7",
    "recorder_toggle_typing": "f8",
    "recorder_enter":         "f9",
    "recorder_stop":          "f10",
}

SHORTCUT_LABELS = {
    "toggle_window": "Show / hide the window",
    "always_on_top": "Always on top",
    "screenshot":    "Take a screenshot",
    "stop_automation": "Stop automation now",
    "recorder_right_click":   "Recorder: right-click at mouse position",
    "recorder_click":         "Recorder: click at mouse position",
    "recorder_toggle_typing": "Recorder: toggle typing mode",
    "recorder_enter":         "Recorder: Enter key",
    "recorder_stop":          "Recorder: stop recording",
}

# The 4 handled by MainWindow.register_global_shortcuts()/rebind_shortcut()
# - separate from RECORDER_SHORTCUT_IDS below since they're bound
# differently (real OS-wide hotkeys via the `keyboard` library, active
# the whole time the app runs) and can't be a mouse button (rebind_shortcut
# hands the combo straight to keyboard.add_hotkey, which only understands
# keyboard combos).
GLOBAL_SHORTCUT_IDS = ("toggle_window", "always_on_top", "screenshot", "stop_automation")

# Read by RecorderController itself, only while actively recording - see
# that class and DEFAULT_SHORTCUTS' docstring-comment above for why these
# support "mouse:<button>" values and the other 4 don't.
RECORDER_SHORTCUT_IDS = (
    "recorder_right_click", "recorder_click", "recorder_toggle_typing",
    "recorder_enter", "recorder_stop",
)


def get_shortcut(shortcut_id: str) -> str:
    """Currently configured key combo for a named global shortcut, falling
    back to its default (e.g. get_shortcut('toggle_window'))."""
    env_key = f"KESTREL_SHORTCUT_{shortcut_id.upper()}"
    val = os.getenv(env_key)
    if val is None:
        val = _read_env_file().get(env_key)
    return val or DEFAULT_SHORTCUTS.get(shortcut_id, "")


def set_shortcut(shortcut_id: str, combo: str) -> None:
    env_key = f"KESTREL_SHORTCUT_{shortcut_id.upper()}"
    _write_env_value(env_key, combo)
    os.environ[env_key] = combo


DEFAULT_MACRO_ACTION_DELAY = 1.0  # seconds - matches the old hardcoded value
MIN_MACRO_ACTION_DELAY = 0.1
MAX_MACRO_ACTION_DELAY = 10.0


def get_macro_action_delay() -> float:
    """
    Seconds to wait after each step while replaying a macro (play_actions in
    ai/actions.py). Needs to be adjustable rather than a fixed constant: a
    step that opens a website or an app depends on network/load speed and
    may need much longer than a step that's just a local click, and a slow
    connection needs more headroom across the board than a fast one.
    """
    val = os.getenv("KESTREL_MACRO_ACTION_DELAY")
    if val is None:
        val = _read_env_file().get("KESTREL_MACRO_ACTION_DELAY")
    try:
        delay = float(val) if val is not None else DEFAULT_MACRO_ACTION_DELAY
    except ValueError:
        delay = DEFAULT_MACRO_ACTION_DELAY
    return min(max(delay, MIN_MACRO_ACTION_DELAY), MAX_MACRO_ACTION_DELAY)


def set_macro_action_delay(seconds: float) -> None:
    seconds = min(max(float(seconds), MIN_MACRO_ACTION_DELAY), MAX_MACRO_ACTION_DELAY)
    value = str(seconds)
    _write_env_value("KESTREL_MACRO_ACTION_DELAY", value)
    os.environ["KESTREL_MACRO_ACTION_DELAY"] = value


# ── Autonomous mode: max steps ───────────────────────────────────────
# A ceiling, not a target: autonomous mode always stops on its own as
# soon as the goal is visibly complete (tool_name_action is called) or a
# single action finishes the task - see run_autonomous_mode()'s docstring
# for the full list of early-stop conditions. This setting only matters
# when NONE of those fire (the model gets stuck, keeps "almost" finishing,
# or the goal turns out to need more steps than expected) - it's what
# stops Kestrel from wandering indefinitely in that case.
DEFAULT_MAX_AUTOMATION_STEPS = 20  # also the recommended value
MIN_AUTOMATION_STEPS = 3
MAX_AUTOMATION_STEPS = 100


def get_max_automation_steps() -> int:
    val = os.getenv("KESTREL_MAX_AUTOMATION_STEPS")
    if val is None:
        val = _read_env_file().get("KESTREL_MAX_AUTOMATION_STEPS")
    try:
        steps = int(val) if val is not None else DEFAULT_MAX_AUTOMATION_STEPS
    except ValueError:
        steps = DEFAULT_MAX_AUTOMATION_STEPS
    return min(max(steps, MIN_AUTOMATION_STEPS), MAX_AUTOMATION_STEPS)


def set_max_automation_steps(steps: int) -> None:
    steps = min(max(int(steps), MIN_AUTOMATION_STEPS), MAX_AUTOMATION_STEPS)
    value = str(steps)
    _write_env_value("KESTREL_MAX_AUTOMATION_STEPS", value)
    os.environ["KESTREL_MAX_AUTOMATION_STEPS"] = value


# ── Safety framework: per-tier action limits ─────────────────────────
# Persisted overrides for ai.safety.DEFAULT_MAX_ACTIONS, editable from
# Settings. The limiter itself (ai.safety.limiter) is reset at the start
# of every autonomous run and whenever reset_safety_limits() is called.

def get_safety_limit(level) -> int:
    from ai.safety import DEFAULT_MAX_ACTIONS
    env_key = f"KESTREL_SAFETY_LIMIT_{level.value.upper()}"
    val = os.getenv(env_key)
    if val is None:
        val = _read_env_file().get(env_key)
    try:
        return int(val) if val is not None else DEFAULT_MAX_ACTIONS[level]
    except ValueError:
        return DEFAULT_MAX_ACTIONS[level]


def set_safety_limit(level, value: int) -> None:
    from ai.safety import limiter
    value = max(0, int(value))
    env_key = f"KESTREL_SAFETY_LIMIT_{level.value.upper()}"
    _write_env_value(env_key, str(value))
    os.environ[env_key] = str(value)
    limiter.set_limit(level, value)


def get_telemetry_enabled() -> bool:
    """
    Opt-in, off by default - GDPR/CCPA require an affirmative choice for
    this, not a pre-checked box. See ai/telemetry.py for what this
    actually gates (crash reports only, no usage/content telemetry).
    """
    val = os.getenv("KESTREL_TELEMETRY_ENABLED")
    if val is None:
        val = _read_env_file().get("KESTREL_TELEMETRY_ENABLED", "0")
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def set_telemetry_enabled(enabled: bool) -> None:
    value = "1" if enabled else "0"
    _write_env_value("KESTREL_TELEMETRY_ENABLED", value)
    os.environ["KESTREL_TELEMETRY_ENABLED"] = value
    import ai.telemetry as telemetry
    telemetry.set_enabled(enabled)


def reset_safety_limits() -> None:
    """
    Applies the currently configured (or default) per-tier caps to the
    shared limiter and clears this session's counts. Called once at
    startup and again at the start of every autonomous run, so each run
    gets a fresh budget rather than sharing one with everything that ran
    before it.
    """
    from ai.safety import limiter, RiskLevel
    for level in RiskLevel:
        limiter.set_limit(level, get_safety_limit(level))
    limiter.reset()


# ── Sound effects / notifications ────────────────────────────────────
# Short, distinct beep patterns (Windows-only via winsound) so events that
# happen while Kestrel isn't in the foreground - an automation finishing,
# the wake word triggering, a screenshot being captured - are still
# noticeable. Silently does nothing if winsound isn't available (e.g. not
# on Windows) or a kind isn't recognized.
_SOUND_PATTERNS = {
    "screenshot":           [(1000, 200)],
    "automation_finished":  [(700, 120), (1000, 120), (1300, 180)],
    "automation_stopped":   [(500, 250)],
    "macro_finished":       [(900, 130), (1200, 160)],
    "wake_word":            [(1200, 120)],
    "confirm_accept":       [(900, 110), (1300, 150)],
    "confirm_refuse":       [(500, 200)],
    "human_handoff":        [(1100, 150), (700, 150), (1100, 150)],
}


def play_notification_sound(kind: str) -> None:
    if winsound is None:
        return
    pattern = _SOUND_PATTERNS.get(kind)
    if not pattern:
        return
    try:
        for freq, duration_ms in pattern:
            winsound.Beep(freq, duration_ms)
    except Exception as e:
        print(f"[sound] Could not play '{kind}' notification: {e}")


def stop_automation_now() -> None:
    """
    Immediately halts whatever autonomous/macro/tool action is in flight
    - same underlying flag the PyAutoGUI corner fail-safe sets
    (actions.emergency_stop), just reachable without touching the mouse.
    Checked at the top of every action tool and before/after every
    autonomous-mode step (see ai.actions._check_emergency_stop and
    run_autonomous_mode), so this takes effect within at most one step.
    Bound to the "stop_automation" global shortcut by MainWindow.
    """
    actions.emergency_stop = True
    print("[stop] Stop shortcut triggered - halting any running automation.")
    play_notification_sound("automation_stopped")


def confirm_human_handoff_resolved() -> None:
    """
    Called by the UI (the "I've handled it - Continue" card MainWindow
    shows after tool_request_human_handoff pauses a run) once the human
    has resolved the CAPTCHA/MFA/verification step. Thin wrapper around
    actions.resume_human_handoff() kept here so main_window.py only ever
    reaches through ai_main for automation control, the same way it
    already does for stop_automation_now().
    """
    actions.resume_human_handoff()
    print("[human_handoff] Human confirmed - resuming autonomous mode.")


def _migrate_legacy_env_file() -> None:
    """
    One-time upgrade path: earlier builds put .env next to the exe
    (BASE_PATH) - see ENV_PATH's own comment above for why that was wrong
    for anyone with a real (non-source) install. Only relevant for frozen
    builds: when running from source, BASE_PATH and USER_DATA_PATH are
    the same directory already, so old_path == ENV_PATH and this is a
    harmless no-op. If someone upgrading a real install already has an
    old .env sitting next to the exe and nothing yet at the new location,
    copy it over so their API key/settings don't just silently vanish -
    without this, ENV_PATH's fix above would look correct in every way
    and still break every existing user on upgrade.
    """
    old_path = os.path.join(BASE_PATH, ".env")
    if old_path == ENV_PATH:
        return
    if os.path.exists(ENV_PATH) or not os.path.exists(old_path):
        return
    try:
        os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
        with open(old_path, "r", encoding="utf-8") as src, open(ENV_PATH, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        print(f"[startup] Migrated .env from {old_path} to {ENV_PATH} (one-time upgrade step)")
    except Exception as e:
        print(f"[startup] Couldn't migrate old .env ({old_path} -> {ENV_PATH}): {e}")


def _seed_instructions_file() -> None:
    """
    Put the shipped instructions.txt in place under USER_DATA_PATH the
    first time the app runs, so the assistant starts from the prompt the
    build ships with rather than load_file's bare fallback.

    Two cases, one code path. On a fresh install the source is the
    read-only bundled copy next to the exe. On an upgrade from a build
    that still wrote to BASE_PATH, that same file is the user's own
    prompt with their learned preferences appended to it, so copying it
    carries those across instead of resetting them. Either way it only
    ever runs when nothing exists at the new location yet, and it is a
    no-op from source, where BASE_PATH and USER_DATA_PATH are the same
    directory and the two paths are identical.
    """
    if BUNDLED_INSTRUCTIONS_PATH == INSTRUCTIONS_PATH:
        return
    if os.path.exists(INSTRUCTIONS_PATH) or not os.path.exists(BUNDLED_INSTRUCTIONS_PATH):
        return
    try:
        os.makedirs(os.path.dirname(INSTRUCTIONS_PATH), exist_ok=True)
        with open(BUNDLED_INSTRUCTIONS_PATH, "r", encoding="utf-8") as src, \
                open(INSTRUCTIONS_PATH, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        print(f"[startup] Seeded instructions.txt at {INSTRUCTIONS_PATH} (one-time step)")
    except Exception as e:
        print(f"[startup] Couldn't seed instructions.txt "
              f"({BUNDLED_INSTRUCTIONS_PATH} -> {INSTRUCTIONS_PATH}): {e}")


def _migrate_legacy_env_keys() -> None:
    """
    Rename settings keys written under a name the app used to ship under
    (see branding.LEGACY_ENV_PREFIXES). Settings live in .env as
    <PREFIX>_THEME and the like, so a rename would otherwise reset
    everyone's theme, shortcuts, model choice and assistant name.

    Only renames a key the new prefix does not already have, and leaves
    every other line alone. Failures are logged, not raised: losing
    settings is bad, not starting is worse.
    """
    if not os.path.exists(ENV_PATH):
        return
    prefix = branding.ENV_PREFIX + "_"
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        print(f"[startup] Couldn't read .env to migrate settings keys: {e}")
        return

    present = {l.split("=", 1)[0].strip() for l in lines if "=" in l and not l.strip().startswith("#")}
    changed = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        for legacy in branding.LEGACY_ENV_PREFIXES:
            old_prefix = legacy + "_"
            if key.startswith(old_prefix):
                new_key = prefix + key[len(old_prefix):]
                if new_key not in present:
                    lines[i] = line.replace(key, new_key, 1)
                    present.add(new_key)
                    changed = True
                break

    if not changed:
        return
    try:
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print(f"[startup] Moved settings keys to the {prefix} prefix (one-time upgrade step)")
    except OSError as e:
        print(f"[startup] Couldn't rewrite .env settings keys: {e}")


def _ensure_gemini_client():
    """
    Builds (once, lazily) the always-Gemini client used by autonomous
    mode, voice mode, and the vision tool - see the `_gemini_client`
    global's own comment for why this stays decoupled from whichever
    provider the main `chat` is currently switched to. Safe to call
    repeatedly; a no-op once built until save_api_key_for("gemini", ...)
    clears it again.
    """
    global _gemini_client
    if _gemini_client is not None:
        return
    import ai.model_client as model_client
    api_key = get_api_key()
    if api_key:
        # Caches a keyring-resolved key into the process environment so
        # every other os.getenv("GEMINI_API_KEY") call site in this run
        # keeps working without its own keyring-aware lookup, the same
        # way load_dotenv() already does for the legacy plaintext .env.
        os.environ["GEMINI_API_KEY"] = api_key
    else:
        print("[AI] WARNING: GEMINI_API_KEY not found - autonomous mode, voice, and vision will not work.")
    _gemini_client = model_client.make_client(api_key_override=api_key)


def drop_macro_tool(name: str) -> None:
    """
    Remove one deleted macro from the live tool list, without rebuilding
    anything else.

    The whole-store alternative is refresh_actions(), which reloads
    ai.actions, re-reads and re-execs every surviving macro, re-renders
    the system prompt and constructs a fresh provider chat session. That
    is a great deal of work to stop one function existing, and deleting
    a macro used to do it twice over.

    The chat session is not rebuilt here. It is marked stale instead, and
    _ensure_chat_session_fresh rebuilds it before the next message
    actually goes to the model - which is the only moment the model's
    view of the tool list matters. Deleting three macros in a row now
    costs one rebuild at the next turn rather than three immediately.
    """
    global tools, _tools_stale

    from ai import macro_store
    macro_store.unregister_macro(actions, name)

    # Rebuild the in-process list from the live namespace. This is
    # inspect.getmembers over one module - no file I/O, no compiling.
    tools = get_all_tools(actions)
    tools.append(get_position_object_and_click)
    _tools_stale = True
    print(f"[AI] Dropped tool for macro {name!r}. {len(tools)} tools available.")


def _ensure_chat_session_fresh() -> None:
    """
    Rebuild the chat session if the tool list changed since it was
    built. Cheap and a no-op in the normal case.
    """
    global chat, _tools_stale
    if not _tools_stale:
        return
    _tools_stale = False

    import ai.providers as providers
    system_instructions = _render_instructions(load_instructions())
    try:
        chat = providers.create_chat_session(
            get_active_provider(), get_active_model(), tools, str(system_instructions)
        )
    except providers.ProviderClientError as e:
        print(f"[AI] Could not rebuild chat session: {e}")
        chat = None


def _ensure_initialized():
    """
    Lazy initialization - only runs once per process (or after
    save_api_key_for/set_active_provider_and_model resets _initialized).
    Loads .env, builds the always-Gemini client (autonomous/voice/vision),
    and builds the main `chat` for whichever provider+model is currently
    active (see get_active_provider/get_active_model) - Gemini by
    default, so an install that's never touched the model switcher
    behaves exactly as it always did.
    """
    global _initialized, chat, tools
    if _initialized:
        return

    _migrate_legacy_env_file()
    _migrate_legacy_env_keys()
    _seed_instructions_file()
    load_dotenv(ENV_PATH)

    _ensure_gemini_client()

    system_instructions = _render_instructions(load_instructions())

    # Base tools = all tool_* functions (including saved macros, loaded
    # from their JSON records - see ai.macro_store) + vision locator
    from ai import macro_store
    macro_store.load_and_register_all(actions)
    tools = get_all_tools(actions)
    tools.append(get_position_object_and_click)

    provider = get_active_provider()
    model = get_active_model()
    import ai.providers as providers
    try:
        chat = providers.create_chat_session(provider, model, tools, str(system_instructions))
        print(f"[AI] {provider}/{model} chat session initialized.")
    except providers.ProviderClientError as e:
        # e.g. a BYO-key install switched to OpenAI/Anthropic without
        # saving a key for it yet. Left as chat=None rather than raising
        # - ai() below checks for exactly this and returns e's own
        # message as the chat reply, the same user-visible shape the
        # old "no Gemini key" message always had.
        print(f"[AI] {e}")
        chat = None

    reset_safety_limits()

    print(f"[AI] {len(tools)} tools loaded.")
    _initialized = True


# ══════════════════════════════════════════════════════════════════
# REFRESH - reload actions module and rebuild chat
# ══════════════════════════════════════════════════════════════════

def refresh_actions():
    """
    Reload ai.actions to pick up newly saved macros, then rebuild the
    main chat session (whichever provider/model is currently active -
    see get_active_provider/get_active_model) with the updated tool
    list. Called automatically after autonomous mode saves/deletes a
    macro, and available in the UI via the Refresh button in Tools tab.
    """
    global chat, tools
    # Not _ensure_initialized(). Registering a macro is entirely local -
    # it reads JSON off disk and execs it into a module namespace - and
    # nothing about it needs a model, a provider client or an API key.
    # Requiring one meant recording a macro failed outright on an install
    # with no key configured yet, which is a perfectly reasonable state
    # to be in: the recorder and the whole macro system work without one.
    #
    # The chat session below is still rebuilt when it can be, and
    # skipped when it cannot.
    try:
        _ensure_initialized()
    except Exception as e:
        log_debug(f"[AI] Refreshing tools without a chat session: {e}")
    print("Refreshing Tools...")
    importlib.reload(actions)

    from ai import macro_store
    macro_store.load_and_register_all(actions)  # also drops any deleted macro's leftover function

    system_instructions = _render_instructions(load_instructions())
    tools = get_all_tools(actions)
    tools.append(get_position_object_and_click)

    import ai.providers as providers
    try:
        chat = providers.create_chat_session(get_active_provider(), get_active_model(), tools, str(system_instructions))
    except providers.ProviderClientError as e:
        # No key, no network, a bad model id. The macro is registered
        # and usable either way; only talking to the model is affected,
        # and that already reports its own "no API key configured"
        # message when someone actually tries to chat.
        print(f"[AI] Tools refreshed, but no chat session: {e}")
        chat = None
    except Exception as e:
        print(f"[AI] Tools refreshed, but no chat session: {e}")
        chat = None
    print(f"[AI] Refreshed. {len(tools)} tools available.")


# ══════════════════════════════════════════════════════════════════
# VISION - find element on screen and click it
# ══════════════════════════════════════════════════════════════════

_POINT_RE = re.compile(r'"point"\s*:\s*\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]')


def get_position_object_and_click(target: str, button: str = "left") -> str:
    """
    Visually locate an element on the current screen and click it.
    Uses Gemini vision to find the center coordinates of the target.
    Always prefer this over hardcoded coordinates.

    Args:
      target: what to click, described the way you'd point it out to someone
              looking at the same screen (e.g. "search bar", "first video
              result", "blue login button").
      button: "left" (default) for a normal click, "right" to open a
              context menu.

    Examples:
      get_position_object_and_click("search bar")
      get_position_object_and_click("address bar")
      get_position_object_and_click("first video result")
      get_position_object_and_click("blue login button")
      get_position_object_and_click("this file", button="right")
    """
    print(f"Getting Position Of: {target!r} (button={button})")
    refusal = actions._check_emergency_stop()
    if refusal:
        return refusal
    _ensure_initialized()

    screen = ImageGrab.grab()
    prompt = f"""
Find the center of '{target}' on this screenshot.
Return ONLY this exact JSON format, nothing else:
[{{"point": [y, x], "label": "{target}"}}]
The point values are normalized to 0-1000.
y is vertical (0=top, 1000=bottom), x is horizontal (0=left, 1000=right).
"""
    response_text = ""

    try:
        import ai.model_client as model_client
        vision_client = model_client.make_client()
        image_response = vision_client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=[screen, prompt],
            config=types.GenerateContentConfig(
                temperature=0.1,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
                response_mime_type="application/json",
            )
        )

        response_text = (image_response.text or "").strip()
        if not response_text:
            raise ValueError("empty response from the vision model")

        # Regex extraction instead of exact-string splitting: tolerates
        # markdown fences, extra whitespace/prose, and float coordinates
        # all of which used to make this silently fail on minor formatting
        # variance from the model, with no visibility into why.
        match = _POINT_RE.search(response_text)
        if not match:
            raise ValueError(f"no 'point' field found in response: {response_text!r}")
        y_norm, x_norm = float(match.group(1)), float(match.group(2))

        # Convert normalized 0-1000 coords to real pixels using pyautogui's
        # OWN reported screen size - NOT the screenshot's pixel dimensions.
        # On Windows with display scaling (125%/150%, very common), a
        # screenshot is captured at the physical pixel resolution while
        # pyautogui.moveTo()/click() operate in the logical coordinate space
        # Windows reports for the mouse. Those two can differ, which used to
        # make every vision click land at the wrong spot on a scaled display
        # even when the model correctly identified the target.
        screen_w, screen_h = pyautogui.size()
        real_x = int((x_norm / 1000) * screen_w)
        real_y = int((y_norm / 1000) * screen_h)

        print(f"[vision] Found {target!r} at ({real_x}, {real_y}) "
              f"[screenshot {screen.width}x{screen.height}, pyautogui screen {screen_w}x{screen_h}]")
        pyautogui.moveTo(real_x, real_y, duration=0.3)
        pyautogui.click(button=button)
        actions.actions_list.append(
            ("right_click", real_x, real_y) if button == "right" else ("click", real_x, real_y)
        )
        result = f"{'Right-c' if button == 'right' else 'C'}licked {target} at ({real_x}, {real_y})"

        # Self-verification: pyautogui.click() never raises just because a
        # click landed on empty space, hit the wrong element, or the app
        # simply didn't respond - it "succeeds" identically either way.
        # THAT'S the actual gap that makes a missed click look identical to
        # a real one to the model: not an exception being swallowed (this
        # function already catches its own and reports them below), but a
        # click that raised nothing at all yet did nothing meaningful.
        #
        # A before/after screenshot diff can't PROVE the click worked, but
        # it CAN cheaply catch the most common failure shape - literally
        # nothing on screen changed - and say so honestly instead of
        # reporting unconditional success. getbbox() is None means the two
        # images are byte-for-byte identical (ImageGrab.grab() doesn't
        # capture the cursor by default on Windows, so cursor movement
        # alone won't produce a false "changed" reading either way).
        # Deliberately compared LOCALLY ONLY, never sent to Gemini - this
        # is one extra screenshot capture (cheap, no API call, no image
        # tokens), not a second image added to the request.
        try:
            time.sleep(0.3)
            after = ImageGrab.grab()
            if ImageChops.difference(screen.convert("RGB"), after.convert("RGB")).getbbox() is None:
                result += (
                    " - but the screen looks pixel-for-pixel identical to right before the "
                    "click. It may have missed or had no effect; consider a fresh screenshot "
                    "to confirm before assuming this worked."
                )
        except Exception as e:
            # Never let the verification step itself break a click that
            # otherwise succeeded - worst case, no caveat gets added.
            print(f"[vision] Post-click verification skipped: {e}")

        return result

    except Exception as e:
        handled = actions._handle_pyautogui_exception(e)
        if handled:
            return handled
        print(f"[vision] Error locating {target!r}: {e}")
        if response_text:
            print(f"[vision] Raw model response was: {response_text!r}")
        return f"Could not locate '{target}': {e}"


# ══════════════════════════════════════════════════════════════════
# TERMINAL OUTPUT (tool_run_command sharing its real output in-chat)
# ══════════════════════════════════════════════════════════════════

# A list, not a single slot like _pending_screenshot_path below - a
# single model turn can call tool_run_command more than once (e.g.
# checking a tool's version, then actually running it), and every one
# of those should get its own terminal block in the chat, in the order
# they actually ran, rather than only the last one surviving.
_pending_terminal_entries: list[dict] = []


def queue_terminal_output(command: str, output: str, success: bool = True) -> None:
    """
    Called by ai.actions.tool_run_command right after it actually runs
    a command, stashing the exact command + its real combined
    stdout/stderr (the same string already being returned to the model
    as the tool result) so the UI can also show it verbatim as its own
    terminal-styled block in the chat - see pop_pending_terminal_output.
    Without this, the raw output only ever reached the model itself;
    the human only saw whatever the model chose to paraphrase about it
    afterward, never the actual bytes that came back.

    `success` (True for exit code 0) only affects how the block is
    colored/labeled in the UI - it's never used to decide whether to
    queue it at all; a failed command's output is exactly the case
    where seeing the real error text matters most.
    """
    _pending_terminal_entries.append({"command": command, "output": output, "success": success})


def pop_pending_terminal_output() -> list[dict]:
    """
    Returns and clears every {"command", "output"} pair queued via
    queue_terminal_output() since the last pop. Called by AIWorker right
    after ai()/ai_stream() returns (same spot/reasoning as
    pop_pending_screenshot_path - see that function), so the UI can drop
    one terminal block per command into the chat, ahead of the
    assistant's own text reply about what it ran.
    """
    global _pending_terminal_entries
    entries = _pending_terminal_entries
    _pending_terminal_entries = []
    return entries


# ══════════════════════════════════════════════════════════════════
# GENERATED FILES (tool_generate_file / tool_merge_files_to_pdf giving
# the chat a "Show in folder" card for what they just wrote to disk)
# ══════════════════════════════════════════════════════════════════

# A list for the same reason _pending_terminal_entries is: one model turn
# can create several files, and each should get its own card, in order.
_pending_file_entries: list[dict] = []


def queue_file_output(path: str, action: str = "created") -> None:
    """
    Called by ai.actions right after a tool actually writes a file to
    disk, stashing its full path so the UI can drop a card into the chat
    with a button that opens File Explorer on the folder it landed in -
    see pop_pending_file_outputs. Without this, the only trace of a
    generated file was the model's own text reply ("saved to
    C:\\Users\\...") - no way to get to it without copying that path by
    hand into Explorer.

    `action` is a short verb key the card uses for its label ("created",
    "merged"); anything else is shown capitalized as-is.
    """
    _pending_file_entries.append({"path": path, "action": action})


def pop_pending_file_outputs() -> list[dict]:
    """
    Returns and clears every {"path", "action"} entry queued via
    queue_file_output() since the last pop. Called by AIWorker right
    after ai()/ai_stream() returns (same spot/reasoning as
    pop_pending_terminal_output), so the UI can drop one card per file
    into the chat ahead of the assistant's own text reply about it.
    """
    global _pending_file_entries
    entries = _pending_file_entries
    _pending_file_entries = []
    return entries


# ══════════════════════════════════════════════════════════════════
# SCREENSHOT HOTKEY
# ══════════════════════════════════════════════════════════════════

_pending_screenshot_path = None


def pop_pending_screenshot_path():
    """
    Return and clear the path of the screenshot most recently captured via
    trigger_screenshot() (including via the AI's own tool_screenshot()
    call, not just the global shortcut). Called by AIWorker right after
    ai()/run_autonomous_mode() returns, so the UI can drop a thumbnail
    bubble into the chat - the same way the global shortcut already does
    - for screenshots the AI took mid-conversation too, not just ones
    triggered by the user's own hotkey.
    """
    global _pending_screenshot_path
    path = _pending_screenshot_path
    _pending_screenshot_path = None
    return path


def discard_pending_screenshot() -> None:
    """
    Drops the in-memory capture trigger_screenshot() stashed for the
    next ai()/ai_stream() call, plus its pending preview path.

    Used when the UI has taken ownership of that screenshot itself -
    app/chat_widget.py's attach_screenshot() stages it as a normal
    attachment chip, which sends it through queue_chat_images() like
    any dragged image. Without this, the same capture would reach the
    model twice on the next message: once as that chip's ImagePart and
    once again as this global, which the prompt builder attaches
    unconditionally.
    """
    global screenshot, _pending_screenshot_path
    screenshot = None
    _pending_screenshot_path = None


def trigger_screenshot(mark_pending: bool = True) -> str:
    """
    Capture the screen and store it for the next AI message (used by both
    text/terminal mode and the voice assistant). Also saves a copy to
    disk under ai/screenshots/ and returns its path - used by the UI to
    show a preview of exactly what was captured, e.g. right after the
    global screenshot shortcut fires (see MainWindow._trigger_screenshot_shortcut)
    or when the AI itself calls tool_screenshot() mid-conversation (see
    pop_pending_screenshot_path()).
    Returns "" if the save fails (the in-memory capture for the AI still
    succeeds either way). Plays a short notification sound.
    mark_pending=False skips stashing the path for pop_pending_screenshot_path
    - used by the terminal-mode hotkey, which isn't backed by a chat UI.
    """
    global screenshot, _pending_screenshot_path
    print("Taking Screenshot...")
    screenshot = ImageGrab.grab()
    play_notification_sound("screenshot")
    try:
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOTS_DIR, f"screenshot_{int(time.time() * 1000)}.png")
        screenshot.save(path)
        if mark_pending:
            _pending_screenshot_path = path
        return path
    except Exception as e:
        print(f"[screenshot] Could not save preview file: {e}")
        return ""


# ══════════════════════════════════════════════════════════════════
# CHAT-DROPPED IMAGE ATTACHMENTS
# ══════════════════════════════════════════════════════════════════
# Generalizes the exact same "stash it now, the next ai()/ai_stream()
# call attaches it and clears it" handoff `screenshot` above already
# uses for the manual-screenshot hotkey - a chat image ATTACHMENT
# (drag-and-dropped onto the chat input; see app/chat_widget.py's
# _handle_dropped_file / ai/file_context.py's load_image_attachment)
# needs the same "attach to whatever's about to be sent" contract,
# just as a list rather than a single value, since more than one
# image can be dropped onto the same outgoing message.

_pending_chat_images: list = []


def queue_chat_images(images: list) -> None:
    """
    Stash PIL Images to attach to the NEXT ai()/ai_stream() call.
    Called by app/chat_widget.py's _send_message right before it emits
    request_sent, for a message with one or more dropped image
    attachments - never called for text-file attachments, which are
    folded into the prompt string itself instead (see
    file_context.format_attachment_block).
    """
    _pending_chat_images.extend(images)


def _pop_pending_chat_images() -> list:
    global _pending_chat_images
    images = _pending_chat_images
    _pending_chat_images = []
    return images


# ══════════════════════════════════════════════════════════════════
# MAIN CHAT FUNCTION
# ══════════════════════════════════════════════════════════════════

_AUTONOMOUS_SUGGEST_RE = re.compile(r"\[\[AUTONOMOUS_SUGGEST:\s*(.+?)\s*\]\]", re.DOTALL)
_pending_autonomous_goal = None


def pop_pending_autonomous_goal():
    """
    Return and clear the autonomous-mode goal the AI last proposed (if any).
    Called by the UI right after ai() returns, so it can ask the user to
    confirm before actually launching run_autonomous_mode().
    """
    global _pending_autonomous_goal
    goal = _pending_autonomous_goal
    _pending_autonomous_goal = None
    return goal


def _extract_autonomous_suggestion(text: str):
    """Strip a trailing [[AUTONOMOUS_SUGGEST: goal]] tag out of the displayed text."""
    match = _AUTONOMOUS_SUGGEST_RE.search(text)
    if not match:
        return text, None
    goal = match.group(1).strip()
    clean = _AUTONOMOUS_SUGGEST_RE.sub("", text).strip()
    return clean, goal


PREFS_START_MARKER = "[PREFERENCES START HERE]"
PREFS_END_MARKER = "[END OF PREFERENCES]"


def _append_preference(current_instructions: str, new_pref_text: str) -> str:
    """
    Add new_pref_text to the END of the preferences list - never removes or
    rewrites any existing line. ai/instructions.txt tells the model that
    when two preferences conflict (e.g. "prefers Chrome" then later
    "prefers Edge"), whichever appears LAST wins - appending (instead of
    the old behavior, which replaced the *entire* block with only
    whatever the model's UPDATE_START/UPDATE_END message contained,
    silently erasing every earlier preference) is what makes that
    last-one-wins rule actually work on real history instead of on the
    model's necessarily-incomplete recollection of it.
    """
    new_pref_text = new_pref_text.strip()
    if not new_pref_text:
        return current_instructions

    new_lines = [
        f"- {line.strip().lstrip('-').strip()}"
        for line in new_pref_text.splitlines()
        if line.strip()
    ]
    if not new_lines:
        return current_instructions

    if PREFS_START_MARKER in current_instructions and PREFS_END_MARKER in current_instructions:
        before, rest = current_instructions.split(PREFS_START_MARKER, 1)
        existing_block, after = rest.split(PREFS_END_MARKER, 1)
        existing_lines = [line for line in existing_block.strip("\n").splitlines() if line.strip()]

        # Skip exact duplicates so re-stating an unchanged preference
        # doesn't spam the list with identical lines.
        for line in new_lines:
            if line not in existing_lines:
                existing_lines.append(line)

        rebuilt_block = "\n".join(existing_lines)
        return f"{before}{PREFS_START_MARKER}\n{rebuilt_block}\n{PREFS_END_MARKER}{after}"

    # No preferences section yet - create one at the end of the file.
    separator = "" if current_instructions.endswith("\n") else "\n"
    return (
        f"{current_instructions}{separator}\n"
        f"{PREFS_START_MARKER}\n" + "\n".join(new_lines) + f"\n{PREFS_END_MARKER}\n"
    )


def _process_full_response(full_text: str) -> str:
    """
    Shared post-processing for ai(): strips the [[AUTONOMOUS_SUGGEST: ...]]
    tag (stashing its goal for pop_pending_autonomous_goal()), handles the
    [[UPDATE_START]]...[[UPDATE_END]] preference-update protocol, and
    returns the final text to display/save. Never shows raw protocol tags.
    """
    if not full_text.strip():
        print("[ia] Gemini returned an empty response (possibly filtered, or a tool-only turn).")
        return "I didn't get a clear response from the model. Could you rephrase your request?"

    full_text, autonomous_goal = _extract_autonomous_suggestion(full_text)
    if autonomous_goal:
        global _pending_autonomous_goal
        _pending_autonomous_goal = autonomous_goal
        print(f"[ia] Autonomous mode suggested: {autonomous_goal}")

    if not full_text.strip():
        # The entire reply was just the [[AUTONOMOUS_SUGGEST: ...]] tag with
        # nothing else - against the instructions (which require asking in
        # plain language first), but better to say *something* than show a
        # blank bubble right before the confirmation dialog pops up.
        full_text = "Voici ce que je te propose :"

    # Handle preference update protocol
    if "[[UPDATE_START]]" in full_text:
        try:
            # Extract clean message (before the update block)
            parts = full_text.split("[[UPDATE_START]]")
            clean_message = parts[0].strip() or "Got it."

            # Extract the new preference(s) the model wants remembered
            new_prefs = parts[1].split("[[UPDATE_END]]")[0].strip()

            # Append-only: never touches or removes any existing preference
            current_instructions = load_instructions()
            updated_file = _append_preference(current_instructions, new_prefs)
            if updated_file != current_instructions:
                save_file(INSTRUCTIONS_PATH, updated_file)
                print("[ia] Preference appended to instructions.txt")

            print(f"\n{get_assistant_name()}: {clean_message}")
            return clean_message

        except Exception as e:
            print(f"[ia] Error parsing preference update: {e}")
            print(f"\n{get_assistant_name()}: {full_text}")
            return full_text
    else:
        print(f"\n{get_assistant_name()}: {full_text}")
        return full_text


def _build_message_contenu(input_text: str, audio_bytes: "bytes | None") -> list:
    """
    Shared content-part assembly for ai() and ai_stream(): text, voice
    audio, a pending manual/tool screenshot (ai.main.screenshot), and
    any images dropped onto the chat input (queue_chat_images) - every
    source of visual/audio input this app has, folded into the one
    provider-agnostic list every ChatSession.send_message[_stream]
    already accepts.
    """
    global screenshot
    from ai.providers.base import TextPart, ImagePart, AudioPart
    contenu = []
    if input_text:
        contenu.append(TextPart(input_text))
    if audio_bytes:
        contenu.append(AudioPart(data=audio_bytes, mime_type="audio/wav"))
    if screenshot is not None:
        contenu.append(ImagePart(screenshot))
        screenshot = None
    for image in _pop_pending_chat_images():
        contenu.append(ImagePart(image))
    return contenu


def ai(input_text: str = "", audio_bytes: bytes = None) -> str:
    """
    Send a message to the AI and get a response.
    `input_text` and/or `audio_bytes` (WAV bytes, e.g. from the chat's mic
    button) can be provided - at least one of them should be non-empty.
    Attaches a pending screenshot if one was captured (via hotkey), and
    any images dropped onto the chat input (see queue_chat_images).
    Handles preference updates ([[UPDATE_START]]...[[UPDATE_END]]) and
    autonomous-mode suggestions ([[AUTONOMOUS_SUGGEST: goal]]) - the latter
    is stripped from the reply and stashed for pop_pending_autonomous_goal().
    Returns the AI's text response.
    """
    _ensure_initialized()
    # Pick up any tool-list change deferred since the last turn (a macro
    # deleted, say). No-op unless something actually changed.
    _ensure_chat_session_fresh()
    print("Handling Input...")
    actions.emergency_stop = False

    if chat is None:
        provider = get_active_provider()
        return (
            f"No {provider.capitalize()} API key is configured. Add one in Settings, "
            "or switch the active model back to a provider that's already set up, "
            "then try again."
        )

    contenu = _build_message_contenu(input_text, audio_bytes)
    if not contenu:
        return "I didn't receive anything to process."

    try:
        response = chat.send_message(contenu)
        full_text = response.text or ""
    except Exception as e:
        print(f"[ia] {get_active_provider()} error: {e}")
        return f"Error: {e}"

    return _process_full_response(full_text)


class _StreamMarkerFilter:
    """
    Buffers streamed text so a [[UPDATE_START]] / [[AUTONOMOUS_SUGGEST:
    ...]] control tag never flashes on screen mid-stream before
    _process_full_response gets a chance to strip it. Safe to treat
    "anything that could be the start of one of these tags" as
    holdable rather than displayable because both only ever get sent
    as a TRAILING tag after the model's real, visible reply (per
    ai/instructions.txt) - never interleaved with more visible content
    after them - so once a chunk of text has genuinely started
    matching one, nothing legitimate is coming after it on the same
    turn.

    Deliberately NOT responsible for reproducing _process_full_response's
    own final text exactly (its trailing .strip(), or its "Got it." /
    "Voici ce que je te propose :" placeholders for a turn that was
    NOTHING but a tag) - see ai_stream()'s docstring for how callers
    reconcile that against the authoritative return value once the
    turn is over.
    """

    _MARKERS = ("[[UPDATE_START]]", "[[AUTONOMOUS_SUGGEST:")

    def __init__(self, on_visible):
        self._on_visible = on_visible
        self._held = ""

    def feed(self, delta: str) -> None:
        self._held += delta
        self._drain()

    def finalize(self) -> None:
        """Call once the stream has ended. Flushes anything still held
        back that never actually turned into a real tag - e.g. a
        stray "[[" the model happened to end its reply on."""
        if self._held and not any(
            self._held.startswith(m) or m.startswith(self._held) for m in self._MARKERS
        ):
            self._emit(self._held)
        self._held = ""

    def _drain(self) -> None:
        while True:
            idx = self._held.find("[[")
            if idx == -1:
                # Nothing marker-like pending - emit everything except a
                # single trailing "[" that could become "[[" on the very
                # next chunk.
                safe_end = len(self._held) - (1 if self._held.endswith("[") else 0)
                if safe_end > 0:
                    self._emit(self._held[:safe_end])
                    self._held = self._held[safe_end:]
                return

            if idx > 0:
                self._emit(self._held[:idx])
                self._held = self._held[idx:]

            if any(self._held.startswith(m) for m in self._MARKERS):
                return  # a real tag has started - hold everything from here on
            if any(m.startswith(self._held) for m in self._MARKERS):
                return  # could still become one as more text arrives - wait

            # Starts with "[[" but can no longer match either tag
            # genuine content the model happened to write, not a
            # control sequence. Emit just the "[[" and keep draining
            # the rest, which may have its own "[[" further along.
            self._emit(self._held[:2])
            self._held = self._held[2:]

    def _emit(self, text: str) -> None:
        if text:
            self._on_visible(text)


def ai_stream(input_text: str = "", on_delta=None, audio_bytes: bytes = None, on_thinking_delta=None) -> str:
    """
    Streaming counterpart to ai() - same inputs, same
    [[UPDATE_START]]/[[AUTONOMOUS_SUGGEST]] post-processing, and the
    same final return value, but calls on_delta(piece: str) as the
    active provider's own network stream delivers the reply - real
    per-provider streaming (see each ai/providers/*_provider.py's
    send_message_stream), not a client-side fake. This is the
    streaming path Settings > "Stream responses" actually turns on;
    app/chat_widget.py's reveal_response() is the OLD local typewriter
    effect, kept only for when that setting is off (or a provider's
    stream errors before a single delta arrives).

    on_thinking_delta(piece: str), if given, is passed straight through
    to chat.send_message_stream() unfiltered (no _StreamMarkerFilter -
    thinking text is never expected to contain this app's own
    [[...]] control tags, only whatever reasoning the model itself
    produced) - see ChatSession.send_message_stream's own docstring
    for which providers actually call it (Anthropic, Gemini, and
    OpenAI's Responses API all do, only when Settings > "Extended
    thinking" is on - see each provider module's own docstring for how
    each one gets its reasoning text).

    on_delta only ever receives plain visible text - see
    _StreamMarkerFilter above for how a control tag is kept from
    flashing on screen mid-stream. That filter is a best-effort LIVE
    view, though, not the source of truth: this function's RETURN
    VALUE (the same _process_full_response(full_text) result ai()
    returns) is what callers should persist/display as the final,
    authoritative text - app/chat_widget.py's finish_streaming_response
    sets the bubble to exactly that once the stream ends, correcting
    the handful of edge cases (a trailing .strip(), or a turn that
    turned out to be nothing but a tag) where the live filtered view
    and the final text can differ slightly.
    """
    _ensure_initialized()
    _ensure_chat_session_fresh()          # see the non-streaming path
    print("Handling Input (streaming)...")
    actions.emergency_stop = False

    if chat is None:
        provider = get_active_provider()
        return (
            f"No {provider.capitalize()} API key is configured. Add one in Settings, "
            "or switch the active model back to a provider that's already set up, "
            "then try again."
        )

    contenu = _build_message_contenu(input_text, audio_bytes)
    if not contenu:
        return "I didn't receive anything to process."

    marker_filter = _StreamMarkerFilter(on_delta or (lambda _text: None))
    try:
        response = chat.send_message_stream(contenu, marker_filter.feed, on_thinking_delta)
        marker_filter.finalize()
        full_text = response.text or ""
    except Exception as e:
        print(f"[ia] {get_active_provider()} error: {e}")
        return f"Error: {e}"

    return _process_full_response(full_text)


class NetworkUnavailableError(Exception):
    """
    Raised by run_autonomous_mode when Gemini calls keep failing with a
    connectivity-shaped error well past the retry window - distinct from
    a plain Exception so it's obvious in logs/dialogs that this was a
    network problem, not something wrong with the goal or the model.
    AutonomousWorker's existing try/except around run_autonomous_mode
    already routes any exception to the `error` signal -> MainWindow
    shows "Error in autonomous mode: {error}" - raising this (instead of
    returning normally, which is what a plain retry-then-give-up used to
    do) is what turns a silent, misleading "Task completed" into an
    accurate error message.
    """
    pass


def _is_transient_network_error(e: Exception) -> bool:
    """
    Best-effort classifier: does this look like "the internet/API is
    temporarily unreachable" rather than a genuine request problem (bad
    goal, auth failure, malformed content, etc.)? Deliberately checks the
    exception's type name and string content rather than importing
    specific classes from requests/httpx/google.api_core/grpc - the
    google-genai SDK's actual transport (and therefore its exact
    exception types) isn't something to hard-couple to, and this same
    heuristic-matching approach is already how ai.actions handles
    pyautogui exceptions elsewhere in this codebase.
    """
    type_name = type(e).__name__
    message = str(e)
    haystack = f"{type_name} {message}".lower()
    NETWORK_MARKERS = (
        "connectionerror", "connection error", "connection reset",
        "connection refused", "connection aborted",
        "timeout", "timed out",
        "unavailable", "service unavailable",
        "deadline exceeded", "deadline_exceeded",
        "temporary failure in name resolution", "name or service not known",
        "getaddrinfo failed", "nodename nor servname",
        "network is unreachable", "no route to host",
        "remote end closed connection",
        "max retries exceeded",
        "failed to establish a new connection",
        "gaierror", "sslerror", "ssl error",
    )
    return any(marker in haystack for marker in NETWORK_MARKERS)


NETWORK_RETRY_BUDGET_SECONDS = 300   # give up reconnecting after this long in total
NETWORK_RETRY_BASE_SECONDS = 3       # first retry waits ~3s
NETWORK_RETRY_MAX_WAIT_SECONDS = 30  # backoff caps here regardless of attempt count


def _retry_send_message_after_network_error(chat, prompt, screen, on_network_retry):
    """
    Keeps retrying the SAME step's send_message call with capped
    exponential backoff for up to NETWORK_RETRY_BUDGET_SECONDS total,
    instead of consuming one of run_autonomous_mode's max_steps for every
    connectivity hiccup (which is what used to happen - a dropped
    connection just silently burned through the step budget until the
    run ended, misreported as "Task completed").

    Returns the response on success. Returns None if the user stopped the
    run while waiting to reconnect, if the retry budget ran out still
    failing with a network-shaped error, or (rare) if a different kind of
    error shows up once reconnected - treated the same way here since
    there's no clean way to hand that back to the per-step handler this
    is called from without restructuring it too; still a visible failure
    either way, never a silent stall.
    """
    deadline = time.monotonic() + NETWORK_RETRY_BUDGET_SECONDS
    attempt = 0
    while time.monotonic() < deadline:
        if actions.emergency_stop:
            return None
        attempt += 1
        wait_seconds = min(
            NETWORK_RETRY_MAX_WAIT_SECONDS,
            NETWORK_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
        )
        if on_network_retry:
            try:
                on_network_retry(attempt, wait_seconds)
            except Exception as e:
                print(f"[autonomous] on_network_retry callback failed: {e}")
        print(f"[autonomous] Network retry {attempt} - waiting {wait_seconds}s before trying again…")

        # Sleep in small increments so the stop shortcut/fail-safe isn't
        # blocked for an entire backoff window.
        slept = 0.0
        while slept < wait_seconds:
            if actions.emergency_stop:
                return None
            chunk = min(1.0, wait_seconds - slept)
            time.sleep(chunk)
            slept += chunk

        try:
            response = chat.send_message([prompt, screen])
            print(f"[autonomous] Reconnected on attempt {attempt}.")
            return response
        except pyautogui.FailSafeException:
            raise  # the caller's own fail-safe branch handles this
        except Exception as e:
            if not _is_transient_network_error(e):
                print(f"[autonomous] Non-network error after reconnecting: {e}")
                return None
            print(f"[autonomous] Still unreachable: {e}")
            continue

    return None


def run_autonomous_mode(
    goal: str,
    max_steps: int | None = None,
    on_human_handoff=None,
    on_network_retry=None,
) -> None:
    """
    Run the AI in autonomous mode to complete a multi-step goal.
    Takes a screenshot before each step, sends it to Gemini,
    and executes whatever tool it calls. Repeats until:
      - tool_name_action is called (goal complete, macro saved)
      - actions.single_fcn is set (simple action done)
      - 3 consecutive steps produce no tool call and no text reply
        (the model isn't acting - no point burning through the rest)
      - the PyAutoGUI fail-safe triggers, or the stop shortcut/emergency
        stop is used (see MainWindow._trigger_stop_automation)
      - max_steps is reached
      - a transient network/API-connectivity error persists past the
        retry window (raises NetworkUnavailableError instead of
        returning - see that class's docstring for why)
    max_steps defaults to get_max_automation_steps() (20, configurable in
    Settings) if not given explicitly - it's a safety ceiling, not a
    target: autonomous mode stops on its own as soon as the goal is done,
    well before this is ever reached, in the overwhelming majority of runs.

    on_network_retry(attempt, wait_seconds), if given, fires right before
    each backoff sleep while retrying a step after a connectivity-shaped
    error (see _is_transient_network_error) - lets the caller show live
    status ("Connection lost - retrying in Ns…") instead of the run just
    looking stalled. A non-network error on send_message does NOT retry
    here - same as before, it's logged and the step is skipped.
    Two-tier pipeline: every step runs through the normal loop below
    (screenshot → Gemini → one tool call, visual targeting throughout -
    "tier 1"). If the model calls tool_request_human_handoff
    (recognizing
    a CAPTCHA, MFA/2FA prompt, or similar identity-verification
    challenge), the loop PAUSES here - "tier 2" - calls on_human_handoff
    (reason) so the caller can surface it (see AutonomousWorker /
    MainWindow, which show a "Continue" card in the chat), and blocks
    until actions.resume_human_handoff() is called, or the stop
    shortcut/emergency stop fires. It then continues normally with a
    fresh screenshot - no different from resuming after any other step.

    After completion, calls refresh_actions() to register the new macro.
    Plays a distinct notification sound for a successful finish vs. an
    early/forced stop, so the outcome is noticeable even if Kestrel isn't
    in the foreground.
    """
    _ensure_initialized()
    if max_steps is None:
        max_steps = get_max_automation_steps()
    print("Autonomous Mode...")
    print(f"[autonomous] Goal: {goal}")
    print(f"[autonomous] Max steps: {max_steps} (safety ceiling - stops earlier once the goal is reached)")
    actions.emergency_stop = False
    actions.human_handoff_requested = False
    actions.human_handoff_reason = None
    actions.human_handoff_event.clear()
    reset_safety_limits()  # fresh per-tier action budget for this run

    # Load autonomous-specific instructions
    auto_instructions = _render_instructions(load_file(AUTO_INSTRUCTIONS_PATH))

    # Create a fresh chat session for this autonomous task
    auto_chat = _gemini_client.chats.create(
        model="models/gemini-3-flash-preview",
        config={
            "tools": tools,
            "system_instruction": auto_instructions,
            "tool_config": {
                "function_calling_config": {"mode": "AUTO"}
            }
        }
    )

    consecutive_empty_steps = 0
    outcome = "max_steps"  # overwritten by whichever branch actually ends the run
    # How many steps actually ran, for the summary. Not `step`: that is
    # undefined if the loop body never executes (max_steps of 0), and
    # after a normal exit it is the last INDEX rather than a count.
    step_count = 0

    try:
        for step in range(max_steps):
            step_count = step + 1
            print(f"\n[autonomous] ── Step {step + 1}/{max_steps} ──")

            # Emergency stop, checked before every step: slam the mouse into
            # any screen corner (top-right included), or use the stop
            # shortcut, to abort right here, before even asking Gemini for
            # the next move. Checks both the live mouse position AND the flag
            # a tool call (or the stop shortcut) may have already set (see
            # ai/actions.py's _check_emergency_stop/_handle_pyautogui_exception
            # and MainWindow._trigger_stop_automation) - the flag is what
            # actually matters in practice, since the google-genai SDK's
            # automatic function calling catches any exception a tool raises
            # and turns it into a normal-looking function response, so the
            # FailSafeException below never actually reaches this code most
            # of the time.
            if actions.emergency_stop:
                print("[autonomous] STOPPED - fail-safe/stop shortcut was triggered before this step.")
                actions.actions_list = []
                outcome = "stopped"
                break
            try:
                pyautogui.failSafeCheck()
            except pyautogui.FailSafeException:
                print("[autonomous] STOPPED - mouse moved to a screen corner (fail-safe).")
                actions.actions_list = []
                outcome = "stopped"
                break

            # Wait between steps to let actions settle
            time.sleep(2)

            # Capture current screen state
            screen = ImageGrab.grab()
            screen_for_model = prepare_autonomous_screenshot(screen)

            # count_tokens() is a free, no-generation API call (not billed
            # like send_message is) - logging it here means the actual
            # token difference between quality presets is directly visible
            # in the Console for every real run, not just asserted. Never
            # allowed to block a step if it fails for any reason (offline,
            # rate-limited, whatever) - it's purely informational.
            try:
                counted = _gemini_client.models.count_tokens(
                    model="models/gemini-3-flash-preview", contents=[screen_for_model]
                )
                print(f"[autonomous] Screenshot tokens this step ({get_autonomous_screenshot_quality()}): {counted.total_tokens}")
            except Exception as e:
                log_debug(f"[main] run_autonomous_mode ignored: {e}")

            # Build prompt for this step
            prompt = (
                f"Goal: {goal}\n"
                f"This is the CURRENT state of the screen (step {step + 1} of {max_steps}).\n"
                f"Call exactly ONE tool to make progress toward the goal.\n"
                f"If the goal is fully complete and visible on screen, call tool_name_action."
            )

            try:
                response = auto_chat.send_message([prompt, screen_for_model])
            except pyautogui.FailSafeException:
                # Same kill switch, but triggered *during* a tool call this
                # time (e.g. mid-click) rather than between steps - this has
                # to be its own branch: the generic `except Exception` right
                # below would otherwise catch it too and just move on to the
                # next step, which defeats the entire point of an emergency
                # stop.
                print("[autonomous] STOPPED - fail-safe triggered during a tool call (mouse moved to a screen corner).")
                actions.actions_list = []
                outcome = "stopped"
                break
            except Exception as e:
                if not _is_transient_network_error(e):
                    print(f"[autonomous] Gemini error on step {step + 1}: {e}")
                    time.sleep(2)
                    continue

                print(f"[autonomous] Network error on step {step + 1}: {e} - retrying instead of skipping the step.")
                response = _retry_send_message_after_network_error(auto_chat, prompt, screen_for_model, on_network_retry)
                if response is None:
                    if actions.emergency_stop:
                        print("[autonomous] STOPPED - fail-safe/stop shortcut triggered while retrying after a network error.")
                        actions.actions_list = []
                        outcome = "stopped"
                        break
                    raise NetworkUnavailableError(
                        f"Lost connection and couldn't reconnect after retrying for "
                        f"about {NETWORK_RETRY_BUDGET_SECONDS // 60} minutes: {e}"
                    )

            if actions.emergency_stop:
                print("[autonomous] STOPPED - fail-safe/stop shortcut triggered during this step's tool call.")
                actions.actions_list = []
                outcome = "stopped"
                break

            # Drain any screenshot tool_screenshot() took this step - it's
            # never surfaced in the chat during autonomous mode (that's just
            # Kestrel looking at the screen mid-task, same as the screenshot
            # grabbed above for every step to feed Gemini); the thumbnail-bubble
            # treatment is for the manual/global-shortcut screenshot a human
            # takes to hand Kestrel context, not this. Still has to be popped
            # here though, or it would sit in the pending slot and get wrongly
            # attached to the next real chat-mode message once this run ends.
            pop_pending_screenshot_path()

            # Same drain, same reasoning, for any tool_run_command() output
            # queued this step - a command run mid-automation isn't a human
            # asking to see a terminal block in a live chat, and leaving it
            # queued would wrongly attach it to whatever regular chat
            # message comes after this run ends.
            pop_pending_terminal_output()

            # And again for file cards - a file the AI writes mid-automation
            # isn't something a live chat should sprout a card for later.
            pop_pending_file_outputs()

            # Tier 2 hand-off: the model recognized a CAPTCHA/MFA/verification
            # challenge and asked a human to take it from here instead of
            # trying to click through it (see tool_request_human_handoff).
            # Pause and wait - polling so the stop shortcut/fail-safe still
            # works while paused, and so this doesn't block forever if the
            # human never comes back and later just stops the run instead.
            if actions.human_handoff_requested:
                reason = actions.human_handoff_reason or f"{get_assistant_name()} needs you to complete a step by hand."
                print(f"[autonomous] PAUSED - human handoff requested: {reason}")
                play_notification_sound("human_handoff")
                if on_human_handoff:
                    try:
                        on_human_handoff(reason)
                    except Exception as e:
                        print(f"[autonomous] on_human_handoff callback failed: {e}")

                while not actions.human_handoff_event.wait(timeout=1):
                    if actions.emergency_stop:
                        break
                if actions.emergency_stop:
                    print("[autonomous] STOPPED - fail-safe/stop shortcut triggered while waiting for a human.")
                    actions.actions_list = []
                    outcome = "stopped"
                    break

                print("[autonomous] Resumed - continuing with a fresh screenshot.")
                continue

            # Check if goal is complete (macro saved)
            if actions.fcn_name:
                print("\n[autonomous] Goal complete - macro saved.")
                actions.fcn_name = False
                # Deferred, deliberately. Generalizing makes a fresh
                # model call and refresh_actions builds a new provider
                # session, and both used to run HERE - between the goal
                # completing and this function returning. So the run was
                # over, the macro was saved, and the user still had the
                # automation overlay on screen and the alert sound going
                # for another twenty-five seconds while a background
                # nicety finished.
                #
                # Neither is needed for the goal to be complete. The
                # literal macro is already on disk and already
                # registered; generalizing only adds a second,
                # parameterised copy alongside it.
                _defer_post_run_work(goal)
                outcome = "success"
                break

            # Check if a single action completed the task
            if actions.single_fcn:
                print("\n[autonomous] Single action completed.")
                actions.single_fcn = False
                _defer_post_run_work(None)     # nothing to generalize
                outcome = "success"
                break

            # Neither of the above fired - a tool call may still have happened
            # (most tools don't set fcn_name/single_fcn), so this isn't
            # necessarily a problem by itself. What matters is making the
            # step's outcome visible either way, since previously this branch
            # printed nothing at all - a model turn with no tool call and no
            # text produced a completely silent step, indistinguishable from
            # one that was actually working.
            try:
                reply_text = (response.text or "").strip()
            except Exception as e:
                log_debug(f"[autonomous] Couldn't read the step's reply text: {e}")
                reply_text = ""

            if reply_text:
                print(f"[autonomous] {get_assistant_name()}: {reply_text}")
                consecutive_empty_steps = 0
            else:
                print("[autonomous] (no tool call, no text this step)")
                consecutive_empty_steps += 1
                if consecutive_empty_steps >= 3:
                    print(
                        f"[autonomous] Stopping - {consecutive_empty_steps} steps in a row with "
                        f"no tool call and no reply. The model isn't acting right now (check the "
                        f"API key, network, or model access) - no point burning through the "
                        f"remaining steps silently."
                    )
                    actions.actions_list = []
                    outcome = "stopped"
                    break

        else:
            print(f"\n[autonomous] Reached max steps ({max_steps}) without completing goal.")
            actions.actions_list = []
            outcome = "max_steps"
    finally:
        # Every early-exit path above (FailSafeException mid-tool-call,
        # emergency stop right after a step's response, a raised
        # NetworkUnavailableError) skips the per-step drain a few lines
        # up (pop_pending_screenshot_path(), right after each successful
        # step) on its way out. Without this, a screenshot tool_screenshot()
        # took mid-step just before one of those aborts stays sitting in
        # the shared pending slot - and the NEXT regular chat message's
        # AIWorker (see app/workers/ai_worker.py) pops it and shows it as
        # a bubble, making it look like autonomous mode itself posted a
        # screenshot at the end of the run, when really it's a leftover
        # from however this run actually stopped.
        pop_pending_screenshot_path()
        pop_pending_terminal_output()  # same leftover-leak risk, same fix
        pop_pending_file_outputs()  # same leftover-leak risk, same fix

    play_notification_sound("automation_finished" if outcome == "success" else "automation_stopped")
    return _describe_outcome(outcome, step_count, max_steps)


def _describe_outcome(outcome: str, steps_used: int, max_steps: int) -> dict:
    """
    What actually happened, in a shape the UI can report.

    run_autonomous_mode computed `outcome` on every exit path and then
    returned None, so the UI had nothing to go on and said "Task
    completed in autonomous mode." regardless - for a run that was
    emergency-stopped, one that exhausted its step budget without
    finishing, and one that gave up after three silent steps, as well as
    for a run that actually worked. Three quarters of the time that
    message was wrong, and it was the only thing the user saw.

    `saved_macro` and `step_count` come from what the run left behind:
    tool_name_action records the macro it wrote, which is the concrete
    thing to report when a goal completes.
    """
    saved = (actions.last_saved_name or "").strip()
    recorded = len(actions.last_saved_actions or ())

    if outcome == "success":
        if saved and recorded:
            summary = (
                f"Goal complete in {steps_used} step{'s' if steps_used != 1 else ''}. "
                f"Saved it as a macro: {saved} ({recorded} action"
                f"{'s' if recorded != 1 else ''})."
            )
        elif saved:
            summary = f"Goal complete in {steps_used} step{'s' if steps_used != 1 else ''}. Saved as {saved}."
        else:
            summary = f"Done in {steps_used} step{'s' if steps_used != 1 else ''}."
    elif outcome == "max_steps":
        summary = (
            f"Stopped after the full {max_steps}-step budget without finishing. "
            f"Raise the step limit in Settings, or give a narrower goal."
        )
    else:
        summary = f"Stopped after {steps_used} step{'s' if steps_used != 1 else ''}."

    return {
        "outcome": outcome,
        "steps_used": steps_used,
        "max_steps": max_steps,
        "saved_macro": saved,
        "recorded_actions": recorded,
        "summary": summary,
    }


def _defer_post_run_work(goal: "str | None") -> None:
    """
    Run the after-the-fact work on a background thread so the run can
    report finished immediately.

    Two slow things used to sit on the completion path: generalizing the
    macro that was just recorded (a model call, often twenty seconds or
    more) and refresh_actions (a module reload plus a new provider
    session). Both are housekeeping - the goal is already done and the
    macro is already saved and registered by the time either starts -
    but because they ran before the function returned, the UI kept the
    automation overlay up and the alert sound going until they finished.

    A daemon thread, so it cannot hold the process open on quit, and it
    swallows its own errors: a failed generalization must not turn a
    successful run into a reported failure.
    """
    def _work():
        try:
            if goal:
                _try_generalize_last_macro(goal)
            refresh_actions()
        except Exception as e:
            print(f"[autonomous] Post-run housekeeping failed (harmless): {e}", level="warn")

    threading.Thread(target=_work, daemon=True, name="post-run").start()


def _try_generalize_last_macro(goal: str) -> None:
    """
    Called right after autonomous mode saves a literal macro. Attempts to
    ALSO save a generalized, parameterized version alongside it (see
    ai/macro_generalizer.py) - the literal macro is always kept either
    way, so this can never break or remove a working macro; on success
    both are available afterward, giving the AI (and the user, from the
    Macros page) the exact-recorded literal version and the more
    flexible generalized one to choose between.
    """
    try:
        from ai.macro_generalizer import generalize_and_rewrite_macro
        name = actions.last_saved_name
        recorded = actions.last_saved_actions
        block = actions.last_appended_block
        if not name or not recorded:
            return
        generalize_and_rewrite_macro(goal=goal, name=name, actions_list=recorded, appended_block=block)
    except Exception as e:
        print(f"[autonomous] Generalization skipped, kept literal macro: {e}")


# ══════════════════════════════════════════════════════════════════
# NO-CODE RECORDER
# ══════════════════════════════════════════════════════════════════

def No_code() -> None:
    """
    Manual macro recorder using keyboard hotkeys.
    Run this from the terminal or via the UI Recorder panel.

    Hotkeys:
      F6  → Record a right-click at the current mouse position
      F7  → Record a click at the current mouse position
      F8  → Enter typing mode (press ESC to stop typing)
      F9  → Record an Enter key press
      F10 → Finish recording and save macro
    """
    print("Entering NoCode Mode")
    print("  F6  = record right-click")
    print("  F7  = record click")
    print("  F8  = record text (ESC to stop)")
    print("  F9  = record Enter")
    print("  F10 = finish and save")

    text = ""

    while True:
        # ── F6: Record right-click ───────────────────────────────
        if keyboard.is_pressed("F6"):
            x, y = pyautogui.position()
            actions.actions_list.append(("right_click", x, y))
            print(f"[recorder] Right-click recorded at ({x}, {y})")
            while keyboard.is_pressed("F6"):
                pass   # debounce

        # ── F7: Record click ─────────────────────────────────────
        elif keyboard.is_pressed("F7"):
            x, y = pyautogui.position()
            actions.actions_list.append(("click", x, y))
            print(f"[recorder] Click recorded at ({x}, {y})")
            while keyboard.is_pressed("F7"):
                pass   # debounce

        # ── F8: Record text input ────────────────────────────────
        elif keyboard.is_pressed("F8"):
            print("[recorder] Typing mode - press ESC to stop")
            while True:
                key = keyboard.read_key()
                if key == "esc":
                    break
                elif key == "space":
                    text += " "
                elif key == "backspace":
                    text = text[:-1]
                elif len(key) == 1:
                    text += key
                time.sleep(0.05)

            if text:
                actions.actions_list.append((text, "write"))
                print(f"[recorder] Text recorded: {text!r}")
                text = ""

        # ── F9: Record Enter ─────────────────────────────────────
        elif keyboard.is_pressed("F9"):
            actions.actions_list.append(("enter", "enter"))
            print("[recorder] Enter recorded")
            while keyboard.is_pressed("F9"):
                pass   # debounce

        # ── F10: Done ────────────────────────────────────────────
        elif keyboard.is_pressed("F10"):
            print(f"[recorder] Recording stopped. {len(actions.actions_list)} actions recorded.")
            break

        time.sleep(0.05)

    # Save the macro
    if actions.actions_list:
        name = input("Give this macro a name (snake_case): ").strip().replace(" ", "_")
        if name:
            # Reuse tool_name_action logic
            from ai.actions import tool_name_action
            tool_name_action(name)
            refresh_actions()
    else:
        print("[recorder] No actions recorded - nothing saved.")
        actions.actions_list = []


# ══════════════════════════════════════════════════════════════════
# VOICE ASSISTANT (mic in / speech out via Gemini TTS)
# ══════════════════════════════════════════════════════════════════
#
# This is a separate chat session from the main text `chat`, with its own
# system prompt tuned for spoken responses (no markdown, numbers spelled
# out, etc) - but it uses the SAME tools as the main chat (ai/actions.py's
# tool_* functions + get_position_object_and_click), not a separate
# hardcoded set. See _ensure_voice_initialized() below.
#
# Requires: speech_recognition + a microphone (PyAudio) to listen,
# and winsound (Windows only) to beep/play the generated audio back.
# ══════════════════════════════════════════════════════════════════

_voice_initialized = False
voice_chat = None

VOICE_SYSTEM_INSTRUCTIONS = """
You are a voice assistant on a PC - very smart, friendly, and concise.

- USE YOUR TOOLS to carry out the actions requested on the PC - these are
  the same tools as the text chat (open/close an application, type and
  submit, click an element described on screen, etc.).
- Do NOT use any emoji, asterisk, or formatting, since your text will be
  read aloud.
- Spell out numbers in words.
- Analyze the image if asked what's on screen.
- For any sensitive or ambiguous action (sending a message, confirming a
  purchase, deleting something), describe what you're about to do and ask
  for a spoken confirmation before acting.

 SYSTEM SHUTDOWN RULE:
If the user asks to stop (e.g. "quit", "stop"), reply with a short sentence
AND ALWAYS include [SHUTDOWN] at the end.
"""

# Which utterance is currently allowed to make noise.
#
# This was a single threading.Event, set by stop_speaking() and cleared
# by speak_reply(), and one shared flag for a thing that can happen twice
# at once does not work. Two speaks overlap easily - Read aloud on one
# message while a voice-mode reply is still talking, or just two Read
# alouds in a row, since nothing cancelled the first. The failure was
# that the SECOND speak's clear() silently un-stopped the FIRST: press
# Stop, start another, and the one you stopped resumes. They also both
# wrote to the same two rotating scratch WAVs.
#
# A counter instead. Every utterance takes a number on the way in, and
# plays only while that number is still current. Taking a number
# therefore cancels whatever came before, which is the behaviour that was
# wanted anyway: starting a new utterance should replace the old one, not
# race it.
_speech_lock = threading.Lock()
_speech_generation = 0


def _begin_speech() -> int:
    """Claim the speaker, cancelling whatever held it. Returns this
    utterance's generation number."""
    global _speech_generation
    with _speech_lock:
        _speech_generation += 1
        return _speech_generation


def _speech_superseded(my_generation: int) -> bool:
    """True once something else has claimed the speaker, or stopped it."""
    return _speech_generation != my_generation


def stop_speaking() -> None:
    """
    Immediately stops whatever Kestrel is currently saying, if anything.
    Safe to call from any thread - it's a counter bump plus a WinAPI
    purge, not tied to the thread that started playback.

    Bumping the counter is what stops it: the playing utterance sees its
    generation is no longer current and returns. Nothing needs clearing
    afterwards, which is what removes the old race.
    """
    _begin_speech()
    if winsound is not None:
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception as e:
            log_debug(f"[main] stop_speaking ignored: {e}")


# How much text goes into one text-to-speech request. Small enough that
# the first chunk comes back quickly, large enough that a sentence is
# not cut into pieces that lose their intonation.
_TTS_CHUNK_CHARS = 240

# ...except the first one, which is what the person is actually waiting
# on. Synthesis time scales with how much text is in the request, and
# nothing can play until the whole chunk comes back, so a greedily
# packed 240-character opening chunk means waiting for roughly fifteen
# seconds of speech to be generated before hearing the first word of it.
# One short sentence is enough to start talking; by the time it has
# finished playing, the full-size chunks behind it have been fetched in
# the background and the seam is inaudible.
_TTS_FIRST_CHUNK_CHARS = 90
_TTS_MODEL = "gemini-2.5-flash-preview-tts"
_TTS_SAMPLE_RATE = 24000

_SENTENCE_END_RE = re.compile(r"(?<=[.!?\u2026])\s+|\n{2,}")


def _split_for_speech(
    text: str,
    limit: int = _TTS_CHUNK_CHARS,
    first_limit: "int | None" = _TTS_FIRST_CHUNK_CHARS,
) -> list:
    """
    Break a reply into pieces small enough to synthesize quickly, on
    sentence boundaries so the speech still sounds like sentences.

    The first chunk gets its own, smaller budget (`first_limit`), for
    the reason _TTS_FIRST_CHUNK_CHARS gives: it is the only one anybody
    waits on. Pass first_limit=None to use `limit` throughout.

    A sentence longer than whichever limit applies is left whole rather
    than cut mid-clause: a slightly slower start beats speech that stops
    dead in the middle of a thought. That means a reply opening with one
    very long sentence still starts slowly, and there is no fix for that
    which does not also cut someone off mid-thought.
    """
    text = (text or "").strip()
    if not text:
        return []

    chunks, current = [], ""
    budget = first_limit if first_limit is not None else limit
    for sentence in _SENTENCE_END_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and len(current) + len(sentence) + 1 > budget:
            chunks.append(current)
            current = sentence
            budget = limit          # only the first chunk is held short
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def _synthesize_speech(text: str):
    """Raw PCM for one chunk, or None. Never raises."""
    try:
        response = _gemini_client.models.generate_content(
            model=_TTS_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
                    )
                ),
            ),
        )
    except Exception as e:
        print(f"[voice] Couldn't generate speech: {e}")
        return None

    candidates = getattr(response, "candidates", None)
    if not candidates or not getattr(candidates[0], "content", None):
        return None
    for part in getattr(candidates[0].content, "parts", None) or []:
        data = getattr(part, "inline_data", None)
        if data and (data.mime_type or "").startswith("audio/"):
            return data.data
    return None


def _play_pcm(pcm: bytes, index: int, generation: int) -> bool:
    """
    Write one chunk to a WAV and play it, returning once it has finished
    or once stop_speaking cut it short. False means it was stopped.

    Written under USER_DATA_PATH, not next to the code: a packaged
    install lives in Program Files, which is not writable by a standard
    user, and the write failing there meant no audio ever played at all.
    The index and generation together keep chunks from fighting over
    one filename: the index alternates within an utterance, the
    generation separates one utterance from the next. Two overlapping
    speaks used to share speech_0.wav and speech_1.wav, so one could
    be playing a file the other was midway through overwriting.
    """
    folder = os.path.join(USER_DATA_PATH, "voice")
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"speech_{generation % 4}_{index % 2}.wav")
        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(_TTS_SAMPLE_RATE)
            wav_file.writeframes(pcm)
    except OSError as e:
        print(f"[voice] Couldn't write the audio file: {e}")
        return False

    duration = (len(pcm) // 2) / _TTS_SAMPLE_RATE
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)

    # Against a real clock, not an accumulated count of nominal sleeps.
    # time.sleep(0.05) really takes about 0.0625s at Windows' timer
    # granularity, so counting steps overran the true duration by about
    # a quarter and left the UI "speaking" well after the audio stopped,
    # which among other things kept the wake word listener deaf.
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        if _speech_superseded(generation):
            winsound.PlaySound(None, winsound.SND_PURGE)
            return False
        time.sleep(0.05)
    return True


def speak_reply(text: str) -> None:
    """
    Say `text` aloud.

    Synthesized and played a chunk at a time, with the next chunk
    fetched while the current one plays. One request for the whole reply
    was the reason Read aloud felt broken: nothing could play until the
    model had finished generating every second of audio, so the wait
    grew with the length of the answer and a long reply sat silent for
    many seconds while the UI already claimed to be speaking.

    Playback is polled rather than blocking, so stop_speaking can cut it
    short mid-sentence.
    """
    print(f"Assistant: {text}")

    if winsound is None:
        print("[voice] winsound is not available on this platform - cannot play audio.")
        return

    # Deliberately not _ensure_initialized(): that builds the whole chat
    # session, tools and macros, and saying one sentence needs none of
    # it. On a cold process that was seconds of setup before the first
    # request was even sent.
    _ensure_voice_client()

    chunks = _split_for_speech(text)
    if not chunks:
        return

    # Claim the speaker. This also cancels whatever was speaking, which
    # is what makes a second Read aloud replace the first rather than
    # play on top of it.
    generation = _begin_speech()

    # Two workers, and chunk 1 is submitted alongside chunk 0 rather than
    # after it. With one worker the queue was strictly serial: chunk 1's
    # request could not start until chunk 0's had returned, so the second
    # chunk only overlapped chunk 0's PLAYBACK, not its synthesis. Since
    # chunk 0 is now deliberately short (_TTS_FIRST_CHUNK_CHARS), its
    # playback is short too, and the old arrangement would have turned
    # the fast start into a gap immediately after it.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        queue = [pool.submit(_synthesize_speech, chunk) for chunk in chunks[:2]]

        for index in range(len(chunks)):
            pcm = queue[index].result()
            if _speech_superseded(generation):
                return

            # Keep exactly two requests in flight: as one is consumed,
            # queue the one two places ahead.
            ahead = index + 2
            if ahead < len(chunks):
                queue.append(pool.submit(_synthesize_speech, chunks[ahead]))

            last = index == len(chunks) - 1
            if not pcm:
                if index == 0:
                    print("[voice] The model didn't return any audio.")
                if last:
                    return
                continue
            if not _play_pcm(pcm, index, generation):
                return
            if last:
                return
    finally:
        # cancel_futures drops whatever has been queued but not started,
        # and wait=False means Stop returns now rather than sitting on
        # the one request already in flight. `with` would have done
        # shutdown(wait=True) instead, which made pressing Stop hold
        # this thread for however long the pending synthesis took.
        pool.shutdown(wait=False, cancel_futures=True)


def _ensure_voice_client() -> None:
    """The Gemini client on its own, without building a chat session."""
    global _gemini_client
    if _gemini_client is not None:
        return
    _migrate_legacy_env_file()
    load_dotenv(ENV_PATH)
    _ensure_gemini_client()


def prewarm_audio() -> None:
    """
    Load PortAudio and enumerate the audio devices ahead of time, off
    the critical path.

    `sr.Microphone()` instantiates PyAudio, which on Windows loads the
    PortAudio DLL and walks every host API and device to resolve the
    default input. The first one in a process also pays `import
    pyaudio`. That was the larger half of the wait between switching
    voice mode on and the mic accepting anything, and none of it depends
    on the user having done anything - it can all happen while the
    splash screen is still up.

    PyAudio caches the module import and Windows caches the loaded DLL,
    so the instance built here is created and immediately terminated:
    what is being kept is the process-wide warm state, not this object.

    Never raises. A machine with no microphone, no PyAudio installed, or
    an audio service that is not running all land here, and all of them
    are fine - voice mode reports its own error later, in the one place
    that knows how to show it.
    """
    if sr is None:
        return
    try:
        import pyaudio
        audio = pyaudio.PyAudio()
        try:
            audio.get_default_input_device_info()
        finally:
            audio.terminate()
    except Exception as e:
        log_debug(f"[voice] audio prewarm skipped: {e}")


def prewarm_voice() -> None:
    """
    Build the Gemini client ahead of time, off the critical path.

    _ensure_voice_client is lazy, so on the first Read aloud of a
    session where nothing else had touched Gemini it did real work
    before the first request was even sent: a .env read, an OS keyring
    lookup (a credential-store round trip on Windows, not free) and the
    client construction. That time landed squarely between pressing the
    button and hearing anything, and only on the first press, which is
    why the delay felt inconsistent.

    Call this from a background thread at startup. Safe to call more
    than once and safe to call when voice is unavailable: it never
    raises, and a failure here just means the first Read aloud pays the
    cost as it did before.
    """
    try:
        _ensure_voice_client()
    except Exception as e:
        log_debug(f"[voice] prewarm skipped: {e}")


def _ensure_voice_initialized():
    """
    Lazily build the dedicated voice chat session (separate system prompt
    from the text `chat`, tuned for spoken replies - but the same tools:
    ai/actions.py's tool_* functions + get_position_object_and_click,
    built by _ensure_initialized() into the module-level `tools` list).
    """
    global _voice_initialized, voice_chat
    if _voice_initialized:
        return

    _ensure_initialized()

    config = types.GenerateContentConfig(
        system_instruction=VOICE_SYSTEM_INSTRUCTIONS,
        tools=tools,
    )

    voice_chat = _gemini_client.chats.create(
        model='gemini-2.5-flash',
        config=config
    )
    _voice_initialized = True
    print("[voice] Voice assistant session initialized.")


def listen_mic():
    """
    Listens to the microphone and returns (audio_wav_bytes, transcript)
    - transcript can be None if local recognition fails (no network,
    quota
    exceeded...); this never blocks the audio sent to Gemini, which stays
    the single source of truth for the reply. transcript is only used for
    display in the UI.
    Returns (None, None) on timeout/silence.
    """
    if sr is None:
        print("[voice] speech_recognition is not installed - cannot listen to the microphone.")
        return None, None

    recognizer = sr.Recognizer()
    recognizer.pause_threshold = 1.2

    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        print("\n Listening...")
        if winsound is not None:
            winsound.Beep(800, 150)

        try:
            audio = recognizer.listen(source, timeout=7, phrase_time_limit=25)
            if winsound is not None:
                winsound.Beep(600, 150)
        except sr.WaitTimeoutError:
            return None, None

    transcript = None
    try:
        transcript = recognizer.recognize_google(audio, language="en-US")
    except Exception as e:
        print(f"[voice] Local transcription unavailable (showing no transcript): {e}")

    return audio.get_wav_data(), transcript


def run_voice_assistant() -> None:
    """
    Hands-free voice loop: push F8 to talk, get a spoken reply back.
    Say "quitter"/"stop" (or anything the AI answers with [SHUTDOWN]) to exit,
    or Ctrl+C from the terminal.

    Requires speech_recognition (+ a microphone) and, for audio playback,
    winsound (Windows only). Prints a clear error and returns if unavailable.
    """
    if sr is None:
        print("[voice] Voice mode unavailable: install 'SpeechRecognition' and 'PyAudio' first.")
        return
    if winsound is None:
        print("[voice] Voice mode unavailable: winsound is Windows-only, audio playback needs it.")
        return

    _ensure_voice_initialized()

    # No add_hotkey() call here anymore: the screenshot shortcut is now a
    # permanent global shortcut registered once at app startup by
    # MainWindow.register_global_shortcuts() (and rebindable from
    # Settings), same as toggle_window and always_on_top. Registering it
    # again here would raise on the `keyboard` lib ("hotkey already
    # registered") every time voice mode starts.
    print(f"[voice] Screenshot hotkey: {get_shortcut('screenshot')}")
    print(" GEMINI VOICE ASSISTANT READY ")

    speak_reply(f"System activated. Press {SHORTCUT_VOICE_TALK.upper()} to talk to me.")

    while True:
        print(f"\n En attente de la touche {SHORTCUT_VOICE_TALK.upper()}...")
        keyboard.wait(SHORTCUT_VOICE_TALK)

        audio_bytes, transcript = listen_mic()

        if audio_bytes is None:
            speak_reply("Silence detected. Going back to sleep.")
            continue

        print(f" Got the voice input - transcript: {transcript!r}" if transcript else " Got the voice input")

        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type='audio/wav')
        content_for_ai = [audio_part]

        global screenshot
        if screenshot is not None:
            content_for_ai.append(screenshot)
            screenshot = None

        try:
            response = voice_chat.send_message(content_for_ai)

            # Safety net in case Gemini has nothing to say (e.g. after opening an email)
            final_text = response.text.strip() if response.text else ""

            if final_text:
                # Check for the shutdown word [SHUTDOWN]
                if "[SHUTDOWN]" in final_text:
                    clean_text = final_text.replace('[SHUTDOWN]', '')
                    speak_reply(clean_text)  # Sign off
                    print(" Stopping the voice assistant.")
                    break
                speak_reply(final_text)
            else:
                speak_reply("Done!")

        except Exception as e:
            print(f" Gemini error: {e}")
            speak_reply("Sorry, I ran into a small network problem.")


# ══════════════════════════════════════════════════════════════════
# ENTRY POINT (terminal mode - bypasses the UI)
# ══════════════════════════════════════════════════════════════════

def main_terminal():
    """
    Run Kestrel in terminal mode (no UI).
    Useful for testing the AI backend without the PyQt6 interface.

    Usage:
        python -c "from ai.main import main_terminal; main_terminal()"
    """
    _ensure_initialized()

    # Install screenshot hotkey
    keyboard.add_hotkey(SHORTCUT_SCREEN, trigger_screenshot)
    print(f"[terminal] Screenshot hotkey: {SHORTCUT_SCREEN}")
    print("[terminal] Ready. Type 'rec' to record a macro, 'auto: <goal>' for autonomous mode, "
          "'voice' for the hands-free voice assistant.")
    print("[terminal] Ctrl+C to exit.\n")

    while True:
        try:
            user_input = input("\nUser: ").strip()
        except KeyboardInterrupt:
            print("\n[terminal] Goodbye.")
            break

        if not user_input:
            continue

        # No-code recorder
        if user_input.lower() == "rec":
            print("Recording started...")
            No_code()
            continue

        # Voice assistant
        if user_input.lower() == "voice":
            run_voice_assistant()
            continue

        # Autonomous mode
        if user_input.lower().startswith("auto:"):
            goal = user_input[5:].strip()
            if goal:
                thread = threading.Thread(
                    target=run_autonomous_mode,
                    args=(goal,),
                    daemon=True
                )
                thread.start()
                thread.join()
            continue

        # Normal chat
        ai(user_input)


if __name__ == "__main__":
    main_terminal()
