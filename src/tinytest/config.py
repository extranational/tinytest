"""Environment loading, paths, constants and thresholds.

Secrets come only from the environment / ``.env`` (gitignored). Nothing here ever logs
or returns a key except ``require_env``, whose result callers must not print.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("TINYTEST_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
RAW_FETCH_DIR = RAW_DIR / "fetch"
RAW_SEARCH_DIR = RAW_DIR / "search"
RAW_PROBE_DIR = RAW_DIR / "probe"
RESULTS_DIR = DATA_DIR / "results"
GOLD_PATH = DATA_DIR / "gold.jsonl"
GOLD_SEARCH_PATH = DATA_DIR / "gold_search.jsonl"
GOLD_SUMMARY_PATH = DATA_DIR / "gold_summary.json"
ENV_PATH = ROOT / ".env"


def load_env(path: Path = ENV_PATH) -> None:
    """Minimal ``KEY=VALUE`` loader; real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def require_env(name: str) -> str:
    load_env()
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set (put it in {ENV_PATH}; never commit it)")
    return value


# --- gold-set inputs: re-point at rollout (or any sibling) by env change only ---------
GOLD_DB = Path(
    os.environ.get("GOLD_DB", Path.home() / "repos/cyberpulse/state/cyberpulse.db")
)
GOLD_CONTENT_DIR = Path(
    os.environ.get("GOLD_CONTENT_DIR", Path.home() / "repos/vrsweb/content/cyber-hose")
)

# Numbers from docs/ab-plan.md section 2; `tinytest gold` reports any discrepancy.
EXPECTED_GOLD = {
    "links": 6183,
    "role:coverage": 5233,
    "role:original": 713,
    "role:seed": 237,
    "distinct_urls": 6150,
    "hosts": 479,
    "stories": 5221,
}

# Feed-native hosts: arrive via dedicated fetchers, not web search (ab-plan 3.1).
FEED_NATIVE_HOSTS = frozenset(
    {
        "reddit.com",
        "mastodon.social",
        "infosec.exchange",
        "arxiv.org",
        "github.com",
        "x.com",
        "twitter.com",
    }
)

# --- sampling -------------------------------------------------------------------------
SEED = 20261010
FETCH_N = 200
SEARCH_N = 150
SEARCH_REPS = 3
CAPPED_HOST = "cve.threatint.com"
CAPPED_HOST_FRACTION = 0.10

# --- provider limits (ab-plan 4, 5) ---------------------------------------------------
TINYFISH_FETCH_MAX_URLS_PER_REQUEST = 10
TINYFISH_FETCH_URLS_PER_MIN = 150
TINYFISH_FETCH_URLS_PER_DAY = 1000
TINYFISH_PER_URL_TIMEOUT_MS = 30000  # API accepts 1..110000; set explicitly, not default
TINYFISH_FETCH_BATCH = 1  # 1 URL/request so wall-clock latency is per URL
TINYFISH_SEARCH_REQ_PER_MIN = 30
TINYFISH_SEARCH_COUNT = 10
BRAVE_COUNT = 5  # CyberPulse's live parameters (kept per host-answers Q6)
BRAVE_FRESHNESS = "pm"
BRAVE_MIN_INTERVAL_S = 1.1
HTTP_TIMEOUT_S = 30.0
MAX_RETRIES = 5
RETRY_BASE_DELAY_S = 2.0

# --- metric thresholds ----------------------------------------------------------------
MIN_SENTENCE_CHARS = 40  # shorter fragments (nav, captions) are ignored for overlap
