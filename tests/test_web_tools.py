# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_web_tools.py
=========================
The web tools as the model sees them (ai/actions.py): a page behind a
human check comes back as a plain explanation, a crawl says where it
stopped, and the pages an answer drew on are queued for the chat's
Sources card (ai.main.queue_sources / pop_pending_sources).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.actions as actions
import ai.main as ai_main
from ai import web_reader, web_search


@pytest.fixture(autouse=True)
def empty_sources():
    ai_main.pop_pending_sources()
    yield
    ai_main.pop_pending_sources()


def _page(url, title="", text="", error="", blocked=False):
    return web_reader.Page(url=url, title=title, text=text, error=error, blocked=blocked)


def test_a_blocked_page_is_explained_not_read(monkeypatch):
    message = web_reader._blocked_message("https://example.com/", "a CAPTCHA")
    monkeypatch.setattr(web_reader, "read_page", lambda url: _page(url, error=message, blocked=True))
    result = actions.tool_read_url("example.com")
    assert result == message
    assert "doesn't try to get past" in result
    assert ai_main.pop_pending_sources() == []


def test_a_page_that_was_read_is_a_source(monkeypatch):
    monkeypatch.setattr(web_reader, "read_page",
                        lambda url: _page(url, title="Pricing", text="Three plans."))
    result = actions.tool_read_url("https://example.com/pricing", include_images=False)
    assert "Three plans." in result
    assert ai_main.pop_pending_sources() == [
        {"url": "https://example.com/pricing", "title": "Pricing", "kind": "read"}]


def test_a_crawl_says_where_it_stopped_and_why(monkeypatch):
    message = web_reader._blocked_message("https://example.com/b", "a Cloudflare bot check")
    pages = [
        _page("https://example.com/a", title="A", text="About us."),
        _page("https://example.com/b", error=message, blocked=True),
    ]
    monkeypatch.setattr(web_reader, "crawl_site", lambda url, max_pages=10: pages)
    result = actions.tool_crawl_site("https://example.com/")
    assert "Read 1 page(s)" in result
    assert result.rstrip().endswith(f"The crawl stopped there. {message}")
    assert "couldn't be read" not in result


def test_a_crawl_stopped_at_the_first_page_is_just_the_explanation(monkeypatch):
    message = web_reader._blocked_message("https://example.com/", "a CAPTCHA")
    monkeypatch.setattr(web_reader, "crawl_site",
                        lambda url, max_pages=10: [_page("https://example.com/", error=message, blocked=True)])
    assert actions.tool_crawl_site("https://example.com/") == message


def test_a_site_that_refuses_crawling_is_explained(monkeypatch):
    def refuse(url, max_pages=10):
        raise web_reader.WebReadError("example.com's robots.txt asks automated readers like Kestrel not to crawl it")
    monkeypatch.setattr(web_reader, "crawl_site", refuse)
    assert "robots.txt" in actions.tool_crawl_site("https://example.com/")


def test_search_results_are_sources_found_not_read(monkeypatch):
    monkeypatch.setattr(web_search, "search", lambda q, max_results=5: [
        {"title": "A", "snippet": "a", "url": "https://a.example/"},
        {"title": "B", "snippet": "b", "url": "https://b.example/"},
    ])
    actions.tool_web_search("anything")
    assert [s["kind"] for s in ai_main.pop_pending_sources()] == ["found", "found"]


def test_a_search_human_check_is_reported(monkeypatch):
    def blocked(q, max_results=5):
        raise web_search.SearchBlockedError("DuckDuckGo asked for a human check instead of showing results.")
    monkeypatch.setattr(web_search, "search", blocked)
    assert "human check" in actions.tool_web_search("anything")


def test_sources_are_listed_once_with_pages_read_first():
    ai_main.queue_sources([{"url": "https://a.example/", "title": "A"},
                           {"url": "https://b.example/", "title": "B"}], "found")
    ai_main.queue_sources([{"url": "https://b.example/", "title": "B page"},
                           {"url": "", "title": "no url"}], "read")
    assert ai_main.pop_pending_sources() == [
        {"url": "https://b.example/", "title": "B page", "kind": "read"},
        {"url": "https://a.example/", "title": "A", "kind": "found"},
    ]
    assert ai_main.pop_pending_sources() == []


def test_a_run_that_did_nothing_on_screen_saves_no_macro(tmp_path, monkeypatch):
    """Reading the web isn't recorded, so a goal met that way has nothing
    to replay; saving it would add an empty "skill" to the tool list."""
    import ai.macro_store as macro_store
    saved = tmp_path / "saved"
    saved.mkdir()
    monkeypatch.setattr(macro_store, "MACROS_DIR", str(saved))
    monkeypatch.setattr(macro_store, "load_and_register_all", lambda module: 0)
    monkeypatch.setattr(actions, "actions_list", [])
    monkeypatch.setattr(actions, "fcn_name", False)
    monkeypatch.setattr(actions, "last_saved_name", "an_older_macro")

    actions.tool_name_action("find_weather_paris")

    assert actions.fcn_name is True
    assert list(saved.iterdir()) == []
    assert actions.last_saved_name is None      # nothing for the generalizer to rewrite
