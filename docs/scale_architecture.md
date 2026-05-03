# System Design: Operationalizing the Crawler at Billions of URLs

## Executive Summary

Part 1 built a single-URL crawler — give it one URL, it returns structured metadata
and topic classifications. This document answers: **how do you run that same crawler
across a billion URLs per month, reliably, affordably, and at production quality?**

The answer is not "run Part 1 in a loop." At scale, the hard problems shift from
code correctness to operational concerns: throughput, fault tolerance, deduplication,
cost management, and freshness. Every design decision below is driven by one of those
five concerns — and each section explains not just what was chosen, but why the
alternatives were rejected.

---

## 1. Input — Accepting Billions of URLs

The system accepts two input formats as specified.

### 1a. MySQL Table

```sql
-- Schema: urls for a given year/month batch
CREATE TABLE urls_july_2026 (
  id         BIGINT PRIMARY KEY AUTO_INCREMENT,
  url        TEXT NOT NULL,
  domain     VARCHAR(255),
  priority   TINYINT DEFAULT 2,       -- 1=high, 2=normal, 3=low
  added_at   DATETIME DEFAULT NOW()
);
```

### 1b. Text File (S3)

```
s3://brightedge-inputs/year=2026/month=07/urls_july_2026.txt
```

One URL per line. The Scheduler reads using streaming (never loads the full file into
memory) and publishes to the Crawl Queue in batches of 10,000.

### Why Workers Cannot Read MySQL Directly

The intuitive approach — workers query MySQL directly for their next URL — fails under
load. With 60–80 workers all issuing `SELECT ... LIMIT 1000 OFFSET ...` concurrently,
MySQL saturates. More critically: if a worker crashes mid-batch, there is no record of
which URLs it completed. You either re-crawl everything it touched (duplicates) or skip
them (data loss). There is no safe middle ground.

The solution is a dedicated **Scheduler service** that reads MySQL once, sequentially,
and publishes to Kafka. Workers then consume from Kafka, which tracks exactly which
messages each worker has acknowledged. A crashed worker causes its unacknowledged
messages to be redelivered — no duplicates, no data loss, no MySQL contention.

### Why Both Inputs Funnel Through the Same Scheduler

Regardless of source, every URL passes through the same deduplication and priority
logic before being queued. The crawl pipeline has no knowledge of whether a URL came
from MySQL or a file — it processes all URLs identically, eliminating two separate
code paths that could diverge over time.

---

## 2. Scale Requirements — The Numbers That Drive Design

Before picking any technology, compute the numbers. These determine worker count,
queue throughput, storage budget, and cost estimates.

### Throughput

| Timeframe | URLs Required |
|---|---|
| Per month | 1,000,000,000 |
| Per day | 33,300,000 |
| Per hour | 1,390,000 |
| Per minute | 23,148 |
| **Per second** | **386** |

This is the sustained rate required to crawl 1 billion URLs in 30 days. The system
is designed with 2× headroom — scaling to 5 billion URLs requires only adding more
workers, not changing the architecture.

### Storage

| Data type | Calculation | Total |
|---|---|---|
| Raw HTML (uncompressed) | 1B URLs × 500 KB avg | 500 TB / month |
| Raw HTML (after gzip, 5:1 ratio) | 500 TB ÷ 5 | ~100 TB stored |
| Structured metadata | 1B URLs × 5 KB avg | 5 TB / month |
| Search index | ~20% of metadata | 1 TB / month |
| URL Frontier state | 1B entries × 100 bytes | 100 GB (in RAM) |

### Compute — Why Async I/O Changes the Math

A naive synchronous worker fetches one URL at a time. At 2 seconds per URL, that is
0.5 URLs/sec per worker — requiring 772 workers to hit 386 URLs/sec.

The key insight: **90% of those 2 seconds is the worker waiting for a network
response**. The CPU is idle. Async I/O (`aiohttp`) lets a single worker open 50
simultaneous connections, all waiting on the network in parallel. While one response
is in-flight, the worker starts 49 others.

```
Synchronous:  1 worker × 1 URL at a time  × (1 ÷ 2s)  =  0.5 URLs/sec/worker
Async:        1 worker × 50 concurrent    × (1 ÷ 2s)  = 25  URLs/sec/worker

To sustain 386 URLs/sec:
  386 ÷ 25 = ~16 workers (theoretical minimum)
  With politeness delays, retries, and burst headroom: 60–80 workers
```

This is also why the crawler stays in Python. Async Python with `aiohttp` achieves
the same I/O concurrency as Go for HTTP-bound work — and avoids rewriting the existing
BeautifulSoup and RAKE-NLTK libraries from scratch.

### Monthly Cost Estimate

| Category | Calculation | Cost |
|---|---|---|
| Raw HTML storage (S3 IA, after compression) | 100 TB × $0.0125/GB | $1,250 |
| Metadata storage (BigQuery) | 5 TB × $0.02/GB | $100 |
| Search index (OpenSearch) | 1 TB | $500 |
| Compute (Fargate Spot) | 80 workers × 720 hrs × $0.048/hr | $2,765 |
| Network (outbound API) | ~1 TB × $0.09/GB | $90 |
| **Total** | | **~$4,705/month** |
| **Cost per URL** | | **$0.0000047** |

Less than half a cent per thousand URLs crawled.

---

## 3. Architecture Overview

The system has six layers. Data flows top to bottom.

```
┌──────────────────────────────────────────────────────────────────────┐
│  INPUT LAYER                                                         │
│  MySQL Table (urls_july_2026)    Text File (S3)                      │
└──────────────────────┬─────────────────────┬────────────────────────┘
                       │                     │
                       └──────────┬──────────┘
                                  │  Scheduler reads, deduplicates,
                                  │  assigns priority, batches
                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│  URL FRONTIER (Redis)                                                │
│  • Bloom filter deduplication — 1.8 GB holds state for 1B URLs      │
│  • robots.txt cache per domain (24-hour TTL)                         │
│  • Politeness: per-domain rate limiting and crawl-delay enforcement  │
│  • Priority tiers: daily / weekly / monthly crawl frequency          │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  CRAWL QUEUE (Kafka — topic: urls-to-crawl)                          │
│  200 partitions, keyed by domain hash                                │
│  Same domain → same partition → same worker → natural rate limiting  │
│  7-day message retention → replay any failed batch within a week     │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
             ┌─────────────────┼─────────────────┐
             ▼                 ▼                 ▼
     ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
     │ FETCH WORKER  │ │ FETCH WORKER  │ │ FETCH WORKER  │  × 60–80
     │ (Fargate Spot)│ │ (Fargate Spot)│ │ (Fargate Spot)│  workers
     │               │ │               │ │               │
     │  I/O-bound    │ │  I/O-bound    │ │  I/O-bound    │
     │  50 concurrent│ │  50 concurrent│ │  50 concurrent│
     │  connections  │ │  connections  │ │  connections  │
     └───────┬───────┘ └───────┬───────┘ └───────┬───────┘
             └─────────────────┼─────────────────┘
                               │
               ┌───────────────┼───────────────┐
               ▼               ▼               ▼
       ┌──────────────┐ ┌────────────┐ ┌──────────────┐
       │ Raw HTML     │ │ Parse      │ │ Dead Letter  │
       │ Store (S3)   │ │ Queue      │ │ Queue (DLQ)  │
       │ .html.gz     │ │ (Kafka)    │ │ failed crawls│
       └──────────────┘ └─────┬──────┘ └──────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  PARSER + CLASSIFIER WORKERS  ← separate tier, CPU-bound            │
│  HtmlExtractor (BeautifulSoup4) → RakeClassifier → PageMetadata      │
│  html_hash check: skip all processing if page unchanged (40–60%)     │
│  Scales independently of fetch workers based on Parse Queue depth    │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
┌──────────────────────────┐   ┌───────────────────────────────┐
│  METADATA STORE          │   │  SEARCH INDEX                 │
│  (BigQuery)              │   │  (OpenSearch)                 │
│  Partitioned by date     │   │  topics, title, domain        │
│  Clustered by domain     │   │  p99 < 50ms full-text queries │
└──────────────────────────┘   └───────────────────────────────┘
               │                               │
               └───────────────┬───────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  QUERY API (FastAPI on Cloud Run)                                    │
│  Redis cache layer (TTL 1hr) → BigQuery / OpenSearch                 │
│  GET /metadata?url=...     GET /topics?domain=...                    │
│  GET /search?topic=...     GET /diff?url=...&since=...               │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 4. How It Works — Component by Component

### 4.1 URL Frontier (Redis)

The Frontier is the scheduler's brain. Before any URL reaches a worker, it passes
three checks:

**Deduplication — Bloom Filter**

A bloom filter stores a compact bit array instead of full URL strings. For 1 billion
URLs at a 0.1% false-positive rate, the required memory is ~1.8 GB — compared to
~100 GB for an exact-match Redis set. That is a 55× reduction.

The accepted trade-off: 0.1% false positives means roughly 1 million URLs per month
are wrongly skipped as "already seen." The cost of those missed crawls: ~$5/month.
The cost of eliminating false positives by switching to exact-match storage: ~$190/month
in extra RAM. The bloom filter is the economically correct choice.

**Politeness — robots.txt and Rate Limiting**

Every domain's `robots.txt` is fetched once and cached in Redis for 24 hours. The
Frontier enforces `Crawl-Delay` if declared, defaulting to 1 request per 2 seconds
per domain if not specified. This prevents the system from being identified and
blocked as a bot.

**Priority — Tiered Crawl Frequency**

| Tier | Content type | Crawl frequency | Reason |
|---|---|---|---|
| 1 | News sites, breaking content | Daily | Content changes daily |
| 2 | E-commerce, product pages | Weekly | Prices and availability change |
| 3 | Blogs, evergreen content | Monthly | Rarely updated |

---

### 4.2 Crawl Queue (Kafka)

**Why Kafka over SQS or RabbitMQ?**

| Capability | Kafka | SQS Standard | RabbitMQ |
|---|---|---|---|
| Message replay from offset | Yes | No — deleted on consume | No — ACKed and gone |
| Throughput | Millions/sec | Millions/sec (no ordering) | ~50K/sec |
| Partition by domain key | Native | No native concept | Manual, complex |
| Guaranteed ordering per domain | Yes (per partition) | No | No |
| Message retention | 7 days configurable | Up to 14 days | Until consumed |

SQS Standard has comparable raw throughput but offers no message ordering and no native
partitioning. To enforce per-domain rate limiting with SQS, you would need a separate
distributed service — a Redis atomic counter per domain, shared across all workers.
That is additional infrastructure that can fail and requires its own operational burden.

**How domain partitioning eliminates the need for a distributed rate limiter**

With domain-aware partitioning, all `amazon.com` URLs are routed to partition 47 by
hashing the domain name. One consumer handles partition 47. That consumer enforces the
per-domain rate limit locally — no shared state, no coordination, no race conditions.
**The architecture solves the rate limiting problem instead of adding infrastructure
to work around it.**

Without domain partitioning: 80 workers pull from the same queue. Ten of them can
simultaneously pull `amazon.com` URLs, fire 10 requests at Amazon at once, get
rate-blocked, and require a separate system to prevent it from happening again. With
partitioning, that situation is structurally impossible.

**Configuration:**

```
Topic:              urls-to-crawl
Partitions:         200   (supports up to 200 concurrent workers)
Retention:          7 days
Replication factor: 3     (survives loss of any 2 brokers)
Partition key:      hash(domain)
```

---

### 4.3 Two-Stage Pipeline: Why Fetch and Parse Are Separated

The Part 1 pipeline runs fetch → extract → classify as one sequential function. At
scale, running all three stages in the same worker tier creates a resource conflict
because **the stages have completely different bottlenecks**.

**Fetch is I/O-bound.** 90% of the worker's time is spent waiting for a network
response. The CPU is nearly idle. The right optimisation is concurrency — more
simultaneous connections per machine, not faster processors. These workers are
right-sized on many small machines running async I/O.

**Parse and classify are CPU-bound.** BeautifulSoup parsing and RAKE tokenisation
are compute-intensive. There is no network wait — HTML arrives from an internal
queue and is processed immediately. The right optimisation is raw CPU throughput.
These workers run best on fewer, compute-optimised machines.

If both stages ran in the same worker, a slow classification job blocks the next
HTTP fetch from starting — parallel I/O becomes serial. You are also paying for CPU
capacity on fetch workers that sits idle 90% of the time.

Separating them into two tiers with a Kafka queue in between means:

- **Add fetch workers** when crawl throughput is below the SLO target
- **Add parse workers** when the Parse Queue depth grows (NLP is the bottleneck)
- Neither tier needs to know the other exists
- If the classifier is upgraded (RAKE → ML model), only the parse tier changes —
  no fetch code is touched

---

### 4.4 Fetch Workers (ECS Fargate Spot)

Each fetch worker runs in a continuous async loop. The exact processing sequence:

```
 1. Pull message from Kafka partition        ← do NOT acknowledge yet
 2. Mark URL as in_flight in Frontier        ← detect stuck URLs if worker crashes
 3. Check robots.txt cache                   ← fetch and cache if missing
 4. Check politeness delay for domain        ← sleep if needed (same partition = same domain)
 5. Fetch HTML via async HTTP GET (aiohttp)
 6. Compute MD5 html_hash; compare to hash stored from previous crawl
 7. ── If unchanged ────────────────────────────────────────────────────
    │  Write no_change record
    │  Update Frontier: status=done
    └─ Acknowledge Kafka offset              ← skip all remaining steps
 8. Write compressed raw HTML to S3
 9. Publish raw HTML reference to Parse Queue (Kafka)
10. Update Frontier: status=fetched, last_fetched=now
11. Acknowledge Kafka offset                 ← message is safely handed off
```

Workers are deployed on **Fargate Spot** (70% cheaper than on-demand). Because
acknowledgement happens only after the HTML is safely written to S3 and the Parse
Queue, any worker interrupted by a Spot reclamation event will have its message
automatically redelivered to another worker. No work is lost.

If a URL fails 3 times consecutively, Kafka routes it to the **Dead Letter Queue**
to prevent a single broken URL from blocking other URLs in the same partition.

---

### 4.5 Parser + Classifier Workers

Each parse worker consumes from the Parse Queue and runs the extract + classify stages:

```
 1. Pull raw HTML reference from Parse Queue
 2. Read compressed HTML from S3
 3. HtmlExtractor.extract()   → ExtractedPage
 4. RakeClassifier.classify() → topics + category
 5. Assemble PageMetadata (unified schema)
 6. Write to BigQuery
 7. Index to OpenSearch
 8. Acknowledge Parse Queue offset
```

These workers are CPU-optimised and scaled independently based on Parse Queue depth.
If the queue grows, add more parse workers without touching the fetch tier.

---

## 5. Storage Design

Three storage systems serve three distinct access patterns. Using one store for all
three would mean compromising on all three.

### 5.1 Raw HTML — Object Storage (S3 / GCS)

**Purpose:** archive the original HTML for re-parsing when the extraction or
classification code is updated. No schema required — raw bytes.

```
Path structure:
s3://brightedge-raw-html/
  year=2026/
    month=07/
      domain=amazon.com/
        {url_hash}_{crawled_at}.html.gz
```

**Storage lifecycle (automatic cost tiering):**

| Age | Storage class | Price/GB | Reason |
|---|---|---|---|
| Day 0–7 | S3 Standard | $0.023 | Hot — recent crawls re-accessed frequently |
| Day 7–30 | S3 Infrequent Access | $0.0125 | Cooler — occasional re-processing |
| Day 30+ | S3 Glacier | $0.004 | Archive — rarely accessed |

HTML is gzip-compressed before storage. Average compression ratio for HTML is 5:1,
reducing 500 TB uncompressed to ~100 TB stored — saving approximately $5,000/month
at S3 Standard rates.

---

### 5.2 Structured Metadata — Analytical Database (BigQuery)

**Purpose:** serve analytical queries — "how many Technology pages did we crawl for
amazon.com last month?" or "which domains have the highest average word count?"

**Why BigQuery over MySQL or PostgreSQL?**

MySQL stores data row-by-row. To evaluate `WHERE page_category = 'E-Commerce'` on
1 billion rows, MySQL reads every column of every row to find the matching ones —
scanning the entire table even when the query needs only one column.

BigQuery stores data column-by-column. The same query reads only the `page_category`
column. The difference is not marginal:

| Database | Data scanned | Query time | Cost per query |
|---|---|---|---|
| MySQL | ~200 GB (entire table) | ~60 seconds | N/A (compute, not metered) |
| BigQuery (no optimisation) | ~200 GB | ~60 seconds | ~$1.00 |
| **BigQuery (partitioned + clustered)** | **~8 GB** | **~2 seconds** | **~$0.04** |

Partitioning and clustering are free schema declarations — not additional services.

**Table configuration:**

```
Table:      crawled_pages
Partition:  DATE(crawled_at)      → eliminates entire date ranges from scans
Cluster:    domain, page_category → skips non-matching blocks within a partition
Retention:  13 months rolling
```

---

### 5.3 Search Index — OpenSearch

**Purpose:** serve keyword-based queries — "find all pages mentioning `coffee maker`"
or "list all E-Commerce pages for bestbuy.com."

BigQuery is optimised for aggregations, not full-text search. OpenSearch returns
keyword matches in under 50ms at scale. If OpenSearch is unavailable, the Query API
falls back to BigQuery automatically — BigQuery is always the source of truth.

```
Index:    page-metadata
Shards:   10  (distributes ~1 TB index across nodes)
Replicas: 1   (each shard has one copy — survives single-node failure)
Fields:   domain, title, topics, page_category, published_date, crawled_at
```

---

## 6. Unified Data Schema

Every crawled page produces exactly one record in this shape. All fields are always
present — `null` when unavailable, never absent. Downstream consumers never need to
handle missing keys.

```json
{
  "_meta": {
    "schema_version": "1.0",
    "crawled_at":     "2026-07-15T08:23:11Z",
    "crawl_job_id":   "job_july_2026_batch_001"
  },

  "identity": {
    "url":           "https://www.amazon.com/Cuisinart-CPT-122/dp/B009GQ034C",
    "final_url":     "https://www.amazon.com/Cuisinart-CPT-122/dp/B009GQ034C",
    "domain":        "amazon.com",
    "canonical_url": "https://www.amazon.com/dp/B009GQ034C",
    "url_hash":      "sha256:a1b2c3..."
  },

  "crawl_context": {
    "status_code":              200,
    "content_type":             "text/html; charset=UTF-8",
    "html_hash":                "md5:f1e2d3c4...",
    "html_size_bytes":          427831,
    "crawl_duration_ms":        847,
    "changed_since_last_crawl": true,
    "error":                    null
  },

  "declared_metadata": {
    "language":         "en",
    "title":            "Cuisinart CPT-122 Compact 2-Slice Toaster, White",
    "meta_description": "Compact yet full-featured, fits in tight kitchen spaces.",
    "meta_keywords":    ["toaster", "cuisinart", "kitchen"],
    "og_title":         "Cuisinart CPT-122 Compact 2-Slice Toaster",
    "og_type":          "product",
    "og_image":         "https://images-na.ssl-images-amazon.com/...",
    "published_date":   null
  },

  "structure": {
    "h1_tags":    ["Cuisinart CPT-122 Compact 2-Slice Toaster, White"],
    "h2_tags":    ["Product description", "Customer reviews", "From the manufacturer"],
    "word_count": 842
  },

  "intelligence": {
    "topics":        ["cuisinart cpt-122", "compact toaster", "kitchen appliance", "2-slice"],
    "page_category": "E-Commerce / Product",
    "classifier":    "RakeClassifier-v1"
  },

  "raw_storage": {
    "s3_path": "s3://brightedge-raw-html/year=2026/month=07/domain=amazon.com/a1b2c3_20260715.html.gz"
  }
}
```

**Why a unified schema matters at scale:**

- Every consumer — search index, BI dashboards, ML pipelines — reads the same fields
  in the same shape. No per-consumer transformation code.
- `schema_version` enables backward-compatible evolution. New fields are ignored by
  old consumers. Removed fields bump the version and consumers are updated first.
- Null-safe design: a consumer checking `published_date` never gets a `KeyError` —
  it always gets a value or `null`.
- `crawl_job_id` links every record to its source batch, enabling targeted reprocessing
  if a bug is found in a specific job.

---

## 7. SLOs and SLAs

An **SLO** (Service Level Objective) is an internal target. An **SLA** (Service Level
Agreement) is an external commitment to customers, always set slightly looser than the
SLO. The gap is a buffer — internal incidents do not automatically trigger customer
penalty clauses.

### Defined Service Levels

| Metric | SLO (internal target) | SLA (customer commitment) | How it is measured |
|---|---|---|---|
| Monthly crawl completion | 99% of input URLs attempted | 98% | % of input URLs with a crawl record |
| Crawl success rate | 95% of attempts return status 200 | 93% | % non-error responses |
| URL freshness (p95) | < 7 days since last crawl | < 14 days | Age of most recent crawl per URL |
| Crawl → indexed latency (p95) | < 2 hours | < 6 hours | Time from crawl completion to BigQuery record |
| API availability | 99.95% | 99.9% | Uptime over 30-day rolling window |
| API latency (p99) | < 200ms | < 500ms | Measured at load balancer |
| API latency (p50) | < 50ms | < 100ms | Measured at load balancer |

### Error Budget

The API availability SLO of 99.95% translates to:

```
100% - 99.95% = 0.05% failure allowed
0.05% × 43,200 minutes/month = 21.6 minutes of downtime allowed per month
```

Engineers track this budget in real time on a Grafana dashboard. When the budget is
consumed before the end of the month, all non-critical deployments are frozen until
the budget resets. This turns "is it safe to deploy?" from a judgment call into a
measurable, auditable decision.

---

## 8. Monitoring, Alerting, and Key Metrics

### 8.1 Crawl Pipeline Metrics

| Metric | Tool | Alert threshold | Why this threshold |
|---|---|---|---|
| Crawl throughput (URLs/sec) | Prometheus + Grafana | < 300 URLs/sec for 5 min | Below 300 we will miss the monthly SLO |
| Crawl success rate | Prometheus | < 90% over 15 min | Early warning before SLA breach |
| Kafka consumer lag (fetch queue) | Kafka + Grafana | > 10M messages behind | > 7 hours of backlog at 386 URLs/sec |
| Kafka consumer lag (parse queue) | Kafka + Grafana | > 5M messages behind | Parse workers are the bottleneck — scale up |
| Dead letter queue depth | CloudWatch | > 100K messages | Indicates systemic crawl failure |
| Worker error rate by type | Prometheus | > 5% timeout rate | Domain blocking or network issue |
| html_hash unchanged rate | Prometheus | > 80% | May indicate URL list is stale |
| Daily cost per URL | AWS Cost Explorer | > $0.00001/URL | 2× above baseline — investigate waste |

### 8.2 API Metrics

| Metric | Tool | Alert threshold | Why this threshold |
|---|---|---|---|
| Request rate (req/sec) | CloudWatch | Sudden 10× spike | Possible abuse or traffic anomaly |
| p99 latency | CloudWatch | > 500ms for 3 min | SLA breach approaching |
| Error rate (5xx) | CloudWatch | > 1% for 5 min | Degraded service |
| Cache hit rate | Redis metrics | < 60% | Cache misconfiguration — excess DB load |

### 8.3 Dashboards

Four purpose-built Grafana dashboards, each for a different audience:

| Dashboard | Primary audience | Key panels |
|---|---|---|
| Crawl Health | On-call engineers | Throughput, success rate, fetch queue depth, parse queue depth, worker count |
| Data Freshness | Product / data teams | % of URLs crawled within 1 / 7 / 30 days |
| API Performance | Engineering | Latency percentiles, error rate, cache hit rate |
| Cost | Engineering + Finance | Daily cost per URL, storage growth, compute utilization |

### 8.4 On-Call Runbooks

Every alert links to a runbook that answers five questions:

1. What is this alert and what does it mean?
2. What is the most likely cause?
3. How do I diagnose it? (specific queries and commands)
4. How do I fix it?
5. How do I verify it is resolved?

Runbooks turn incident response from a guessing exercise into a checklist.

---

## 9. Reliability — Failure Modes and Recovery

### 9.1 Failure Response Table

| Failure | User impact | Detection | Recovery |
|---|---|---|---|
| Fetch worker crashes | URLs temporarily unprocessed | Kafka consumer lag spike | ECS restarts container; Kafka redelivers in-flight messages |
| Parse worker crashes | Metadata delayed | Parse queue depth grows | Same pattern — Kafka redelivers; raw HTML in S3 is not lost |
| Domain blocks crawler (403/429) | Missed URLs for that domain | High 4xx rate per domain alert | Circuit breaker backs off 1 hour; URL requeued automatically |
| Kafka broker failure | Queue paused briefly | Consumer lag drops to 0 + error alert | Replication factor 3 → automatic failover to replica |
| BigQuery write failure | Metadata delayed, not lost | Failed write metric | Retry queue → stage to S3 → replay into BigQuery on recovery |
| OpenSearch cluster failure | Search queries fail | 5xx on search endpoint | API falls back to BigQuery; rebuild index from BigQuery when recovered |
| S3 outage | Raw HTML unavailable | S3 error rate alert | Cross-region replication bucket as automatic fallback |
| Spot instance reclamation | Temporary worker reduction | Reduced worker count metric | Autoscaler replaces within 2 min; Kafka offset tracking ensures no data loss |

### 9.2 Graceful Degradation

**If OpenSearch is down:** the Query API automatically falls back to BigQuery for
search queries. Responses are slower (200ms vs 50ms) but correct. No errors returned
to customers. The search index is rebuilt from BigQuery when OpenSearch recovers —
BigQuery is always the source of truth.

**If BigQuery is unavailable:** recent metadata is served from the Redis cache.
New crawl results are staged to S3. When BigQuery recovers, the staged records are
replayed in order. Crawling continues uninterrupted — a storage failure does not
stop the pipeline.

### 9.3 Deployment Safety — Canary Releases

All code changes are deployed to 1% of workers first. Monitoring watches for
anomalies in that cohort (success rate, topics-per-page averages, error types) for
30 minutes before promoting to 100%. A bad deployment affecting 1% of workers touches
at most 10M records — correctable. A full rollout with the same bug touches 1B
records — a multi-day recovery.

---

## 10. Cost Optimization

### Summary of Optimizations

| Optimization | Mechanism | Estimated saving |
|---|---|---|
| html_hash change detection | Skip parse + classify + write if page unchanged | 40–60% of compute costs |
| Tiered crawl frequency | News daily, e-commerce weekly, blogs monthly | 30% fewer crawls per month |
| Spot instances for workers | Stateless workers tolerate interruption; 70% cheaper | ~$5,500/month |
| S3 storage lifecycle | Auto-tier raw HTML to IA then Glacier after 7/30 days | ~$3,000/month |
| Gzip HTML compression | 5:1 compression ratio before S3 write | ~$5,000/month |

### html_hash: the Most Impactful Single Optimization

Web crawl research shows that 40–60% of pages are identical between monthly crawl
cycles. For every unchanged page, the html_hash check eliminates:

- HTML parsing (CPU)
- Topic classification (CPU + memory)
- BigQuery write (storage + network)
- OpenSearch indexing (storage + network)

Only the HTTP fetch and the hash comparison are performed — roughly 10% of the cost
of a full crawl. At 1 billion URLs, skipping 500 million full crawls saves more than
any other single technique.

---

## 11. Methods, Services, and Frameworks

| Layer | Technology | Why this choice | Why not the alternative |
|---|---|---|---|
| Crawl workers | Python + aiohttp | Async I/O achieves the same concurrency as Go for HTTP-bound work; reuses Part 1 pipeline without a rewrite | Go would require rewriting BeautifulSoup and RAKE-NLTK from scratch — cost without benefit at this stage |
| Crawl infrastructure | AWS ECS Fargate Spot | Serverless containers; Spot reduces compute cost 70%; stateless workers tolerate interruption gracefully | Kubernetes (EKS) adds operational overhead; EC2 on-demand requires capacity planning and costs 70% more |
| Message queue | Apache Kafka (MSK) | Domain partitioning, message replay, millions of msgs/sec | SQS Standard lacks domain ordering (requires a separate rate-limiting service); RabbitMQ is memory-limited at billions of queued messages |
| URL state management | Redis | Sub-millisecond lookups (0.1ms vs 5–20ms for MySQL) for the hot-path dedup check | MySQL saturates under 386 concurrent lookups/sec without complex connection pooling |
| URL deduplication | Redis Bloom Filter | 1.8 GB vs 100 GB for 1B URL exact-match set; 55× memory reduction | Exact Redis SET is accurate but prohibitively expensive; DB lookup is too slow for the hot path |
| Raw HTML archive | Amazon S3 + lifecycle | Object storage is the right tool for unstructured blobs; lifecycle policies apply automatically | HDFS requires a cluster to manage; no serverless equivalent at this scale |
| Analytical queries | Google BigQuery | Columnar storage; partition + cluster reduces query cost 25×; serverless — no capacity planning | MySQL/PostgreSQL are row-oriented — scan 200 GB to read 3 columns from 1B rows |
| Full-text search | OpenSearch | p99 < 50ms keyword search; scales independently; BigQuery is source of truth if it fails | BigQuery is not optimised for full-text search; Elasticsearch is equivalent but OpenSearch is open-source |
| Query API | FastAPI on Cloud Run | Serverless; scales to zero; same framework as Part 1 | Always-on instances waste money at 12 req/sec average load |
| API caching | Redis | Sub-millisecond reads for repeated queries | CDN caching works for static assets, not database-backed API responses |
| Monitoring | Prometheus + Grafana | Industry standard; rich alerting and cross-service dashboards | CloudWatch alone lacks cross-service correlation; Datadog is equivalent but expensive |

---

## 12. Next Steps — Optimizations for Phase 2

These improvements are not required for the initial release but represent the natural
evolution path as the system matures.

| Enhancement | Business value | Technical complexity |
|---|---|---|
| JavaScript rendering (Headless Chrome) | Capture React/Next.js SPA content — currently invisible to the crawler | High — 10× more expensive per URL; requires a dedicated rendering fleet separate from the fetch workers |
| ML-based topic classifier | Higher accuracy than RAKE; understands context and synonyms, not just keyword frequency | Medium — needs labeled training data; the `BaseClassifier` interface in Part 1 already supports a drop-in replacement |
| Content change alerting | Notify customers when a competitor's page changes | Low — `html_hash` is already computed on every crawl; wire to a notification service |
| Multi-language NLP | Route non-English pages to language-appropriate classifiers | Medium — language detection is already implemented in Part 1; requires language-specific NLP models |
| Link graph extraction | Build a domain-level graph of which pages link to which | High — massive storage requirements; requires a graph database (Neptune/Neo4j) |
| Schema.org structured data | Parse JSON-LD for richer product, article, and event signals | Low — extractor already partially supports it; small extension to existing code |
