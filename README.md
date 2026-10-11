# tinytest

Independent A/B evaluation harness for the **TinyFish** web infrastructure APIs
(TinySearch / TinyFetch) against the fetch and search paths used by the **CyberPulse**
cybersecurity intel pipeline.

This repo is deliberately **separate** from `~/repos/cyberpulse` and
`~/repos/rollout`. It reads their published output and the native extraction
routine as **frozen inputs**; it never modifies either repo.

**This repository is public.** `.gitignore` keeps `data/` (raw third-party response
bodies) and `.env` (API keys) out. Never commit either.

## The question

> At the provider boundary — search result discovery and page fetch/extraction —
> does TinyFish beat what CyberPulse uses today, on CyberPulse's own published workload?

Not: "do the digests come out better." Digest prose is non-reproducible (live
feeds, a rolling item window, LLM sampling at temperature 0.3), so a digest-level
diff cannot attribute any difference to the provider. This harness measures the
provider boundary only.

## Two experiments

| # | Experiment | Native arm | Candidate arm | Gold truth |
|---|---|---|---|---|
| 1 | Fetch | in-house `httpx` GET + BeautifulSoup extraction (vendored, pinned) | TinyFetch API | CyberPulse's published coverage URLs |
| 2 | Search | Brave Search API | TinySearch API | the URL CyberPulse actually published for each story |

Full design, metrics, decision rules and threats to validity:
[`docs/ab-plan.md`](docs/ab-plan.md).

Implementation breakdown for the coding harness:
[`docs/implementation-plan.md`](docs/implementation-plan.md).

## Status

- [x] Repo scaffolded
- [x] A/B plan documented (cyberpulse corpus, D1 resolved)
- [x] Implementation plan documented
- [x] Decisions D1–D6 recorded (`docs/ab-plan.md` §8)
- [ ] Execution split decided (`docs/ab-plan.md` §9) — container cannot run the experiments
- [ ] TinyFish API key placed in `~/repos/tinytest/.env` (host runner only)
- [x] Harness implemented (M0–M3) — code + unit tests written; **not executed in the container** (no interpreter access), host must run `pytest`; `native.py` vendoring, gold extraction and live runs are host-only and unverified
- [x] `src/tinytest/PINNED.md` filled; `native_adapter.py` confirmed on the host against pinned CyberPulse
- [ ] Experiments run (host)
- [ ] `docs/findings.md` written

## Layout

```
docs/           A/B plan + implementation plan
src/tinytest/   harness source
data/           gold-set extracts, raw API responses, results (gitignored)
```

`data/` is gitignored: it holds raw third-party response bodies and API payloads.