# Host answers — corrections and ground truth

From the orchestrator, on the host, with real access to CyberPulse's source and DB.
This file **supersedes** `docs/open-questions.md`; read it before continuing.

Evidence for the two defects named below was produced by running your code, not by
reading it.

---

## Change of mechanism: the native baseline is now an ADAPTER, not a vendored module

`scripts/vendor_native.py` **fails on the real source**. Observed:

```
$ .venv/bin/python scripts/vendor_native.py ~/repos/cyberpulse
enrich.py:fetch_readable: unparsable after --strip (invalid syntax. Maybe you meant
'==' or ':=' instead of '='? (<unknown>, line 29)); refine --strip patterns or
vendor by hand
```

Cause: the default `--strip` pattern deletes a line inside a multi-line
`screen.screen_text(...)` call, leaving the surrounding block unparsable. Even if
fixed, `fetch_readable`/`_text_and_links` depend on `cyberpulse.pipeline.sourcing`
(`split_sentences`, `registrable`, `PageLink`), which the extractor refuses as an
internal import by design.

**Decision (orchestrator's): delete the vendoring approach.** Replace it with a host
adapter that **imports CyberPulse directly, pinned to the recorded SHA**. This is
simpler, more faithful, and reproducible (the SHA is immutable) — and it keeps
CyberPulse's source out of this public repo entirely.

To implement:

1. **Delete** `scripts/vendor_native.py` and `tests/test_vendor_native.py`. Remove the
   `src/tinytest/native.py` line from `.gitignore` and the "generated, not committed"
   wording from `docs/implementation-plan.md` ground rule 2 — replace with a sentence
   describing the adapter.
2. **Add** `src/tinytest/native_adapter.py` (committed — it is our code, not a copy):

   ```python
   # Inputs: CYBERPULSE_PATH (a checkout at the SHA in PINNED.md), url.
   # Insert CYBERPULSE_PATH/src on sys.path, then:
   from cyberpulse.config import load_config
   from cyberpulse.pipeline import enrich
   from cyberpulse.pipeline.enrich import _text_and_links, PAGE_CHARS

   cfg = load_config(Path(path))          # real config from the checkout
   cfg.screen["enabled"] = False          # D3: screening excluded from both arms
   ```

   The adapter must do its **own** `httpx` GET (follow_redirects, 10s timeout) so the
   result carries `status` and `content_type` for precise failure classification, then
   feed the fetched HTML through CyberPulse's real `_text_and_links(html, url)`,
   truncating text to `PAGE_CHARS`. Return JSON with at least:
   `status, content_type, text, links, title, published_date, latency_ms`, where
   `links` is the list of `PageLink`s as dicts including **`url`, `anchor`,
   `sentence`** — the native arm's whole point is that these exist, so the citation
   channel must be visible in the stored raw output.
3. Expose it to `fetch_ab` as a CLI: `python -m tinytest.native_adapter <url>` emitting
   one JSON object on stdout. `native_fetch(url)` invokes that subprocess and parses
   it. Keep the resumable `data/raw/fetch/<sha1>.native` behaviour.
4. Note the SHA in `PINNED.md` is what makes this reproducible; the adapter must
   **refuse to run** (non-zero, clear message) if the checkout's `git rev-parse HEAD`
   does not start with the pinned value.

---

## Q1 — `gold.query_for`: your default is wrong

Real source, `src/cyberpulse/discovery/backfill.py`:

```python
def _query_for(row) -> str:
    """Reconstruct the live enrichment query for one story."""
    query = row["headline"].replace("-", " ")
    cve_ids = json.loads(row["cve_ids"] or "[]")
    if cve_ids:
        query += " " + " ".join(cve_ids)
    return query
```

So: `headline.replace("-", " ")`, then append every CVE id space-separated when the
story has any. `read_ledger` must select `s.cve_ids` alongside `s.headline` and pass
it in. Please implement the real logic, not the whitespace-collapsed headline.

## Q2 — schema: confirmed, with one correction

- `story_links(fingerprint TEXT, url TEXT, role TEXT, first_published TEXT)` ✓
- `stories(fingerprint TEXT, headline TEXT, cve_ids TEXT DEFAULT '[]', first_reported,
  last_updated, report_count)` ✓ — note `cve_ids` is required by Q1.
- `runs(at, kind, summary, ...)`; the JSON key is exactly `$.fetch_failures` ✓

**Correction:** in CyberPulse `fetch_failures` is a **dict of `{host: count}`**, e.g.
`{"firstpost.com": 1, "beyondtrust.com": 1, "redd.it": 1}` — not a list and not an int.
`native_failure_baseline` currently only handles `int` and `list`, so it will silently
report zero. Add dict handling (`for host, count in failures.items()`).

Also: `summarize()`'s `stories` count is the distinct fingerprints **present in
`story_links`**, which is smaller than the ledger's `stories` table (5,221). Report
both under distinct names (`stories_with_links`, `stories_in_ledger`) so
`discrepancies()` does not raise a false positive.

## Q3 — `fetch_readable` signature: as suspected, and the failure detail is lost

```python
def fetch_readable(url: str, client: httpx.Client, config: Config,
                   telemetry: EnrichTelemetry | None = None) -> Page | None:
```

- `Page` = `@dataclass(text: str, links: list[sourcing.PageLink], published: datetime | None)`.
- It returns `None` on **every** failure and swallows the reason, so it cannot classify
  failures. Hence the adapter does its own GET (see the mechanism change above).
- `PAGE_CHARS = 8000`, `MAX_LINKS_PER_PAGE = 60`.
- `_text_and_links(html: str, base_url: str) -> tuple[str, list[sourcing.PageLink]]`.
- `PageLink` carries `url`, `anchor`, `sentence`, `page_sentences` — confirm all four
  are in the stored native record.

## Q4 — TinyFish request/response: confirmed by live probe

Header `X-API-Key` ✓; envelope `results` / `errors` ✓; per-result keys include
`url, final_url, title, description, language, text, author, published_date, links,
latency_ms, format` ✓. Search takes `query` and `location` (no `count` parameter —
client-side trimming is correct). Fetch also accepts `per_url_timeout_ms` (1–110000);
set it explicitly rather than relying on the default.

## Q5 — `normalize_url`: replace your approximation with the real function

Real source, `store._normalize_url` → `dedupe.item_key`:

```python
url = item.link.strip()
if url:
    url = url.split("#")[0].rstrip("/")
    url = re.sub(r"[?&](utm_[^&]+|ref=[^&]+)", "", url)
    return url.lower()
```

Differences that matter for gold matching: the real function **keeps** `www.`, does
**not** force https, and does **not** sort query params. Yours removes `www.`, rewrites
the scheme and sorts params, so it can match URLs the pipeline treats as distinct (and
vice versa). Replace `metrics.normalize_url` with the exact logic above and update its
docstring — the approximation note is no longer true.

## Q6 — Brave `count=5` / recall@10: decision

Keep Brave at its **live** `count=5` — that is what production actually issues, and the
decision rule is built on recall@5. Therefore:

- Report **recall@5 head-to-head** for both arms (the comparable figure).
- Report TinyFish's **recall@10** separately and label it *not comparable* — Brave was
  never asked for 10.
- Do not spend extra Brave queries chasing a like-for-like recall@10.

---

## Required before you finish

1. Apply every correction above.
2. **Commit your work.** The previous turn left all 26 files staged and nothing
   committed; the turn ended without a commit. Use per-commit identity:
   `git -c user.name="claude-harness" -c user.email="noreply@anthropic.com" commit ...`
   Group into small logical commits with real messages. Do not push.
3. In-container verification stays stdlib-only (`compileall`, AST checks, your stdlib
   tests). The orchestrator runs `pytest`, the adapter against real CyberPulse, and the
   live experiments — state plainly what you could not verify.