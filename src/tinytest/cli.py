"""Entry points: gold, probe, fetch-ab, search-ab, analyze.

Everything except ``gold`` and ``analyze`` makes live calls and must run on the host.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tinytest import config


def _cmd_gold(args) -> int:
    from tinytest import gold

    summary = gold.build(Path(args.db), Path(args.content_dir), config.DATA_DIR)
    print(f"links={summary['links']} distinct_urls={summary['distinct_urls']} "
          f"hosts={summary['hosts']} stories_with_links={summary['stories_with_links']} "
          f"stories_in_ledger={summary['stories_in_ledger']}")
    print("by_role:", json.dumps(summary["by_role"]))
    print("by_month:", json.dumps(summary["by_month"]))
    print("top hosts:", json.dumps(dict(list(summary["by_host"].items())[:12])))
    print(f"search-eligible: {summary['search_eligible_links']} links / "
          f"{summary['search_eligible_stories']} stories")
    print("crosscheck:", json.dumps({k: v for k, v in summary["crosscheck"].items()
                                     if not k.endswith("_sample")}))
    if summary["discrepancies_vs_ab_plan"]:
        print("DISCREPANCIES vs docs/ab-plan.md section 2 (explain these):")
        for line in summary["discrepancies_vs_ab_plan"]:
            print("  -", line)
    else:
        print("counts match docs/ab-plan.md section 2")
    return 0


def _cmd_probe(args) -> int:
    from tinytest.tinyfish import TinyFishClient

    client = TinyFishClient()
    config.RAW_PROBE_DIR.mkdir(parents=True, exist_ok=True)
    fetched = client.fetch([args.url], fmt="html", links=True, ttl=0)
    searched = client.search(args.query, count=10)
    for name, payload in (("fetch", fetched), ("search", searched)):
        (config.RAW_PROBE_DIR / f"{name}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"fetch: http={fetched['http_status']} wall_ms={fetched['wall_ms']:.0f} "
          f"429s={fetched['retries_429']}")
    print(f"search: http={searched['http_status']} results={len(searched['results'])} "
          f"wall_ms={searched['wall_ms']:.0f}")
    print(f"raw payloads in {config.RAW_PROBE_DIR}")
    return 0


def _cmd_fetch_ab(args) -> int:
    from tinytest import fetch_ab, gold
    from tinytest.tinyfish import TinyFishClient

    sample = fetch_ab.draw_sample(gold.read_jsonl(config.GOLD_PATH))[: args.limit]
    print(f"sample: {len(sample)} URLs")
    counts = fetch_ab.run(sample, tinyfish_client=TinyFishClient())
    print("fetch-ab:", json.dumps(counts))
    return 0


def _cmd_search_ab(args) -> int:
    from tinytest import gold, search_ab
    from tinytest.brave import BraveClient
    from tinytest.tinyfish import TinyFishClient

    stories = search_ab.draw_stories(gold.read_jsonl(config.GOLD_SEARCH_PATH))[: args.limit]
    print(f"sample: {len(stories)} stories x {config.SEARCH_REPS} reps x 2 arms")
    clients = {"brave": BraveClient(),
               "tinyfish": search_ab.TinyFishSearchArm(TinyFishClient())}
    counts = search_ab.run(stories, clients)
    print("search-ab:", json.dumps(counts))
    return 0


def _cmd_analyze(args) -> int:
    from tinytest import report

    written = report.write_all(fetch_limit=args.limit, search_limit=args.limit,
                               baseline_db=config.GOLD_DB)
    for name, path in written.items():
        print(f"{name}: {path}")
    if not written:
        print("nothing to analyze: run `gold` first", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tinytest")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("gold", aliases=["golden"], help="build gold sets (host: needs DB)")
    p.add_argument("--db", default=str(config.GOLD_DB))
    p.add_argument("--content-dir", default=str(config.GOLD_CONTENT_DIR))
    p.set_defaults(fn=_cmd_gold)

    p = sub.add_parser("probe", help="one live TinyFetch + TinySearch call (host)")
    p.add_argument("--url", required=True)
    p.add_argument("--query", default="ransomware attack disclosed this week")
    p.set_defaults(fn=_cmd_probe)

    for name, fn in (("fetch-ab", _cmd_fetch_ab), ("search-ab", _cmd_search_ab)):
        p = sub.add_parser(name, help="live experiment run (host)")
        p.add_argument("--limit", type=int, default=None, help="first N of the sample")
        p.set_defaults(fn=fn)

    p = sub.add_parser("analyze", help="regenerate result tables from data/raw/")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(fn=_cmd_analyze)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
