"""
Crawl pipeline — orchestrates the three stages.

  Stage 1: Fetch    — HTTP request              → FetchResult
  Stage 2: Extract  — HTML parsing              → ExtractedPage
  Stage 3: Classify — topic/category inference  → ClassifyResult

  Output: PageMetadata (assembled from all three stage outputs)

Design — Dependency Injection:
  Each stage is injected via the constructor. This makes it easy to:
    - Swap implementations without modifying this file
      (e.g. upgrade classifier: CrawlPipeline(classifier=MLClassifier()))
    - Test each stage in isolation by injecting mocks
    - Add new stages (e.g. a link extractor) without breaking existing ones

Usage:
    # Default pipeline
    result = CrawlPipeline().run("https://example.com")

    # Custom classifier (e.g. ML model in production)
    result = CrawlPipeline(classifier=MLClassifier()).run("https://example.com")
"""

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from .classifier import BaseClassifier, ClassifyResult, RakeClassifier
from .extractor import ExtractedPage, HtmlExtractor
from .fetcher import FetchResult, fetch
from .models import PageMetadata

logger = logging.getLogger(__name__)


class CrawlPipeline:
    """
    Three-stage crawl pipeline: fetch → extract → classify → PageMetadata.

    Each stage is a separate, swappable component following the Strategy
    and Dependency Injection patterns. The pipeline itself has no knowledge
    of HTTP, HTML, or NLP — it only coordinates the flow.
    """

    def __init__(
        self,
        extractor:  HtmlExtractor  = None,
        classifier: BaseClassifier = None,
    ):
        self.extractor  = extractor  or HtmlExtractor()
        self.classifier = classifier or RakeClassifier()

    def run(self, url: str) -> PageMetadata:
        """Run the full pipeline for a single URL."""
        crawled_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        start      = time.monotonic()

        # Stage 1 — Fetch
        fetch_result = fetch(url)
        if not fetch_result.ok or not fetch_result.is_html:
            return self._failed_result(url, fetch_result, crawled_at, start)

        # Stage 2 — Extract
        page = self.extractor.extract(
            fetch_result.html, url, fetch_result.headers
        )

        # Stage 3 — Classify
        classification = self.classifier.classify(page)

        return self._success_result(url, fetch_result, page, classification, crawled_at, start)

    # ------------------------------------------------------------------
    # Result builders — assemble PageMetadata from stage outputs
    # ------------------------------------------------------------------

    def _success_result(
        self,
        url:            str,
        fetch_result:   FetchResult,
        page:           ExtractedPage,
        classification: ClassifyResult,
        crawled_at:     str,
        start:          float,
    ) -> PageMetadata:
        return PageMetadata(
            # Layer 1 — Identity
            url           = url,
            final_url     = fetch_result.final_url,
            domain        = _extract_domain(fetch_result.final_url),
            canonical_url = page.canonical_url,
            # Layer 2 — Crawl context
            status_code       = fetch_result.status_code,
            content_type      = fetch_result.content_type,
            crawled_at        = crawled_at,
            html_hash         = _md5(fetch_result.html),
            crawl_duration_ms = _elapsed_ms(start),
            # Layer 3 — Head signals
            language         = page.language,
            title            = page.title,
            meta_description = page.meta_description,
            meta_keywords    = page.meta_keywords,
            og_title         = page.og_title,
            og_type          = page.og_type,
            og_image         = page.og_image,
            published_date   = page.published_date,
            # Layer 4 — Structure
            h1_tags    = page.h1_tags,
            h2_tags    = page.h2_tags,
            body_text  = page.body_text,
            word_count = page.word_count,
            # Layer 5 — Intelligence
            topics        = classification.topics,
            page_category = classification.category,
        )

    def _failed_result(
        self,
        url:          str,
        fetch_result: FetchResult,
        crawled_at:   str,
        start:        float,
    ) -> PageMetadata:
        return PageMetadata(
            url               = url,
            final_url         = fetch_result.final_url,
            domain            = _extract_domain(url),
            canonical_url     = None,
            status_code       = fetch_result.status_code,
            content_type      = fetch_result.content_type,
            crawled_at        = crawled_at,
            html_hash         = None,
            crawl_duration_ms = _elapsed_ms(start),
            error             = fetch_result.error,
        )


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

def crawl(url: str) -> PageMetadata:
    """
    Crawl a URL using the default pipeline.

    This is the main public function of the crawler package.
    For custom configurations (e.g. ML classifier), instantiate
    CrawlPipeline directly.
    """
    return CrawlPipeline().run(url)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _extract_domain(url: str) -> str:
    """
    Return the www-stripped domain from a URL.

    Groups www.amazon.com and amazon.com under the same key ("amazon.com").
    Every BrightEdge customer query is domain-scoped, so pre-computing this
    avoids repeated urlparse calls at query time across billions of rows.
    """
    netloc = urlparse(url).netloc.lower()
    return netloc.removeprefix("www.")


def _md5(text: Optional[str]) -> Optional[str]:
    """MD5 hash of the HTML. Used for change detection between crawls."""
    if not text:
        return None
    return hashlib.md5(text.encode("utf-8", errors="replace")).hexdigest()


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
