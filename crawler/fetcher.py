"""
HTTP fetch layer.

Responsibility: make an HTTP GET request and return a FetchResult.

Nothing about HTML parsing, topic extraction, or classification belongs here.
Swapping the HTTP client (e.g., requests → httpx for async) only touches
this file — all other layers remain unchanged.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Identifies our crawler to web servers.
# A well-formed User-Agent is both polite and reduces bot-detection blocks.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; BrightEdgeCrawler/1.0; "
        "+https://brightedge.com/bot)"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_TIMEOUT = 15  # seconds — covers slow sites without blocking the worker forever


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class FetchResult:
    """
    Everything the HTTP layer produces — before any HTML is touched.

    Consumers should check `ok` before reading `html`.
    `error` is None on success and populated with a human-readable
    message on failure — never raises an exception.
    """

    # Raw HTML string. None when the request failed or returned non-HTML.
    html: Optional[str]

    # HTTP status code. 0 means the TCP connection itself failed.
    status_code: int

    # Value of the HTTP Content-Type header.
    content_type: str

    # URL after following all redirects. May differ from the input URL
    # (http→https, www→non-www, short links expanding, etc.).
    final_url: str

    # Raw HTTP response headers — passed downstream for language detection.
    headers: dict = field(default_factory=dict)

    # Human-readable error message. None on success.
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """True when the request succeeded and html is available."""
        return self.error is None and self.html is not None

    @property
    def is_html(self) -> bool:
        """True when the server returned an HTML document."""
        return "text/html" in self.content_type


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def fetch(url: str) -> FetchResult:
    """
    GET the given URL and return a FetchResult.

    Behaviour:
    - Follows redirects automatically (allow_redirects=True).
    - On SSL certificate errors: retries once without verification so
      sites with misconfigured certificates still work.
    - All exceptions are caught and surfaced in FetchResult.error —
      callers never need a try/except around this function.
    """
    try:
        resp = _get(url, verify=True)
        return _to_result(url, resp)

    except requests.exceptions.SSLError:
        logger.warning("SSL error for %s — retrying without certificate verification", url)
        try:
            resp = _get(url, verify=False)
            return _to_result(url, resp)
        except Exception as e:
            return _failure(url, f"SSL error: {e}")

    except requests.exceptions.ConnectionError as e:
        return _failure(url, f"Connection error: {e}")

    except requests.exceptions.Timeout:
        return _failure(url, "Request timed out")

    except Exception as e:
        return _failure(url, str(e))


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _get(url: str, verify: bool) -> requests.Response:
    return requests.get(
        url,
        headers=_HEADERS,
        timeout=_TIMEOUT,
        allow_redirects=True,
        verify=verify,
    )


def _to_result(original_url: str, resp: requests.Response) -> FetchResult:
    """Convert a successful requests.Response into a FetchResult."""
    content_type = resp.headers.get("Content-Type", "")
    is_html      = "text/html" in content_type

    return FetchResult(
        html         = resp.text if is_html else None,
        status_code  = resp.status_code,
        content_type = content_type,
        final_url    = resp.url,
        headers      = dict(resp.headers),
        error        = None if is_html else f"Non-HTML content: {content_type}",
    )


def _failure(url: str, error: str) -> FetchResult:
    """Build a FetchResult representing a failed request."""
    return FetchResult(
        html         = None,
        status_code  = 0,
        content_type = "",
        final_url    = url,
        error        = error,
    )
