"""
Unit tests for the HTML extraction layer (extractor.py).

These tests use inline HTML strings — no HTTP calls, no network required.
Each test validates exactly one extraction behaviour so failures point
directly at the broken method.

Run:
    pytest tests/test_extractor.py -v
"""

import pytest
from crawler.extractor import HtmlExtractor

extractor     = HtmlExtractor()
EMPTY_HEADERS = {}


# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------

def test_title_extracted():
    html = "<html><head><title>My Page Title</title></head><body></body></html>"
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.title == "My Page Title"


def test_title_returns_none_when_missing():
    html = "<html><head></head><body></body></html>"
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.title is None


# ---------------------------------------------------------------------------
# Meta description
# ---------------------------------------------------------------------------

def test_meta_description_from_name_tag():
    html = '<html><head><meta name="description" content="A great page."></head><body></body></html>'
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.meta_description == "A great page."


def test_meta_description_falls_back_to_og():
    html = '<html><head><meta property="og:description" content="OG description."></head><body></body></html>'
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.meta_description == "OG description."


# ---------------------------------------------------------------------------
# OpenGraph tags
# ---------------------------------------------------------------------------

def test_og_type_product():
    html = '<html><head><meta property="og:type" content="product"></head><body></body></html>'
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.og_type == "product"


def test_og_type_article():
    html = '<html><head><meta property="og:type" content="article"></head><body></body></html>'
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.og_type == "article"


def test_og_image_extracted():
    html = '<html><head><meta property="og:image" content="https://example.com/img.jpg"></head><body></body></html>'
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.og_image == "https://example.com/img.jpg"


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def test_language_from_html_lang_attribute():
    html = '<html lang="en-US"><head></head><body></body></html>'
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.language == "en"


def test_language_strips_region_code():
    html = '<html lang="pt-BR"><head></head><body></body></html>'
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.language == "pt"


def test_language_falls_back_to_content_language_header():
    html    = "<html><head></head><body></body></html>"
    headers = {"Content-Language": "fr-FR"}
    page    = extractor.extract(html, "https://example.com", headers)
    assert page.language == "fr"


def test_language_returns_none_when_not_detectable():
    html = "<html><head></head><body></body></html>"
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.language is None


# ---------------------------------------------------------------------------
# Published date
# ---------------------------------------------------------------------------

def test_published_date_from_opengraph():
    html = '<html><head><meta property="article:published_time" content="2025-09-23T10:00:00Z"></head><body></body></html>'
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.published_date == "2025-09-23T10:00:00Z"


def test_published_date_from_time_element():
    html = '<html><body><time datetime="2025-09-23">September 23</time></body></html>'
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.published_date == "2025-09-23"


def test_published_date_from_url_pattern():
    html = "<html><head></head><body></body></html>"
    page = extractor.extract(html, "https://cnn.com/2025/09/23/tech/ai-article", EMPTY_HEADERS)
    assert page.published_date == "2025-09-23"


def test_published_date_returns_none_when_missing():
    html = "<html><head></head><body></body></html>"
    page = extractor.extract(html, "https://example.com/no-date", EMPTY_HEADERS)
    assert page.published_date is None


# ---------------------------------------------------------------------------
# Body text and word count
# ---------------------------------------------------------------------------

def test_noise_tags_stripped_from_body_text():
    html = """
    <html><body>
      <nav>Menu item</nav>
      <p>Real content here.</p>
      <footer>Footer text</footer>
      <script>alert('hi')</script>
    </body></html>
    """
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert "Menu item"   not in page.body_text
    assert "Footer text" not in page.body_text
    assert "alert"       not in page.body_text
    assert "Real content here" in page.body_text


def test_word_count_matches_body_words():
    html = "<html><body><p>one two three four five</p></body></html>"
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.word_count == 5


def test_word_count_zero_on_empty_body():
    html = "<html><body></body></html>"
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.word_count == 0


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------

def test_h1_extracted():
    html = "<html><body><h1>Main Title</h1></body></html>"
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.h1_tags == ["Main Title"]


def test_h2_extracted():
    html = "<html><body><h2>Section One</h2><h2>Section Two</h2></body></html>"
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.h2_tags == ["Section One", "Section Two"]


def test_empty_page_returns_empty_lists():
    html = "<html><head></head><body></body></html>"
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.h1_tags       == []
    assert page.h2_tags       == []
    assert page.meta_keywords == []


# ---------------------------------------------------------------------------
# Canonical URL
# ---------------------------------------------------------------------------

def test_canonical_extracted():
    html = '<html><head><link rel="canonical" href="https://example.com/canonical"></head><body></body></html>'
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.canonical_url == "https://example.com/canonical"


def test_relative_canonical_resolved_to_absolute():
    html = '<html><head><link rel="canonical" href="/canonical-path"></head><body></body></html>'
    page = extractor.extract(html, "https://example.com", EMPTY_HEADERS)
    assert page.canonical_url == "https://example.com/canonical-path"
