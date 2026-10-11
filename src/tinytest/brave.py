"""Brave Search client for the native search arm.

Request parameters (``count``, ``freshness``) mirror CyberPulse's live
``websearch.search`` and live in ``config`` (confirmed: count=5 stays, host-answers Q6).
"""

from __future__ import annotations

import time
from typing import Any, Callable

from tinytest import config
from tinytest.tinyfish import ProviderError, RateLimiter, default_http, send_with_retry

SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
AUTH_HEADER = "X-Subscription-Token"


def build_search_request(query: str, *, count: int = config.BRAVE_COUNT,
                         freshness: str | None = config.BRAVE_FRESHNESS) -> dict[str, Any]:
    params: dict[str, Any] = {"q": query, "count": count}
    if freshness:
        params["freshness"] = freshness
    return {"method": "GET", "url": SEARCH_URL, "params": params}


class BraveClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        http=None,
        sleep: Callable[[float], None] = time.sleep,
        limiter: RateLimiter | None = None,
        count: int = config.BRAVE_COUNT,
        freshness: str | None = config.BRAVE_FRESHNESS,
    ):
        self._key = api_key if api_key is not None else config.require_env("BRAVE_API_KEY")
        self._http = http if http is not None else default_http()
        self._sleep = sleep
        self._limiter = limiter or RateLimiter(min_interval=config.BRAVE_MIN_INTERVAL_S)
        self.count, self.freshness = count, freshness

    def search(self, query: str) -> dict[str, Any]:
        req = build_search_request(query, count=self.count, freshness=self.freshness)
        resp, retries, wall_ms = send_with_retry(
            self._http, req["method"], req["url"],
            headers={AUTH_HEADER: self._key, "Accept": "application/json"},
            params=req["params"], limiter=self._limiter, sleep=self._sleep,
        )
        if resp.status_code != 200:
            raise ProviderError(f"HTTP {resp.status_code}", status=resp.status_code,
                                retries_429=retries)
        try:
            body = resp.json()
        except ValueError:
            raise ProviderError("invalid JSON body", status=200, retries_429=retries) from None
        web = (body.get("web") or {}).get("results") or []
        results = [
            {
                "rank": i,
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "snippet": r.get("description", ""),
            }
            for i, r in enumerate(web[: self.count], start=1)
        ]
        return {
            "http_status": 200,
            "results": results,
            "retries_429": retries,
            "wall_ms": wall_ms,
            "n_requested": self.count,
            "raw": body,
        }
