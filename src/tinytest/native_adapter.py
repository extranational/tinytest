"""Native fetch arm: the pipeline's real extraction, imported from a pinned checkout.

    TINYTEST_CORPUS=rollout NATIVE_CHECKOUT=~/repos/rollout python -m tinytest.native_adapter <url>

Prints exactly one JSON object on stdout. Host-only: needs the checkout's dependencies
and network. Refuses to run (exit 2, message on stderr) unless the checkout's
``git rev-parse HEAD`` starts with ``<corpus>_head`` in PINNED.md. The checkout defaults
to ``NATIVE_CHECKOUT`` (else ``CYBERPULSE_PATH``), or ``config.NATIVE_CHECKOUT`` for the
configured corpus; the package is imported as ``cyberpulse`` in either corpus (Rollout is
a re-skin of the same engine and keeps the module name). This module contains no
pipeline source; it imports it at runtime from the checkout.

Design (docs/host-answers.md): screening disabled (D3); its own httpx GET so ``status``
and ``content_type`` survive (``fetch_readable`` swallows failures and returns None);
the fetched HTML goes through CyberPulse's real ``_text_and_links``; ``links`` are the
``PageLink`` objects as dicts (url, anchor, sentence, page_sentences).

UNVERIFIED in the container (no CyberPulse access). Assumptions: the GET headers below
approximate CyberPulse's client; ``MAX_LINKS_PER_PAGE`` truncation mirrors
``fetch_readable``; ``published`` is obtained by replaying the already-fetched response
through the real ``fetch_readable`` over a mock transport (no second network request).
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

EXIT_REFUSED = 2
TIMEOUT_S = 10.0
PINNED_MD = Path(__file__).with_name("PINNED.md")


def default_checkout() -> Path:
    override = os.environ.get("NATIVE_CHECKOUT") or os.environ.get("CYBERPULSE_PATH")
    if override:
        return Path(override).expanduser()
    from tinytest import config

    return Path(config.NATIVE_CHECKOUT).expanduser()


def pinned_head(path: Path = PINNED_MD, corpus: str | None = None) -> str | None:
    """Pinned HEAD for ``corpus`` (default: the configured corpus, else cyberpulse)."""
    name = corpus or os.environ.get("TINYTEST_CORPUS", "cyberpulse")
    m = re.search(rf"^\s*[-*]?\s*`?{re.escape(name)}_head`?\s*:\s*`?([0-9a-fA-F]{{7,40}})`?\s*$",
                  path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1).lower() if m else None


def check_pin(checkout: Path, pin: str | None) -> str:
    """Return the checkout HEAD, or raise RuntimeError if it does not match the pin."""
    if not pin:
        raise RuntimeError("PINNED.md cyberpulse_head is not filled in (TODO); refusing to run")
    head = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    if not head.startswith(pin):
        raise RuntimeError(f"CyberPulse HEAD {head} does not match pinned {pin}; refusing to run")
    return head


def _link_dict(link) -> dict:
    if dataclasses.is_dataclass(link):
        d = dataclasses.asdict(link)
    else:
        d = {k: getattr(link, k, None) for k in ("url", "anchor", "sentence", "page_sentences")}
    return {k: d.get(k) for k in ("url", "anchor", "sentence", "page_sentences")}


def fetch(url: str, checkout: Path, head: str) -> dict:
    sys.path.insert(0, str(checkout / "src"))
    import httpx
    from cyberpulse.config import load_config
    from cyberpulse.pipeline import enrich
    from cyberpulse.pipeline.enrich import PAGE_CHARS, _text_and_links

    cfg = load_config(checkout)
    cfg.screen["enabled"] = False  # D3: screening excluded from both arms
    rec: dict = {"url": url, "cyberpulse_head": head, "ok": False, "error": None,
                 "status": None, "content_type": None, "final_url": None, "text": "",
                 "links": [], "title": None, "published_date": None}
    t0 = time.monotonic()
    try:
        headers = {"User-Agent": getattr(enrich, "USER_AGENT", "Mozilla/5.0 (compatible)")}
        with httpx.Client(follow_redirects=True, timeout=TIMEOUT_S, headers=headers) as client:
            resp = client.get(url)
            rec.update(status=resp.status_code, final_url=str(resp.url),
                       content_type=resp.headers.get("content-type", ""))
            ctype = rec["content_type"].lower()
            if resp.status_code >= 400:
                rec["error"] = f"HTTP {resp.status_code}"
            elif "html" not in ctype and "xml" not in ctype:
                rec["error"] = f"non-html content-type: {ctype}"
            else:
                html = resp.text
                text, links = _text_and_links(html, str(resp.url))
                limit = getattr(enrich, "MAX_LINKS_PER_PAGE", None)
                links = list(links)[:limit] if limit else list(links)
                rec.update(text=text[:PAGE_CHARS], links=[_link_dict(x) for x in links],
                           title=_title(html))
                rec["ok"] = bool(rec["text"].strip())
                if not rec["ok"]:
                    rec["error"] = "empty extraction"
                rec["published_date"] = _published(enrich, url, cfg, resp)
    except Exception as exc:  # classified downstream; never crash the run
        rec["error"] = f"{type(exc).__name__}: {exc}"
    rec["latency_ms"] = (time.monotonic() - t0) * 1000.0
    return rec


def _title(html: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return " ".join(m.group(1).split()) if m else None


def _published(enrich, url, cfg, resp) -> str | None:
    """Replay the fetched response through the real fetch_readable (no network)."""
    import httpx

    headers = {k: v for k, v in resp.headers.items()
               if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")}
    transport = httpx.MockTransport(
        lambda request: httpx.Response(resp.status_code, headers=headers, content=resp.content))
    try:
        with httpx.Client(transport=transport, follow_redirects=True) as client:
            page = enrich.fetch_readable(url, client, cfg)
    except Exception:
        return None
    published = getattr(page, "published", None)
    return published.isoformat() if published else None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m tinytest.native_adapter <url>", file=sys.stderr)
        return EXIT_REFUSED
    checkout = default_checkout()
    try:
        head = check_pin(checkout, pinned_head())
    except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
        print(f"native_adapter: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    print(json.dumps(fetch(args[0], checkout, head), sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
