"""Request-shape and retry tests with a fake transport. No network, no httpx."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tinytest import brave, tinyfish  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
KEY = "sk-test-SECRET-123"


class FakeResp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeHttp:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "params": params, "json": json})
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def load(name):
    return json.loads((FIX / name).read_text())


def client(responses, sleeps=None):
    sleeps = sleeps if sleeps is not None else []
    http = FakeHttp(responses)
    c = tinyfish.TinyFishClient(
        KEY, http=http, sleep=sleeps.append, base_delay=2.0, max_retries=2,
        fetch_limiter=tinyfish.RateLimiter(min_interval=0, sleep=lambda s: None),
        search_limiter=tinyfish.RateLimiter(min_interval=0, sleep=lambda s: None),
    )
    return c, http, sleeps


class RequestShape(unittest.TestCase):
    def test_fetch_request(self):
        req = tinyfish.build_fetch_request(["https://a.example"], fmt="html", links=True, ttl=0)
        self.assertEqual(req, {"method": "POST", "url": "https://api.fetch.tinyfish.ai",
                               "json": {"urls": ["https://a.example"], "format": "html",
                                        "links": True, "ttl": 0,
                                        "per_url_timeout_ms": 30000}})
        req = tinyfish.build_fetch_request(["https://a.example"], per_url_timeout_ms=5000)
        self.assertEqual(req["json"]["per_url_timeout_ms"], 5000)
        with self.assertRaises(ValueError):
            tinyfish.build_fetch_request(["https://a.example"], per_url_timeout_ms=0)

    def test_fetch_url_limit(self):
        with self.assertRaises(ValueError):
            tinyfish.build_fetch_request([f"https://a.example/{i}" for i in range(11)])
        with self.assertRaises(ValueError):
            tinyfish.build_fetch_request([])

    def test_search_request(self):
        req = tinyfish.build_search_request("acme vpn flaw")
        self.assertEqual(req["method"], "GET")
        self.assertEqual(req["url"], "https://api.search.tinyfish.ai")
        self.assertEqual(req["params"], {"query": "acme vpn flaw", "location": "US"})

    def test_brave_request_uses_cyberpulse_params(self):
        req = brave.build_search_request("q")
        self.assertEqual(req["url"], "https://api.search.brave.com/res/v1/web/search")
        self.assertEqual(req["params"], {"q": "q", "count": 5, "freshness": "pm"})


class ClientBehaviour(unittest.TestCase):
    def test_fetch_sends_key_header_and_parses(self):
        c, http, _ = client([FakeResp(200, load("tinyfish_fetch_response.json"))])
        out = c.fetch(["https://news.example.com/acme-vpn"])
        call = http.calls[0]
        self.assertEqual(call["headers"][tinyfish.AUTH_HEADER], KEY)
        self.assertEqual(call["json"]["ttl"], 0)
        self.assertEqual(out["retries_429"], 0)
        self.assertEqual(out["results"][0]["author"], "A. Writer")
        self.assertEqual(out["errors"], [])

    def test_search_trims_and_ranks(self):
        c, http, _ = client([FakeResp(200, load("tinyfish_search_response.json"))])
        out = c.search("Acme VPN flaw exploited", count=3)
        self.assertEqual([r["rank"] for r in out["results"]], [1, 2, 3])
        self.assertEqual(len(out["results"]), 3)
        self.assertEqual(http.calls[0]["params"]["query"], "Acme VPN flaw exploited")

    def test_429_backoff_then_success_is_counted(self):
        body = load("tinyfish_search_response.json")
        c, _, sleeps = client([FakeResp(429), FakeResp(429, headers={"Retry-After": "7"}),
                               FakeResp(200, body)])
        out = c.search("q")
        self.assertEqual(out["retries_429"], 2)
        self.assertEqual(sleeps, [2.0, 7.0])  # exponential floor 2*2^1=4 < Retry-After 7

    def test_retries_exhausted_raises_without_leaking_key(self):
        c, _, _ = client([FakeResp(429), FakeResp(429), FakeResp(429)])
        with self.assertRaises(tinyfish.ProviderError) as ctx:
            c.search("q")
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(ctx.exception.retries_429, 3)
        self.assertNotIn(KEY, str(ctx.exception))

    def test_non_200_and_transport_errors_surface(self):
        c, _, _ = client([FakeResp(401)])
        with self.assertRaises(tinyfish.ProviderError) as ctx:
            c.search("q")
        self.assertEqual(ctx.exception.status, 401)
        c, _, _ = client([TimeoutError("read timed out")])
        with self.assertRaises(tinyfish.ProviderError) as ctx:
            c.search("q")
        self.assertIn("timed out", str(ctx.exception))

    def test_brave_parse(self):
        http = FakeHttp([FakeResp(200, load("brave_response.json"))])
        b = brave.BraveClient(KEY, http=http, limiter=tinyfish.RateLimiter(
            min_interval=0, sleep=lambda s: None))
        out = b.search("q")
        self.assertEqual(len(out["results"]), 5)  # trimmed to count=5
        self.assertEqual(out["results"][0]["snippet"], "da")
        self.assertEqual(http.calls[0]["headers"][brave.AUTH_HEADER], KEY)
        self.assertEqual(out["n_requested"], 5)


class Limiter(unittest.TestCase):
    def test_spacing(self):
        now = [0.0]
        sleeps = []

        def sleep(s):
            sleeps.append(s)
            now[0] += s

        lim = tinyfish.RateLimiter(per_minute=30, clock=lambda: now[0], sleep=sleep)
        lim.wait()
        lim.wait()
        lim.wait()
        self.assertEqual(sleeps, [2.0, 2.0])


if __name__ == "__main__":
    unittest.main()
