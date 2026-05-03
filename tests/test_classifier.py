"""
Unit tests for the classification layer (classifier.py).

These tests exercise RakeClassifier with mock ExtractedPage inputs.
No HTTP calls, no HTML parsing, no network required.

Run:
    pytest tests/test_classifier.py -v
"""

import pytest
from crawler.extractor import ExtractedPage
from crawler.classifier import RakeClassifier

classifier = RakeClassifier()


def _page(**kwargs) -> ExtractedPage:
    """
    Build a minimal ExtractedPage for testing.

    Only pass the fields relevant to each test — defaults fill the rest.
    This keeps each test focused on one variable.
    """
    defaults = dict(
        canonical_url    = None,
        language         = "en",
        title            = None,
        meta_description = None,
        meta_keywords    = [],
        og_title         = None,
        og_type          = None,
        og_image         = None,
        published_date   = None,
        h1_tags          = [],
        h2_tags          = [],
        body_text        = "",
        word_count       = 0,
    )
    return ExtractedPage(**{**defaults, **kwargs})


# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

def test_ecommerce_category():
    page = _page(
        title     = "Buy Cuisinart Toaster — Best Price",
        body_text = "Add to cart. Free shipping. Product review. Price $29.99. Kitchen appliance.",
    )
    result = classifier.classify(page)
    assert result.category == "E-Commerce / Product"


def test_technology_category():
    page = _page(
        title     = "Google Study Finds AI Will Impact Tech Jobs",
        body_text = "Software developers and machine learning engineers. AI platform. Tech startup.",
    )
    result = classifier.classify(page)
    assert result.category == "Technology"


def test_outdoors_category():
    page = _page(
        title     = "How to Introduce Your Friend to the Outdoors",
        body_text = "Hiking, camping, trail running. Backpacking adventure in nature.",
    )
    result = classifier.classify(page)
    assert result.category == "Travel / Outdoors"


def test_news_category():
    page = _page(
        title     = "CNN Breaking News Report",
        body_text = "Journalist and correspondent report on headline. News article published.",
    )
    result = classifier.classify(page)
    assert result.category == "News / Media"


def test_finance_category():
    page = _page(
        title     = "Stock Market Update",
        body_text = "Investors watch as market revenue and earnings reports. IPO portfolio banking.",
    )
    result = classifier.classify(page)
    assert result.category == "Finance"


def test_no_category_for_sparse_page():
    """A page with very little text should return None rather than a false positive."""
    page = _page(title="Welcome", body_text="Hello world.")
    result = classifier.classify(page)
    assert result.category is None


def test_no_category_for_completely_empty_page():
    page = _page()
    result = classifier.classify(page)
    assert result.category is None


# ---------------------------------------------------------------------------
# Topic extraction
# ---------------------------------------------------------------------------

def test_meta_keywords_appear_first_in_topics():
    """
    Author-declared keywords are the highest-trust signal and should
    come before RAKE-inferred topics.
    """
    page = _page(
        meta_keywords = ["toaster", "cuisinart"],
        title         = "Cuisinart Toaster Product Page",
    )
    result = classifier.classify(page)
    assert result.topics[0] == "toaster"
    assert result.topics[1] == "cuisinart"


def test_topics_deduplicated():
    """A keyword that appears in both meta_keywords and RAKE should appear once."""
    page = _page(
        meta_keywords = ["toaster"],
        title         = "Toaster Review — Best Toasters",
    )
    result = classifier.classify(page)
    lower = [t.lower() for t in result.topics]
    assert lower.count("toaster") == 1


def test_topics_capped_at_20():
    page = _page(
        meta_keywords = [f"keyword{i}" for i in range(25)],
    )
    result = classifier.classify(page)
    assert len(result.topics) <= 20


def test_empty_page_returns_empty_topics():
    page = _page()
    result = classifier.classify(page)
    assert result.topics == []


def test_topics_extracted_from_title():
    """RAKE should find keyword phrases in the title when no meta_keywords exist."""
    page = _page(title="Compact 2-Slice Toaster Kitchen Appliance")
    result = classifier.classify(page)
    assert len(result.topics) > 0


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

def test_classify_always_returns_classify_result():
    """classify() must never raise — always returns a ClassifyResult."""
    from crawler.classifier import ClassifyResult
    page   = _page()
    result = classifier.classify(page)
    assert isinstance(result, ClassifyResult)
    assert isinstance(result.topics, list)
