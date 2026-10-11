# Open questions

All six questions from the first pass (query construction, DB schema, native
`fetch_readable` signature, TinyFish shapes, URL normalisation, Brave `count`) were
answered by the orchestrator and are **resolved in [`host-answers.md`](host-answers.md)**,
which supersedes this file. Remaining unverified items are listed there and in the
implementation summary:

- `native_adapter.py` has never run against real CyberPulse (HTTP headers, link
  truncation, published-date replay are best guesses until it does).
- `PINNED.md` values are still TODO; the adapter refuses to run until `cyberpulse_head`
  is filled in.
