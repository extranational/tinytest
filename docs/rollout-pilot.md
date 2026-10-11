# Rollout pilot — TinyFish Search & Fetch on a second corpus

Host-run, 2026-10-11. Same harness, same decision rules and the **same
search-eligibility filter** as `docs/findings.md` (CyberPulse), re-pointed at Rollout
with `TINYTEST_CORPUS=rollout`. Raw responses in `data/rollout/raw/`, tables in
`data/rollout/results/`; every number below regenerates from them with
`tinytest analyze` (verified byte-identical). CyberPulse's `data/cyberpulse/` was not
touched.

Purpose: a **replication**, not a new experiment. CyberPulse's corpus is ~7× larger and
was the powered run (ab-plan D1); this checks whether the same two effects hold on a
different domain, a different ingestion profile and a much smaller ledger.

---

## Corpus (Rollout, measured 2026-10-11)

Same tables as CyberPulse (`story_links` + `stories`), different numbers:

| | Rollout | CyberPulse (§2) |
|---|---|---|
| published links | **860** | 6,183 |
| roles | 552 coverage / 308 original | 5,233 / 713 / 237 seed |
| distinct URLs | **860** | 6,150 |
| distinct hosts | **212** | 479 |
| stories in ledger | **526** | 5,221 |
| published digests (`.mdx`) | **128** (2026-08-25 → 2026-10-11) | 819 |
| search-eligible (after §3.1) | **206 links / 201 stories** | 618 / 610 |

Sample sizes are the ab-plan defaults, so both corpora are directly comparable — but
they mean different things. On CyberPulse N=200/150 was a 3% sample; on Rollout it is a
**23% draw of the ledger and a 75% draw of the search-eligible set**. The search arm is
therefore closer to a census than a sample: good for coverage, but it means the
confidence interval is not the thing to lean on — the *effect size* is.

`tinytest gold` records no frozen §2 expectation for Rollout: its ledger is live (crons
republish 3×/day), so the counts above are a snapshot, not an invariant.

---

## Experiment 1 — Fetch: **replicates — fallback-only**

N=200 host-stratified URLs, both arms live in the same session.

| Metric | native (`fetch_readable`, screen off) | TinyFish Fetch |
|---|---|---|
| success rate | **82.5%** (165/200) | **90.0%** (180/200) |
| failure classes | anti_bot 12, http_404 12, non_html 4, other_error 3, timeout 2, empty_body 1, http_419 1 | other_error 20 |
| latency p50 | **704 ms** | 1,689 ms |
| latency p90 | 1,895 ms | 7,096 ms |
| latency p95 | **2,376 ms** | 9,966 ms |
| mean extracted chars (ok) | 6,592 | 14,967 |
| `published_date` present (ok) | **0.0%** | **64.4%** |
| link URLs recovered vs native | — | **96.9%** (n=154) |
| anchor text + sentence | **yes** (`PageLink`) | **no** |

**Native failed on 35 URLs; TinyFish succeeded on 18 of them** — 51% of the native
failure set, worth **+7.5 points of overall success** (82.5% → 90.0%). Seven of the 18
rescues are `anti_bot`; the rest are `non_html` (4), `other_error` (3), `timeout` (2),
`http_419` (1), `empty_body` (1) — the same picture as CyberPulse (23 rescued there, half its failure
set, with 19 `anti_bot` failures).

`content_overlap` is **mean 0.637 / median 0.703** (n=160) — statistically
indistinguishable from CyberPulse's 0.635 / 0.707, and it carries the same caveat
recorded there: the metric counts native's site chrome that TinyFish strips, not lost
article substance.

### One genuine engine difference, not a harness defect

The native arm's `published_date` rate is **0.0% on Rollout vs 60.4% on CyberPulse**.
Cause: Rollout's re-skinned `enrich.Page` carries only `(text, links)` — it dropped the
`published` field that CyberPulse's `fetch_readable` fills from JSON-LD. The adapter
reads it with `getattr(page, "published", None)`, which is correctly `None` throughout.
So on Rollout the recency signal (`max_source_age_days`) is **unavailable to the native
path and free from TinyFetch** — a stronger argument than CyberPulse had, and one that
does *not* lift the citation gate.

### Verdict — unchanged from CyberPulse

Rule 3 of the ab-plan §4 decision table (citation channel preserved) **fails by
construction** — TinyFetch returns bare URLs with no anchor and no sentence, so the
source-first attribution cannot be reproduced on any page it fetches. That is a hard
fail and cannot be traded off. Rule 4 is again marginal (p95 10.0 s vs the native path's
10 s cap). **Verdict: reject as a replacement; adopt as a host-scoped fallback where
native 403s / bot-blocks / times out, with its own longer timeout.**

---

## Experiment 2 — Search: **replicates — TinySearch wins again**

150 stories × 2 arms × 3 reps = 900 requests, **900 ok, 0 errors, 0 rate-limit
retries**. Same query construction as the pipeline's `backfill._query_for`.

| Metric | Brave (native) | TinySearch | ratio |
|---|---|---|---|
| **recall@5** | **22.0%** (99/450) | **34.7%** (156/450) | **1.58×** |
| **MRR** | 0.1549 | **0.2852** | **1.84×** |
| recall@10 | *not comparable* (never asked for 10) | 38.7% | — |
| query error rate | 0.0% | 0.0% | — |

**Variance across the 3 reps: zero, on both arms** — search is deterministic over this
corpus too.

Unique stories with at least one hit: Brave **33/150** (every hit at rank ≤5), TinySearch
**58/150** (52 at rank ≤5, 6 more in ranks 6–10). Unlike CyberPulse — where TinySearch's
recall@10 *equalled* its recall@5 — here the extra results beyond rank 5 add 6 stories
(34.7% → 38.7%): a real, if small, long tail this time.

**Decision rule (ab-plan §5): recall@5 ≥ Brave's → PASS.** 1.58× the recall, 1.84× the
MRR, zero errors, free tier. The margin is smaller than CyberPulse's 2.15×, but the
direction and the decisiveness are the same, and the §3.2 home-field bias (Brave
produced the candidate pool CyberPulse chose from) is *weaker* here anyway — Rollout
reaches most primary sources through feed citations, not Brave, so TinySearch is being
scored against a baseline with less of an advantage.

### Sensitivity: the feed-native composition of Rollout's gold

Rollout ingests far more of its corpus through dedicated fetchers than CyberPulse does.
`src/cyberpulse/sources.yaml` pulls **huggingface.co** (a `type: huggingface` fetcher),
arXiv, GitHub releases, Reddit and Mastodon, and every lab/vendor blog and AI outlet via
**RSS**. Under the ab-plan §3.1 rule (exclude hosts that arrive via a dedicated fetcher,
not search) the as-run filter — carried over unchanged from CyberPulse for
comparability — leaves **59 of 206** eligible links on huggingface.co and more on
vendor/AI-outlet hosts: URLs neither engine was ever meant to find, which dilute both
arms toward zero.

Recomputing recall@5 on the **same stored responses**, restricted to draw subsets that
exclude those hosts:

| Filter | drawn stories | Brave recall@5 | TinySearch recall@5 | ratio |
|---|---|---|---|---|
| **A — as-run (CyberPulse's host list)** | 150 | 0.220 | 0.347 | 1.58× |
| **B — A + huggingface.co** | 106 | 0.226 | 0.330 | 1.46× |
| **C — B + lab/vendor-blog and AI-outlet RSS hosts** | 91 | 0.209 | 0.275 | 1.32× |

The advantage narrows as contamination is removed but **never disappears**, and with
87–106 stories per cell it is not a small-sample artefact. A fully strict filter would
leave only ~126 eligible stories — just under the 150 target — so the as-run pass is the
headline and this table is the honest haircut on it.

---

## Bottom line

| Seam | CyberPulse | Rollout | Verdict |
|---|---|---|---|
| **Search** | TinySearch 2.15× recall@5 | TinySearch 1.58× (1.32× strict) | **Adopt** — replicates |
| **Fetch** | fallback-only (rescues 23/46) | fallback-only (rescues 18/35) | **Adopt as host-scoped fallback only** — replicates |

The CyberPulse result was not an artefact of that corpus. On a different domain, a
different ingestion profile and a 7× smaller ledger, the same two effects appear with
the same sign and the same decision outcome: **TinySearch is a clean win on the search
seam; TinyFetch is a bounded repair on the fetch seam and can never replace
`fetch_readable` because it cannot carry citations.**

### Threats to validity (additions to `docs/findings.md` §7)

1. **Search is a census, not a sample** — 150 of 201 eligible stories (75%). Effect
   size is trustworthy; a confidence interval is not.
2. **Feed-native gold composition** — see the sensitivity table above; the as-run
   headline overstates the margin by ~16%.
3. **Live pages drift** — Rollout published 2026-08…10; a URL fetched today is not the
   page published then. Both arms see the same drift.
4. **`published_date` asymmetry** — a property of Rollout's engine (native cannot
   extract it), not of the provider comparison; it inflates TinyFetch's apparent edge
   on the recency gate and is stated rather than scored.

### Reproduction

```
set -a; . ~/.hermes/.env; . ~/repos/rollout/.env; set +a
export TINYTEST_CORPUS=rollout NATIVE_CHECKOUT=~/repos/rollout PYTHONPATH=src
PY=~/repos/rollout/.venv/bin/python       # adapter imports this checkout's cyberpulse
$PY -m tinytest gold        # -> data/rollout/gold.jsonl
$PY -m tinytest fetch-ab    # 200 URLs x 2 arms
$PY -m tinytest search-ab   # 150 stories x 2 arms x 3 reps
$PY -m tinytest analyze     # tables from data/rollout/raw/ only
```

`data/rollout/results/*` regenerates byte-identically from `data/rollout/raw/`.
