"""
Classification layer.

Responsibility: given an ExtractedPage, determine topics and category.

Design — Strategy Pattern:
  BaseClassifier is an abstract interface. The pipeline accepts any
  implementation. Swap RakeClassifier for MLClassifier without touching
  a single line of pipeline, extractor, or fetcher code.

  Current:  RakeClassifier — rule-based, no ML, no API calls, fast
  Upgrade:  MLClassifier   — zero-shot transformer (e.g. BART-large-mnli)
                             for higher accuracy when labeled data is available
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from rake_nltk import Rake

from .extractor import ExtractedPage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ClassifyResult:
    """Output of the classification stage."""
    topics:   list          = field(default_factory=list)
    category: Optional[str] = None


# ---------------------------------------------------------------------------
# Strategy interface
# ---------------------------------------------------------------------------

class BaseClassifier(ABC):
    """
    Abstract base class for page classifiers.

    Implement this interface to plug in a different classification strategy
    without changing the pipeline or any other component.

    Example — future ML upgrade:

        class MLClassifier(BaseClassifier):
            def classify(self, page: ExtractedPage) -> ClassifyResult:
                # Use a zero-shot classifier (BART-large-mnli) or a
                # fine-tuned model for higher topic accuracy
                labels = zero_shot(page.title + page.body_text[:500], CATEGORIES)
                return ClassifyResult(
                    topics=extract_entities(page.body_text),
                    category=labels[0],
                )

        # Inject without touching pipeline.py:
        result = CrawlPipeline(classifier=MLClassifier()).run(url)
    """

    @abstractmethod
    def classify(self, page: ExtractedPage) -> ClassifyResult:
        """Classify a page and return its topics and category."""


# ---------------------------------------------------------------------------
# Default implementation: rule-based RAKE classifier
# ---------------------------------------------------------------------------

class RakeClassifier(BaseClassifier):
    """
    Rule-based classifier. Two sub-tasks:

    Topics
    ------
    Uses RAKE (Rapid Automatic Keyword Extraction) on the title, headings,
    and meta description. RAKE was chosen for the POC because:
      - Unsupervised — no training data or labeled corpus required
      - Single-document — works on one page at a time (no TF-IDF corpus needed)
      - Local — no external API calls, no added latency
      - Fast — typically < 50ms per page
      - Deterministic — same input always produces same output (debuggable)

    Category
    --------
    Keyword signal matching: counts category-specific words in the full page
    text; the category with the most matches wins. Requires a minimum of
    2 matches to avoid false positives on sparse pages (homepages, error
    pages, etc.).

    Known limitations (addressed in Part 2 upgrade path):
      - RAKE does not understand semantic similarity ("sofa" ≠ "couch")
      - Category signals are hand-written — new categories require a code change
      - No confidence scores are produced

    Upgrade path: replace with a zero-shot transformer classifier
    (BART-large-mnli) which needs no hand-written signals and produces
    per-category confidence scores.
    """

    CATEGORY_SIGNALS: dict = {
        "E-Commerce / Product": [
            "buy", "add to cart", "price", "product", "shop", "order",
            "checkout", "shipping", "review", "rating", "toaster",
            "appliance", "kitchen", "amazon", "walmart", "bestbuy",
            "sale", "discount",
        ],
        "News / Media": [
            "breaking", "report", "journalist", "news", "correspondent",
            "editor", "cnn", "bbc", "reuters", "ap news", "article",
            "headline",
        ],
        "Travel / Outdoors": [
            "trail", "hike", "camping", "outdoors", "adventure", "nature",
            "rei", "backpacking", "climbing", "travel", "destination",
        ],
        "Technology": [
            "software", "hardware", "ai", "machine learning", "startup",
            "cloud", "developer", "programming", "tech", "app", "platform",
        ],
        "Health / Wellness": [
            "health", "fitness", "diet", "nutrition", "medical", "wellness",
            "exercise", "doctor", "treatment",
        ],
        "Finance": [
            "stock", "invest", "finance", "market", "revenue", "earnings",
            "ipo", "fund", "portfolio", "banking",
        ],
    }

    def classify(self, page: ExtractedPage) -> ClassifyResult:
        full_text = self._build_full_text(page)
        return ClassifyResult(
            topics   = self._extract_topics(page, full_text),
            category = self._infer_category(full_text.lower()),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_full_text(self, page: ExtractedPage) -> str:
        """Combine all text signals into a single string for category scoring."""
        return " ".join(filter(None, [
            page.title,
            page.meta_description,
            " ".join(page.meta_keywords),
            " ".join(page.h1_tags),
            " ".join(page.h2_tags),
            page.body_text[:5000],
        ]))

    def _extract_topics(self, page: ExtractedPage, full_text: str) -> list:
        """
        Build a ranked topic list from highest-trust to lowest-trust sources.

        Priority order:
          1. meta_keywords — author-declared, highest trust
          2. RAKE on title + headings + description — structured, high signal
        """
        topics = list(page.meta_keywords)

        seed = " ".join(filter(None, [
            page.title,
            page.meta_description,
            " ".join(page.h1_tags),
            " ".join(page.h2_tags),
        ]))

        for phrase in self._rake_keywords(seed or full_text):
            if phrase.lower() not in {t.lower() for t in topics}:
                topics.append(phrase)

        return self._deduplicate(topics, limit=20)

    def _rake_keywords(self, text: str, max_phrases: int = 15) -> list:
        if not text.strip():
            return []
        try:
            r = Rake(min_length=1, max_length=4, include_repeated_phrases=False)
            r.extract_keywords_from_text(text[:10_000])
            return [p for p in r.get_ranked_phrases()[:max_phrases] if len(p) > 3]
        except Exception:
            return []

    def _infer_category(self, text_lower: str) -> Optional[str]:
        scores = {
            cat: sum(1 for signal in signals if signal in text_lower)
            for cat, signals in self.CATEGORY_SIGNALS.items()
        }
        best = max(scores, key=scores.get)
        # Minimum 2 signal matches prevents false positives on thin pages
        return best if scores[best] >= 2 else None

    @staticmethod
    def _deduplicate(items: list, limit: int) -> list:
        seen:   set  = set()
        result: list = []
        for item in items:
            key = item.lower()
            if key not in seen:
                seen.add(key)
                result.append(item)
            if len(result) >= limit:
                break
        return result
