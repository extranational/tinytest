"""Experiment 1 runner: native fetch+extract vs TinyFetch on a host-stratified sample.

Raw per-URL results are persisted to ``data/raw/fetch/<sha1>.{native,tinyfish}`` (JSON)
and every metric is computed later, from those files only (see ``report``).
Existing raw files are never refetched, so re-runs are cheap and resumable.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from tinytest import config
from tinytest.metrics import host_of


def sha1_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


# --- sampling -------------------------------------------------------------------------
def draw_sample(
    gold_rows: list[dict],
    n: int = config.FETCH_N,
    seed: int = config.SEED,
    capped_host: str = config.CAPPED_HOST,
    cap_fraction: float = config.CAPPED_HOST_FRACTION,
) -> list[dict]:
    """Deterministic host-stratified draw of ``n`` distinct URLs.

    Hosts are visited in a size-weighted random order (Efraimidis-Spirakis keys), then
    taken round-robin one URL per host per pass, so breadth across hosts is maximised
    while larger hosts are likelier to be represented. ``capped_host`` is limited to
    ``cap_fraction`` of ``n``. Returns gold rows (one per URL), sorted by sha1.
    """
    rng = random.Random(seed)
    by_host: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in sorted(gold_rows, key=lambda r: (r["url"], r["role"], r["fingerprint"])):
        by_host[row["host"]].setdefault(row["url"], row)
    hosts = sorted(by_host)
    keyed = sorted(
        hosts,
        key=lambda h: -(rng.random() ** (1.0 / len(by_host[h]))),
    )
    queues = {}
    for h in hosts:  # per-host URL order is fixed by the seeded rng, in sorted-host order
        urls = sorted(by_host[h])
        rng.shuffle(urls)
        queues[h] = urls
    cap = math.floor(n * cap_fraction)
    taken: dict[str, int] = defaultdict(int)
    picked: list[dict] = []
    while len(picked) < n:
        progressed = False
        for h in keyed:
            if len(picked) >= n:
                break
            if not queues[h] or (h == capped_host and taken[h] >= cap):
                continue
            picked.append(by_host[h][queues[h].pop()])
            taken[h] += 1
            progressed = True
        if not progressed:
            break
    return sorted(picked, key=lambda r: sha1_key(fetch_url_of(r)))


def fetch_url_of(row: dict) -> str:
    return row.get("url_raw") or row["url"]


# --- native arm adapter ---------------------------------------------------------------
def _link_url(item: Any) -> str | None:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get("url") or item.get("href")
    return getattr(item, "url", None) or getattr(item, "href", None)


NATIVE_TIMEOUT_S = 60.0


class NativeRefused(RuntimeError):
    """The adapter refused to run (pin mismatch / unset); aborts the run, not one URL."""


def native_fetch(url: str) -> dict[str, Any]:
    """Run ``python -m tinytest.native_adapter <url>`` and parse its one JSON object.

    Adapter failures for a single URL come back as ``ok: false`` records; only a refusal
    (exit 2: pin mismatch or unset) raises ``NativeRefused`` so the whole run stops.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(config.ROOT / "src")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    t0 = time.monotonic()
    try:
        proc = subprocess.run([sys.executable, "-m", "tinytest.native_adapter", url], env=env,
                              capture_output=True, text=True, timeout=NATIVE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout: adapter subprocess timed out", "text": "",
                "links": [], "latency_ms": (time.monotonic() - t0) * 1000.0}
    if proc.returncode == 2:
        raise NativeRefused(proc.stderr.strip() or "native_adapter refused to run")
    try:
        rec = json.loads(proc.stdout)
    except ValueError:
        rec = {"ok": False, "text": "", "links": [],
               "error": f"adapter exit {proc.returncode}: {proc.stderr.strip()[-300:]}"}
    rec.setdefault("latency_ms", (time.monotonic() - t0) * 1000.0)
    return rec


# --- runner ---------------------------------------------------------------------------
def _write(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def _match_result(results: list[dict], url: str) -> dict | None:
    for r in results:
        if r.get("url") == url or r.get("final_url") == url:
            return r
    return results[0] if len(results) == 1 else None


def run(
    sample: list[dict],
    *,
    native_fn: Callable[[str], dict] = native_fetch,
    tinyfish_client=None,
    raw_dir: Path = config.RAW_FETCH_DIR,
    batch: int = config.TINYFISH_FETCH_BATCH,
    daily_budget: int = config.TINYFISH_FETCH_URLS_PER_DAY,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """Fetch every sample URL on both arms. Returns counters; never raises per-URL."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    counts = {"native": 0, "tinyfish": 0, "skipped_existing": 0, "tinyfish_budget_stop": 0}
    spent = 0
    todo_tf: list[tuple[str, str]] = []
    for row in sample:
        url = fetch_url_of(row)
        key = sha1_key(url)
        npath = raw_dir / f"{key}.native"
        if npath.exists():
            counts["skipped_existing"] += 1
        else:
            rec = native_fn(url)
            rec.update(arm="native", url=url)
            _write(npath, rec)
            counts["native"] += 1
        if not (raw_dir / f"{key}.tinyfish").exists() and tinyfish_client is not None:
            todo_tf.append((url, key))
    for i in range(0, len(todo_tf), batch):
        chunk = todo_tf[i : i + batch]
        if spent + len(chunk) > daily_budget:
            counts["tinyfish_budget_stop"] = len(todo_tf) - i
            log(f"TinyFish daily budget {daily_budget} reached; stopping (resume tomorrow)")
            break
        spent += len(chunk)
        urls = [u for u, _ in chunk]
        try:
            resp = tinyfish_client.fetch(urls, fmt="html", links=True, ttl=0)
            base = {
                "ok": True, "error": None, "http_status": resp["http_status"],
                "wall_ms": resp["wall_ms"], "retries_429": resp["retries_429"],
                "format": resp["format"], "api_errors": resp["errors"],
            }
            results = resp["results"]
        except Exception as exc:
            base = {
                "ok": False, "error": f"{type(exc).__name__}: {exc}",
                "http_status": getattr(exc, "status", None), "wall_ms": None,
                "retries_429": getattr(exc, "retries_429", 0), "format": "html",
                "api_errors": [],
            }
            results = []
        for url, key in chunk:
            result = _match_result(results, url)
            rec = dict(base, arm="tinyfish", url=url, result=result)
            if result is None and base["ok"]:
                rec["ok"] = False
                rec["error"] = "; ".join(str(e) for e in base["api_errors"]) or "no result"
            _write(raw_dir / f"{key}.tinyfish", rec)
            counts["tinyfish"] += 1
        if base["retries_429"]:
            log(f"429 backoff x{base['retries_429']} on batch starting {chunk[0][0]}")
    return counts


# --- native baseline from CyberPulse run summaries ------------------------------------
def native_failure_baseline(db_path: Path) -> dict[str, Any]:
    """Per-host native fetch failure counts from ``runs`` summaries (best effort).

    ``runs.summary`` is JSON whose ``fetch_failures`` is a ``{host: count}`` dict
    (docs/host-answers.md Q2); list and int shapes are still tolerated. Anything
    unreadable returns ``{"unavailable": reason}``.
    """
    import sqlite3
    from urllib.parse import quote

    try:
        con = sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True)
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(runs)")]
            col = next((c for c in cols if "summary" in c.lower()), None)
            if col is None:
                return {"unavailable": f"no summary column in runs {cols}"}
            rows = con.execute(f"SELECT {col} FROM runs").fetchall()  # noqa: S608
        finally:
            con.close()
    except Exception as exc:
        return {"unavailable": f"{type(exc).__name__}: {exc}"}
    per_host: dict[str, int] = defaultdict(int)
    total = 0
    n_runs = 0
    for (blob,) in rows:
        try:
            data = json.loads(blob) if isinstance(blob, str) else None
        except ValueError:
            continue
        failures = data.get("fetch_failures") if isinstance(data, dict) else None
        if failures is None:
            continue
        n_runs += 1
        if isinstance(failures, dict):  # real shape: {host: count}
            for host, count in failures.items():
                if isinstance(count, int):
                    per_host[str(host).lower().removeprefix("www.")] += count
                    total += count
        elif isinstance(failures, int):
            total += failures
        elif isinstance(failures, list):
            total += len(failures)
            for item in failures:
                url = item if isinstance(item, str) else (item or {}).get("url", "")
                if url:
                    per_host[host_of(url)] += 1
    return {"runs_with_fetch_failures": n_runs, "total_failures": total,
            "per_host": dict(sorted(per_host.items()))}


def load_raw(raw_dir: Path, url: str, arm: str) -> dict | None:
    path = raw_dir / f"{sha1_key(url)}.{arm}"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
