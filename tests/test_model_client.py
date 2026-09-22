# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_model_client.py
=============================
ai.model_client.make_client() builds one kind of client: the user's own
key, straight to Google. These check it without making network calls.
"""

import ai.model_client as model_client


def test_builds_a_client_with_the_given_key(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(model_client, "genai", type("M", (), {"Client": FakeClient}))
    model_client.make_client(api_key_override="test-key-123")
    assert captured == {"api_key": "test-key-123"}


def test_falls_back_to_the_configured_key(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import ai.main as ai_main
    monkeypatch.setattr(model_client, "genai", type("M", (), {"Client": FakeClient}))
    monkeypatch.setattr(ai_main, "get_api_key", lambda: "from-settings")
    model_client.make_client()
    assert captured["api_key"] == "from-settings"


def test_no_base_url_override_is_ever_applied(monkeypatch):
    """Nothing is proxied through anyone else's server, so a client must
    never come back pointed somewhere other than Google."""
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(model_client, "genai", type("M", (), {"Client": FakeClient}))
    model_client.make_client(api_key_override="k")
    assert "http_options" not in captured
    assert "base_url" not in captured
