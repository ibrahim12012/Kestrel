# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/web_search.py
===================
Backs ai.actions.tool_web_search - the one thing every provider (Gemini,
OpenAI, Anthropic) was completely missing until now: a way to look
anything up beyond its own training data. Kept as its own module for
the same reason ai/file_generation.py is: a genuinely separate concern
from the desktop-automation code actions.py is otherwise full of, with
its own network/parsing logic and its own way of failing.

Deliberately NOT wired through Gemini's own built-in google_search
grounding tool (dropping `types.Tool(google_search=types.GoogleSearch())`
into gemini_provider.py's tools list would've been the "a few lines"
version) - mixing that built-in tool with this app's own custom
function-calling tools (every tool_* in actions.py) in the same request
is finicky and version-dependent across Gemini model families, and
would've meant Gemini's web search behaved completely differently from
OpenAI's/Anthropic's (a different tool, a different result shape, a
different set of edge cases) rather than one consistent tool_web_search
that works the same way - and is tested the same way - on all three.

No API key, no signup, no paid tier: this scrapes DuckDuckGo's "no-JS"
HTML results page (html.duckduckgo.com/html/) rather than calling a
real search API (Google/Bing/Brave's search APIs all require their own
separate paid API key, which would mean yet another key field in
Settings just for this one tool). The real trade-off worth knowing:
this is a THIRD PARTY'S HTML PAGE, parsed with a regex tuned to its
current markup, not a stable documented API contract - if DuckDuckGo
ever changes that markup, parsing breaks silently (returns zero
results, not an exception) until this regex gets updated to match.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import requests

_SEARCH_URL = "https://html.duckduckgo.com/html/"

# A real browser's User-Agent - DuckDuckGo's HTML endpoint has been
# known to serve a different (JS-only, unparsable) page or an outright
# block to requests that look scripted otherwise.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Matches one result block's title link + snippet together. DOTALL so
# ".*?" can cross the newlines/other markup that sits between them in
# the real page. Non-greedy throughout so this can't accidentally span
# from one result's title all the way to a LATER result's snippet.
_RESULT_RE = re.compile(
    r'<a rel="nofollow" class="result__a" href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
    r'<a class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html_fragment: str) -> str:
    """Result titles/snippets can contain <b> around the matched query
    term(s) - this drops any tag, not just <b>, and unescapes entities
    (&amp; etc.) so the model gets plain, readable text."""
    return unescape(_TAG_RE.sub("", html_fragment)).strip()


def _resolve_redirect(href: str) -> str:
    """
    DuckDuckGo's HTML results don't link straight to the real page -
    every href is DuckDuckGo's own /l/?uddg=<url-encoded-real-url>
    redirect wrapper, presumably for its own click tracking. Unwraps it
    to the actual destination URL; falls back to the href as-is if it
    isn't in that shape (defensive - if DDG ever stops wrapping links,
    this should degrade to "the href IS the URL" rather than breaking).
    """
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        query_params = parse_qs(parsed.query)
        if "uddg" in query_params:
            return unquote(query_params["uddg"][0])
    return href


def _parse_results(html_text: str, max_results: int) -> "list[dict]":
    results = []
    for match in _RESULT_RE.finditer(html_text):
        title = _strip_tags(match.group("title"))
        snippet = _strip_tags(match.group("snippet"))
        url = _resolve_redirect(match.group("href"))
        if title and url:
            results.append({"title": title, "snippet": snippet, "url": url})
        if len(results) >= max_results:
            break
    return results


def search(query: str, max_results: int = 5) -> "list[dict]":
    """
    Returns up to `max_results` {"title", "snippet", "url"} dicts for
    `query`, or an empty list if nothing came back - either because the
    query genuinely had no results, or (silently, by design - see
    module docstring) because DuckDuckGo's markup no longer matches
    _RESULT_RE. Raises requests.RequestException (network error, bad
    status code, timeout) rather than swallowing it - ai.actions.
    tool_web_search is what turns that into a plain message the model
    can read instead of a traceback.
    """
    response = requests.get(
        _SEARCH_URL, params={"q": query}, headers=_HEADERS, timeout=10,
    )
    response.raise_for_status()
    return _parse_results(response.text, max_results)
