# Engineering Plan: Proof of Concept & Release

## Table of Contents

| # | Section | What it covers |
|---|---|---|
| — | [What a POC Is — and Isn't](#what-a-poc-is--and-isnt) | Why we build a POC before committing to full production infrastructure |
| 1 | [POC Scope](#1-poc-scope) | What the POC proves and what it deliberately excludes |
| 2 | [Engineering Breakdown](#2-engineering-breakdown) | Six phases with tasks, owners, and week-by-week schedule |
| 3 | [Potential Blockers](#3-potential-blockers) | Known risks, unknown risks, and mitigations for each |
| 4 | [Time Estimates](#4-time-estimates) | Per-phase estimates with confidence levels |
| 5 | [POC Evaluation Criteria](#5-poc-evaluation-criteria) | How we decide if the POC passed or failed |
| 6 | [Release Plan](#6-release-plan) | Phased rollout from POC to 1B URLs in production |
| 7 | [Release Quality Checklist](#7-release-quality-checklist) | What must be true before each phase goes live |
| 8 | [AI Tools Used](#8-ai-tools-used) | Transparency on AI assistance in this assignment |

---

## What a POC Is — and Isn't

A POC is not a miniature version of the production system. It is a **targeted
experiment to validate the riskiest assumptions before committing to full
engineering**. The Part 2 design makes specific assumptions that drive every cost
and timeline estimate. If any of them are wrong, the architecture needs to change.
Discovering that in Week 6 with 10M URLs costs far less than discovering it in Month 6
with 1B URLs already in production.

The table below maps the riskiest assumptions to the specific POC gate that validates them.

| Assumption from Part 2 | Risk if wrong | Gate |
|---|---|---|
| Async Python sustains 25 URLs/sec/worker | Worker count and compute cost are 50× under-estimated | Gate 1 — Throughput |
| Major sites won't block crawls at scale | Entire approach needs proxy infrastructure — cost model changes by 10–100× | Gate 2 — IP Blocking |
| 40–60% of pages are unchanged between monthly crawls | Cost model understated by up to 2× | Gate 3 — html_hash Rate |
| RAKE produces usable topics on production content | Topic quality is too low for search — need ML classifier in Phase 1 | Gate 4 — Topic Quality |
| BigQuery (partitioned + clustered) serves p99 < 200ms | Query API redesign required | Gate 5 — API Latency |
| Failure recovery works as designed (Kafka redeliver) | Data loss or duplicates at scale — reliability promise is broken | Gate 6 — Recovery |

**The POC succeeds when all six gates pass.** A single failed gate extends the POC
until the root cause is resolved — it is not skipped.

---

## 1. POC Scope

### What the POC proves

The POC validates that the scaled architecture described in `scale_architecture.md`
works in practice before committing to full production infrastructure. It is not
a mini version of production — it is a controlled experiment that answers specific
questions with measurable outcomes.

**The five questions the POC must answer:**

1. Can the existing Part 1 crawler pipeline (`fetcher` → `extractor` → `classifier`)
   run reliably inside a Kafka consumer loop without modification?
2. Does domain-partitioned Kafka enforce politeness correctly at scale?
3. Does the `html_hash` change detection skip processing for unchanged pages,
   and does the skip rate match the expected 40–60%?
4. Does BigQuery with date partitioning and domain clustering deliver query
   performance within SLO targets?
5. Is the cost-per-URL model accurate — does $0.0000097 hold in practice?

### POC target: 10 million URLs

Production target is 1 billion URLs per month. The POC targets **10 million URLs**
across a representative sample of domains (news, e-commerce, blogs, homepages).

**Why 10 million and not 1 billion:**
- 10M is enough to surface real failure modes (domain blocking, DLQ depth, parse errors)
- 10M generates enough data to validate query performance on BigQuery
- 10M costs approximately $97 — affordable for a POC
- 10M can complete in ~7 hours at POC worker capacity — fast enough to iterate

### What the POC explicitly excludes

| Excluded | Reason | When addressed |
|---|---|---|
| JavaScript rendering | 10× cost increase — validate base pipeline first | Phase 2 |
| ML-based classifier | Requires labeled training data not yet available | Phase 2 |
| Cross-region replication | Disaster recovery — not needed at POC scale | Phase 1 production |
| Full monitoring stack | Grafana dashboards built during Phase 1, not POC | Phase 1 |
| Multi-language NLP | English-only for POC | Phase 2 |

---

## 2. Engineering Breakdown

### Phase 0 — Environment Setup (Week 1)

**Goal:** all infrastructure is running and reachable from a local dev machine.

| Task | Description | Estimate |
|---|---|---|
| Provision Kafka cluster | 3-broker cluster, 200 partitions on `urls-to-crawl` topic | 2 days |
| Provision Redis instance | Single node, 4 GB RAM (sufficient for 10M URL frontier) | 0.5 days |
| Provision BigQuery dataset | Create `crawled_pages` table with partitioning + clustering schema | 0.5 days |
| Provision OpenSearch cluster | Single-node, 3 shards for POC | 1 day |
| Provision S3 bucket | With lifecycle rules configured | 0.5 days |
| Set up CI pipeline | GitHub Actions: lint → test → Docker build on every push | 1 day |
| **Phase total** | | **5.5 days** |

---

### Phase 1 — Frontier + Scheduler (Week 2)

**Goal:** URLs from the MySQL table and S3 file are deduplicated and published to Kafka.

| Task | Description | Estimate |
|---|---|---|
| `frontier.py` — Redis client | URL state management: `pending`, `in_flight`, `done` | 2 days |
| `frontier.py` — Bloom filter | Deduplication for 10M URLs; validate 0.1% false-positive rate | 1 day |
| `frontier.py` — Politeness | Per-domain request rate tracking; robots.txt cache | 1 day |
| `scheduler.py` — MySQL reader | Batch-read URLs, check Frontier, publish to Kafka | 1 day |
| `scheduler.py` — S3 reader | Stream-read text file, same dedup + publish logic | 0.5 days |
| Unit tests | Frontier deduplication, politeness enforcement, scheduler batch logic | 1 day |
| **Phase total** | | **6.5 days** |

---

### Phase 2 — Crawler Worker (Week 3)

**Goal:** a Kafka consumer runs the existing Part 1 pipeline reliably in a loop.

| Task | Description | Estimate |
|---|---|---|
| `worker.py` — Kafka consumer loop | Pull message, mark `in_flight`, process, acknowledge | 1.5 days |
| `worker.py` — `html_hash` check | Retrieve stored hash from Frontier; skip if unchanged | 0.5 days |
| `worker.py` — retry + DLQ | Exponential backoff; route to DLQ after 3 failures | 1 day |
| `worker.py` — circuit breaker | Per-domain backoff when 10 consecutive errors occur | 1 day |
| Docker image | Package worker with all Part 1 dependencies + NLTK data | 0.5 days |
| Integration test | Run 1,000 URLs end-to-end; verify records in BigQuery | 1 day |
| **Phase total** | | **5.5 days** |

---

### Phase 3 — Storage Integration (Week 4)

**Goal:** crawl results are written to BigQuery, OpenSearch, and S3 correctly.

| Task | Description | Estimate |
|---|---|---|
| `storage.py` — BigQuery writer | Batch writes using BigQuery streaming inserts | 1.5 days |
| `storage.py` — OpenSearch writer | Index `title`, `topics`, `domain`, `page_category` | 1 day |
| `storage.py` — S3 writer | Write gzip-compressed raw HTML with correct path structure | 0.5 days |
| `storage.py` — retry queue | Stage failed writes to a retry Kafka topic | 1 day |
| Schema validation | Verify every record matches the unified schema from `scale_architecture.md` | 0.5 days |
| **Phase total** | | **4.5 days** |

---

### Phase 4 — Query API Extension (Week 5)

**Goal:** the existing FastAPI service gains three new endpoints backed by BigQuery and OpenSearch.

| Task | Description | Estimate |
|---|---|---|
| `GET /metadata?url=` | Single URL lookup — Redis cache → BigQuery fallback | 1 day |
| `GET /topics?domain=` | All topics for a domain — Redis TTL 6hr | 1 day |
| `GET /search?topic=` | Full-text keyword search — OpenSearch | 1 day |
| Redis cache layer | Cache-aside pattern; TTL configuration per endpoint | 0.5 days |
| Load test | Simulate 1M queries/day; verify p99 < 200ms SLO | 1 day |
| **Phase total** | | **4.5 days** |

---

### Phase 5 — POC Validation Run (Week 6)

**Goal:** run 10 million URLs through the full pipeline and measure outcomes against
the POC evaluation criteria.

| Task | Description | Estimate |
|---|---|---|
| Prepare URL sample | 10M URLs across news, e-commerce, blog, homepage categories | 0.5 days |
| Run full crawl | Monitor throughput, error rate, consumer lag | 2 days |
| Measure results | Compare actual metrics against POC criteria (Section 5) | 1 day |
| Cost reconciliation | Compare actual AWS/GCP bill against $97 estimate | 0.5 days |
| POC review meeting | Go / no-go decision for Phase 1 production | 0.5 days |
| **Phase total** | | **4.5 days** |

---

## 3. Potential Blockers

### Known Blockers (high confidence these will occur)

#### Blocker 1 — IP Blocking by Major Sites (CRITICAL)

Major e-commerce sites (Amazon, Walmart, Best Buy) and news sites (CNN) aggressively
block automated crawlers. The Part 1 demo crawled 5 URLs once. Production crawls the
same domains thousands of times per day. These are completely different threat models.

At 386 URLs/sec from a small set of AWS IP addresses, most major domains will return
403/429 responses within minutes of the crawl starting. This is not an edge case — it
is the default behavior of every large commercial website.

**What the POC must measure (Gate 2):**
Crawl 1,000 URLs from each of the 10 largest domains in the corpus. Measure the
403/429 rate per domain after 1 hour at the politeness-limited rate.

**Decision tree based on results:**

| Observed 4xx rate | Decision | Cost impact |
|---|---|---|
| < 5% across all major domains | Current approach works; respect `robots.txt` + `Crawl-Delay` | No change to estimates |
| 5–30% on ≤ 3 domains | Add AWS NAT Gateway rotation (multiple outbound IPs) | +~$500/month |
| > 30% on any major domain | Residential proxy service required | **+$1.5M–$5M/month** — full cost model revision before Phase 1 |

The proxy scenario is not a minor adjustment — it changes the cost per URL from
$0.0000047 to potentially $0.0015–$0.005, making the service economically unviable
without a fundamentally different approach (negotiated crawl access, ISP partnerships,
or a CDN-integrated architecture). **This must be resolved in the POC before any
Phase 1 commitment.**

---

#### Blocker 2 — RAKE Topic Quality on Production Content (HIGH)

RAKE works well on editorial text but degrades on e-commerce pages — long product codes,
price strings, navigation text, and disclaimer copy all contaminate keyword extraction.
A `topics` array containing `["B009GQ034C", "0.5 stars", "add to cart"]` is useless.

**Mitigation already in Part 1:** the classifier filters short pages and uses a
minimum signal threshold. But at 1B records, even 10% garbage topics pollute the
search index permanently.

**What the POC must measure (Gate 4):** human-evaluate 100 randomly sampled `topics`
arrays (see Section 5). If the average quality score is below 1.5, a topic
post-processing step must be built before Phase 1 (max phrase length, stop-phrase
filter, minimum word count per topic).

---

#### Blocker 3 — BigQuery Streaming Insert Quotas (MEDIUM)

BigQuery streaming inserts have a quota of 1 GB/sec per table. At 5 KB per record
and 386 records/sec, the peak write rate is ~2 MB/sec — well within quota. However,
the quota applies per table per project, and BigQuery has a 1-minute dedup window for
streaming inserts that requires careful handling of retries.

**Mitigation:** use batch inserts (not streaming) for the bulk crawl pipeline;
reserve streaming inserts for the real-time Query API write path only.

---

#### Blocker 4 — robots.txt Parsing Edge Cases (MEDIUM)

| Blocker | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Malformed `robots.txt` files | Medium | Incorrectly blocked/unblocked domains | Use `robotparser` from the standard library; treat parse errors as "allow" |
| Per-path wildcard rules (`Disallow: /dp/*`) | High | Crawling disallowed product pages | Test against real robots.txt from Amazon, Walmart, CNN before Phase 1 |

### Unknown Blockers (may surface during POC)

| Risk area | Why it is uncertain | How to detect | Contingency |
|---|---|---|---|
| **JavaScript-rendered page prevalence** | Unknown what % of the corpus uses React/Angular/Next.js — current fetcher returns empty content for SPAs | Audit 1K random URLs: measure % with `word_count` of zero or near-zero | If > 20% of corpus is JS-rendered, add Playwright rendering tier in Phase 1 |
| **Memory leaks in worker at scale** | Part 1 was tested on hundreds of URLs, not millions | Monitor worker RSS over 24-hour run | Restart workers every N crawls as a temporary fix; profile with `memory_profiler` |
| **HTML size outliers crashing the parser** | A small number of pages may be 10–50 MB | DLQ depth; worker crash rate | Add a 5 MB HTML size cap in `fetcher.py`; route oversized pages to DLQ |
| **Kafka partition rebalancing latency** | Untested at 200 partitions with real workload | Consumer lag spikes during rebalancing | Tune `session.timeout.ms` and `max.poll.interval.ms` |
| **html_hash dedup rate lower than expected** | General web research says 40–60%; e-commerce corpus may be 15–25% due to price changes | Measured in Gate 3 — re-crawl 10K URLs 7 days later | If < 20%, revise cost model before Phase 1 budget approval |
| **OpenSearch index size growing faster than estimated** | Actual field sizes may exceed 5 KB estimate | Disk usage monitoring | Reduce indexed fields; increase shard count |

### Trivial Items (low risk, straightforward to resolve)

| Item | Resolution |
|---|---|
| NLTK data not available in Docker container | Already handled in `Dockerfile` — downloaded at build time |
| Python version compatibility | `requirements.txt` pins all versions; use the same Python 3.11 base image |
| AWS/GCP credentials in CI | Use GitHub Actions secrets + IAM role with least-privilege permissions |
| Kafka topic creation | Automated in Phase 0 setup scripts |

---

## 4. Time Estimates

| Phase | Work | Calendar time | Confidence |
|---|---|---|---|
| 0 — Environment setup | 5.5 days | Week 1 | High — standard infrastructure work |
| 1 — Frontier + Scheduler | 6.5 days | Week 2 | High — well-defined requirements |
| 2 — Crawler Worker | 5.5 days | Week 3 | Medium — retry/DLQ edge cases may take longer |
| 3 — Storage Integration | 4.5 days | Week 4 | Medium — BigQuery quota issues are possible |
| 4 — Query API Extension | 4.5 days | Week 5 | High — building on existing FastAPI structure |
| 5 — POC Validation Run | 4.5 days | Week 6 | Low — real-world crawl may surface unknown blockers |
| **Total** | **31 days** | **6 weeks** | |

**Buffer:** add 1 week (Week 7) for unknown blockers that surface during the
validation run. A 6-week estimate with no buffer is an aggressive plan —
7 weeks is the realistic commitment.

### What "done" means for each phase

A phase is done when:
1. The code is reviewed and merged to `main`
2. Unit tests pass in CI
3. The phase-specific integration test passes against real infrastructure
4. The next phase's inputs are verified to be correct

---

## 5. POC Evaluation Criteria

The POC passes if **all six gates** are met after the validation run. A single
failure triggers root-cause investigation before proceeding to production.

### Gate 1 — Throughput

**Test:** Run the 10M URL batch with 16 workers. Measure sustained URLs/sec over 1 hour.

| Result | Decision |
|---|---|
| ≥ 350 URLs/sec sustained | PASS |
| 250–350 URLs/sec | PASS with action: add 10 more workers in Phase 1; revise cost estimate |
| < 250 URLs/sec | FAIL — async I/O implementation must be debugged |

### Gate 2 — IP Blocking Rate

**Test:** Crawl 1,000 URLs from each of the 10 largest domains at the politeness-limited
rate. Measure 403/429 rate per domain after 1 hour.

| Result | Decision |
|---|---|
| < 5% across all domains | PASS |
| 5–30% on ≤ 3 domains | PASS with action: add NAT Gateway rotation for affected domains |
| > 30% on any domain | FAIL — proxy infrastructure required; full cost model revision before Phase 1 |

### Gate 3 — html_hash Dedup Rate

**Important:** the first crawl has nothing to compare against — every URL is processed.
To measure the dedup rate, the same 10,000 URLs must be re-crawled 7 days after the
initial run. **A first-crawl-only measurement tells you nothing about this assumption.**

**Test:** Re-crawl 10,000 URLs 7 days after the initial 10M run. Measure % with
identical `html_hash` values.

| Result | Decision |
|---|---|
| ≥ 35% unchanged | PASS — cost model validated |
| 20–35% unchanged | PASS with action: revise monthly cost estimate before Phase 1 budget |
| < 20% unchanged | FAIL — cost model needs full revision before proceeding |

### Gate 4 — Topic Quality

**Test:** Human evaluation of 100 randomly sampled `topics` arrays. Score each 0–3:
3 = accurate and usable, 2 = mostly correct, 1 = noisy, 0 = useless.

| Average score | Decision |
|---|---|
| ≥ 2.5 | PASS — RAKE is acceptable for Phase 1 |
| 1.5–2.5 | PASS with action: implement topic post-processing before Phase 1 |
| < 1.5 | FAIL — ML classifier must be scheduled as a Phase 1 blocker |

### Gate 5 — BigQuery Query Latency

**Test:** Run 100 representative queries (domain + category filter, date-range scan,
topic keyword lookup) against the 10M URL dataset. Measure p99 latency.

| Result | Decision |
|---|---|
| p99 < 2 seconds | PASS |
| p99 2–5 seconds | PASS with action: review cluster key selection and Redis cache TTL |
| p99 > 5 seconds | FAIL — schema or partition configuration needs redesign |

### Gate 6 — Failure Recovery

**Test:** Kill a fetch worker mid-crawl (100K URL batch). Kill a parse worker
mid-processing. Verify no data is lost or duplicated.

| Expected outcome | Pass condition |
|---|---|
| No URLs permanently skipped | 0 missing records in BigQuery after recovery |
| No duplicate records | Uniqueness check on `(url, crawled_at)` in final dataset |
| Recovery time | All workers processing again within 3 minutes |

Any violation is a **FAIL** — this tests the core reliability promise.

### Secondary Metrics (tracked, not blocking)

| Criterion | Target |
|---|---|
| Average topics per page | ≥ 3 for pages where category was assigned |
| Category assignment rate | ≥ 70% of successfully crawled pages have a non-null `page_category` |
| DLQ depth after run | < 0.5% of total URLs (< 50,000 messages) |
| Worker crash rate | < 1% of workers restarted during the run |
| Cost accuracy | Actual cost within 20% of $97 estimate for 10M URLs |

---

## 6. Release Plan

### Phase 0 — POC (Weeks 1–6)
10 million URLs. Internal only. Goal: validate architecture and cost model.

### Phase 1 — Limited Production (Months 2–3)
100 million URLs per month. One customer dataset. Goal: validate SLOs under real
customer load.

**Entry criteria:**
- All six POC gates passed (or passed with documented action items completed)
- All Tier 3 blockers (IP blocking, JS rendering, RAKE quality) resolved or explicitly accepted with documented rationale
- Monitoring dashboards live (Grafana)
- On-call runbooks written for the top 5 alert types
- Query API load-tested at 10× expected traffic

**Exit criteria:**
- API availability ≥ 99.95% over a 30-day window
- Crawl success rate ≥ 95%
- No P1 incidents in the final two weeks

### Phase 2 — Full Production (Months 4–6)
1 billion URLs per month. All customer datasets. Goal: meet all SLOs at full scale.

**Entry criteria:**
- Phase 1 exit criteria met
- Worker autoscaling tested and verified (scale up to 80, scale down to 10)
- Storage lifecycle policies active on S3
- Cost per URL confirmed ≤ $0.00002 at 100M URL scale

**Exit criteria:**
- All seven SLOs met for two consecutive months
- Error budget consumed < 50% in each month
- Cost per URL ≤ $0.00002

### Phase 3 — Enhancements (Month 7+)
JavaScript rendering, ML-based classifier, content change alerting.
Planned based on customer feedback and Phase 2 operational learnings.

---

## 7. Release Quality Checklist

Before any phase goes live, the following must be true:

### Code Quality
- [ ] All tests pass in CI (`pytest --cov` ≥ 80% coverage on crawler package)
- [ ] No linter errors (`ruff` or `flake8` clean)
- [ ] All new code reviewed by at least one other engineer
- [ ] No hardcoded secrets, credentials, or environment-specific values in code

### Infrastructure
- [ ] Infrastructure provisioned via code (Terraform or CDK) — no manual console changes
- [ ] All services have health checks configured
- [ ] Autoscaling policies tested under synthetic load
- [ ] Backup and recovery procedure documented and tested

### Observability
- [ ] All new components emit metrics to Prometheus
- [ ] Alerts configured for every SLO with linked runbooks
- [ ] Dashboards reviewed by on-call team before go-live
- [ ] Log aggregation confirmed working (no silent failures)

### Deployment Safety
- [ ] Canary deployment to 1% of workers first; 30-minute observation window
- [ ] Rollback procedure documented and tested (< 5 minutes to revert)
- [ ] Feature flags in place for any major behaviour change
- [ ] Comms plan ready if a customer-facing SLA is at risk

---

## 8. AI Tools Used

This assignment was completed with AI assistance in accordance with the FAQ
which explicitly permits AI tools. Below is a transparent account of how AI
was used and what value it provided.

| Tool | How it was used |
|---|---|
| **Cursor (Claude Sonnet)** | Primary development assistant throughout the assignment. Used for: code generation and review (`crawler/` package, `main.py`, `Dockerfile`), design document drafting and iteration, debugging Python and deployment issues, explaining architectural trade-offs. |

### What AI did and did not do

**AI assisted with:**
- Translating design intent into working Python code
- Structuring documentation to match assignment requirements
- Stress-testing design assumptions and identifying gaps in reasoning
- Identifying the html_hash evaluation flaw (first-crawl-only measurement proves nothing)
- Flagging IP blocking as a potentially architecture-changing risk, not a minor mitigation

**Engineering judgment that remained with the developer:**
- Choosing which fields to include in the output schema and why
- Deciding on the trade-off between bloom filter false positive rate and memory cost
- Selecting Railway over Cloud Run for the POC deployment based on speed
- Scoping the POC to 10M URLs and defining the six evaluation gates
- All architectural decisions in `scale_architecture.md`

AI accelerated the implementation and documentation but did not replace the
engineering reasoning behind the design.
