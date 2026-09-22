# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_local_connection.py
================================
ai.providers.test_local_connection.

This exists to answer "why is my local model not responding", so the
thing worth testing is whether each failure produces a message that
points at the actual fix. A test that only checks ok is False would pass
for a button that always said "something went wrong".
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai.providers as providers


class _Response:
    def __init__(self, status_code=200, payload=None, raises=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"data": []}
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("not json")
        return self._payload


def _server(monkeypatch, response=None, error=None, record=None):
    import requests

    def fake_get(url, timeout=None, headers=None):
        if record is not None:
            record.append({"url": url, "headers": headers or {}})
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(requests, "get", fake_get)


def _configured(monkeypatch, url="http://localhost:11434/v1", model="llama3.2", key=""):
    import ai.main as ai_main
    monkeypatch.setattr(ai_main, "get_local_base_url", lambda: url)
    monkeypatch.setattr(ai_main, "get_local_model_name", lambda: model)
    monkeypatch.setattr(ai_main, "get_api_key_for", lambda provider: key)


# -- the good case -----------------------------------------------------

def test_a_working_server_with_the_model_loaded(monkeypatch):
    _configured(monkeypatch)
    _server(monkeypatch, _Response(payload={"data": [{"id": "llama3.2"}, {"id": "other"}]}))
    ok, message = providers.test_local_connection()
    assert ok
    assert "llama3.2" in message


def test_it_asks_the_models_endpoint(monkeypatch):
    """The cheapest endpoint that proves the server is up, and it loads
    no weights."""
    _configured(monkeypatch)
    calls = []
    _server(monkeypatch, _Response(payload={"data": [{"id": "llama3.2"}]}), record=calls)
    providers.test_local_connection()
    assert calls[0]["url"] == "http://localhost:11434/v1/models"


def test_a_trailing_slash_does_not_double_up(monkeypatch):
    _configured(monkeypatch)
    calls = []
    _server(monkeypatch, _Response(payload={"data": [{"id": "m"}]}), record=calls)
    providers.test_local_connection("http://localhost:11434/v1/", model="m")
    assert calls[0]["url"] == "http://localhost:11434/v1/models"


def test_a_configured_key_is_sent(monkeypatch):
    _configured(monkeypatch, key="secret-token")
    calls = []
    _server(monkeypatch, _Response(payload={"data": [{"id": "llama3.2"}]}), record=calls)
    providers.test_local_connection()
    assert calls[0]["headers"].get("Authorization") == "Bearer secret-token"


# -- each failure names its own fix ------------------------------------

def test_nothing_listening_says_to_check_the_server_is_running(monkeypatch):
    import requests
    _configured(monkeypatch)
    _server(monkeypatch, error=requests.exceptions.ConnectionError("refused"))
    ok, message = providers.test_local_connection()
    assert not ok
    assert "running" in message and "port" in message


def test_a_timeout_says_so(monkeypatch):
    import requests
    _configured(monkeypatch)
    _server(monkeypatch, error=requests.exceptions.Timeout("slow"))
    ok, message = providers.test_local_connection()
    assert not ok
    assert "didn't answer" in message


def test_a_401_points_at_the_key_field(monkeypatch):
    _configured(monkeypatch)
    _server(monkeypatch, _Response(status_code=401))
    ok, message = providers.test_local_connection()
    assert not ok
    assert "API key" in message


def test_a_404_points_at_the_missing_v1(monkeypatch):
    """The single most common misconfiguration: the port is right and
    the /v1 is missing."""
    _configured(monkeypatch)
    _server(monkeypatch, _Response(status_code=404))
    ok, message = providers.test_local_connection()
    assert not ok
    assert "/v1" in message


def test_a_server_that_is_not_a_model_server(monkeypatch):
    _configured(monkeypatch)
    _server(monkeypatch, _Response(raises=True))
    ok, message = providers.test_local_connection()
    assert not ok
    assert "OpenAI-compatible" in message


def test_a_reachable_server_with_no_models_is_not_a_failure(monkeypatch):
    _configured(monkeypatch)
    _server(monkeypatch, _Response(payload={"data": []}))
    ok, message = providers.test_local_connection()
    assert ok, "the server answered; having nothing loaded is a different problem"
    assert "no models" in message


def test_the_wrong_model_lists_what_is_actually_there(monkeypatch):
    _configured(monkeypatch, model="llama3.2")
    _server(monkeypatch, _Response(payload={"data": [{"id": "mistral"}, {"id": "phi3"}]}))
    ok, message = providers.test_local_connection()
    assert not ok
    assert "llama3.2" in message
    assert "mistral" in message and "phi3" in message


def test_no_url_configured(monkeypatch):
    _configured(monkeypatch, url="")
    ok, message = providers.test_local_connection("")
    assert not ok
    assert "No server URL" in message
