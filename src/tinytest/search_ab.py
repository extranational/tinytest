"""Experiment 2 runner: Brave vs TinySearch, identical query per story, 3 reps.

Raw records go to ``data/raw/search/<fingerprint>.<arm>.rep<k>.json``; existing records
are skipped so a partial run resumes. Metrics are computed from these files only.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Callable

from tinytest import config

ARMS = ("brave", "tinyfish")


def draw_stories(gold_search_rows: list[dict], n: int = config.SEARCH_N,
                 seed: int = config.SEED) -> list[dict]:
    """Deterministic draw of ``n`` stories from the search-eligible gold set.

    One entry per story: fingerprint, headline, query and ``gold_urls`` (every eligible
    published URL of that story; a hit on any counts).
    """
    stories: dict[str, dict] = {}
    for row in sorted(gold_search_rows, key=lambda r: (r["fingerprint"], r["url"])):
        entry = stories.setdefault(
            row["fingerprint"],
            {"fingerprint": row["fingerprint"], "headline": row["headline"],
             "query": row["query"], "gold_urls": [], "host": row["host"]},
        )
        if row["url"] not in entry["gold_urls"]:
            entry["gold_urls"].append(row["url"])
    ordered = [stories[k] for k in sorted(stories)]
    return sorted(random.Random(seed).sample(ordered, min(n, len(ordered))),
                  key=lambda s: s["fingerprint"])


def raw_path(raw_dir: Path, fingerprint: str, arm: str, rep: int) -> Path:
    return raw_dir / f"{fingerprint}.{arm}.rep{rep}.json"


def run(
    stories: list[dict],
    clients: dict[str, object],
    *,
    reps: int = config.SEARCH_REPS,
    raw_dir: Path = config.RAW_SEARCH_DIR,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """``clients`` maps arm name -> object with ``search(query)`` (TinyFish: pass a
    wrapper fixing ``count``). Per-call failures are recorded, never raised."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    counts = {"done": 0, "skipped_existing": 0, "errors": 0}
    for rep in range(1, reps + 1):
        for story in stories:
            for arm, client in clients.items():
                path = raw_path(raw_dir, story["fingerprint"], arm, rep)
                if path.exists():
                    counts["skipped_existing"] += 1
                    continue
                rec = {"arm": arm, "fingerprint": story["fingerprint"], "rep": rep,
                       "query": story["query"]}
                try:
                    resp = client.search(story["query"])
                    rec.update(ok=True, error=None, http_status=resp["http_status"],
                               results=resp["results"], n_requested=resp["n_requested"],
                               retries_429=resp["retries_429"], wall_ms=resp["wall_ms"],
                               raw=resp["raw"])
                except Exception as exc:
                    counts["errors"] += 1
                    rec.update(ok=False, error=f"{type(exc).__name__}: {exc}",
                               http_status=getattr(exc, "status", None), results=[],
                               n_requested=getattr(client, "count", None),
                               retries_429=getattr(exc, "retries_429", 0), wall_ms=None,
                               raw=None)
                    log(f"search error arm={arm} rep={rep} story={story['fingerprint']}: "
                        f"{rec['error']}")
                path.write_text(json.dumps(rec, sort_keys=True, ensure_ascii=False),
                                encoding="utf-8")
                counts["done"] += 1
    return counts


class TinyFishSearchArm:
    """Adapter giving TinyFishClient the same ``search(query)`` shape as BraveClient."""

    def __init__(self, client, count: int = config.TINYFISH_SEARCH_COUNT):
        self._client, self.count = client, count

    def search(self, query: str) -> dict:
        return self._client.search(query, count=self.count)
