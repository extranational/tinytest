"""TinyFish Search + Fetch clients, plus the shared rate-limit / retry transport.

``httpx`` is imported lazily so this module (and its request-shape tests) import
without third-party dependencies. The client never logs or embeds the API key in errors.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from tinytest import config

FETCH_URL = "https://api.fetch.tinyfish.ai"
SEARCH_URL = "https://api.search.tinyfish.ai"
AUTH_HEADER = "X-API-Key"  # ASSUMPTION: not recorded in docs/tinyfish-probe.md


class ProviderError(Exception):
    """A provider call failed; ``kind`` is a coarse class, never contains secrets."""

    def __init__(self, message: str, *, status: int | None = None, retries_429: int = 0):
        super().__init__(message)
        self.status = status
        self.retries_429 = retries_429


TinyFishError = ProviderError


class RateLimiter:
    """Enforce a minimum interval between units of work (clock/sleep injectable)."""

    def __init__(
        self,
        per_minute: float | None = None,
        *,
        min_interval: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.interval = min_interval if min_interval is not None else 60.0 / per_minute
        self._clock, self._sleep = clock, sleep
        self._next = None

    def wait(self, units: int = 1) -> None:
        now = self._clock()
        if self._next is not None and now < self._next:
            self._sleep(self._next - now)
            now = self._next
        self._next = now + self.interval * units


def default_http():
    import httpx  # lazy: host runner only

    return httpx.Client(timeout=config.HTTP_TIMEOUT_S, follow_redirects=False)


def send_with_retry(
    http,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    json: Any = None,
    limiter: RateLimiter | None = None,
    units: int = 1,
    sleep: Callable[[float], None] = time.sleep,
    max_retries: int = config.MAX_RETRIES,
    base_delay: float = config.RETRY_BASE_DELAY_S,
):
    """Send, backing off on 429 (honouring Retry-After) and 5xx.

    Returns ``(response, retries_429, wall_ms)``; raises ``ProviderError`` on transport
    failure or when retries are exhausted. Every 429 is counted.
    """
    retries_429 = 0
    attempt = 0
    t0 = time.monotonic()
    while True:
        if limiter is not None:
            limiter.wait(units)
        try:
            resp = http.request(
                method, url, headers=headers, params=params, json=json,
                timeout=config.HTTP_TIMEOUT_S,
            )
        except Exception as exc:  # transport: timeout, DNS, TLS ...
            raise ProviderError(
                f"{type(exc).__name__}: {exc}", retries_429=retries_429
            ) from None
        status = resp.status_code
        if status == 429 or status >= 500:
            if status == 429:
                retries_429 += 1
            if attempt >= max_retries:
                raise ProviderError(
                    f"HTTP {status} after {attempt} retries", status=status,
                    retries_429=retries_429,
                )
            delay = base_delay * 2**attempt
            try:
                delay = max(delay, float(resp.headers.get("Retry-After", 0)))
            except (TypeError, ValueError):
                pass
            sleep(delay)
            attempt += 1
            continue
        return resp, retries_429, (time.monotonic() - t0) * 1000.0


def build_fetch_request(urls: list[str], *, fmt: str = "html", links: bool = True,
                        ttl: int = 0,
                        per_url_timeout_ms: int = config.TINYFISH_PER_URL_TIMEOUT_MS,
                        ) -> dict[str, Any]:
    if not 1 <= per_url_timeout_ms <= 110000:
        raise ValueError("per_url_timeout_ms must be 1..110000")
    if not 1 <= len(urls) <= config.TINYFISH_FETCH_MAX_URLS_PER_REQUEST:
        raise ValueError(
            f"TinyFetch takes 1..{config.TINYFISH_FETCH_MAX_URLS_PER_REQUEST} URLs per request"
        )
    return {
        "method": "POST",
        "url": FETCH_URL,
        "json": {"urls": list(urls), "format": fmt, "links": links, "ttl": ttl,
                 "per_url_timeout_ms": per_url_timeout_ms},
    }


def build_search_request(query: str, *, location: str = "US") -> dict[str, Any]:
    return {
        "method": "GET",
        "url": SEARCH_URL,
        "params": {"query": query, "location": location},
    }


class TinyFishClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        http=None,
        sleep: Callable[[float], None] = time.sleep,
        fetch_limiter: RateLimiter | None = None,
        search_limiter: RateLimiter | None = None,
        max_retries: int = config.MAX_RETRIES,
        base_delay: float = config.RETRY_BASE_DELAY_S,
    ):
        self._key = api_key if api_key is not None else config.require_env("TINYFISH_API_KEY")
        self._http = http if http is not None else default_http()
        self._sleep = sleep
        self._fetch_limiter = fetch_limiter or RateLimiter(config.TINYFISH_FETCH_URLS_PER_MIN)
        self._search_limiter = search_limiter or RateLimiter(config.TINYFISH_SEARCH_REQ_PER_MIN)
        self._retry = {"max_retries": max_retries, "base_delay": base_delay}

    def _headers(self) -> dict[str, str]:
        return {AUTH_HEADER: self._key, "Accept": "application/json"}

    def fetch(self, urls: list[str], *, fmt: str = "html", links: bool = True,
              ttl: int = 0) -> dict[str, Any]:
        """One TinyFetch call. Returns the parsed body plus transport facts."""
        req = build_fetch_request(urls, fmt=fmt, links=links, ttl=ttl)
        resp, retries, wall_ms = send_with_retry(
            self._http, req["method"], req["url"], headers=self._headers(),
            json=req["json"], limiter=self._fetch_limiter, units=len(urls),
            sleep=self._sleep, **self._retry,
        )
        body = _json_or_error(resp, retries)
        return {
            "http_status": resp.status_code,
            "results": body.get("results") or [],
            "errors": body.get("errors") or [],
            "retries_429": retries,
            "wall_ms": wall_ms,
            "format": fmt,
            "raw": body,
        }

    def search(self, query: str, *, count: int = config.TINYFISH_SEARCH_COUNT) -> dict[str, Any]:
        """One TinySearch call; results trimmed client-side to ``count``."""
        req = build_search_request(query)
        resp, retries, wall_ms = send_with_retry(
            self._http, req["method"], req["url"], headers=self._headers(),
            params=req["params"], limiter=self._search_limiter, sleep=self._sleep,
            **self._retry,
        )
        body = _json_or_error(resp, retries)
        results = [
            {
                "rank": i,
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "snippet": r.get("snippet", ""),
            }
            for i, r in enumerate((body.get("results") or [])[:count], start=1)
        ]
        return {
            "http_status": resp.status_code,
            "results": results,
            "retries_429": retries,
            "wall_ms": wall_ms,
            "n_requested": count,
            "raw": body,
        }


def _json_or_error(resp, retries_429: int) -> dict[str, Any]:
    if resp.status_code != 200:
        raise ProviderError(f"HTTP {resp.status_code}", status=resp.status_code,
                            retries_429=retries_429)
    try:
        body = resp.json()
    except ValueError:
        raise ProviderError("invalid JSON body", status=resp.status_code,
                            retries_429=retries_429) from None
    if not isinstance(body, dict):
        raise ProviderError("unexpected JSON shape", status=resp.status_code,
                            retries_429=retries_429)
    return body
