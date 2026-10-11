# tinytest

Independent A/B evaluation harness for the **TinyFish** web infrastructure APIs
(TinySearch / TinyFetch) against the fetch and search paths currently used by the
Rollout news-digest pipeline.

This repo is deliberately **separate** from `~/repos/rollout` and
`~/repos/cyberpulse`. It reads their published output and the native extraction
routine as **frozen inputs**; it never modifies either repo.

## The question

> At the provider boundary — search result discovery and page fetch/extraction —
> does TinyFish beat what Rollout uses today, on Rollout's own published workload?

Not: "do the digests come out better." Digest prose is non-reproducible (live
feeds, a rolling item window, LLM sampling at temperature 0.3), so a digest-level
diff cannot attribute any difference to the provider. This harness measures the
provider boundary only.

## Two experiments

| # | Experiment | Native arm | Candidate arm | Gold truth |
|---|---|---|---|---|
| 1 | Fetch | in-house `httpx` GET + BeautifulSoup extraction (vendored, pinned) | TinyFetch API | Rollout's published coverage URLs |
| 2 | Search | Brave Search API | TinySearch API | the URL Rollout actually published for each story |

Full design, metrics, decision rules and threats to validity:
[`docs/ab-plan.md`](docs/ab-plan.md).

Implementation breakdown for the coding harness:
[`docs/implementation-plan.md`](docs/implementation-plan.md).

## Status

- [x] Repo scaffolded
- [x] A/B plan documented
- [x] Implementation plan documented
- [ ] TinyFish API key provisioned (blocker — see `docs/ab-plan.md` §8)
- [ ] Harness implemented
- [ ] Experiments run
- [ ] Report written

## Layout

```
docs/           A/B plan + implementation plan
src/tinytest/   harness source
data/           gold-set extracts, raw API responses, results (gitignored)
```

`data/` is gitignored: it holds raw third-party response bodies and API payloads.