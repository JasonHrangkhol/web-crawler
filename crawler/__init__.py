"""
BrightEdge Web Crawler package.

Public API — everything a caller needs is exported here:

    from crawler import crawl, PageMetadata

    result: PageMetadata = crawl("https://example.com")
    print(result.to_dict())

For custom pipelines (e.g. injecting an ML classifier):

    from crawler.pipeline import CrawlPipeline
    from crawler.classifier import MLClassifier   # future

    result = CrawlPipeline(classifier=MLClassifier()).run(url)

Internal modules:
    models.py     — PageMetadata dataclass (the output schema)
    fetcher.py    — HTTP fetch layer
    extractor.py  — HTML parsing layer
    classifier.py — Strategy interface + RakeClassifier
    pipeline.py   — Orchestrates all three stages
"""

from .pipeline import crawl
from .models import PageMetadata

__all__ = ["crawl", "PageMetadata"]
