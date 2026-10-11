"""Regenerate result tables from stored raw responses only (no network, no re-fetch).

Output is deterministic: rows sorted, floats rounded, no timestamps, ``\\n`` line ends.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tinytest import config, fetch_ab, search_ab
from tinytest.metrics import (
    classify_failure, content_overlap, link_url_overlap, percentile, rank_of_gold,
    recall_at_k, reciprocal_rank, strip_html,
)

FETCH_ARMS = ("native", "tinyfish")


def _fmt(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float):
        return str(round(value, 4))
    return value


def _write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow([_fmt(row.get(h)) for h in header])


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --- fetch ----------------------------------------------------------------------------
def arm_view(arm: str, rec: dict | None) -> dict[str, Any]:
    """One arm's stored raw record -> common fields. Identical logic for both arms."""
    if rec is None:
        return {"failure_class": "missing", "ok": False, "text": "", "links": [],
                "published_date": None, "latency_ms": None, "server_latency_ms": None,
                "retries_429": None}
    if arm == "native":
        text, links = rec.get("text") or "", rec.get("links") or []
        pub, latency, server = rec.get("published_date"), rec.get("latency_ms"), None
        status, ctype, ok, error = rec.get("status"), rec.get("content_type"), rec.get("ok"), rec.get("error")
        retries = None
    else:
        result = rec.get("result") or {}
        text = result.get("text") or ""
        if rec.get("format", "html") == "html":
            text = strip_html(text)
        links = [fetch_ab._link_url(i) for i in (result.get("links") or [])]
        links = [u for u in links if u]
        pub, latency = result.get("published_date"), rec.get("wall_ms")
        server = result.get("latency_ms")
        status, ctype, error = rec.get("http_status"), None, rec.get("error")
        ok = bool(rec.get("ok")) and bool(rec.get("result"))
        retries = rec.get("retries_429")
    return {
        "failure_class": classify_failure(bool(ok), error, status, ctype, text),
        "ok": bool(ok) and bool(text.strip()),
        "text": text, "links": links, "published_date": pub, "latency_ms": latency,
        "server_latency_ms": server, "retries_429": retries,
    }


def fetch_rows(sample: list[dict], raw_dir: Path) -> list[dict]:
    rows = []
    for g in sample:
        url = fetch_ab.fetch_url_of(g)
        views = {a: arm_view(a, fetch_ab.load_raw(raw_dir, url, a)) for a in FETCH_ARMS}
        nat, tf = views["native"], views["tinyfish"]
        both = nat["ok"] and tf["ok"]
        row = {"url": url, "host": g["host"], "role": g["role"],
               "sha1": fetch_ab.sha1_key(url),
               "content_overlap": content_overlap(nat["text"], tf["text"]) if both else None,
               "link_url_overlap": link_url_overlap(nat["links"], tf["links"]) if both else None,
               "tinyfish_server_latency_ms": tf["server_latency_ms"],
               "tinyfish_retries_429": tf["retries_429"]}
        for arm, v in views.items():
            row.update({
                f"{arm}_failure_class": v["failure_class"],
                f"{arm}_extract_chars": len(v["text"]),
                f"{arm}_link_url_count": len(v["links"]),
                f"{arm}_published_date_present": int(bool(v["published_date"])),
                f"{arm}_latency_ms": v["latency_ms"],
            })
        rows.append(row)
    return sorted(rows, key=lambda r: r["sha1"])


FETCH_HEADER = (
    ["url", "host", "role", "sha1"]
    + [f"{a}_{m}" for a in FETCH_ARMS
       for m in ("failure_class", "extract_chars", "link_url_count",
                 "published_date_present", "latency_ms")]
    + ["tinyfish_server_latency_ms", "tinyfish_retries_429", "content_overlap",
       "link_url_overlap"]
)


def fetch_summary(rows: list[dict]) -> dict:
    out: dict[str, Any] = {"n_urls": len(rows), "arms": {}, "by_host": {}}
    for arm in FETCH_ARMS:
        ok = [r for r in rows if r[f"{arm}_failure_class"] == "ok"]
        lat = [r[f"{arm}_latency_ms"] for r in rows if r[f"{arm}_latency_ms"] is not None]
        out["arms"][arm] = {
            "n_ok": len(ok),
            "success_rate": len(ok) / len(rows) if rows else None,
            "failure_classes": dict(sorted(Counter(r[f"{arm}_failure_class"] for r in rows).items())),
            "latency_ms_p50": percentile(lat, 50),
            "latency_ms_p90": percentile(lat, 90),
            "latency_ms_p95": percentile(lat, 95),
            "mean_extract_chars_ok": statistics.fmean(r[f"{arm}_extract_chars"] for r in ok) if ok else None,
            "published_date_rate_ok": (sum(r[f"{arm}_published_date_present"] for r in ok) / len(ok)) if ok else None,
        }
    overlaps = [r["content_overlap"] for r in rows if r["content_overlap"] is not None]
    links = [r["link_url_overlap"] for r in rows if r["link_url_overlap"] is not None]
    out["content_overlap"] = {"n": len(overlaps),
                              "mean": statistics.fmean(overlaps) if overlaps else None,
                              "median": statistics.median(overlaps) if overlaps else None}
    out["link_url_overlap"] = {"n": len(links),
                               "mean": statistics.fmean(links) if links else None}
    failed = [r for r in rows if r["native_failure_class"] not in ("ok", "missing")]
    out["native_failed_rescue"] = {
        "native_failed": len(failed),
        "tinyfish_ok_on_those": sum(r["tinyfish_failure_class"] == "ok" for r in failed),
    }
    by_host: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "native_ok": 0, "tinyfish_ok": 0})
    for r in rows:
        h = by_host[r["host"]]
        h["n"] += 1
        h["native_ok"] += r["native_failure_class"] == "ok"
        h["tinyfish_ok"] += r["tinyfish_failure_class"] == "ok"
    out["by_host"] = dict(sorted(by_host.items()))
    return out


# --- search ---------------------------------------------------------------------------
SEARCH_HEADER = ["fingerprint", "host", "arm", "rep", "status", "error", "n_results",
                 "rank", "hit_at_5", "hit_at_10_not_comparable", "rr", "retries_429", "n_requested"]


def search_rows(stories: list[dict], raw_dir: Path, reps: int = config.SEARCH_REPS) -> list[dict]:
    rows = []
    for s in stories:
        for arm in search_ab.ARMS:
            for rep in range(1, reps + 1):
                path = search_ab.raw_path(raw_dir, s["fingerprint"], arm, rep)
                row = {"fingerprint": s["fingerprint"], "host": s["host"], "arm": arm,
                       "rep": rep}
                if not path.exists():
                    row.update(status="missing", error="no raw record (not run / run aborted)")
                    rows.append(row)
                    continue
                rec = json.loads(path.read_text(encoding="utf-8"))
                urls = [r["url"] for r in rec.get("results") or []]
                row.update(status="ok" if rec.get("ok") else "error", error=rec.get("error"),
                           n_results=len(urls), retries_429=rec.get("retries_429"),
                           n_requested=rec.get("n_requested"))
                if rec.get("ok"):
                    rank = rank_of_gold(urls, s["gold_urls"])
                    row.update(rank=rank, hit_at_5=recall_at_k(rank, 5),
                               hit_at_10_not_comparable=recall_at_k(rank, 10),
                               rr=reciprocal_rank(rank))
                rows.append(row)
    return rows


def search_summary(rows: list[dict]) -> tuple[dict, list[dict]]:
    """Per arm/rep metrics (recall over *successful* queries; errors reported apart)."""
    summary: dict[str, Any] = {}
    variance: list[dict] = []
    for arm in search_ab.ARMS:
        per_rep = {}
        for rep in sorted({r["rep"] for r in rows}):
            sel = [r for r in rows if r["arm"] == arm and r["rep"] == rep]
            ok = [r for r in sel if r["status"] == "ok"]
            attempted = [r for r in sel if r["status"] != "missing"]
            requested = max((r.get("n_requested") or 0 for r in ok), default=0)
            per_rep[rep] = {
                "n_stories": len(sel), "n_attempted": len(attempted), "n_ok": len(ok),
                "n_missing": len(sel) - len(attempted),
                "query_error_rate": (1 - len(ok) / len(attempted)) if attempted else None,
                "rate_limit_retries": sum(r.get("retries_429") or 0 for r in attempted),
                "recall_at_5": statistics.fmean(r["hit_at_5"] for r in ok) if ok else None,
                # recall@5 is the head-to-head figure. TinyFish's recall@10 is reported
                # apart and is NOT comparable: Brave is run at its live count=5 and was
                # never asked for 10 (docs/host-answers.md Q6).
                "recall_at_10_not_comparable": (
                    statistics.fmean(r["hit_at_10_not_comparable"] for r in ok)
                    if ok and arm == "tinyfish" and requested >= 10 else None),
                "mrr": statistics.fmean(r["rr"] for r in ok) if ok else None,
            }
        summary[arm] = {"per_rep": per_rep}
        for metric in ("recall_at_5", "recall_at_10_not_comparable", "mrr", "query_error_rate"):
            vals = [m[metric] for m in per_rep.values() if m[metric] is not None]
            variance.append({
                "arm": arm, "metric": metric, "n_reps": len(vals),
                "mean": statistics.fmean(vals) if vals else None,
                "min": min(vals) if vals else None, "max": max(vals) if vals else None,
                "stdev": statistics.stdev(vals) if len(vals) > 1 else (0.0 if vals else None),
            })
    return summary, variance


# --- orchestration --------------------------------------------------------------------
def write_all(
    gold_path: Path = config.GOLD_PATH,
    gold_search_path: Path = config.GOLD_SEARCH_PATH,
    raw_fetch: Path = config.RAW_FETCH_DIR,
    raw_search: Path = config.RAW_SEARCH_DIR,
    out_dir: Path = config.RESULTS_DIR,
    fetch_limit: int | None = None,
    search_limit: int | None = None,
    baseline_db: Path | None = None,
    fetch_n: int = config.FETCH_N,
    search_n: int = config.SEARCH_N,
    seed: int = config.SEED,
) -> dict[str, Path]:
    from tinytest import gold

    written: dict[str, Path] = {}
    if gold_path.exists():
        sample = fetch_ab.draw_sample(gold.read_jsonl(gold_path), fetch_n, seed)[:fetch_limit]
        frows = fetch_rows(sample, raw_fetch)
        _write_csv(out_dir / "fetch.csv", FETCH_HEADER, frows)
        _write_json(out_dir / "fetch_summary.json", fetch_summary(frows))
        written["fetch"] = out_dir / "fetch.csv"
        if baseline_db is not None and baseline_db.exists():
            _write_json(out_dir / "fetch_native_baseline.json",
                        fetch_ab.native_failure_baseline(baseline_db))
    if gold_search_path.exists():
        stories = search_ab.draw_stories(gold.read_jsonl(gold_search_path), search_n,
                                         seed)[:search_limit]
        srows = search_rows(stories, raw_search)
        summary, variance = search_summary(srows)
        _write_csv(out_dir / "search.csv", SEARCH_HEADER, srows)
        _write_csv(out_dir / "search_variance.csv",
                   ["arm", "metric", "n_reps", "mean", "min", "max", "stdev"], variance)
        _write_json(out_dir / "search_summary.json", summary)
        written["search"] = out_dir / "search.csv"
    return written
