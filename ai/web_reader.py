# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/web_reader.py
==================
Reading web pages, for ai.actions.tool_read_url and tool_crawl_site.

tool_web_search finds pages; this is what opens them. Without it the
assistant can be handed a link and has nothing to do with it but guess
from the URL, which is the failure the two tools here exist to fix.

Three things, in order of how much they cost:

  read_page(url)        one page: text, headings, links, image refs
  discover_pages(url)   a site's URLs, from robots.txt and sitemap.xml
  crawl_site(url, n)    read_page over the first n of those

Images
------
A page's images are part of what it says, and alt text alone is thin.
Each <img> is given a number, a marker goes into the extracted text
exactly where the image sat, and prepare_images() downloads the ones
worth looking at and shrinks them. So the text reads:

    ## Pricing

    [image 2: comparison table of the three tiers]

    Every plan includes...

and the model knows both what the image is captioned and where on the
page it belongs, which a flat list of image URLs at the end does not
convey.

Shrinking is not politeness, it is the point: a 4000px hero image costs
a lot of tokens to look at and says nothing a 900px copy does not.

Parsing
-------
html.parser from the standard library, not BeautifulSoup or lxml. Those
are better parsers, and neither is currently a dependency; the markup
this has to survive is real-world bad but the job (text, headings,
links, images) is shallow enough that the standard library does it.

Limits are enforced on bytes read, not on what a server claims in
Content-Length, because a server can lie or omit it and the failure
mode is filling the machine's memory.
"""

from __future__ import annotations

import io
import os
import re
import time
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import requests

from ai.log import log as print

# A real browser's User-Agent. Plenty of sites serve a stub page or a
# block to anything that looks scripted.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

REQUEST_TIMEOUT = 15          # seconds, per request
MAX_PAGE_BYTES = 3_000_000    # a page past this is an asset, not an article
MAX_IMAGE_BYTES = 8_000_000
MAX_SITEMAP_BYTES = 10_000_000
DEFAULT_MAX_PAGES = 12        # a crawl is context for a question, not a mirror
DEFAULT_MAX_IMAGES = 6
IMAGE_MAX_EDGE = 900          # px, longest side after shrinking
CRAWL_DELAY = 0.4             # seconds between requests to one host

# Tags whose contents are markup, not prose.
_SKIP_CONTENT = {"script", "style", "noscript", "template", "svg", "canvas"}

# Tags that end a line of text.
_BLOCK_TAGS = {
    "p", "div", "section", "article", "header", "footer", "main", "aside",
    "li", "ul", "ol", "table", "tr", "br", "hr", "blockquote", "pre",
    "figure", "figcaption", "form", "nav", "h1", "h2", "h3", "h4", "h5", "h6",
}

_HEADINGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}

# Images this small are icons, spacers and tracking pixels.
MIN_IMAGE_EDGE = 80

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif")


class WebReadError(Exception):
    """Raised with a sentence meant to be shown to the user or handed
    back to the model as the tool's result."""


@dataclass
class ImageRef:
    """One <img> on a page, before anything has been downloaded."""
    number: int
    url: str
    alt: str = ""
    caption: str = ""
    local_path: str = ""      # set by prepare_images
    width: int = 0            # after shrinking
    height: int = 0

    def label(self) -> str:
        return self.caption or self.alt or "no caption"


@dataclass
class Page:
    url: str
    title: str = ""
    text: str = ""
    links: list = field(default_factory=list)
    images: list = field(default_factory=list)
    error: str = ""


# ══════════════════════════════════════════════════════════════════
# Fetching
# ══════════════════════════════════════════════════════════════════

def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en,*;q=0.5",
    })
    return session


def _fetch(url: str, session=None, max_bytes: int = MAX_PAGE_BYTES,
           accept_types=("text/html", "application/xhtml", "text/plain")) -> "tuple[bytes, str]":
    """
    (body, final_url). Streams so an oversized response is cut off rather
    than read into memory whole, since Content-Length is optional and
    not always honest.
    """
    session = session or _session()
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT, stream=True, allow_redirects=True)
    except requests.RequestException as e:
        raise WebReadError(f"Couldn't reach {url}: {e}") from e

    if response.status_code >= 400:
        response.close()
        raise WebReadError(f"{url} returned HTTP {response.status_code}.")

    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if accept_types and content_type and not any(content_type.startswith(t) for t in accept_types):
        response.close()
        raise WebReadError(f"{url} is {content_type}, not a readable page.")

    chunks, total = [], 0
    try:
        for chunk in response.iter_content(8192):
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                print(f"[web] {url} is over {max_bytes} bytes; reading the first part only.")
                break
    except requests.RequestException as e:
        raise WebReadError(f"The connection to {url} broke: {e}") from e
    finally:
        response.close()

    return b"".join(chunks), response.url


def _decode(body: bytes) -> str:
    """Text from bytes, preferring the document's own declared encoding
    over requests' guess, and never raising on a bad byte."""
    head = body[:2048].decode("ascii", "ignore").lower()
    match = re.search(r'charset=["\']?([a-z0-9_\-]+)', head)
    if match:
        try:
            return body.decode(match.group(1), "replace")
        except (LookupError, UnicodeDecodeError):
            pass
    return body.decode("utf-8", "replace")


# ══════════════════════════════════════════════════════════════════
# Extraction
# ══════════════════════════════════════════════════════════════════

class _PageParser(HTMLParser):
    """
    Text, headings, links and image markers out of one page.

    Not a readability implementation: it keeps the whole document rather
    than guessing which div is the article. Guessing wrong silently
    drops the thing that was being looked for, and the model is better
    at ignoring a nav menu than this is at identifying one.
    """

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.links: list = []
        self.images: list = []

        self._parts: list = []
        self._skip_depth = 0
        self._in_title = False
        self._image_number = 0
        self._pending_figure_image = None   # an <img> awaiting its <figcaption>
        self._in_figcaption = False
        self._figcaption: list = []
        self._seen_image_urls: set = set()

    # -- tags ---------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        if self._skip_depth:
            if tag in _SKIP_CONTENT:
                self._skip_depth += 1
            return
        if tag in _SKIP_CONTENT:
            self._skip_depth = 1
            return

        attributes = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "img":
            self._handle_image(attributes)
        elif tag == "a":
            href = attributes.get("href")
            if href:
                resolved = self._resolve(href)
                if resolved:
                    self.links.append(resolved)
        elif tag == "figcaption":
            self._in_figcaption = True
            self._figcaption = []
        elif tag in _HEADINGS:
            self._parts.append(f"\n\n{_HEADINGS[tag]} ")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self._skip_depth:
            if tag in _SKIP_CONTENT:
                self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        elif tag == "figcaption":
            self._in_figcaption = False
            caption = " ".join("".join(self._figcaption).split())
            if caption and self._pending_figure_image is not None:
                self._pending_figure_image.caption = caption
            self._figcaption = []
        elif tag == "figure":
            self._pending_figure_image = None
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data
            return
        if self._in_figcaption:
            self._figcaption.append(data)
            if self._pending_figure_image is not None:
                # It is about to become the image's marker text. Emitting
                # it as body text too prints the caption twice, once in
                # the marker and once right under it.
                return
        self._parts.append(data)

    # -- images -------------------------------------------------------

    def _handle_image(self, attributes: dict) -> None:
        # src first, then the lazy-loading attributes half the web uses
        # instead. srcset holds several candidates; the first is enough,
        # since this shrinks whatever it gets anyway.
        source = (
            attributes.get("src")
            or attributes.get("data-src")
            or attributes.get("data-original")
            or (attributes.get("srcset") or attributes.get("data-srcset") or "").split(",")[0].strip().split(" ")[0]
        )
        if not source:
            return
        resolved = self._resolve(source)
        if not resolved or resolved.startswith("data:"):
            return
        if resolved in self._seen_image_urls:
            return   # the same logo in the header and the footer is one image
        if _declared_too_small(attributes):
            return

        self._seen_image_urls.add(resolved)
        self._image_number += 1
        ref = ImageRef(
            number=self._image_number,
            url=resolved,
            alt=" ".join((attributes.get("alt") or "").split()),
        )
        self.images.append(ref)
        self._pending_figure_image = ref
        # The marker goes in the text at the point the image appeared,
        # which is the only way the model can tell what it illustrates.
        self._parts.append(f"\n[image {ref.number}: {ref.alt or 'no caption'}]\n")

    # -- helpers ------------------------------------------------------

    def _resolve(self, href: str) -> str:
        href = unescape(href.strip())
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            return ""
        try:
            return urldefrag(urljoin(self.base_url, href))[0]
        except ValueError:
            return ""

    def text(self) -> str:
        return _tidy("".join(self._parts))


def _declared_too_small(attributes: dict) -> bool:
    """True for an image the markup itself says is icon-sized. Only the
    declared size: nothing has been downloaded at this point."""
    for key in ("width", "height"):
        raw = (attributes.get(key) or "").strip().rstrip("px")
        if raw.isdigit() and int(raw) < MIN_IMAGE_EDGE:
            return True
    return False


def _tidy(text: str) -> str:
    """Collapse the whitespace an HTML-to-text pass leaves behind,
    without joining lines that were separate."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    out, blanks = [], 0
    for line in lines:
        if line:
            out.append(line)
            blanks = 0
        else:
            blanks += 1
            if blanks == 1 and out:
                out.append("")
    return "\n".join(out).strip()


def extract(html: str, base_url: str) -> Page:
    """A Page from already-fetched markup. Separate from read_page so it
    can be tested without a network."""
    parser = _PageParser(base_url)
    try:
        parser.feed(html)
        parser.close()
    except Exception as e:
        # html.parser gives up on some real-world markup. Whatever was
        # parsed before that point is still worth returning.
        print(f"[web] {base_url} stopped parsing early: {e}")

    # A caption found after the marker was written has to be put back in.
    text = parser.text()
    for ref in parser.images:
        if ref.caption:
            text = text.replace(
                f"[image {ref.number}: {ref.alt or 'no caption'}]",
                f"[image {ref.number}: {ref.caption}]",
                1,
            )
    return Page(
        url=base_url,
        title=" ".join(parser.title.split()),
        text=text,
        links=_unique(parser.links),
        images=parser.images,
    )


def _unique(items: list) -> list:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def read_page(url: str, session=None) -> Page:
    """One page. Never raises: a failure comes back as Page.error, so a
    crawl does not stop because one page 404s."""
    try:
        body, final_url = _fetch(url, session=session)
    except WebReadError as e:
        return Page(url=url, error=str(e))
    return extract(_decode(body), final_url)


# ══════════════════════════════════════════════════════════════════
# Sitemaps
# ══════════════════════════════════════════════════════════════════

_SITEMAP_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)
_SITEMAP_INDEX_RE = re.compile(r"<sitemapindex", re.IGNORECASE)


def _origin(url: str) -> str:
    parts = urlparse(url)
    if not parts.scheme or not parts.netloc:
        raise WebReadError(f"{url!r} is not a full URL. It needs an https:// prefix.")
    return f"{parts.scheme}://{parts.netloc}"


def sitemap_candidates(url: str, session=None) -> list:
    """
    Where a site's sitemap might be: what robots.txt points at first,
    since that is the answer the site itself gives, then the conventional
    paths.
    """
    origin = _origin(url)
    found = []
    try:
        body, _ = _fetch(f"{origin}/robots.txt", session=session,
                         accept_types=("text/plain", "text/html"))
        for line in _decode(body).splitlines():
            if line.lower().startswith("sitemap:"):
                target = line.split(":", 1)[1].strip()
                if target:
                    found.append(target)
    except WebReadError:
        pass  # no robots.txt is normal

    for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml", "/sitemap1.xml"):
        candidate = origin + path
        if candidate not in found:
            found.append(candidate)
    return found


def read_sitemap(url: str, session=None, depth: int = 0, seen=None) -> list:
    """
    Every page URL a sitemap lists, following a sitemap index to the
    sitemaps it points at. Depth-capped because an index can point at
    itself, directly or in a ring.
    """
    seen = seen if seen is not None else set()
    if depth > 2 or url in seen:
        return []
    seen.add(url)

    try:
        body, _ = _fetch(url, session=session, max_bytes=MAX_SITEMAP_BYTES,
                         accept_types=("application/xml", "text/xml", "text/plain", "application/x-gzip"))
    except WebReadError:
        return []

    if url.endswith(".gz") or body[:2] == b"\x1f\x8b":
        import gzip
        try:
            body = gzip.decompress(body)
        except OSError:
            return []

    xml = _decode(body)
    locations = [unescape(m.strip()) for m in _SITEMAP_LOC_RE.findall(xml)]
    if not locations:
        return []

    if _SITEMAP_INDEX_RE.search(xml):
        pages = []
        for child in locations[:50]:
            pages.extend(read_sitemap(child, session=session, depth=depth + 1, seen=seen))
        return _unique(pages)
    return _unique(locations)


def discover_pages(url: str, session=None, limit: int = DEFAULT_MAX_PAGES) -> list:
    """
    A site's page URLs. Sitemaps first, because that is the site saying
    which pages matter; the links on the starting page as a fallback,
    because plenty of sites have no sitemap at all.
    """
    session = session or _session()
    for candidate in sitemap_candidates(url, session=session):
        pages = read_sitemap(candidate, session=session)
        if pages:
            print(f"[web] {candidate}: {len(pages)} URL(s).")
            return pages[:limit]

    print(f"[web] No sitemap for {url}; following the page's own links instead.")
    page = read_page(url, session=session)
    if page.error:
        raise WebReadError(page.error)
    host = urlparse(url).netloc
    same_site = [link for link in page.links if urlparse(link).netloc == host]
    return _unique([page.url] + same_site)[:limit]


def _allowed_by_robots(url: str, session=None) -> bool:
    """A site that asks not to be crawled is not crawled. This is a
    courtesy the tool extends on the user's behalf; a single read_url on
    a link someone pasted is not a crawl and does not consult it."""
    try:
        parser = RobotFileParser()
        parser.set_url(f"{_origin(url)}/robots.txt")
        parser.read()
        return parser.can_fetch(USER_AGENT, url)
    except Exception:
        return True   # unreadable robots.txt is not a refusal


# ══════════════════════════════════════════════════════════════════
# Crawling
# ══════════════════════════════════════════════════════════════════

def crawl_site(url: str, max_pages: int = DEFAULT_MAX_PAGES,
               respect_robots: bool = True) -> list:
    """
    read_page over the pages discover_pages finds. Returns Page objects,
    failures included: which pages could not be read is itself something
    the model should be told rather than have hidden.
    """
    session = _session()
    max_pages = max(1, min(int(max_pages or DEFAULT_MAX_PAGES), 50))
    urls = discover_pages(url, session=session, limit=max_pages)
    if not urls:
        raise WebReadError(f"No pages could be found for {url}.")

    pages = []
    for index, page_url in enumerate(urls[:max_pages]):
        if respect_robots and not _allowed_by_robots(page_url, session=session):
            pages.append(Page(url=page_url, error="Skipped: the site's robots.txt disallows it."))
            continue
        if index:
            time.sleep(CRAWL_DELAY)   # one host, one request at a time
        pages.append(read_page(page_url, session=session))
    return pages


# ══════════════════════════════════════════════════════════════════
# Images
# ══════════════════════════════════════════════════════════════════

def prepare_images(refs: list, out_dir: str, max_images: int = DEFAULT_MAX_IMAGES,
                   max_edge: int = IMAGE_MAX_EDGE, session=None) -> list:
    """
    Download and shrink the images worth looking at, writing them into
    `out_dir`. Returns the refs that made it, with local_path set.

    Downloading is what tells an icon from a diagram: the markup lies
    about size or says nothing, so anything whose real dimensions turn
    out to be icon-sized is dropped here rather than costing tokens.
    """
    try:
        from PIL import Image
    except ImportError:
        print("[web] Pillow isn't installed; skipping the page's images.")
        return []

    session = session or _session()
    os.makedirs(out_dir, exist_ok=True)
    kept = []

    for ref in refs:
        if len(kept) >= max_images:
            break
        if not _looks_like_an_image_url(ref.url):
            continue
        try:
            body, _ = _fetch(ref.url, session=session, max_bytes=MAX_IMAGE_BYTES,
                             accept_types=("image/",))
        except WebReadError as e:
            print(f"[web] image {ref.number} skipped: {e}")
            continue

        try:
            image = Image.open(io.BytesIO(body))
            image.load()
        except Exception as e:
            print(f"[web] image {ref.number} isn't readable: {e}")
            continue

        if max(image.size) < MIN_IMAGE_EDGE:
            continue   # an icon or a tracking pixel, whatever the markup said

        if max(image.size) > max_edge:
            image.thumbnail((max_edge, max_edge), Image.LANCZOS)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        path = os.path.join(out_dir, f"image_{ref.number:02d}.jpg")
        try:
            image.save(path, "JPEG", quality=82, optimize=True)
        except OSError as e:
            print(f"[web] image {ref.number} couldn't be saved: {e}")
            continue

        ref.local_path = path
        ref.width, ref.height = image.size
        kept.append(ref)

    return kept


def _looks_like_an_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if path.endswith(_IMAGE_EXTENSIONS):
        return True
    # A CDN that serves images from an extensionless path is common
    # enough that refusing those would drop real images; the
    # Content-Type check in _fetch is what actually decides.
    return "." not in os.path.basename(path)
