"""Pure metric functions. Stdlib only; no network, no third-party imports.

``signal_ratio`` is deliberately NOT implemented: TinyFish returns only
boilerplate-stripped content, so ``body_text`` does not exist on that arm
(docs/tinyfish-probe.md). ``content_overlap`` replaces it.
"""

from __future__ import annotations

import math
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from tinytest.config import MIN_SENTENCE_CHARS

def normalize_url(url: str) -> str:
    """CyberPulse's real URL key (``store._normalize_url`` -> ``dedupe.item_key``).

    Exact logic: strip, drop the ``#fragment``, strip trailing ``/``, remove ``utm_*`` and
    ``ref=`` query params, lowercase the whole URL. It KEEPS ``www.``, does NOT force
    https and does NOT sort params; the ``/`` strip runs before the param removal, so
    ``/a/?utm_x=1`` keys as ``/a/``. These quirks are deliberate: matching must agree
    with what the pipeline treats as the same URL.
    """
    url = url.strip()
    if url:
        url = url.split("#")[0].rstrip("/")
        url = re.sub(r"[?&](utm_[^&]+|ref=[^&]+)", "", url)
    return url.lower()


def host_of(url: str) -> str:
    return (urlsplit(url.strip()).hostname or "").lower().removeprefix("www.")


class _TagStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def strip_html(markup: str) -> str:
    stripper = _TagStripper()
    stripper.feed(markup)
    stripper.close()
    return re.sub(r"[ \t\r\f\v]+", " ", "".join(stripper.parts)).strip()


def _squash(text: str) -> str:
    """Casefold and collapse every non-alphanumeric run to one space."""
    return re.sub(r"[\W_]+", " ", text.casefold()).strip()


def sentences(text: str, min_chars: int = MIN_SENTENCE_CHARS) -> list[str]:
    """Split on sentence punctuation / newlines; drop fragments under ``min_chars``."""
    out = []
    for chunk in re.split(r"(?<=[.!?])\s+|\n+", text):
        chunk = " ".join(chunk.split())
        if len(chunk) >= min_chars:
            out.append(chunk)
    return out


def content_overlap(native_text: str, other_text: str) -> float | None:
    """Fraction of the native arm's sentences found in ``other_text``.

    A sentence counts as present when its squashed form is a substring of the squashed
    other text. ``None`` when native has no qualifying sentences (undefined).
    """
    native = [s for s in (_squash(s) for s in sentences(native_text)) if s]
    if not native:
        return None
    haystack = f" {_squash(other_text)} "
    return sum(f" {s} " in haystack for s in native) / len(native)


def link_url(link) -> str:
    """URL of a link given as a bare string or a dict (native: url/anchor/sentence/...)."""
    if isinstance(link, dict):
        return link.get("url") or link.get("href") or ""
    return link or ""


def link_url_overlap(native_links: list, other_links: list) -> float | None:
    """Fraction of native's outbound link URLs present in the other arm's list.

    Either list may hold strings or link dicts with a ``url`` field.
    """
    native = {normalize_url(u) for u in map(link_url, native_links) if u}
    if not native:
        return None
    other = {normalize_url(u) for u in map(link_url, other_links) if u}
    return len(native & other) / len(native)


def classify_failure(
    ok: bool,
    error: str | None = None,
    status: int | None = None,
    content_type: str | None = None,
    text: str | None = None,
) -> str:
    """Failure class for one arm's stored result; ``ok`` when it produced content."""
    err = (error or "").lower()
    if content_type:
        ctype = content_type.lower()
        if "html" not in ctype and "xml" not in ctype and "text/plain" not in ctype:
            return "non_html"
    if status is not None and status in (401, 403, 429):
        return "anti_bot"
    if status is not None and status >= 400:
        return f"http_{status}"
    if "timeout" in err or "timed out" in err:
        return "timeout"
    if any(k in err for k in ("getaddrinfo", "name or service", "dns", "resolve")):
        return "dns"
    if "non-html" in err or "non_html" in err or "content-type" in err:
        return "non_html"
    if "empty extraction" in err:
        return "empty_body"
    if not ok:
        return "no_content" if "no content" in err else "other_error"
    if not (text or "").strip():
        return "empty_body"
    return "ok"


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; ``None`` for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    idx = max(math.ceil(pct / 100 * len(ordered)) - 1, 0)
    return ordered[idx]


# --- search ---------------------------------------------------------------------------
def rank_of_gold(result_urls: list[str], gold_urls: set[str] | list[str]) -> int | None:
    """1-based rank of the first result matching any gold URL (normalised), else None."""
    gold = {normalize_url(u) for u in gold_urls}
    for rank, url in enumerate(result_urls, start=1):
        if url and normalize_url(url) in gold:
            return rank
    return None


def recall_at_k(rank: int | None, k: int) -> int:
    return int(rank is not None and rank <= k)


def reciprocal_rank(rank: int | None) -> float:
    return 0.0 if rank is None else 1.0 / rank
