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

## Experiment 2 — Search: *pending*

The first run aborted on a harness defect: the Brave client required `BRAVE_API_KEY`
while CyberPulse's environment names it `BRAVE_SEARCH_API_KEY`. Fixed at landing to
accept either. Full run (150 stories × 2 arms × 3 reps) in progress.

Decision rule (ab-plan §5): TinySearch is a candidate replacement if recall@5 ≥ Brave's,
subject to the home-field-bias caveat (Brave produced the candidate pool CyberPulse
historically chose from). Recall@10 is reported for TinyFish only and is **not
comparable** — Brave was run at its live `count=5`.