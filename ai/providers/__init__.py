# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/providers/__init__.py
===========================
The one place that knows how to turn (provider name, model id) into a
live ChatSession, for every provider.

Every provider uses the user's own API key, read from Settings, talking
straight to that provider. This app ships no key of its own and proxies
nothing through a server of ours. See ai/model_client.py for Gemini's
own client, which predates this module and is still what the vision tool
calls directly.
"""

from __future__ import annotations

import os

from ai.providers.base import ChatSession, default_model_for
from ai.providers import gemini_provider, openai_provider, anthropic_provider, local_provider

PROVIDERS = ("gemini", "openai", "anthropic", "local")


class ProviderClientError(Exception):
    """Raised when the requested provider needs a configuration this
    install doesn't have - same role as ai.model_client.ModelClientError,
    just not provider-specific anymore. Callers surface this as a
    normal chat-visible error rather than a silent empty response."""


def _make_gemini_client():
    """Unchanged from before this module existed - ai/model_client.py
    already does exactly this, for exactly this provider, and stays
    the thing the vision tool (ai/main.py's get_position_object_and_click)
    calls directly, independent of whichever provider the main chat
    is currently switched to."""
    import ai.model_client as model_client
    return model_client.make_client()


def _make_openai_client(api_key_override: "str | None"):
    import ai.main as ai_main
    try:
        import openai
    except ImportError as e:
        raise ProviderClientError(
            "The 'openai' package isn't installed on this build. "
            "Run: pip install openai (see requirements.txt)."
        ) from e

    api_key = api_key_override if api_key_override is not None else ai_main.get_api_key_for("openai")
    if not api_key:
        raise ProviderClientError(
            "No OpenAI API key is configured. Add one in Settings, or switch "
            "the model back to Gemini/Anthropic."
        )
    return openai.OpenAI(api_key=api_key)


def _make_anthropic_client(api_key_override: "str | None"):
    import ai.main as ai_main
    try:
        import anthropic
    except ImportError as e:
        raise ProviderClientError(
            "The 'anthropic' package isn't installed on this build. "
            "Run: pip install anthropic (see requirements.txt)."
        ) from e

    api_key = api_key_override if api_key_override is not None else ai_main.get_api_key_for("anthropic")
    if not api_key:
        raise ProviderClientError(
            "No Anthropic API key is configured. Add one in Settings, or switch "
            "the model back to Gemini/OpenAI."
        )
    return anthropic.Anthropic(api_key=api_key)


def _make_local_client():
    """
    Unlike the other three providers, "local" does not strictly require
    a key: the model is running on the user's own machine or LAN.

    Reuses the `openai` package (already required for openai_provider.py)
    purely as an HTTP client for whatever OpenAI-compatible `/v1`
    endpoint the local server exposes - see local_provider.py's module
    docstring for why Chat Completions rather than the Responses API.
    """
    import ai.main as ai_main
    try:
        import openai
    except ImportError as e:
        raise ProviderClientError(
            "The 'openai' package isn't installed on this build. "
            "Run: pip install openai (see requirements.txt)."
        ) from e

    base_url = ai_main.get_local_base_url()
    if not base_url:
        raise ProviderClientError(
            "No local model server URL is configured. Add one in Settings "
            "(e.g. http://localhost:11434/v1 for Ollama)."
        )
    # A local server that doesn't check auth at all (the common case)
    # ignores whatever's sent here; one placed behind a reverse proxy
    # that does enforce a bearer token gets the key saved for the
    # "Local" row in Settings (ai.main's generalized per-provider key
    # storage - see get_api_key_for's own docstring). Either way the
    # SDK requires a non-empty string, so a harmless placeholder covers
    # the no-auth case.
    api_key = ai_main.get_api_key_for("local") or "not-needed"
    return openai.OpenAI(api_key=api_key, base_url=base_url)


def test_local_connection(base_url: str = None, model: str = None, timeout: float = 8.0) -> "tuple[bool, str]":
    """
    (ok, message) for whether the configured local model server is
    actually there. Never raises: this exists to answer the question
    "why is nothing happening", so it has to produce an answer.

    Asks for the model list rather than a completion: it is the cheapest
    endpoint every OpenAI-compatible server implements, it needs no
    tokens and loads no weights, and it answers the two things worth
    knowing at once, that the server is up and which models it has.

    The distinction that matters most in the message is between "nothing
    is listening on that port" and "something is, but it is not a model
    server", because the fix is different and the symptom is identical.
    """
    import ai.main as ai_main

    base_url = (base_url or ai_main.get_local_base_url() or "").strip().rstrip("/")
    if not base_url:
        return False, "No server URL is set."

    wanted = (model or ai_main.get_local_model_name() or "").strip()
    url = base_url + "/models"
    headers = {}
    key = ai_main.get_api_key_for("local")
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        import requests
    except ImportError:
        return False, "The 'requests' package isn't installed on this build."

    # Caught by class rather than by the name of the class: a connection
    # refused and a request that timed out have different fixes, and
    # matching on type names silently stops working the moment anything
    # wraps the exception.
    try:
        response = requests.get(url, timeout=timeout, headers=headers)
    except requests.exceptions.ConnectionError:
        return False, (
            f"Nothing is answering at {base_url}. Check the server is "
            "running and that the port matches."
        )
    except requests.exceptions.Timeout:
        return False, f"{base_url} didn't answer within {timeout:.0f}s."
    except Exception as e:
        return False, f"Couldn't reach {base_url}: {e}"

    if response.status_code in (401, 403):
        return False, (
            f"{base_url} is there but refused the request ({response.status_code}). "
            "It wants an API key; add one in the Local row above."
        )
    if response.status_code == 404:
        return False, (
            f"{base_url} answered, but has no /models endpoint. The URL usually "
            "needs to end in /v1, e.g. http://localhost:11434/v1"
        )
    if response.status_code >= 400:
        return False, f"{base_url} returned HTTP {response.status_code}."

    try:
        models = [m.get("id", "") for m in (response.json().get("data") or [])]
    except Exception:
        return False, (
            f"Something is running at {base_url}, but it didn't answer like an "
            "OpenAI-compatible server. Check the URL points at the right service."
        )

    if not models:
        return True, f"Connected to {base_url}, but it has no models loaded yet."
    if wanted and wanted not in models:
        shown = ", ".join(models[:6]) + ("\u2026" if len(models) > 6 else "")
        return False, (
            f"Connected, but {wanted!r} isn't loaded there. Available: {shown}"
        )
    return True, f"Connected. {len(models)} model(s) available, including {wanted or models[0]}."


def create_chat_session(
    provider: str,
    model: "str | None" = None,
    tool_functions: "list | None" = None,
    system_instruction: str = "",
    api_key_override: "str | None" = None,
) -> ChatSession:
    """
    The single entry point ai/main.py should call to (re)build `chat`.
    `model` defaults to that provider's curated default (see
    ai.providers.base.default_model_for) if not given. `tool_functions`
    defaults to an empty list - a chat session with no tools is
    perfectly valid (e.g. autonomous mode's separate auto_chat, which
    stays on gemini_provider directly rather than through this factory
    - see ai/main.py's own comments on that boundary).
    """
    if provider not in PROVIDERS:
        raise ProviderClientError(f"Unknown provider '{provider}'. Expected one of {PROVIDERS}.")
    model = model or default_model_for(provider)
    tool_functions = tool_functions or []

    if provider == "gemini":
        client = _make_gemini_client()
        return gemini_provider.create_chat_session(client, model, tool_functions, system_instruction)
    if provider == "openai":
        client = _make_openai_client(api_key_override)
        return openai_provider.create_chat_session(client, model, tool_functions, system_instruction)
    if provider == "anthropic":
        client = _make_anthropic_client(api_key_override)
        return anthropic_provider.create_chat_session(client, model, tool_functions, system_instruction)
    if provider == "local":
        client = _make_local_client()
        # `model` here is base.MODEL_CHOICES' "__local__" sentinel (or
        # whatever a stored profile/setting still has, from before a
        # local model name was configured) - swapped for the real
        # configured local model name, since that name is arbitrary
        # user text (whatever they've pulled into Ollama/LM Studio/etc)
        # and can't live in the curated dropdown the way every other
        # provider's model ids do. See base.py's MODEL_CHOICES entry
        # for "local" for the other half of this.
        import ai.main as ai_main
        local_model = ai_main.get_local_model_name()
        if not local_model:
            raise ProviderClientError(
                "No local model name is configured. Add one in Settings "
                "(e.g. \"llama3.2\" for Ollama)."
            )
        return local_provider.create_chat_session(client, local_model, tool_functions, system_instruction)
    raise AssertionError("unreachable")  # PROVIDERS check above already excludes anything else


def switch_provider(
    old_session: "ChatSession | None",
    new_provider: str,
    new_model: "str | None",
    tool_functions: list,
    system_instruction: str,
) -> ChatSession:
    """
    Builds a fresh ChatSession for (new_provider, new_model) and, if
    old_session had any history, reseeds the new one via seed_history()
    - see that method's own docstring for why this must NOT be done by
    replaying each past turn through send_message() (that would fire a
    real, billed model call per historical turn on every single
    switch, and the replayed answers wouldn't even match what was
    actually said the first time).

    See ChatSession.history_as_text_turns()'s docstring for exactly
    what does and doesn't carry over (text only; no images, audio, or
    tool-call plumbing).
    """
    new_session = create_chat_session(new_provider, new_model, tool_functions, system_instruction)
    if old_session is None:
        return new_session

    turns = old_session.history_as_text_turns()
    if turns:
        new_session.seed_history(turns)
    return new_session
