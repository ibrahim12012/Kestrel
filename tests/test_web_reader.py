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
