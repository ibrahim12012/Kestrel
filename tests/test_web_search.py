# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_web_search.py
==========================
ai/web_search.py against canned DuckDuckGo responses: results parse, and
a human check or a 429 is reported as what it is instead of coming back
as "no results". No network.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

from ai import web_search


RESULTS_PAGE = """
<div class="result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x">Example <b>A</b></a>
  <a class="result__snippet" href="x">The first &amp; best result.</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a" href="https://example.org/b">Example B</a>
  <a class="result__snippet" href="x">Second.</a>
</div>
"""

ANOMALY_PAGE = """<html><body><div class="anomaly-modal__title">Unfortunately, bots use DuckDuckGo too.</div>
<div>Please complete the following challenge to confirm this search was made by a human.</div></body></html>"""


class _Response:
    def __init__(self, status=200, text=""):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _serve(monkeypatch, response):
    monkeypatch.setattr(web_search.requests, "get", lambda *a, **k: response)


def test_results_are_parsed_and_unwrapped(monkeypatch):
    _serve(monkeypatch, _Response(200, RESULTS_PAGE))
    results = web_search.search("example")
    assert results[0] == {"title": "Example A", "snippet": "The first & best result.", "url": "https://example.com/a"}
    assert results[1]["url"] == "https://example.org/b"


def test_a_human_check_is_reported_not_returned_as_no_results(monkeypatch):
    _serve(monkeypatch, _Response(200, ANOMALY_PAGE))
    try:
        web_search.search("example")
    except web_search.SearchBlockedError as e:
        assert "human check" in str(e)
    else:
        raise AssertionError("the challenge page should raise SearchBlockedError")


def test_a_429_is_reported_as_too_many_searches(monkeypatch):
    _serve(monkeypatch, _Response(429, ""))
    try:
        web_search.search("example")
    except web_search.SearchBlockedError as e:
        assert "429" in str(e)
    else:
        raise AssertionError("a 429 should raise SearchBlockedError")


def test_other_http_errors_still_raise(monkeypatch):
    _serve(monkeypatch, _Response(500, ""))
    try:
        web_search.search("example")
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("a 500 should raise")
