"""Build and freeze the gold sets from CyberPulse's published output.

Schema confirmed by the orchestrator (docs/host-answers.md Q2):
``story_links(fingerprint,url,role,first_published)`` and ``stories(fingerprint,
headline,cve_ids,...)``. The DB is opened read-only.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from tinytest import config
from tinytest.metrics import host_of, normalize_url

ROLES = ("coverage", "original", "seed")


def query_for(headline: str, cve_ids: str | None = None) -> str:
    """Live enrichment query, as ``discovery/backfill.py:_query_for`` (docs/host-answers.md Q1).

    ``headline`` with ``-`` replaced by a space, then every CVE id from the JSON list in
    ``stories.cve_ids`` appended space-separated.
    """
    query = (headline or "").replace("-", " ")
    ids = json.loads(cve_ids or "[]")
    if ids:
        query += " " + " ".join(ids)
    return query


def read_ledger(db_path: Path) -> tuple[list[dict], int]:
    """Rows from story_links joined to stories, plus the ledger story count."""
    uri = f"file:{quote(str(db_path))}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.execute(
            "SELECT l.fingerprint, COALESCE(s.headline, ''), l.url, l.role, "
            "COALESCE(l.first_published, ''), s.cve_ids "
            "FROM story_links l LEFT JOIN stories s ON s.fingerprint = l.fingerprint"
        )
        raw = cur.fetchall()
        n_stories = con.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
    finally:
        con.close()
    rows = []
    for fp, headline, url, role, first_published, cve_ids in raw:
        rows.append(
            {
                "fingerprint": fp,
                "headline": headline,
                "query": query_for(headline, cve_ids),
                "url": normalize_url(url),
                "url_raw": url,
                "host": host_of(url),
                "role": role,
                "first_published": first_published,
            }
        )
    rows.sort(key=lambda r: (r["fingerprint"], r["url"], r["role"]))
    return rows, n_stories


_MDX_LINK = re.compile(r"\]\((https?://[^)\s]+)\)")
_MDX_TAGGED = re.compile(r"\[(?:Source|Coverage):\s*(https?://[^\]\s]+)")
_MDX_FM_URL = re.compile(r"^\s*-?\s*url:\s*[\"']?(https?://[^\s\"']+)", re.MULTILINE)


def mdx_urls(content_dir: Path) -> tuple[int, set[str]]:
    files = sorted(content_dir.glob("*.mdx"))
    urls: set[str] = set()
    for path in files:
        text = path.read_text(encoding="utf-8")
        frontmatter = ""
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                frontmatter = text[:end]
        for rx, src in ((_MDX_FM_URL, frontmatter), (_MDX_LINK, text), (_MDX_TAGGED, text)):
            urls.update(normalize_url(u) for u in rx.findall(src))
    return len(files), urls


def crosscheck(rows: list[dict], content_dir: Path, cap: int = 50) -> dict:
    if not content_dir.is_dir():
        return {"skipped": f"content dir not found: {content_dir}"}
    n_files, published = mdx_urls(content_dir)
    ledger = {r["url"] for r in rows}
    only_mdx = sorted(published - ledger)
    only_ledger = sorted(ledger - published)
    return {
        "mdx_files": n_files,
        "mdx_distinct_urls": len(published),
        "in_mdx_not_ledger_count": len(only_mdx),
        "in_mdx_not_ledger_sample": only_mdx[:cap],
        "in_ledger_not_mdx_count": len(only_ledger),
        "in_ledger_not_mdx_sample": only_ledger[:cap],
    }


def is_search_eligible(row: dict) -> bool:
    """ab-plan 3.1: web-discoverable host, role == original, and a usable query.

    The non-empty-query test was added at landing: `story_links` carries 18
    fingerprints with no row in `stories` (orphans), whose headline — and so whose
    reconstructed query — is empty. An empty query cannot be issued to either arm and
    would score as a guaranteed miss, so such rows are dropped from the search gold
    set. The fetch gold set keeps them: fetching their URL is still valid.
    """
    if row["role"] != "original":
        return False
    if not (row.get("query") or "").strip():
        return False
    host = row["host"]
    return not any(host == h or host.endswith("." + h) for h in config.FEED_NATIVE_HOSTS)


def summarize(rows: list[dict], n_ledger_stories: int | None = None) -> dict:
    hosts = Counter(r["host"] for r in rows)
    summary = {
        "links": len(rows),
        "by_role": dict(sorted(Counter(r["role"] for r in rows).items())),
        "distinct_urls": len({r["url"] for r in rows}),
        "hosts": len(hosts),
        "stories_with_links": len({r["fingerprint"] for r in rows}),
        "stories_in_ledger": n_ledger_stories,
        "by_host": dict(sorted(hosts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_month": dict(
            sorted(Counter((r["first_published"] or "unknown")[:7] for r in rows).items())
        ),
        "search_eligible_links": sum(is_search_eligible(r) for r in rows),
        "search_eligible_stories": len(
            {r["fingerprint"] for r in rows if is_search_eligible(r)}
        ),
    }
    return summary


def discrepancies(summary: dict) -> list[str]:
    got = {
        "links": summary["links"],
        "distinct_urls": summary["distinct_urls"],
        "hosts": summary["hosts"],
        "stories": summary["stories_in_ledger"],  # ab-plan's 5,221 is the ledger count
        **{f"role:{k}": v for k, v in summary["by_role"].items()},
    }
    out = []
    for key, want in config.EXPECTED_GOLD.items():
        if got.get(key, 0) != want:
            out.append(f"{key}: expected {want}, got {got.get(key, 0)}")
    return out


def dumps_jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def build(db_path: Path, content_dir: Path, out_dir: Path) -> dict:
    """Write gold.jsonl, gold_search.jsonl and gold_summary.json; return the summary."""
    rows, n_stories = read_ledger(db_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gold.jsonl").write_text(dumps_jsonl(rows), encoding="utf-8")
    search_rows = [r for r in rows if is_search_eligible(r)]
    (out_dir / "gold_search.jsonl").write_text(dumps_jsonl(search_rows), encoding="utf-8")
    summary = summarize(rows, n_stories)
    summary["crosscheck"] = crosscheck(rows, content_dir)
    summary["discrepancies_vs_ab_plan"] = discrepancies(summary)
    (out_dir / "gold_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary
