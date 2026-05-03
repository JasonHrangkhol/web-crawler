"""
Data model — the unified output schema of the entire crawl pipeline.

PageMetadata is the single type returned by crawl(). Every field is always
present (null when unavailable) so consumers never need to check for key
existence — only for null values.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PageMetadata:
    """
    Represents everything we know about a crawled page.

    Fields are grouped into five layers — each layer answers a different
    question:

      Layer 1 — Identity:      Which page is this? Is it unique?
      Layer 2 — Crawl context: How and when was it fetched?
      Layer 3 — Head signals:  What does the <head> declare about itself?
      Layer 4 — Structure:     How is the content laid out?
      Layer 5 — Intelligence:  What did we infer from the content?

    Design note on body_text:
      The full body text is stored internally for topic extraction but only
      a 500-character preview is returned in the API response to keep
      payloads small. At scale, full body text lives in object storage
      (S3 / GCS) referenced by (url, crawled_at).
    """

    # -------------------------------------------------------------------------
    # Layer 1 — Identity
    # -------------------------------------------------------------------------

    # The URL as submitted by the caller — the input echo.
    url: str

    # The URL after following all HTTP redirects.
    # Differs from `url` when http→https or www→non-www redirects occur.
    # Used for deduplication: two different input URLs may land on the same page.
    final_url: str

    # www-stripped domain (e.g. "amazon.com" for both www.amazon.com and amazon.com).
    # Pre-computed because every customer query is domain-scoped and computing
    # urlparse() across billions of rows at query time is expensive.
    domain: str

    # The canonical URL declared by the page via <link rel="canonical">.
    # If present, multiple URLs pointing here are the same content — prevents
    # storing duplicates.
    canonical_url: Optional[str]

    # -------------------------------------------------------------------------
    # Layer 2 — Crawl context
    # -------------------------------------------------------------------------

    # HTTP response status. 0 means the connection failed entirely.
    status_code: int

    # HTTP Content-Type header (e.g. "text/html; charset=utf-8").
    content_type: str

    # ISO-8601 UTC timestamp of when this crawl completed.
    # Required for any time-scoped query ("show competitor content from this month").
    crawled_at: str

    # MD5 fingerprint of the raw HTML.
    # Enables change detection: if the hash matches the previous crawl,
    # all downstream processing (parsing, classification, storage writes)
    # can be skipped — a major cost saving at scale.
    html_hash: Optional[str]

    crawl_duration_ms: int

    # Populated when the crawl fails; None on success.
    error: Optional[str] = None

    # -------------------------------------------------------------------------
    # Layer 3 — Head signals (declared by the page author)
    # -------------------------------------------------------------------------

    # Language code, e.g. "en", "fr".
    # Gates all NLP: running English keyword extraction on a French page
    # produces garbage. Always check before applying language-specific logic.
    language: Optional[str] = None

    title: Optional[str] = None
    meta_description: Optional[str] = None

    # Comma-separated keywords from <meta name="keywords">.
    # Mostly unused by modern sites but free to collect; e-commerce sites
    # still populate it.
    meta_keywords: list = field(default_factory=list)

    og_title: Optional[str] = None

    # OpenGraph page type: "product" | "article" | "website" | "video" | ...
    # High-value field — tells us the kind of page without any inference.
    og_type: Optional[str] = None

    og_image: Optional[str] = None

    # Publication date in ISO-8601 format.
    # Required for freshness queries: "articles about AI published this quarter."
    # Extracted from OpenGraph, schema.org, <time> elements, or URL patterns.
    published_date: Optional[str] = None

    # -------------------------------------------------------------------------
    # Layer 4 — Structural signals
    # -------------------------------------------------------------------------

    # <h1> tags — almost always the article title or product name.
    h1_tags: list = field(default_factory=list)

    # <h2> tags — reveal sub-topics; useful for content-depth analysis.
    h2_tags: list = field(default_factory=list)

    body_text: str = ""

    # Word count pre-computed at crawl time.
    # SEO content-depth metric. Pre-computing avoids len(body.split()) across
    # billions of rows at query time.
    word_count: int = 0

    # -------------------------------------------------------------------------
    # Layer 5 — Derived intelligence
    # -------------------------------------------------------------------------

    # Ranked keyword phrases describing what the page is specifically about.
    # Enables queries like "find all pages mentioning air fryers".
    topics: list = field(default_factory=list)

    # High-level page type bucket: "E-Commerce / Product", "News / Media", etc.
    # Enables type-scoped queries: "audit all my product pages".
    page_category: Optional[str] = None

    # -------------------------------------------------------------------------
    # Serialisation
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary of all output fields."""
        return {
            # Layer 1 — Identity
            "url":           self.url,
            "final_url":     self.final_url,
            "domain":        self.domain,
            "canonical_url": self.canonical_url,
            # Layer 2 — Crawl context
            "crawled_at":        self.crawled_at,
            "html_hash":         self.html_hash,
            "status_code":       self.status_code,
            "content_type":      self.content_type,
            "crawl_duration_ms": self.crawl_duration_ms,
            "error":             self.error,
            # Layer 3 — Head signals
            "language":         self.language,
            "title":            self.title,
            "meta_description": self.meta_description,
            "meta_keywords":    self.meta_keywords,
            "og_title":         self.og_title,
            "og_type":          self.og_type,
            "og_image":         self.og_image,
            "published_date":   self.published_date,
            # Layer 4 — Structure
            "h1_tags":           self.h1_tags,
            "h2_tags":           self.h2_tags,
            "word_count":        self.word_count,
            "body_text_preview": self.body_text[:500],
            # Layer 5 — Intelligence
            "topics":        self.topics,
            "page_category": self.page_category,
        }
