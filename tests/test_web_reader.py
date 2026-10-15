# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_web_reader.py
==========================
ai/web_reader.py's extraction and sitemap parsing, against markup shaped
like the real thing rather than a tidy example: lazy-loaded images,
srcset, missing closing tags, tracking pixels, a sitemap index.

No network anywhere. extract() and read_sitemap() are deliberately
separable from fetching so they can be tested on bytes.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from ai import web_reader
from ai.web_reader import extract


PAGE = """
<!DOCTYPE html>
<html><head>
  <title>  Pricing   &amp; plans </title>
  <style>body { color: red }</style>
  <script>var tracking = "do not read me";</script>
</head>
<body>
  <nav><a href="/about">About</a> <a href="https://other.example.com/x">Elsewhere</a></nav>
  <h1>Pricing</h1>
  <p>Three plans, billed monthly.</p>
  <figure>
    <img src="/img/table.png" alt="plan table">
    <figcaption>How the three tiers compare</figcaption>
  </figure>
  <h2>Starter</h2>
  <p>For one person.</p>
  <img data-src="/img/starter.jpg" alt="starter screenshot">
  <img src="/img/pixel.gif" width="1" height="1" alt="">
  <img srcset="/img/wide-800.jpg 800w, /img/wide-1600.jpg 1600w" alt="wide shot">
  <a href="#top">Back to top</a>
  <a href="javascript:void(0)">Nothing</a>
  <a href="/about">About</a>
</body></html>
"""


def _page():
    return extract(PAGE, "https://example.com/pricing")


# -- text --------------------------------------------------------------

def test_title_is_unescaped_and_collapsed():
    assert _page().title == "Pricing & plans"


def test_script_and_style_contents_are_not_text():
    text = _page().text
    assert "do not read me" not in text
    assert "color: red" not in text


def test_headings_keep_their_level():
    text = _page().text
    assert "# Pricing" in text
    assert "## Starter" in text


def test_paragraph_text_survives():
    assert "Three plans, billed monthly." in _page().text


def test_blank_lines_are_collapsed_but_breaks_are_kept():
    text = _page().text
    assert "\n\n\n" not in text
    assert "\n" in text


# -- images ------------------------------------------------------------

def test_relative_image_urls_are_resolved():
    urls = [i.url for i in _page().images]
    assert "https://example.com/img/table.png" in urls


def test_lazy_loaded_images_are_found():
    urls = [i.url for i in _page().images]
    assert "https://example.com/img/starter.jpg" in urls, "data-src was missed"


def test_srcset_images_are_found():
    urls = [i.url for i in _page().images]
    assert "https://example.com/img/wide-800.jpg" in urls


def test_a_declared_tracking_pixel_is_skipped():
    urls = [i.url for i in _page().images]
    assert "https://example.com/img/pixel.gif" not in urls


def test_images_are_numbered_from_one_without_gaps():
    numbers = [i.number for i in _page().images]
    assert numbers == list(range(1, len(numbers) + 1))


def test_a_marker_sits_where_the_image_was():
    text = _page().text
    before = text.index("Pricing\n") if "Pricing\n" in text else 0
    marker = text.index("[image 1:")
    starter = text.index("## Starter")
    assert before < marker < starter, "the first image marker is out of place"


def test_a_figcaption_replaces_the_alt_text_in_the_marker():
    page = _page()
    assert "[image 1: How the three tiers compare]" in page.text
    assert page.images[0].caption == "How the three tiers compare"


def test_a_figcaption_is_not_also_printed_as_body_text():
    """It reads once, in the marker. Printing it again underneath is
    noise, and on a page of figures it is a lot of noise."""
    text = _page().text
    assert text.count("How the three tiers compare") == 1


def test_a_figcaption_with_no_image_is_still_text():
    page = extract("<figure><figcaption>Standalone</figcaption></figure>",
                   "https://example.com/")
    assert "Standalone" in page.text


def test_alt_text_is_used_when_there_is_no_caption():
    assert "[image 2: starter screenshot]" in _page().text


def test_the_same_image_twice_is_one_image():
    html = '<img src="/logo.png" alt="logo"><p>hi</p><img src="/logo.png" alt="logo">'
    assert len(extract(html, "https://example.com/").images) == 1


def test_an_image_with_no_alt_still_gets_a_marker():
    page = extract('<img src="/a.png">', "https://example.com/")
    assert "[image 1: no caption]" in page.text


def test_data_uri_images_are_skipped():
    html = '<img src="data:image/png;base64,AAAA" alt="inline">'
    assert extract(html, "https://example.com/").images == []


# -- links -------------------------------------------------------------

def test_links_are_absolute_and_deduplicated():
    links = _page().links
    assert "https://example.com/about" in links
    assert links.count("https://example.com/about") == 1


def test_javascript_and_fragment_links_are_dropped():
    links = _page().links
    assert not any(l.startswith("javascript:") for l in links)
    assert not any(l.endswith("#top") for l in links)


def test_offsite_links_are_kept_not_filtered_here():
    # Filtering by host is the crawler's job, not the parser's.
    assert "https://other.example.com/x" in _page().links


# -- broken markup -----------------------------------------------------

def test_unclosed_tags_do_not_lose_the_text():
    html = "<html><body><p>first<p>second<div>third</body>"
    text = extract(html, "https://example.com/").text
    assert "first" in text and "second" in text and "third" in text


def test_an_empty_document_is_an_empty_page():
    page = extract("", "https://example.com/")
    assert page.text == "" and page.images == [] and page.links == []


def test_a_document_that_is_only_script_has_no_text():
    page = extract("<script>alert(1)</script>", "https://example.com/")
    assert page.text == ""


def test_entities_are_decoded():
    page = extract("<p>caf&eacute; &amp; cr&egrave;me</p>", "https://example.com/")
    assert "café & crème" in page.text


# -- sitemaps ----------------------------------------------------------

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/</loc></url>
  <url><loc>https://example.com/pricing</loc></url>
  <url><loc>https://example.com/pricing</loc></url>
</urlset>"""

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-pages.xml</loc></sitemap>
</sitemapindex>"""


def _sitemap_server(monkeypatch, pages: dict):
    def fake_fetch(url, session=None, max_bytes=None, accept_types=None):
        if url not in pages:
            raise web_reader.WebReadError(f"{url} not found")
        return pages[url].encode("utf-8"), url
    monkeypatch.setattr(web_reader, "_fetch", fake_fetch)


def test_sitemap_urls_are_read_and_deduplicated(monkeypatch):
    _sitemap_server(monkeypatch, {"https://example.com/sitemap.xml": SITEMAP})
    urls = web_reader.read_sitemap("https://example.com/sitemap.xml")
    assert urls == ["https://example.com/", "https://example.com/pricing"]


def test_a_sitemap_index_is_followed(monkeypatch):
    _sitemap_server(monkeypatch, {
        "https://example.com/sitemap.xml": SITEMAP_INDEX,
        "https://example.com/sitemap-pages.xml": SITEMAP,
    })
    urls = web_reader.read_sitemap("https://example.com/sitemap.xml")
    assert "https://example.com/pricing" in urls


def test_a_self_referencing_sitemap_index_terminates(monkeypatch):
    loop = """<?xml version="1.0"?><sitemapindex>
      <sitemap><loc>https://example.com/sitemap.xml</loc></sitemap>
    </sitemapindex>"""
    _sitemap_server(monkeypatch, {"https://example.com/sitemap.xml": loop})
    assert web_reader.read_sitemap("https://example.com/sitemap.xml") == []


def test_a_missing_sitemap_is_not_an_error(monkeypatch):
    _sitemap_server(monkeypatch, {})
    assert web_reader.read_sitemap("https://example.com/sitemap.xml") == []


def test_sitemap_candidates_prefer_what_robots_txt_says(monkeypatch):
    _sitemap_server(monkeypatch, {
        "https://example.com/robots.txt": "User-agent: *\nSitemap: https://example.com/custom.xml\n",
    })
    candidates = web_reader.sitemap_candidates("https://example.com/pricing")
    assert candidates[0] == "https://example.com/custom.xml"
    assert "https://example.com/sitemap.xml" in candidates


def test_a_url_with_no_scheme_is_refused():
    try:
        web_reader._origin("example.com/pricing")
    except web_reader.WebReadError as e:
        assert "https://" in str(e)
    else:
        raise AssertionError("a bare hostname should have been refused")


# -- saying who is asking -----------------------------------------------

def test_the_user_agent_names_kestrel_instead_of_claiming_to_be_chrome():
    assert "compatible; Kestrel-User/" in web_reader.USER_AGENT
    assert "Chrome/" not in web_reader.USER_AGENT
    assert web_reader.USER_AGENT.startswith("Mozilla/5.0")


# -- human checks -------------------------------------------------------

CLOUDFLARE_CHALLENGE = """<!DOCTYPE html><html><head><title>Just a moment...</title></head>
<body><div id="challenge-error-text">Enable JavaScript and cookies to continue</div>
<script>window._cf_chl_opt={cvId: '3'};</script></body></html>"""

AKAMAI_DENIED = """<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY>
<H1>Access Denied</H1>You don't have permission to access this server.<P>
Reference&#32;&#35;18&#46;2f1e3b17&#46;1695812345&#46;1a2b3c</BODY></HTML>"""

ARTICLE_WITH_A_COMMENT_FORM = (
    "<html><body><h1>How reCAPTCHA works</h1>"
    + "<p>" + ("A long article about bots, and whether the reader is a robot. " * 60) + "</p>"
    + '<form><div class="g-recaptcha" data-sitekey="x"></div><button>Post comment</button></form>'
    + "</body></html>"
)

LOGIN_PAGE_WITH_A_CAPTCHA_BOX = """<html><body><h1>Sign in</h1>
<form><input name="email"><input name="password" type="password">
<div class="g-recaptcha" data-sitekey="x"></div><button>Sign in</button></form></body></html>"""

APP_SHELL_ON_CLOUDFLARE = """<html><head><title>Dashboard</title></head><body><div id="root"></div>
<script>a.src='/cdn-cgi/challenge-platform/scripts/jsd/main.js';</script></body></html>"""


def test_a_cloudflare_challenge_is_recognised_on_a_403():
    assert web_reader._challenge_kind(403, {}, CLOUDFLARE_CHALLENGE) == "a Cloudflare bot check"


def test_cloudflare_says_so_in_a_header():
    assert web_reader._challenge_kind(403, {"cf-mitigated": "challenge"}, "") == "a Cloudflare bot check"


def test_a_challenge_page_served_with_200_is_still_a_challenge():
    assert web_reader._challenge_kind(200, {}, CLOUDFLARE_CHALLENGE) == "a Cloudflare bot check"


def test_an_entity_encoded_akamai_denial_is_recognised():
    assert web_reader._challenge_kind(403, {}, AKAMAI_DENIED) == "bot protection"


def test_a_vendor_script_on_a_refused_request_counts():
    page = '<html><script src="https://js.datadome.co/tags.js"></script></html>'
    assert web_reader._challenge_kind(403, {}, page) == "bot protection"


def test_a_long_page_that_mentions_robots_is_not_a_wall():
    assert web_reader._challenge_kind(200, {}, ARTICLE_WITH_A_COMMENT_FORM) is None


def test_a_captcha_box_on_a_login_form_is_not_a_wall():
    assert web_reader._challenge_kind(200, {}, LOGIN_PAGE_WITH_A_CAPTCHA_BOX) is None


def test_cloudflares_everyday_script_is_not_a_wall():
    assert web_reader._challenge_kind(200, {}, APP_SHELL_ON_CLOUDFLARE) is None


def test_an_ordinary_403_is_not_called_a_human_check():
    assert web_reader._challenge_kind(403, {}, "<html><h1>Forbidden</h1></html>") is None


class _FakeResponse:
    def __init__(self, status=200, body="", headers=None, url="https://example.com/"):
        self.status_code = status
        self.headers = {"Content-Type": "text/html; charset=utf-8", **(headers or {})}
        self._body = body.encode("utf-8")
        self.url = url
        self.closed = False

    def iter_content(self, size):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, url, **kwargs):
        self.response.url = url
        return self.response


def test_a_429_stops_with_the_wait_the_site_asked_for():
    session = _FakeSession(_FakeResponse(429, "slow down", {"Retry-After": "30"}))
    try:
        web_reader._fetch("https://example.com/a", session=session)
    except web_reader.BlockedError as e:
        assert "429" in str(e) and "30 seconds" in str(e)
        assert e.status == 429
    else:
        raise AssertionError("a 429 should stop the read")


def test_a_challenge_on_a_403_raises_blocked():
    session = _FakeSession(_FakeResponse(403, CLOUDFLARE_CHALLENGE))
    try:
        web_reader._fetch("https://example.com/a", session=session)
    except web_reader.BlockedError as e:
        assert "Cloudflare" in str(e) and "doesn't try to get past" in str(e)
    else:
        raise AssertionError("a challenge should raise BlockedError")


def test_a_plain_404_is_an_error_with_its_status_but_not_blocked():
    session = _FakeSession(_FakeResponse(404, "<h1>Not found</h1>"))
    try:
        web_reader._fetch("https://example.com/a", session=session)
    except web_reader.BlockedError:
        raise AssertionError("a 404 is not a human check")
    except web_reader.WebReadError as e:
        assert e.status == 404
    else:
        raise AssertionError("a 404 should raise")


# -- a fake site for read_page / crawl_site -----------------------------

def _fake_site(monkeypatch, pages: dict, blocked=(), statuses=None):
    """`pages` maps URL -> body. URLs in `blocked` raise BlockedError,
    `statuses` maps URL -> an HTTP error status. Returns the list of URLs
    fetched, in order."""
    statuses = statuses or {}
    fetched = []

    def fake_fetch(url, session=None, max_bytes=None, accept_types=None):
        fetched.append(url)
        if url in blocked:
            raise web_reader.BlockedError(web_reader._blocked_message(url, "a CAPTCHA"), status=403)
        if url in statuses:
            raise web_reader.WebReadError(f"{url} returned HTTP {statuses[url]}.", status=statuses[url])
        if url not in pages:
            raise web_reader.WebReadError(f"{url} returned HTTP 404.", status=404)
        return pages[url].encode("utf-8"), url

    monkeypatch.setattr(web_reader, "_fetch", fake_fetch)
    monkeypatch.setattr(web_reader.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("KESTREL_RENDER_PAGES", "0")      # see the rendering tests below
    return fetched


def _sitemap_of(*urls):
    return ("<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>")


def _article(title):
    return f"<html><head><title>{title}</title></head><body><p>{title} " + ("words " * 80) + "</p></body></html>"


def test_read_page_reports_a_blocked_page_as_blocked(monkeypatch):
    _fake_site(monkeypatch, {}, blocked=("https://example.com/a",))
    page = web_reader.read_page("https://example.com/a")
    assert page.blocked and "CAPTCHA" in page.error


def test_read_page_catches_a_challenge_served_with_200(monkeypatch):
    _fake_site(monkeypatch, {"https://example.com/a": CLOUDFLARE_CHALLENGE})
    page = web_reader.read_page("https://example.com/a")
    assert page.blocked and "Cloudflare" in page.error


def test_a_javascript_page_says_so_and_gives_its_metadata(monkeypatch):
    shell = """<html><head><title>Acme</title>
    <meta name="description" content="Acme sells rockets &amp; anvils.">
    <script type="application/ld+json">{"@type": "Organization", "name": "Acme Corporation"}</script>
    </head><body><div id="root"></div></body></html>"""
    _fake_site(monkeypatch, {"https://example.com/": shell})
    page = web_reader.read_page("https://example.com/")
    assert not page.error
    assert "JavaScript" in page.text
    assert "Acme sells rockets & anvils." in page.text
    assert "Acme Corporation" in page.text


def test_a_short_plain_page_is_left_alone(monkeypatch):
    _fake_site(monkeypatch, {"https://example.com/": "<html><body><p>Hello.</p></body></html>"})
    page = web_reader.read_page("https://example.com/")
    assert page.text == "Hello."


# -- crawling -----------------------------------------------------------

def test_a_crawl_stops_at_the_first_human_check(monkeypatch):
    urls = [f"https://example.com/{n}" for n in range(1, 5)]
    fetched = _fake_site(monkeypatch, {
        "https://example.com/sitemap.xml": _sitemap_of(*urls),
        urls[0]: _article("One"), urls[2]: _article("Three"), urls[3]: _article("Four"),
    }, blocked=(urls[1],))
    pages = web_reader.crawl_site("https://example.com/", max_pages=10)
    assert [p.url for p in pages] == urls[:2]
    assert pages[1].blocked
    assert urls[2] not in fetched and urls[3] not in fetched


def test_a_crawl_reads_robots_txt_once(monkeypatch):
    fetched = _fake_site(monkeypatch, {
        "https://example.com/robots.txt": "User-agent: *\nSitemap: https://example.com/pages.xml\n",
        "https://example.com/pages.xml": _sitemap_of("https://example.com/a"),
        "https://example.com/a": _article("A"),
    })
    pages = web_reader.crawl_site("https://example.com/")
    assert [p.title for p in pages] == ["A"]
    assert fetched.count("https://example.com/robots.txt") == 1


def test_robots_rules_for_kestrel_are_followed(monkeypatch):
    _fake_site(monkeypatch, {
        "https://example.com/robots.txt": "User-agent: Kestrel-User\nDisallow: /private\n",
        "https://example.com/sitemap.xml": _sitemap_of("https://example.com/a", "https://example.com/private/b"),
        "https://example.com/a": _article("A"),
        "https://example.com/private/b": _article("B"),
    })
    pages = web_reader.crawl_site("https://example.com/")
    assert pages[0].title == "A"
    assert "robots.txt" in pages[1].error


def test_a_site_that_disallows_crawling_is_not_crawled(monkeypatch):
    fetched = _fake_site(monkeypatch, {"https://example.com/robots.txt": "User-agent: *\nDisallow: /\n"})
    try:
        web_reader.crawl_site("https://example.com/")
    except web_reader.WebReadError as e:
        assert "robots.txt" in str(e)
    else:
        raise AssertionError("a disallowed site should not be crawled")
    assert fetched == ["https://example.com/robots.txt"]


def test_a_robots_txt_server_error_means_not_now(monkeypatch):
    _fake_site(monkeypatch, {}, statuses={"https://example.com/robots.txt": 503})
    try:
        web_reader.crawl_site("https://example.com/")
    except web_reader.WebReadError as e:
        assert "server error" in str(e)
    else:
        raise AssertionError("a 5xx robots.txt should stop the crawl")


def test_a_missing_robots_txt_means_no_rules(monkeypatch):
    _fake_site(monkeypatch, {
        "https://example.com/sitemap.xml": _sitemap_of("https://example.com/a"),
        "https://example.com/a": _article("A"),
    })
    assert [p.title for p in web_reader.crawl_site("https://example.com/")] == ["A"]


def test_a_site_behind_a_check_is_not_crawled(monkeypatch):
    _fake_site(monkeypatch, {}, blocked=("https://example.com/robots.txt",))
    try:
        web_reader.crawl_site("https://example.com/")
    except web_reader.BlockedError as e:
        assert "CAPTCHA" in str(e)
    else:
        raise AssertionError("a site behind a human check should not be crawled")


def test_crawl_delay_is_honoured_up_to_a_cap(monkeypatch):
    for declared, expected in (("30", web_reader.MAX_CRAWL_DELAY), ("2", 2.0), ("0", web_reader.CRAWL_DELAY)):
        _fake_site(monkeypatch, {"https://example.com/robots.txt": f"User-agent: *\nCrawl-delay: {declared}\n"})
        assert web_reader._Robots("https://example.com/").delay() == expected
    _fake_site(monkeypatch, {})
    assert web_reader._Robots("https://example.com/").delay() == web_reader.CRAWL_DELAY


def test_known_robots_text_is_not_fetched_again(monkeypatch):
    fetched = _fake_site(monkeypatch, {})
    candidates = web_reader.sitemap_candidates(
        "https://example.com/", robots_text="Sitemap: https://example.com/s.xml\n")
    assert candidates[0] == "https://example.com/s.xml"
    assert fetched == []


# -- pages that build themselves with JavaScript --------------------------

APP_SHELL = """<html><head><title>Acme</title></head><body><div id="root"></div>
<script src="/app.js"></script></body></html>"""

RENDERED_APP = ("<html><head><title>Acme - Pricing</title></head><body><div id=\"root\">"
                "<h1>Pricing</h1><p>" + "Starter is 9 euros a month. " * 30 + "</p></div></body></html>")


@pytest.fixture
def browser(monkeypatch):
    """An off-screen browser that returns canned HTML; records the URLs."""
    from ai import web_render
    rendered = []
    result = {"html": RENDERED_APP}

    def fake_render(url, user_agent):
        rendered.append(url)
        assert "Kestrel-User/" in user_agent
        if isinstance(result["html"], Exception):
            raise result["html"]
        return result["html"]

    monkeypatch.setattr(web_render, "available", lambda: True)
    monkeypatch.setattr(web_render, "render", fake_render)
    return rendered, result


def _serve(monkeypatch, pages):
    fetched = _fake_site(monkeypatch, pages)
    monkeypatch.setenv("KESTREL_RENDER_PAGES", "1")
    return fetched


def test_an_empty_app_shell_is_read_in_a_browser(monkeypatch, browser):
    rendered, _ = browser
    _serve(monkeypatch, {"https://example.com/": APP_SHELL})
    page = web_reader.read_page("https://example.com/")
    assert rendered == ["https://example.com/"]
    assert "Starter is 9 euros a month." in page.text
    assert page.title == "Acme - Pricing"
    assert page.render_tried and not page.error


def test_a_check_that_appears_once_rendered_stops_the_read(monkeypatch, browser):
    _, result = browser
    result["html"] = CLOUDFLARE_CHALLENGE
    _serve(monkeypatch, {"https://example.com/": APP_SHELL})
    page = web_reader.read_page("https://example.com/")
    assert page.blocked and "Cloudflare" in page.error


def test_a_page_with_text_is_not_rendered(monkeypatch, browser):
    rendered, _ = browser
    _serve(monkeypatch, {"https://example.com/": _article("Plenty") + "<script>x()</script>"})
    web_reader.read_page("https://example.com/")
    assert rendered == []


def test_a_blocked_page_is_never_rendered(monkeypatch, browser):
    rendered, _ = browser
    _fake_site(monkeypatch, {}, blocked=("https://example.com/",))
    monkeypatch.setenv("KESTREL_RENDER_PAGES", "1")
    assert web_reader.read_page("https://example.com/").blocked
    assert rendered == []


def test_a_failed_render_falls_back_to_what_the_page_says_about_itself(monkeypatch, browser):
    from ai import web_render
    _, result = browser
    result["html"] = web_render.RenderError("the page was still loading after 20 seconds")
    shell = APP_SHELL.replace("<title>Acme</title>",
                              '<title>Acme</title><meta name="description" content="Rockets and anvils.">')
    _serve(monkeypatch, {"https://example.com/": shell})
    page = web_reader.read_page("https://example.com/")
    assert not page.error and "Rockets and anvils." in page.text


def test_rendering_can_be_skipped(monkeypatch, browser):
    rendered, _ = browser
    _serve(monkeypatch, {"https://example.com/": APP_SHELL})
    web_reader.read_page("https://example.com/", render=False)
    assert rendered == []


def test_a_crawl_renders_only_a_few_pages(monkeypatch, browser):
    rendered, _ = browser
    urls = [f"https://example.com/{n}" for n in range(1, 10)]
    _serve(monkeypatch, {"https://example.com/sitemap.xml": _sitemap_of(*urls), **{u: APP_SHELL for u in urls}})
    pages = web_reader.crawl_site("https://example.com/", max_pages=9)
    assert len(pages) == 9
    assert len(rendered) == web_reader.MAX_RENDERED_PAGES
