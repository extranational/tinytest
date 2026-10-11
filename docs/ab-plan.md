# A/B Evaluation Plan — TinyFish Search & Fetch vs the CyberPulse pipeline

Status: **decisions D1–D6 resolved; one execution-split decision outstanding** (§9).
Target system: `~/repos/cyberpulse` (cybersecurity intel digest engine).
Corpus: CyberPulse's published Cyber Hose digests and its story/URL ledger.

> Supersedes the Rollout framing. D1 resolved: the powered run is **cyberpulse**,
> whose corpus is ~7× larger. Rollout remains a one-line re-point
> (`GOLD_DB` / `GOLD_CONTENT_DIR`) if the pilot is ever re-run there.

---

## 1. The question, scoped precisely

CyberPulse's enrichment stage gives each story a summary by (a) **searching** for
candidate coverage URLs, (b) **fetching** those pages, and (c) condensing them.
We are replacing only (a) and (b):

| Layer | Native | Candidate |
|---|---|---|
| Search discovery | Brave Search API (`pipeline/websearch.py`) | TinySearch API |
| Page fetch + extraction | in-house `httpx` GET + BeautifulSoup (`enrich.fetch_readable`) | TinyFetch API |

Explicitly **not** tested: TinyFish Agent, TinyBrowser, the LLM summariser, triage,
screening, or digest rendering. Unchanged on both arms.

**Why not diff re-run digests.** CyberPulse is not reproducible: feeds are live, the
item table is a rolling 90-day window, and every stage samples an LLM at temperature
0.3. A digest-level diff would be dominated by those sources and the provider change
would be unidentifiable. Measure at the provider boundary.

---

## 2. Target corpus (cyberpulse, measured 2026-10-10)

From `~/repos/cyberpulse/state/cyberpulse.db` and `~/repos/vrsweb/content/cyber-hose/`:

- published digests: **819** (2026-07-15 → 2026-10-10, ~12 weeks)
- stories in ledger: **5,221**
- published links (`story_links`): **6,183** rows — 5,233 `coverage`, 713 `original`,
  237 `seed`
- distinct published URLs: **6,150** (normalised; 6,152 raw — CyberPulse keys URLs by
  `dedupe.item_key`, which lowercases and drops fragments/trailing slashes/`utm_*`/`ref`)
- distinct hosts: **479** (normalised)
- stories in `stories` table: 5,221; distinct fingerprints present in `story_links`:
  5,239 — **18 fingerprints are orphans** (no `stories` row). They carry no headline and
  so no search query; they are excluded from the search gold set (see §3.1) but kept in
  the fetch gold set.
- item window in DB: 2026-07-15 → 2026-10-10 (23,589 items; retention 90 days)
- search-eligible after the §3.1 filter: **618 links / 610 stories** (≥150 available)

> The distinct-URL and host figures above are the *normalised* values, which supersede
> the raw counts first written here. `tinytest gold` prints both and flags the
> difference, so a future mismatch is visible rather than silent.

Host concentration (top): **cve.threatint.com 2,517 (41% of all links)**,
cybersecuritynews.com 457, reddit.com 268, securityboulevard.com 237, arxiv.org 236,
bleepingcomputer.com 203, thehackernews.com 202, securityweek.com 163,
thehackerwire.com 149, infosec.exchange 109, mastodon.social 106, github.com 71.

**Two stratification consequences:**

1. **cve.threatint.com is 41% of the corpus.** A uniform random draw would be a
   CVE-advisory-host test. The fetch sample must **cap** it (target ≤10% of the
   sample) and spread the remainder across publisher hosts.
2. `reddit.com`, `mastodon.social`, `infosec.exchange`, `arxiv.org`, `github.com` and
   `x.com` arrive via dedicated feed fetchers, not web search (§3.1).

### 2.1 Sample sizes (D1 resolved)

N=200 URLs (Fetch) and N=150 stories (Search), drawn from the cyberpulse corpus:

- Fetch: 200 / 6,152 distinct URLs = **3.3%** — a genuine sample.
- Search: 150 / 5,221 stories = **2.9%** — a genuine sample.

Comfortable headroom for host stratification without thin cells — unlike Rollout,
where the same N was a 23%/28% census.

---

## 3. Gold set construction

Built by `tinytest gold`, reading **CyberPulse's published output** — not the DB's
item table, which is a rolling window and may have pruned relevant rows.

Sources, in priority order:

1. `story_links` (fingerprint, url, role, first_published).
2. Each published `.mdx` frontmatter `top_stories[].url` and the body's
   `[Source: …]` / `[Coverage: …]` links — cross-check the ledger against what shipped.

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

Not every published URL was *search-discoverable*. Feed-native sources —
reddit.com, mastodon.social, infosec.exchange, arxiv.org, github.com, x.com — arrive
via dedicated fetchers. Scoring Brave and TinySearch on those would count both as
failures for a URL neither was ever meant to find.

Rules:
- The **Search** gold set keeps only rows on a web-discoverable host (excludes the
  above), and preferentially `role == "original"` — the 713 source-first citations,
  i.e. exactly the URLs the search channel exists to find. 713 ≫ 150, so the search
  sample draws entirely from `original`-role links.
- The **Fetch** gold set keeps every host: fetching a GitHub release page, an arXiv
  paper or a CVE advisory is a legitimate fetch job.

### 3.2 Known bias, stated up front

Brave produced the candidate pool CyberPulse historically chose from, so the
published URL is **partly Brave's own selection** — home-field advantage for the
native arm. This does not invalidate the test; it raises the bar: TinySearch must
**match or beat** Brave, not merely approach it.

---

## 4. Experiment 1 — Fetch

**Arms**

- `native` — vendored copy of CyberPulse's extraction routine, pinned to a recorded
  git SHA: `httpx` GET (10s timeout) → content-type gate → BeautifulSoup text + links
  → truncate to `PAGE_CHARS`.
- `tinyfish` — `POST https://api.fetch.tinyfish.ai`,
  `{"urls":[…], "format":"html", "links":true, "ttl":0}`.

**Citation channel: FAIL, by construction (measured 2026-10-10, `docs/tinyfish-probe.md`).**
CyberPulse's `PageLink` carries `(url, anchor, sentence, page_sentences)`, feeding the
R5 corroboration that produces `original_source_url` — 713 of 6,183 published links are
`original`. A live probe settled the question the wrong way:

- TinyFetch `links: true` returns **142 bare URLs** — strings only, no anchor, no
  position, no sentence.
- TinyFetch `format: "html"` returns **semantic** HTML, not the source DOM: `<a` count
  **0**, `href` count **0** (only `article, h1, h6, li, p, ul` survive).

So anchor text and sentence context are **not recoverable**, and the originally planned
mitigation — re-extracting links from TinyFish's rendered HTML — is impossible.
TinyFetch **cannot** be a drop-in replacement for `fetch_readable`. This is recorded as
a **binary gate that TinyFish fails**, not a metric to be traded off.

**Metric substitution.** The planned **signal ratio** (`main_text / body_text`) is
**undefined for the TinyFish arm**: TinyFish returns only boilerplate-stripped
extraction, so there is no `body_text` to divide by. Replace it with **content overlap
with the native arm on the same page** — the fraction of the native arm's extracted
sentences present in TinyFish's text — plus absolute extracted characters. That is
well-defined for both arms and measures directly whether TinyFish loses content the
pipeline needs.

**What the probe found in TinyFish's favour** (recorded for completeness): 200 OK,
867 ms server-reported latency on a static article, and `author` + `published_date`
returned directly — which the native path only obtains by parsing JSON-LD. Useful on
the `max_source_age_days: 14` recency gate.

**Screening excluded from both arms (D3 approved).** `fetch_readable` runs SafePrompt
on every page (up to 8,000 chars of third-party text egressed to a hosted API, 600s/run
budget). Screening is provider-independent; including it would re-consume SafePrompt
quota, add ~5s/page latency noise, and confound the extraction comparison.

**Protocol**

1. Draw the host-stratified N=200 sample, `cve.threatint.com` capped at ≤10%.
2. Both arms fetch each URL in the same session, both live: native as-is; TinyFish
   `ttl: 0` (no cache).
3. Store both raw responses to `data/raw/fetch/`, keyed by URL hash.
4. TinyFish limits: ≤10 URLs/request, ≤150 URLs/min, 1,000 URLs/day free — 200 URLs
   fits one day's allowance.

**Metrics (per arm)**

- success/failure, with failure class (timeout, DNS, HTTP status, anti-bot, empty body,
  non-HTML)
- extracted characters; **content overlap with the native arm** (fraction of native's
  extracted sentences present in TinyFish's text), computed identically for both arms
  from the stored raw response
- outbound links recovered as **URLs** (the `links` list) — the anchor/sentence channel
  is separately gated, above
- latency p50 / p90 / p95
- `published_date` availability (native must parse JSON-LD; TinyFish returns it)
- per-host success rate vs the native `fetch_failures` baseline in CyberPulse's
  `runs` summaries

**Decision rule (revised — fallback-only).** The drop-in-replacement outcome is closed:
the citation channel fails by construction. TinyFetch is adoptable only as a
**host-scoped fallback**, considered for hosts where native `fetch_readable` fails
(403 / bot-block / timeout), and only if:
1. its success rate on the failing hosts is materially above native's; and
2. content overlap shows it did not lose the substance of the page; and
3. the pipeline can tolerate a fetch-without-citations path there — the source-first
   attribution simply does not exist on those pages.

If (3) is unacceptable, the verdict is **reject for fetch entirely**, and the useful
residue is the Search result alone.

---

## 5. Experiment 2 — Search

**Arms**

- `native` — Brave `GET https://api.search.brave.com/res/v1/web/search`, `count=5`,
  `freshness="pm"` (CyberPulse's live parameters).
- `tinyfish` — `GET https://api.search.tinyfish.ai?query=…&location=US`, up to 10 results.

**Query construction.** Identical to `discovery/backfill.py:_query_for`, so both arms
receive the same string. Frozen into `data/gold_search.jsonl`.

**Gold truth.** The URL CyberPulse actually published for that story (§3.1-filtered).

**Protocol**

- N=150 stories from the Search-eligible gold set.
- **3 repetitions per arm** (search is the one live, non-deterministic part).
- TinySearch free tier: 30 req/min, 12k/day → 150 × 3 = 450 requests, throttled to
  ≤30/min (~15 min) with backoff on 429.
- Raw responses to `data/raw/search/`.

**Metrics (per arm)**

- **recall@5**, **recall@10** of the published gold URL
- **MRR** (mean reciprocal rank of the gold URL)
- query error rate; rate-limit incidents
- exploratory for TinySearch: would `domain_type`/date filters have changed the outcome?

**Decision rule.** Candidate replacement if recall@5 ≥ Brave's, subject to §3.2. If
materially below, still worth a **zero-cost fallback slot** in a provider chain (free
tier), but not a drop-in replacement.

---

## 6. Analysis & reporting

- Per-metric comparison with N, and **3-rep variance reported** for Search.
- No aggregate "score" — each metric separate, each decision rule applied explicitly.
- Output: `data/results/*.csv|json` + a hand-written `docs/findings.md`. Every claim
  traceable to a stored raw response.
- **Report the negatives.** TinyFish Agent-mode fabrication (vendor-acknowledged) and
  the paywalled/aggregator fetch unreliability are recorded as context even though
  Agent mode is out of scope.

---

## 7. Threats to validity

1. **Live pages drift** — a URL fetched today is not the page published in July. Both
   arms see the same drift, so the comparison is fair, but absolute success rates
   understate what either would have achieved historically.
2. **Host concentration** — cve.threatint.com is 41%; mitigated by the cap, disclosed.
3. **Brave home-field bias** on Search (§3.2).
4. **N is small relative to the effect you'd want to detect** — 200/150 with 3 reps
   detects a large effect, not a subtle one. Do not over-read sub-5% differences.
5. **TinyFetch caching** — `ttl: 0` forces live fetches; the native path has no cache,
   so the arms stay comparable.
6. **CyberPulse's own `max_fetches_per_story: 5`** shapes which URLs are ever
   candidates. The A/B re-uses the *published* URLs, so it tests the provider on the
   URLs the pipeline actually chose — not on its ability to find different ones.

---

## 8. Decisions (resolved)

- **D1 — corpus**: cyberpulse (§2). Resolved.
- **D2 — API key**: Brett holds one. Must be placed so the *host runner* can read it
  (§9) — into `~/repos/tinytest/.env`, never into chat or git. Resolved, pending placement.
- **D3 — screening excluded from both fetch arms**: approved.
- **D4 — coding harness**: claude-harness; escalation channel Slack `C0C84BQUDL6`.
- **D5 — Brave bias caveat + search-eligibility filter**: accepted.
- **D6 — repo**: `extranational/tinytest`, **public** — so `.gitignore` must keep
  `data/` and `.env` out, and no raw third-party bodies are ever committed.

---

## 9. Execution split (decided: option A)

The claude-harness container is egress-allowlisted to `api.anthropic.com` **only**
(verified: `example.com` → 403 filtered; no proxy → DNS failure). It cannot call
TinyFish, Brave, or target URLs, and with only the project dir mounted it cannot read
CyberPulse's DB or `~/repos/vrsweb`.

**Decided (A):** the harness writes **code + unit tests** in the container; the **host**
(Hermes) extracts the gold set, runs the M1 probe (done) and both experiments, and
writes `docs/findings.md`. Same code, invoked outside the container.

Consequence for the brief: the harness makes **no live API calls**, attempts **no
dependency installs** (no network; policy-denied), and its own verification is limited
to what the image's stdlib Python can execute. The orchestrator runs `pytest` on the
host.