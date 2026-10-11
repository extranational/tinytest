# Findings — TinyFish Search & Fetch vs CyberPulse

Host-run, 2026-10-10. Raw responses in `data/raw/`, tables in `data/results/`.
Design and decision rules: `docs/ab-plan.md`. Search results: see the final section
(still running at the time this file was created).

Every number here is regenerated from stored raw responses by
`uv run python -m tinytest analyze` — nothing is transcribed by hand.

---

## Experiment 1 — Fetch: **adopt as a host-scoped fallback only**

N=200 host-stratified URLs (`cve.threatint.com` capped at 10%; realised n=1).
Both arms fetched each URL live in the same session.

| Metric | native (`fetch_readable`, screen off) | TinyFish Fetch |
|---|---|---|
| success rate | **77.0%** (154/200) | **88.0%** (176/200) |
| failure classes | anti_bot 19, http_404 11, non_html 7, timeout 4, empty_body 3, http_406 1, http_422 1 | other_error 23, timeout 1 |
| latency p50 | **667 ms** | 1,624 ms |
| latency p90 | 1,616 ms | 8,205 ms |
| latency p95 | 2,821 ms | **12,296 ms** |
| mean extracted chars (ok) | 6,659 | 13,743 |
| `published_date` present (ok) | 60.4% | 63.1% |
| link URLs recovered vs native | — | **98.5%** (n=143) |
| anchor text + sentence | **yes** (`PageLink`) | **no** |

### The decisive number

**Native failed on 46 URLs; TinyFish succeeded on 23 of them** — it rescues half of
native's failure set, worth **+11.5 points of overall success** (77% → 88%). The 19
`anti_bot` failures are exactly the class where a server-side renderer behind a
residential-grade egress helps, and they are the fallback case the plan anticipated.

### Decision rules (ab-plan §4, revised fallback-only framing)

| # | Rule | Result |
|---|---|---|
| 1 | success rate ≥ native | **PASS** (88.0% vs 77.0%) |
| 2 | did not lose the substance of the page | **PASS, with a caveat** — see below |
| 3 | citation channel preserved | **FAIL by construction** (probe: no anchor, no sentence) |
| 4 | latency within the run's budget | **MARGINAL** — p95 12.3 s exceeds the 10 s timeout the native path uses |

**Verdict: reject as a replacement; adopt as a fallback for the hosts where native
fails.** Rule 3 is a hard fail and cannot be traded off — on any page fetched by
TinyFish the pipeline gets text without citations, so the source-first attribution
(`original_source_url`, 713 published links) simply does not exist there. Rule 4 means
the fallback needs its own longer timeout rather than inheriting the native 10 s.

### The content-overlap metric measures chrome, not substance — verified

`content_overlap` came out at **mean 0.635 / median 0.707** (n=148): only ~70% of
native's sentences appear in TinyFish's text. Read naively that says "TinyFish loses a
third of the page". It does not.

Manual inspection of the missing sentences across 153 ok-pairs shows they are
overwhelmingly site furniture that native's BeautifulSoup pull includes and TinyFish
strips — verbatim examples:

- `About Us Leadership Principles Leadership Team Testimonials ...`
- `Skip to Main Content Menu Close Subscribe Support Store`
- `Topics Armed Conflict Congress Courts & Litigation Criminal Law`
- `Google Tag Manager ...`
- `Visit us doyensec.com Follow us @doyensec Engage us info@doyensec.com Blog Archive`
- `html <head> Google's Gemini AI hacked three companies ... Skip to content`

So the metric is measuring *how much chrome native keeps*, not how much article
TinyFish loses — and TinyFish's larger mean character count (13,743 vs 6,659) reflects
that native is truncated at `PAGE_CHARS = 8000` while TinyFish is not. **This should be
reported as a metric limitation, not as a TinyFish failure.** A cleaner test would score
only native's body sentences, which the native output does not let us isolate.

### What this does not show

- It does **not** measure whether TinyFish returns the article a summariser needs. That
  is a different experiment (LLM-in-the-loop), deliberately out of scope.
- 200 URLs is enough to detect a large effect, not a small one. Do not read sub-5-point
  per-host differences as real.
- `cve.threatint.com` realised n=1, so the cap worked but the largest host in the corpus
  is barely represented — by design; a separate question is whether it is worth
  fetching at all.

---

## Experiment 2 — Search: **TinySearch beats Brave, by more than 2×**

150 stories × 2 arms × 3 reps = 900 requests, **900 ok, 0 errors, 0 rate-limit
retries**. Identical query string per story, built exactly as `backfill._query_for`
does. Brave ran at its live `count=5`; TinySearch returned 7–10 results (mean 9.8).
Gold bar: the URL CyberPulse actually published (1 URL for 147 stories, 2 for 3).

| Metric | Brave (native) | TinySearch |
|---|---|---|
| **recall@5** | **13.3%** (60/450) | **28.7%** (129/450) |
| **MRR** | 0.0896 | **0.1973** |
| hits at rank 1 | possible (best rank 1) | possible (best rank 1) |
| query error rate | 0.0% | 0.0% |
| recall@10 | *not comparable* (never asked for 10) | 28.7% |

**Variance across the 3 reps: zero, for every metric on both arms** (stdev 0.0). Search
is effectively deterministic over this corpus and window — the 3 reps cost nothing and
confirmed it rather than being discarded.

**Decision rule (ab-plan §5): recall@5 ≥ Brave's → PASS, decisively.** 2.15× the recall
and 2.20× the MRR. This survives the home-field-bias caveat in §3.2 in the strongest
way: Brave produced the candidate pool CyberPulse historically chose *from*, so the gold
URL is partly Brave's own selection, and TinySearch still more than doubles it. A
replacement that had merely *matched* Brave would have been unproven; this one does not
need that benefit of the doubt.

Notably, TinySearch's recall@10 equals its recall@5 (28.7%): **every gold URL it found
was in the top 5.** It is not scraping a hit out of a longer tail — its ranking is
either right early or it misses.

### Do not read the absolute numbers as "search is weak"

Both arms score low in absolute terms, and that is the metric, not the engines. The gold
URL is the page the pipeline *published*, and CyberPulse's source-first enrichment
deliberately prefers the **primary document** reached via a citation over the coverage
article a search engine surfaces. A primary advisory or vendor bulletin is often not a
top-5 web-search hit for the story's headline. The valid signal here is the **relative**
comparison on identical inputs; the absolute rate is a property of the gold bar.

---

## Bottom line

| Seam | Verdict |
|---|---|
| **Search** | **Adopt.** recall@5 28.7% vs 13.3%, MRR 2.2×, zero errors, zero variance, free tier. Beats a baseline with home-field advantage. |
| **Fetch** | **Adopt as a host-scoped fallback only.** Rescues half of native's failures (+11.5 pts overall) but cannot carry citations, so it can never replace `fetch_readable`. Needs its own timeout (p95 12.3 s vs native's 10 s cap). |

A coherent combination: **TinySearch as the search provider, `fetch_readable` retained
for pages it wins, TinyFetch as the fallback where native 403s or times out** — the
search half is a clean win, the fetch half is a bounded repair.

### Reproduction

```
set -a; . ~/.hermes/.env; . ~/repos/cyberpulse/.env; set +a
export CYBERPULSE_PATH=~/repos/cyberpulse PYTHONPATH=src
python -m tinytest gold        # freeze the corpus
python -m tinytest fetch-ab    # ~200 native + 200 TinyFish fetches
python -m tinytest search-ab   # 150 stories x 2 arms x 3 reps
python -m tinytest analyze     # tables from stored raw responses only
```

`data/results/*` regenerates byte-identically from `data/raw/`; no metric is
transcribed by hand.