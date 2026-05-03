# BrightEdge Web Crawler — Engineering Assignment

**Part 1 of 3:** Working crawler + REST API  
**Parts 2 & 3:** See [`docs/`](./docs/)

---

## Live Demo

| | |
|---|---|
| **API endpoint** | `https://<your-service>.run.app/crawl?url=<url>` |
| **Interactive docs** | `https://<your-service>.run.app/docs` |

> Replace `<your-service>` with the deployed service URL after deployment.

---

## What This Does

Given any URL, this service runs a three-stage pipeline and returns structured JSON:

```
URL  →  [Fetch]  →  [Extract]  →  [Classify]  →  JSON
```

| Stage | What it does |
|---|---|
| **Fetch** | HTTP GET, follows redirects, handles SSL errors and timeouts |
| **Extract** | Parses HTML — title, description, OpenGraph tags, headings, body text |
| **Classify** | RAKE keyword extraction for topics; rule-based category detection |

---

## Project Structure

```
Assignment/
├── main.py                    FastAPI entrypoint — exposes /crawl and /health
├── requirements.txt           Runtime dependencies (pinned versions)
├── requirements-dev.txt       Dev/test dependencies
├── Dockerfile                 Container definition for cloud deployment
├── README.md
│
├── crawler/                   Core crawler package
│   ├── __init__.py            Public API: exports crawl() and PageMetadata
│   ├── models.py              Output schema — PageMetadata dataclass
│   ├── fetcher.py             HTTP layer — FetchResult + fetch()
│   ├── extractor.py           HTML parsing — ExtractedPage + HtmlExtractor
│   ├── classifier.py          Strategy interface + RakeClassifier
│   └── pipeline.py            Orchestrates the three stages → CrawlPipeline
│
├── tests/
│   ├── test_extractor.py      Unit tests for HTML extraction (no HTTP needed)
│   └── test_classifier.py     Unit tests for topic/category classification
│
├── scripts/
│   └── run_demo.py            Crawls all 5 test URLs, saves JSON to sample_outputs/
│
├── sample_outputs/            JSON output from crawling each test URL
│   ├── amazon_toaster.json
│   ├── rei_blog.json
│   ├── cnn_article.json
│   ├── walmart_home.json
│   └── bestbuy_home.json
│
└── docs/
    ├── part2_design.md        System design for crawling billions of URLs
    └── part3_execution.md     POC plan, blockers, estimates, release plan
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Download NLTK language data (one-time setup — needed by RAKE)
python3 -c "import nltk; nltk.download('stopwords'); nltk.download('punkt'); nltk.download('punkt_tab')"

# 3. Start the server locally
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000/docs** for the interactive Swagger UI.

---

## Test the 5 Assignment URLs

```bash
python scripts/run_demo.py
```

Crawls all 5 test URLs and saves the JSON outputs to `sample_outputs/`.

---

## Run Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

---

## API Reference

### `GET /crawl?url=<url>`

Crawl any URL and return structured metadata.

**Example:**
```
GET /crawl?url=https://www.amazon.com/Cuisinart-CPT-122-Compact-2-Slice-Toaster/dp/B009GQ034C
```

**Response schema:**

| Field | Type | Description |
|---|---|---|
| `url` | string | Original URL submitted |
| `final_url` | string | URL after all redirects |
| `domain` | string | www-stripped domain (e.g. `amazon.com`) |
| `canonical_url` | string\|null | `<link rel="canonical">` — used for deduplication |
| `crawled_at` | string | ISO-8601 UTC timestamp |
| `html_hash` | string\|null | MD5 of raw HTML — enables change detection |
| `status_code` | int | HTTP status (0 = connection failure) |
| `error` | string\|null | Error message on failure; null on success |
| `language` | string\|null | Detected page language — gates NLP pipelines |
| `title` | string\|null | `<title>` tag content |
| `meta_description` | string\|null | `<meta name="description">` |
| `meta_keywords` | string[] | `<meta name="keywords">` |
| `og_title` | string\|null | OpenGraph title |
| `og_type` | string\|null | OpenGraph type: `product` / `article` / `website` |
| `og_image` | string\|null | OpenGraph image URL |
| `published_date` | string\|null | Publication date in ISO-8601 |
| `h1_tags` | string[] | All `<h1>` headings |
| `h2_tags` | string[] | All `<h2>` headings |
| `word_count` | int | Body word count (pre-computed SEO metric) |
| `body_text_preview` | string | First 500 chars of cleaned body text |
| `topics` | string[] | RAKE-extracted keyword phrases |
| `page_category` | string\|null | `E-Commerce / Product` / `News / Media` / `Technology` / etc. |

### `GET /health`

Returns `{"status": "ok"}`. Used by Cloud Run health checks.

---

## Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Language | Python | Best ecosystem for HTML parsing (BeautifulSoup) and NLP (RAKE-NLTK) |
| Web framework | FastAPI | Auto-generated Swagger UI, request validation, production-grade |
| HTML parser | BeautifulSoup4 | Handles malformed real-world HTML gracefully |
| Topic extraction | RAKE-NLTK | Unsupervised, single-document, no training data, local, deterministic |
| Category detection | Rule-based keyword signals | Interpretable, no ML infrastructure for POC; clear upgrade path |
| Deployment | Cloud Run | Serverless containers, zero idle cost, auto-HTTPS, mirrors production |

---

## Architecture

```
CrawlPipeline (pipeline.py)
  │
  ├── fetch(url)              → FetchResult      (fetcher.py)
  ├── extractor.extract()     → ExtractedPage    (extractor.py)
  └── classifier.classify()   → ClassifyResult   (classifier.py)
                                       ↓
                              PageMetadata       (models.py)
```

**Strategy pattern** — `BaseClassifier` in `classifier.py` is an abstract interface.
Upgrade from rule-based to ML without touching any other file:

```python
CrawlPipeline(classifier=MLClassifier()).run(url)
```

---

## Deployment

### Local with Docker

```bash
docker build -t brightedge-crawler .
docker run -p 8080:8080 brightedge-crawler
curl "http://localhost:8080/crawl?url=https://example.com"
```

### Google Cloud Run

```bash
gcloud config set project YOUR_PROJECT_ID
docker build -t gcr.io/YOUR_PROJECT_ID/brightedge-crawler .
docker push gcr.io/YOUR_PROJECT_ID/brightedge-crawler
gcloud run deploy brightedge-crawler \
  --image gcr.io/YOUR_PROJECT_ID/brightedge-crawler \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080
```

---

## AI Tools Used

| Tool | How it was used |
|---|---|
| **Cursor (Claude Sonnet)** | Scaffolded project structure, design pattern guidance, documentation drafting |

All AI-generated code was reviewed, understood, and tested before submission.
