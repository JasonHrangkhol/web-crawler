"""
HTML extraction layer.

Responsibility: given raw HTML, return a structured ExtractedPage.

All BeautifulSoup logic lives here. Nothing about HTTP requests, topic
scoring, or category classification belongs in this file.

Extensibility: to add a new signal (e.g., JSON-LD structured data, schema.org
breadcrumbs), add a private _method and call it from extract(). No other
file needs to change.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Tags that contain navigation, ads, scripts, and other non-content noise.
# Stripped before body text extraction so topics aren't polluted with
# "add to cart" menu labels or cookie-banner text.
_NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "form"]

# Body text is truncated to keep memory usage bounded.
# 50k characters covers ~8,000–10,000 words — sufficient for classification.
_MAX_BODY_CHARS = 50_000


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ExtractedPage:
    """
    All structured signals pulled from a single HTML document.

    These are facts extracted directly from the markup — no inference
    or scoring applied yet. Classification happens downstream.
    """

    canonical_url:    Optional[str]       = None
    language:         Optional[str]       = None
    title:            Optional[str]       = None
    meta_description: Optional[str]       = None
    meta_keywords:    list                = field(default_factory=list)
    og_title:         Optional[str]       = None
    og_type:          Optional[str]       = None
    og_image:         Optional[str]       = None
    published_date:   Optional[str]       = None
    h1_tags:          list                = field(default_factory=list)
    h2_tags:          list                = field(default_factory=list)
    body_text:        str                 = ""
    word_count:       int                 = 0


# ---------------------------------------------------------------------------
# Extractor class
# ---------------------------------------------------------------------------

class HtmlExtractor:
    """
    Extracts structured metadata from raw HTML using BeautifulSoup.

    Each private method extracts exactly one signal, making it easy to:
    - Read: find the code for any specific field in one place
    - Test: unit-test individual extraction methods in isolation
    - Extend: add new signals without touching existing logic
    """

    def extract(self, html: str, url: str, resp_headers: dict) -> ExtractedPage:
        """
        Parse the HTML and return an ExtractedPage.

        All extraction is done in a single BeautifulSoup parse to avoid
        repeatedly tokenising the same document.
        """
        soup = BeautifulSoup(html, "html.parser")
        self._strip_noise(soup)

        body      = soup.find("body")
        body_text = self._text(body)[:_MAX_BODY_CHARS] if body else ""

        return ExtractedPage(
            canonical_url    = self._canonical(soup, url),
            language         = self._language(soup, resp_headers),
            title            = self._title(soup),
            meta_description = self._description(soup),
            meta_keywords    = self._keywords(soup),
            og_title         = self._meta(soup, prop="og:title"),
            og_type          = self._meta(soup, prop="og:type"),
            og_image         = self._meta(soup, prop="og:image"),
            published_date   = self._published_date(soup, url),
            h1_tags          = [self._text(t) for t in soup.find_all("h1")][:10],
            h2_tags          = [self._text(t) for t in soup.find_all("h2")][:15],
            body_text        = body_text,
            word_count       = len(body_text.split()) if body_text else 0,
        )

    # ------------------------------------------------------------------
    # Private extraction methods — one method per signal
    # ------------------------------------------------------------------

    def _strip_noise(self, soup: BeautifulSoup) -> None:
        """Remove non-content elements before body text extraction."""
        for tag in soup(_NOISE_TAGS):
            tag.decompose()

    def _text(self, tag) -> str:
        """Extract all visible text from a tag, collapsed to a single string."""
        return tag.get_text(separator=" ", strip=True) if tag else ""

    def _meta(self, soup: BeautifulSoup, name: str = "", prop: str = "") -> Optional[str]:
        """
        Find a <meta> tag and return its content attribute.

        Tries three lookups in order:
        1. <meta name="...">  — standard HTML meta tags
        2. <meta property="..."> — OpenGraph / RDFa tags
        3. Case-insensitive name match — catches inconsistent capitalisation
        """
        tag = None
        if name:
            tag = soup.find("meta", attrs={"name": name})
        if not tag and prop:
            tag = soup.find("meta", property=prop)
        if not tag and name:
            tag = soup.find("meta", attrs={"name": re.compile(name, re.I)})
        return tag.get("content", "").strip() if tag else None

    def _canonical(self, soup: BeautifulSoup, url: str) -> Optional[str]:
        tag  = soup.find("link", rel="canonical")
        href = tag.get("href") if tag else None
        if href and not href.startswith("http"):
            href = urljoin(url, href)
        return href

    def _title(self, soup: BeautifulSoup) -> Optional[str]:
        tag = soup.find("title")
        return self._text(tag) or None

    def _description(self, soup: BeautifulSoup) -> Optional[str]:
        # Try standard meta first; fall back to OpenGraph description
        return (
            self._meta(soup, name="description")
            or self._meta(soup, prop="og:description")
        )

    def _keywords(self, soup: BeautifulSoup) -> list:
        raw = self._meta(soup, name="keywords") or ""
        return [k.strip() for k in raw.split(",") if k.strip()]

    def _language(self, soup: BeautifulSoup, resp_headers: dict) -> Optional[str]:
        """
        Detect the page language. Checked in order of reliability:

        1. <html lang="en-US"> — explicitly set by the page author
        2. HTTP Content-Language header — set by the web server
        3. <meta name="language"> — older but still used convention

        Language is a gating field: running English keyword extraction on a
        French or Japanese page produces meaningless topics. Downstream
        NLP should check this field before processing.
        """
        html_tag = soup.find("html")
        if html_tag and html_tag.get("lang"):
            return html_tag.get("lang").split("-")[0].lower()

        content_lang = resp_headers.get("Content-Language", "")
        if content_lang:
            return content_lang.split("-")[0].lower()

        meta_lang = self._meta(soup, name="language")
        if meta_lang:
            return meta_lang.split("-")[0].lower()

        return None

    def _published_date(self, soup: BeautifulSoup, url: str) -> Optional[str]:
        """
        Extract when the page's content was written.

        Checked in order of reliability:
        1. <meta property="article:published_time"> — OpenGraph article standard, ISO-8601
        2. <meta prop="datePublished"> / <meta name="publishdate"> — schema.org conventions
        3. <time datetime="..."> — semantic HTML element
        4. URL date pattern — e.g. /2025/09/23/ common on news sites

        Required for freshness queries: "find competitor articles published
        about keyword X this quarter."
        """
        # 1. OpenGraph
        date = self._meta(soup, prop="article:published_time")
        if date:
            return date

        # 2. Schema.org / misc meta conventions
        for name in ("publishdate", "date", "pubdate", "article_date_original"):
            date = self._meta(soup, name=name)
            if date:
                return date
        date = self._meta(soup, prop="datePublished")
        if date:
            return date

        # 3. Semantic <time> element
        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag:
            return time_tag.get("datetime")

        # 4. URL date pattern (covers most news sites)
        match = re.search(r"/(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})(?:[/\-]|$)", url)
        if match:
            y, m, d = match.groups()
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"

        return None
