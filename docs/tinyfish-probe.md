# TinyFish API probe — M1 results

Run: 2026-10-10, host (Hermes), live calls. Raw payloads: `data/raw/probe/`.
Key: `TINYFISH_API_KEY` in `~/.hermes/.env` (host only; never in this repo).

## Verdict on the load-bearing question: **FAIL — anchor text and sentence context are not recoverable**

The fetch experiment's original design assumed TinyFish's `format: "html"` output
could be fed through CyberPulse's own link extraction, preserving the
`PageLink(url, anchor, sentence, page_sentences)` structure that the R5 corroboration
in `sourcing` uses to produce `original_source_url`.

**It cannot.** Evidence:

| Probe | Finding |
|---|---|
| `links: true` | Returns **142 bare URLs**. Strings only — no anchor, no position, no sentence. |
| `format: "html"` | Returns **semantic** HTML, not the source DOM. `<a` count: **0**. `href` count: **0**. Tags present: `article, h1, h6, li, p, ul` only. |
| Markdown-style inline links in the html output | **0** (`](http` matches). |

So TinyFetch strips every `<a>` element from the extracted content and reports the
link *targets* separately as a flat URL list. There is no channel through which
anchor text or the sentence surrounding a link can be recovered.

Impact: 713 of CyberPulse's 6,183 published links are `original`-role, produced by
exactly the citation channel that depends on those fields. **TinyFetch cannot serve
as a drop-in replacement for `fetch_readable`.**

## Other probe findings (all favourable)

| Property | Result |
|---|---|
| `POST https://api.fetch.tinyfish.ai`, HTTP | **200**, `errors: []` |
| Result keys | `url, final_url, title, description, language, text, author, published_date, links, latency_ms, format` |
| Latency (static news article, live/`ttl:0`) | **867 ms** (server-reported `latency_ms` 867.4; wall clock 1.42 s) — far better than the advertised p50 ~12 s, which applies to JS-heavy pages |
| Extracted text length | 3,023 chars, article-content only |
| `published_date` | **2026-10-10T10:16:17-04:00** — present |
| `author` | **Bill Toulas** — present |
| `GET https://api.search.tinyfish.ai` | **200**, `total_results: 10`, 10 returned, 1.07 s |
| Search result shape | `position, site_name, title, snippet, url` — matches CyberPulse's `SearchResult(url, title, snippet)` |
| Search quality spot-check | Rank 1 `crowdstrike.com` (the primary researcher in the story), then reuters, nytimes, technology.org, wsj — plausible and source-first |

Two of these matter downstream:

- **`published_date` is returned.** CyberPulse's recency gate
  (`enrichment.max_source_age_days: 14`) needs a publish date, and the native path
  derives one by parsing JSON-LD out of raw HTML. TinyFetch hands it over directly —
  a genuine advantage on that gate.
- **The extracted text is already boilerplate-stripped**, so TinyFetch has no
  `body_text` to divide by. The planned **signal ratio** metric
  (`main_text / body_text`) is **undefined for the TinyFish arm** and must be
  replaced (see below).

## Consequence for Experiment 1

The experiment is still worth running — but it can no longer answer "is TinyFetch a
drop-in replacement?" (answer: no). It answers the narrower, still-valuable
question: **is TinyFetch a viable fallback for the hosts where the native path fails?**

Revised framing:

1. **Citation-channel preservation: FAIL, by construction.** Recorded as a binary
   gate, not a metric. No amount of success-rate advantage recovers it.
2. The metrics that remain genuinely comparable: success/failure by host, latency
   p50/p90/p95, extracted characters, link-URL recovery (does the `links` list contain
   the same outbound URLs the native path found), and `published_date` availability.
3. **Metric substitution.** Instead of `main_text/body_text`, measure
   **content overlap with the native arm** on the same page — e.g. the fraction of
   native's extracted sentences present in TinyFish's text — plus absolute chars.
   That is well-defined for both arms and directly measures "did TinyFish lose the
   content the pipeline needs?"
4. **Decision rule becomes:** adopt TinyFish for the fetch seam only as a
   **host-scoped fallback** — for hosts where native `fetch_readable` fails (403 /
   bot-block / timeout). Even there it may only contribute *text*, never citations,
   so the pipeline's source-first attribution is unavailable on those pages.

Search (Experiment 2) is **unaffected** — the result shape matches
`SearchResult(url, title, snippet)` exactly, and the probe surfaced a strong
source-first top hit.