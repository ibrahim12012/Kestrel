# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_web_render.py
==========================
ai/web_render.py: which browser renders JavaScript pages off screen, how
it is started (headless, a throwaway profile, Kestrel's own identity),
and what happens when it fails. The last test drives a real browser when
one is installed (Edge or Chrome on Windows, or KESTREL_HEADLESS_BROWSER)
against a page served on this machine.
"""

import http.server
import os
import subprocess
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ai import web_reader, web_render

UA = web_reader.USER_AGENT


def test_an_explicit_browser_wins(monkeypatch, tmp_path):
    browser = tmp_path / "browser.exe"
    browser.write_bytes(b"")
    monkeypatch.setenv("KESTREL_HEADLESS_BROWSER", str(browser))
    assert web_render.find_browser() == str(browser)
    monkeypatch.setenv("KESTREL_HEADLESS_BROWSER", str(tmp_path / "missing.exe"))
    assert web_render.find_browser() is None


def test_edge_is_found_where_windows_installs_it(monkeypatch, tmp_path):
    monkeypatch.delenv("KESTREL_HEADLESS_BROWSER", raising=False)
    monkeypatch.setattr(web_render.sys, "platform", "win32")
    for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(tmp_path / name))
    chrome = tmp_path / "ProgramFiles" / "Google" / "Chrome" / "Application" / "chrome.exe"
    edge = tmp_path / "ProgramFiles(x86)" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    for path in (chrome, edge):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
    assert web_render.find_browser() == str(edge)
    edge.unlink()
    assert web_render.find_browser() == str(chrome)


def test_rendering_can_be_turned_off(monkeypatch, tmp_path):
    browser = tmp_path / "browser.exe"
    browser.write_bytes(b"")
    monkeypatch.setenv("KESTREL_HEADLESS_BROWSER", str(browser))
    assert web_render.available()
    monkeypatch.setenv("KESTREL_RENDER_PAGES", "0")
    assert not web_render.available()
    with pytest.raises(web_render.RenderError):
        web_render.render("https://example.com/", UA)


@pytest.fixture
def fake_browser(monkeypatch):
    calls = []
    outcome = {"stdout": b"<html><body><p>rendered</p></body></html>", "raise": None}

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        profile = next(a.split("=", 1)[1] for a in args if a.startswith("--user-data-dir="))
        assert os.path.isdir(profile)
        if outcome["raise"] is not None:
            raise outcome["raise"]
        return subprocess.CompletedProcess(args, 0, stdout=outcome["stdout"], stderr=b"")

    monkeypatch.setattr(web_render, "find_browser", lambda: "C:/Edge/msedge.exe")
    monkeypatch.delenv("KESTREL_RENDER_PAGES", raising=False)
    monkeypatch.setattr(web_render.subprocess, "run", fake_run)
    return calls, outcome


def test_the_browser_runs_headless_as_kestrel_with_a_throwaway_profile(fake_browser):
    calls, _ = fake_browser
    assert web_render.render("https://example.com/app", UA) == "<html><body><p>rendered</p></body></html>"
    (args, kwargs), = calls
    assert args[0] == "C:/Edge/msedge.exe"
    assert "--headless=new" in args
    assert f"--user-agent={UA}" in args
    assert args[-2:] == ["--dump-dom", "https://example.com/app"]
    assert kwargs["timeout"] == web_render.TIMEOUT
    profile = next(a.split("=", 1)[1] for a in args if a.startswith("--user-data-dir="))
    assert os.path.basename(profile).startswith("kestrel-render-")
    assert not os.path.exists(profile)


def test_a_page_that_never_settles_is_given_up_on(fake_browser):
    _, outcome = fake_browser
    outcome["raise"] = subprocess.TimeoutExpired("msedge", web_render.TIMEOUT)
    with pytest.raises(web_render.RenderError, match="still loading"):
        web_render.render("https://example.com/", UA)


def test_an_empty_dump_is_an_error(fake_browser):
    _, outcome = fake_browser
    outcome["stdout"] = b"  \n"
    with pytest.raises(web_render.RenderError):
        web_render.render("https://example.com/", UA)


def test_only_web_addresses_are_rendered(fake_browser):
    calls, _ = fake_browser
    for address in ("file:///C:/Windows/win.ini", "javascript:alert(1)", "C:/x.html"):
        with pytest.raises(web_render.RenderError):
            web_render.render(address, UA)
    assert calls == []


JS_PAGE = b"""<!DOCTYPE html><html><head><title>Shell</title></head><body><div id="root"></div>
<script>setTimeout(function () {
  document.getElementById('root').innerHTML = '<h1>Built by script</h1><p>' + 'Rendered text. '.repeat(40) + '</p>';
  document.title = 'Built';
}, 200);</script></body></html>"""


@pytest.fixture
def local_site():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(JS_PAGE)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


def test_a_real_browser_renders_a_script_built_page(local_site, monkeypatch):
    monkeypatch.delenv("KESTREL_RENDER_PAGES", raising=False)
    if web_render.find_browser() is None:
        pytest.skip("no Edge or Chrome here (set KESTREL_HEADLESS_BROWSER to use another)")
    page = web_reader.read_page(local_site)
    assert page.render_tried
    assert "Rendered text." in page.text
    assert page.title == "Built"
