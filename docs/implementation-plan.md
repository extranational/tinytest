# Implementation Plan — tinytest

Executable breakdown of [`docs/ab-plan.md`](ab-plan.md). Written for a coding
harness to implement unsupervised; every milestone has objective acceptance
criteria that a test or a recorded artifact can check.

Repo: `~/repos/tinytest` (uv-managed; run via `.venv/bin/python` or
`uv run --directory ~/repos/tinytest …`).

---

## Ground rules for the implementer

1. **Never modify `~/repos/cyberpulse` or `~/repos/rollout`.** They are read-only
   inputs. No edits, no commits, no config changes, no new files in either tree.
2. The native baseline is a **host adapter**, `src/tinytest/native_adapter.py`
   (committed — it is our code, and no CyberPulse source is copied into this public
   repo). It imports CyberPulse from a checkout (`CYBERPULSE_PATH`) pinned to the SHA in
   `src/tinytest/PINNED.md` and refuses to run if the checkout's HEAD does not match
   (see `docs/host-answers.md`). It runs only on the host; the container cannot read
   CyberPulse.
3. **There is no network in the container, and you must not attempt to install
   dependencies.** Write the TinyFish/Brave clients fully, but never call them from
   inside the container — the orchestrator runs every live call on the host. The
   `pyproject.toml` dependencies (`httpx`, `beautifulsoup4`, `lxml`, `python-dotenv`)
   are declared for the host runner; a policy denial on an install or a probe command
   means *use the corresponding tool*, not that the environment is broken. In-container
   verification is limited to what stdlib Python can execute (`python -m compileall`,
   AST checks, stdlib-only tests); the orchestrator runs `pytest` on the host.
4. Secrets only via `.env` (gitignored). Never write a key into a tracked file or
   a log.
5. `data/` is gitignored and holds gold sets, raw responses and results. Raw
   third-party bodies must never be committed.
6. If a decision in `docs/ab-plan.md` §8 is still unresolved when it blocks you,
   **stop and record the question** in `docs/open-questions.md` with the options
   and your recommended default — do not guess silently.
7. Every run must be deterministic given the frozen gold set, except the search
   experiment's live results (3 reps recorded).

---

## Target layout

```
src/tinytest/
  __init__.py
  config.py        # env loading, paths, constants, thresholds
  native_adapter.py # host adapter: imports pinned CyberPulse, emits one JSON record per URL
  tinyfish.py      # TinyFish Search + Fetch clients (httpx), rate limiting, retry
  brave.py         # Brave Search client (native search arm)
  gold.py          # build/freeze the gold sets from Rollout's published output
  fetch_ab.py      # Experiment 1 runner
  search_ab.py     # Experiment 2 runner
  metrics.py       # signal ratio, recall@k, MRR, link/anchor coverage
  report.py        # results -> data/results/*.csv|json
  cli.py           # entry points: gold, probe, fetch-ab, search-ab, analyze
tests/
  test_metrics.py
  test_gold.py
  test_tinyfish.py   # request-shape tests, no live calls
  fixtures/          # small hand-built HTML + canned API payloads
```

---

## M0 — Freeze inputs (no API key needed)

**Tasks**

1. `git init` the repo; commit the scaffold and both docs.
2. Record CyberPulse's current `git rev-parse HEAD` and the sha256 of
   `src/cyberpulse/pipeline/enrich.py` + `src/cyberpulse/pipeline/websearch.py` into
   `src/tinytest/PINNED.md`.
3. `native_adapter.py` (replaces the earlier vendoring plan): own `httpx` GET, the real
   `_text_and_links`, `PAGE_CHARS` truncation, screening disabled via
   `cfg.screen["enabled"] = False`. Brave request params live in `config.py`.
4. `gold.py` builds the gold set from `~/repos/cyberpulse/state/cyberpulse.db`
   (`story_links`, `stories`) cross-checked against
   `~/repos/vrsweb/content/cyber-hose/*.mdx` frontmatter and body links; writes
   `data/gold.jsonl` and `data/gold_summary.json` (counts by role, host, month).
   Open the DB **read-only** (`?mode=ro`). Paths come from `GOLD_DB` /
   `GOLD_CONTENT_DIR` in `config.py` so the builder re-points at rollout (or any
   sibling) with an env change only.
5. Apply the §3.1 search-eligibility filter into `data/gold_search.jsonl`;
   `data/gold.jsonl` keeps everything for the fetch experiment.

**Acceptance**

- `uv run python -m tinytest gold` prints role/host/month counts matching the numbers
  in `docs/ab-plan.md` §2 (6,183 links; 5,233 coverage / 713 original / 237 seed;
  6,152 distinct URLs; 475 hosts; 5,221 stories), with any discrepancy explained.
- `data/gold.jsonl` is stable across two consecutive runs (byte-identical).
- CyberPulse and Rollout both report `git status --porcelain` clean.

---

## M1 — Provider smoke probe (needs `TINYFISH_API_KEY`)

Resolves the one empirical unknown that gates the fetch design.

**Tasks**

1. `tinyfish.py`: `fetch(urls, *, fmt="html", links=True, ttl=0)` and
   `search(query, *, count=10)`, with 429 backoff and explicit error surfacing.
2. `uv run python -m tinytest probe --url <a real Rollout-published URL>` →
   one live TinyFetch call with `format:"html"`, `links:true`, plus one live
   TinySearch call.
3. Record raw payloads to `data/raw/probe/` and write `docs/tinyfish-probe.md`
   answering, with evidence:
   - Does the response include a `links` field of `<a href>` values? (Expect yes.)
   - Is anchor text or sentence context recoverable from `format:"html"` output by
     the vendored local link-extraction? (This is the load-bearing question.)
   - Measured latency for a live fetch of a JS-heavy page.
   - Rate-limit headers / behaviour observed.

**Acceptance**

- `docs/tinyfish-probe.md` exists and answers each question with a pasted excerpt
  from the raw payload.
- If the anchor/sentence context is NOT recoverable, stop and escalate: the fetch
  experiment's premise changes (record in `docs/open-questions.md`), because the
  citation channel cannot be preserved.

---

## M2 — Fetch A/B runner

**Tasks**

1. `fetch_ab.py`: draw the host-stratified N=200 sample deterministic from the seed
   in `config.py`; for each URL call both arms; persist raw responses under
   `data/raw/fetch/<sha1>.{native,tinyfish}`.
2. `metrics.py`: `content_overlap` (native-sentence recall in the TinyFish text),
   `extract_chars`, `link_url_count`, `published_date_present`, `failure_class` —
   computed identically from each arm's stored raw output. **Do not implement
   `signal_ratio`**: TinyFish returns only boilerplate-stripped content, so
   `body_text` does not exist on that arm (see `docs/tinyfish-probe.md`).
3. Respect TinyFish limits: ≤10 URLs/request, ≤150 URLs/min, ≤1,000 URLs/day.
   Log any 429 and back off.
4. Compare per-host success rate against CyberPulse's recorded `fetch_failures`
   (query the `runs` summaries directly; count them, don't assume) as the native
   baseline; write `data/results/fetch.csv`.

**Acceptance**

- 200 URLs attempted, both arms, zero unhandled exceptions.
- `data/results/fetch.csv` has one row per URL with both arms' metrics and a
  failure class.
- A unit test proves `content_overlap` on fixture HTML gives the hand-computed value,
  and that a non-HTML response is classified, not crashed on.

---

## M3 — Search A/B runner

**Tasks**

1. `search_ab.py`: N=150 stories from `data/gold_search.jsonl`; run Brave and
   TinySearch, **3 reps each**, identical query string per story.
2. Throttle TinySearch to ≤30 req/min with backoff; store raw payloads to
   `data/raw/search/`.
3. `metrics.py`: `recall_at_5`, `recall_at_10`, `mrr`, query error rate.
4. Write `data/results/search.csv` and `data/results/search_variance.csv`
   (per-metric mean and spread across the 3 reps).

**Acceptance**

- 150 stories × 2 arms × 3 reps accounted for; any missing rep has a logged reason.
- Recall is computed against the exact gold URL (normalised consistently), and a
  fixture test proves a known hit at rank 3 yields MRR = 1/3.

---

## M4 — Analysis and report

**Tasks**

1. `report.py` regenerates both result tables from the stored raw responses (no
   re-fetch), so the report is reproducible from `data/raw/`.
2. Produce `docs/findings.md` from the decision rules in `docs/ab-plan.md` §4 and
   §5, each verdict citing the specific metric and its value. Include the §7
   threats-to-validity section verbatim, updated with anything observed.
3. **Do not** headline a single summary score.

**Acceptance**

- `data/results/*` regenerates byte-identically from `data/raw/` a second time.
- `docs/findings.md` states an explicit adopt / fallback-only / reject verdict for
  **each** of Search and Fetch, each tied to a numbered decision rule.

---

## M5 — Hardening and handover

**Tasks**

1. `pytest` green; `ruff check` clean.
2. README status checklist updated; `docs/ab-plan.md` §8 decisions marked resolved
   or carried forward.
3. A `--limit N` flag on both runners for a cheap re-run, and a documented path to
   re-point the gold builder at `~/repos/cyberpulse` (the stated next step).

**Acceptance**

- Fresh clone + `uv sync` + `uv run python -m tinytest golden` works with no manual
  steps beyond providing `.env`.
- The cyberpulse re-point is documented and requires only a DB path change.

---

## Where each milestone runs (execution split)

The claude-harness container has egress allowlisted to `api.anthropic.com` **only**,
and mounts only the project directory. It therefore cannot reach TinyFish, Brave or
target URLs, and cannot read CyberPulse's DB. Split accordingly:

| Milestone | Runs in | Why |
|---|---|---|
| M0 vendor + PINNED.md | container | pure local file work |
| M0 gold builder **code + tests** | container | against a committed fixture |
| M0 gold **extraction** | host (Hermes) | needs the CyberPulse DB |
| M1 probe | host (Hermes) | needs egress + the API key |
| M2 / M3 **implementation + unit tests** | container | fixtures, no live calls |
| M2 / M3 **live runs** | host (Hermes) | needs egress + key |
| M4 report regeneration | host (Hermes) | needs `data/raw/` |
| M5 tests, lint, docs | container | local |

The harness's acceptance criterion for M2/M3 is therefore **code + unit tests +
a dry-run against fixtures**; the live numbers are produced by the host runner. The
host runner is the same code, invoked outside the container.

---

## Out of scope (do not implement)

- TinyFish Agent or Browser APIs.
- Any change to Rollout's or cyberpulse's source.
- LLM calls of any kind; this harness measures the provider boundary only.
- Publishing anything; the deliverable is `docs/findings.md` plus the result tables.