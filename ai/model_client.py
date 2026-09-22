# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/model_client.py
====================
The one place that builds the Gemini client, so ai/main.py's call sites
(chat, vision, autonomous mode, voice) don't each construct their own.

The key is the user's own, from Settings. There is no other path: this
app ships no key of its own and proxies nothing through anyone else's
server. See ai/providers/__init__.py for the same arrangement for
OpenAI, Anthropic and local models.
"""

from google import genai


class ModelClientError(Exception):
    """Raised when a client cannot be built. Callers surface this as a
    chat-visible error rather than a silent empty response."""


def make_client(api_key_override: "str | None" = None) -> genai.Client:
    """
    `api_key_override`: pass None (the default) to resolve the key via
    ai.main.get_api_key(), which reads os.environ, then the OS keyring,
    then a legacy plaintext .env value.
    """
    if api_key_override is not None:
        api_key = api_key_override
    else:
        import ai.main as ai_main  # lazy: ai.main imports this module at top level
        api_key = ai_main.get_api_key()
    return genai.Client(api_key=api_key)
