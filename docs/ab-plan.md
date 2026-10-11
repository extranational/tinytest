# A/B Evaluation Plan — TinyFish Search & Fetch vs the Rollout pipeline

Status: **draft, awaiting three decisions** (§8).
Target system: `~/repos/rollout` (AI-industry news digest engine).
Corpus: Rollout's published digests and its story/URL ledger.

---

## 1. The question, scoped precisely

Rollout's enrichment stage gives each story a summary by (a) **searching** for
candidate coverage URLs, (b) **fetching** those pages, and (c) condensing them.
We are replacing only (a) and (b):

| Layer | Native | Candidate |
|---|---|---|
| Search discovery | Brave Search API (`pipeline/websearch.py`) | TinySearch API |
| Page fetch + extraction | in-house `httpx` GET + BeautifulSoup (`enrich.fetch_readable`) | TinyFetch API |

We are explicitly **not** testing: TinyFish Agent, TinyBrowser, the LLM summariser,
triage, screening, or digest rendering. Those are out of scope and unchanged on
both arms.

**Why not diff re-run digests.** Rollout is not reproducible: feeds are live, the
item table is a rolling window, and every stage samples an LLM at temperature 0.3.
A digest-level diff would be dominated by those sources and the provider change
would be unidentifiable. Measure at the provider boundary.

---

## 2. Target corpus (measured 2026-10-10)

From `~/repos/rollout/state/cyberpulse.db` and `~/repos/vrsweb/content/rollout/`:

- published digests: **127** (2026-08-25 → 2026-10-10, ~7 weeks)
- stories in ledger: **528**
- published links (`story_links`): **862** rows — 556 `coverage`, 306 `original`
- distinct published URLs: **862**; distinct hosts: **205**
- item window in DB: 2026-08-25 → 2026-10-10 (1,898 items)
- runs with a recorded `fetch_failures` map: **130** (native fetch-failure baseline)

Host concentration (top): techcrunch.com 149, reddit.com 100, github.com 88,
huggingface.co 79, theverge.com 63, arstechnica.com 45 — six hosts are ~60% of
all published links. **Any sample must be stratified by host** or it collapses
into a TechCrunch/Reddit test.

### 2.1 Corpus-size caveat (decision D1)

Requested sample sizes were **200 URLs** (Fetch) and **150 stories** (Search).
Against this corpus that is:

- Fetch: 200 / 862 = **23%** of all distinct published URLs
- Search: 150 / 528 = **28%** of the entire story ledger

These are large fractions, so the "sample" is closer to a census. That is not
statistically wrong (more data is better) but it means: (i) results describe
Rollout's corpus rather than a generalisation from it, and (ii) host stratification
becomes tight — forcing non-overlap across strata at 200 URLs leaves thin cells.

Options: (a) accept as a census of Rollout; (b) scale to ~100/75 to leave headroom
and re-run on cyberpulse for power; (c) run the powered test on cyberpulse's
corpus (6,183 links / 5,221 stories / 819 digests) and treat Rollout as the pilot.
**Recommendation: (a) for Rollout as a pilot, with the harness written so the same
run is trivially re-pointed at cyberpulse** — which is also the stated intent of
running it separately from both repos.

---

## 3. Gold set construction

Built by `tinytest gold`, reading **Rollout's published output** — not the DB's
item table, which is a rolling window and may have pruned the relevant rows.

Sources, in priority order:

1. `story_links` (fingerprint, url, role, first_published) — the pipeline's own
   record of which URLs it published per story.
2. Each published `.mdx` frontmatter `top_stories[].url` and the body's
   `[Source: …]` / `[Coverage: …]` links — cross-check that the ledger matches what
   actually shipped.

Each gold row:

```
{
  "fingerprint": str,
  "headline": str,            # from stories.headline
  "query": str,               # reconstructed the same way backfill._query_for does
  "url": str,                 # the published URL, normalised as store._normalize_url
  "host": str,
  "role": "coverage" | "original" | "seed",
  "first_published": iso8601
}
```

Deterministic and frozen to `data/gold.jsonl`, so both arms and all repetitions see
byte-identical inputs.

### 3.1 Search-eligibility filter (important)

Not every published URL was *search-discoverable*. Feed-native sources — reddit.com
(100), github.com (88), huggingface.co (79), arxiv.org (22), x.com (10) — arrive
via dedicated fetchers, not via web search. Scoring Brave and TinySearch on those
would count both as failures for a URL that neither was ever supposed to find.

Rules:
- The **Search** gold set keeps only rows whose URL is on a web-discoverable host
  (exclude reddit.com, github.com, huggingface.co, arxiv.org, x.com and
  cve/advisory reference hosts), and preferentially `role == "original"` — the 306
  source-first citations, i.e. exactly the URLs the search channel exists to find.
- The **Fetch** gold set keeps every host: fetching a GitHub release page or a
  HuggingFace paper page is a legitimate fetch job.

### 3.2 Known bias, stated up front

Brave produced the candidate pool that Rollout historically chose from. The
published URL is therefore **partly Brave's own selection**, giving the native arm
home-field advantage on the Search experiment. This does not invalidate the test —
it raises the bar: TinySearch must **match or beat** Brave, not merely approach it.
Report the two recall figures side by side and interpret accordingly.

---

## 4. Experiment 1 — Fetch

**Arms**

- `native` — the vendored copy of Rollout's extraction routine, pinned to a recorded
  Rollout git SHA: `httpx` GET (10s timeout, UA as configured) → content-type gate
  → BeautifulSoup text + links → truncate to `PAGE_CHARS`.
- `tinyfish` — `POST https://api.fetch.tinyfish.ai`, `{"urls":[...], "format":"html",
  "links":true, "ttl":0}`.

**Why `format: "html"` and not `markdown`.** Rollout's `PageLink` carries
`(url, anchor, sentence, page_sentences)`, used by `sourcing` for the R5
corroboration that produces `original_source_url` — 306 of 862 published links are
`original`. TinyFetch's `links: true` returns **bare `<a href>` URLs only**: no
anchor text, no sentence context. Requesting semantic HTML lets the harness re-run
the *same local link-extraction logic* over TinyFish's rendered HTML, so the
citation channel is preserved and the two arms are compared on equal footing.
*(To be confirmed empirically with one live call before implementation — see
implementation-plan M1.)*

**Screening is excluded from both arms.** `fetch_readable` runs SafePrompt on every
page (up to 8,000 chars of third-party text egressed to a hosted API, with a
600s/run deadline budget). Screening is provider-independent — including it would
re-consume SafePrompt quota, add 5s/page latency noise, and confound the comparison
with an unrelated subsystem. The harness exercises extraction only.

**Protocol**

1. Draw a host-stratified sample of N=200 gold URLs (all roles, all hosts).
2. For each URL, call both arms with the same URL, in the same session, and both
   with a live (uncached) request: native as-is; TinyFish `ttl: 0`.
3. Store the raw response of both arms to `data/raw/fetch/` keyed by URL hash.
4. TinyFish allows 10 URLs/request and 150 URLs/min; 1,000 URLs/day free. 200 URLs
   is within the free allowance for one day.

**Metrics (per arm)**

- success / failure, and failure class (timeout, DNS, HTTP status, anti-bot block,
  empty/degenerate body, non-HTML)
- extracted characters, and **signal ratio** = `len(main_text) / len(body_text)`,
  computed by the harness identically for both arms from the raw response
- distinct outbound links recovered; **anchor-text and sentence-context coverage**
  (does the arm preserve enough for R5 corroboration)
- wall-clock latency: p50 / p90 / p95
- per-host success rate, compared against the native `fetch_failures` baseline
  recorded in Rollout's `runs` summaries

**Decision rule**

Adopt TinyFetch for the fetch seam if **all** hold:
1. success rate ≥ native (no host-class regression beyond noise), and
2. median signal ratio > native, and
3. anchor/sentence preservation sufficient that the citation channel still functions
   (link-structure check passes), and
4. latency within the enrichment run's practical budget (native timeout is 10s;
   TinyFish advertises p50 ~12s, p95 ~30s — a real risk to flag).

Otherwise: keep native, or adopt TinyFish as a *fallback* for the hosts where
native 403s (darkreading.com-style bot blocking) — a narrower, safer win.

---

## 5. Experiment 2 — Search

**Arms**

- `native` — Brave `GET https://api.search.brave.com/res/v1/web/search`,
  `count=5`, `freshness="pm"` (the live Rollout parameters).
- `tinyfish` — `GET https://api.search.tinyfish.ai?query=…&location=US`, up to 10
  results.

**Query construction.** Reuse the same construction `discovery/backfill.py:_query_for`
applies, so both arms receive the identical string. Freeze the query list to
`data/gold.jsonl`.

**Gold truth.** The URL Rollout actually published for that story (§3.1-filtered).

**Protocol**

- N=150 stories from the Search-eligible gold set.
- Each arm runs **3 repetitions** (search is the one live, non-deterministic part).
- Rate limits: Brave is metered by plan; TinySearch free is 30 req/min, 12k/day.
  150 × 3 = 450 requests → throttle to ≤30/min (~15 min) with backoff on 429.
- Store raw responses to `data/raw/search/`.

**Metrics (per arm)**

- **recall@5** and **recall@10** of the published gold URL
- **MRR** (mean reciprocal rank of the gold URL)
- query error rate, and rate-limit incidents
- for TinySearch additionally: whether `domain_type` / date filters would have
  changed the outcome (exploratory)

**Decision rule**

TinySearch is a candidate replacement if recall@5 ≥ Brave's, with the caveat in
§3.2. If recall is materially below Brave, the provider is still worth keeping as
the **zero-cost fallback** in a provider chain (free tier makes it free to add),
but not as a drop-in replacement.

---

## 6. Analysis & reporting

- Both arms compared per-metric with N, and with **3-rep variance reported** for
  Search (mean and spread), not a single number.
- No aggregate "score" — each metric is reported separately, with the decision
  rules above applied explicitly.
- Output: `data/results/` tables (CSV/JSON) + `docs/findings.md` written by hand
  after inspection. Every claim traceable to a stored raw response.
- **Report the negatives.** Fabrication observed in TinyFish Agent mode (G2 review,
  vendor-acknowledged) and the paywalled/aggregator fetch unreliability are recorded
  as context even though Agent mode is out of scope here.

---

## 7. Threats to validity

1. **Corpus is 7 weeks** — no seasonal/cadence coverage; a flattering or unflattering
   fortnight can dominate.
2. **Host concentration** — six hosts are 60% of links; mitigated by stratification,
   disclosed in results.
3. **Brave home-field bias** on Search (§3.2).
4. **Live pages drift** — a URL fetched today is not the page that was published in
   August. Both arms see the *same* drift, so the comparison is fair, but absolute
   success rates understate what either would have achieved historically.
5. **Small N** — 200/150 with 3 reps is enough to detect a large effect, not a small
   one. Do not over-read sub-5% differences.
6. **TinyFetch caching** — `ttl: 0` forces live fetches; a cached native-side result
   is impossible (no cache in the native path), so the arms stay comparable.

---

## 8. Open decisions (blocking)

- **D1 — sample size vs corpus** (§2.1): census 200/150 as requested, or scale down
  and use cyberpulse for the powered run? *Recommendation: keep 200/150 on Rollout
  as the pilot, harness re-pointable at cyberpulse.*
- **D2 — TinyFish API key**: none exists on this machine. TinyFish signup is free
  and needs no card but requires an account (email verification) — a human step.
  Needed before any experiment runs.
- **D3 — screening**: confirm exclusion from both fetch arms (§4) is acceptable.