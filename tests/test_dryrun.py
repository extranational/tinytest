"""Fixture dry-run of both experiments end to end with fake providers (no network)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tinytest import fetch_ab, gold, report, search_ab  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
NATIVE_TEXT = (FIX / "native_article.txt").read_text()
TF_HTML = (FIX / "tinyfish_article.html").read_text()


def gold_rows():
    rows = []
    for i in range(30):  # capped host: 30 URLs
        rows.append(row(f"t{i}", f"https://cve.threatint.com/cve/{i}", "coverage"))
    for h in range(12):
        for i in range(3):
            rows.append(row(f"h{h}s{i}", f"https://host{h}.example/p/{i}", "original"))
    return rows


def row(fp, url, role):
    return {"fingerprint": fp, "headline": f"Headline {fp}", "query": f"Headline {fp}",
            "url": url, "url_raw": url, "host": url.split("/")[2], "role": role,
            "first_published": "2026-09-01T00:00:00Z"}


class FakeTinyFish:
    def __init__(self):
        self.calls = 0

    def fetch(self, urls, fmt="html", links=True, ttl=0):
        self.calls += 1
        results = []
        errors = []
        for u in urls:
            if u.endswith("/2"):
                errors.append({"url": u, "error": "HTTP 403"})
                continue
            results.append({"url": u, "final_url": u, "text": TF_HTML, "links": [u + "/a"],
                            "published_date": "2026-10-10T00:00:00Z", "latency_ms": 100.0})
        return {"http_status": 200, "results": results, "errors": errors, "retries_429": 0,
                "wall_ms": 150.0, "format": fmt, "raw": {}}


def fake_native(url):
    if url.endswith("/0"):
        return {"ok": False, "error": "HTTP 404", "status": 404, "text": "", "links": [],
                "latency_ms": 10.0}
    if url.endswith("/1"):
        return {"ok": True, "error": None, "text": "", "links": [],
                "content_type": "application/pdf", "latency_ms": 10.0}
    return {"ok": True, "error": None, "status": 200, "content_type": "text/html",
            "text": NATIVE_TEXT, "published_date": None, "latency_ms": 20.0,
            "links": [{"url": url + "/a", "anchor": "a", "sentence": "See a.",
                       "page_sentences": ["See a."]}]}


class FakeSearch:
    def __init__(self, gold_by_query, hit_rank, fail_every=0):
        self.gold, self.hit_rank, self.fail_every, self.n = gold_by_query, hit_rank, fail_every, 0
        self.count = 10

    def search(self, query):
        self.n += 1
        if self.fail_every and self.n % self.fail_every == 0:
            raise RuntimeError("boom")
        urls = [f"https://noise{i}.example/x" for i in range(1, 11)]
        if self.hit_rank:
            urls[self.hit_rank - 1] = self.gold[query]
        return {"http_status": 200, "n_requested": 10, "retries_429": 0, "wall_ms": 5.0,
                "raw": {}, "results": [{"rank": i, "url": u, "title": "", "snippet": ""}
                                       for i, u in enumerate(urls, 1)]}


class FetchDryRun(unittest.TestCase):
    def test_sample_is_deterministic_distinct_and_capped(self):
        rows = gold_rows()
        a = fetch_ab.draw_sample(rows, n=20, seed=1)
        b = fetch_ab.draw_sample(rows, n=20, seed=1)
        self.assertEqual([r["url"] for r in a], [r["url"] for r in b])
        self.assertEqual(len(a), 20)
        self.assertEqual(len({r["url"] for r in a}), 20)
        self.assertLessEqual(sum(r["host"] == "cve.threatint.com" for r in a), 2)  # 10% of 20
        self.assertNotEqual([r["url"] for r in a],
                            [r["url"] for r in fetch_ab.draw_sample(rows, n=20, seed=2)])

    def test_run_and_report_are_byte_stable(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            gold_path = tmp / "gold.jsonl"
            gold_path.write_text(gold.dumps_jsonl(gold_rows()))
            raw = tmp / "raw" / "fetch"
            sample = fetch_ab.draw_sample(gold.read_jsonl(gold_path), n=36, seed=7)
            counts = fetch_ab.run(sample, native_fn=fake_native, tinyfish_client=FakeTinyFish(),
                                  raw_dir=raw, log=lambda s: None)
            self.assertEqual(counts["native"], 36)
            self.assertEqual(counts["tinyfish"], 36)
            again = fetch_ab.run(sample, native_fn=fake_native,
                                 tinyfish_client=FakeTinyFish(), raw_dir=raw)
            self.assertEqual(again["native"], 0)
            self.assertEqual(again["skipped_existing"], 36)

            rows = report.fetch_rows(sample, raw)
            self.assertEqual(len(rows), 36)
            by_url = {r["url"]: r for r in rows}
            for url, r in by_url.items():
                if url.endswith("/0"):
                    self.assertEqual(r["native_failure_class"], "http_404")
                elif url.endswith("/1"):
                    self.assertEqual(r["native_failure_class"], "non_html")
                else:
                    self.assertEqual(r["native_failure_class"], "ok")
                    if url.endswith("/2"):
                        self.assertNotEqual(r["tinyfish_failure_class"], "ok")
                        self.assertIsNone(r["content_overlap"])
                    else:
                        self.assertEqual(r["tinyfish_failure_class"], "ok")
                        self.assertAlmostEqual(r["content_overlap"], 2 / 3, places=3)

            out1, out2 = tmp / "r1", tmp / "r2"
            for out in (out1, out2):
                report.write_all(gold_path=gold_path, gold_search_path=tmp / "none",
                                 raw_fetch=raw, out_dir=out, fetch_n=36, seed=7)
            self.assertEqual((out1 / "fetch.csv").read_bytes(), (out2 / "fetch.csv").read_bytes())
            self.assertEqual((out1 / "fetch_summary.json").read_bytes(),
                             (out2 / "fetch_summary.json").read_bytes())
            summary = json.loads((out1 / "fetch_summary.json").read_text())
            self.assertEqual(summary["n_urls"], 36)
            self.assertIn("native_failed_rescue", summary)


class SearchDryRun(unittest.TestCase):
    def test_accounting_mrr_and_variance(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            grows = [row(f"s{i}", f"https://pub{i}.example/story", "original") for i in range(8)]
            gpath = tmp / "gold_search.jsonl"
            gpath.write_text(gold.dumps_jsonl(grows))
            stories = search_ab.draw_stories(grows, n=5, seed=3)
            self.assertEqual(len(stories), 5)
            gold_by_query = {s["query"]: s["gold_urls"][0] for s in stories}
            raw = tmp / "raw" / "search"
            clients = {"brave": FakeSearch(gold_by_query, hit_rank=3),
                       "tinyfish": FakeSearch(gold_by_query, hit_rank=0, fail_every=4)}
            counts = search_ab.run(stories, clients, reps=3, raw_dir=raw, log=lambda s: None)
            self.assertEqual(counts["done"], 5 * 2 * 3)
            self.assertEqual(len(list(raw.glob("*.json"))), 30)

            # draw_stories(n=150) in write_all differs from n=5, so exercise rows directly
            srows = report.search_rows(stories, raw)
            self.assertEqual(len(srows), 30)
            brave_rows = [r for r in srows if r["arm"] == "brave"]
            self.assertTrue(all(r["rank"] == 3 and r["rr"] == 1 / 3 for r in brave_rows))
            tf_rows = [r for r in srows if r["arm"] == "tinyfish"]
            # 15 tinyfish calls, every 4th raises => calls 4, 8, 12
            self.assertEqual(sum(r["status"] == "error" for r in tf_rows), 3)
            summary, variance = report.search_summary(srows)
            self.assertAlmostEqual(summary["brave"]["per_rep"][1]["mrr"], 1 / 3)
            self.assertEqual(summary["brave"]["per_rep"][1]["recall_at_5"], 1.0)
            # recall@10 is never reported for Brave (count=5 live), only for TinyFish
            self.assertIsNone(summary["brave"]["per_rep"][1]["recall_at_10_not_comparable"])
            self.assertEqual(summary["tinyfish"]["per_rep"][1]["recall_at_10_not_comparable"], 0.0)
            self.assertEqual(summary["tinyfish"]["per_rep"][1]["recall_at_5"], 0.0)
            vrow = next(v for v in variance if v["arm"] == "brave" and v["metric"] == "mrr")
            self.assertEqual(vrow["n_reps"], 3)
            self.assertAlmostEqual(vrow["stdev"], 0.0)

    def test_missing_reps_are_accounted(self):
        with tempfile.TemporaryDirectory() as t:
            stories = [{"fingerprint": "x", "host": "h", "gold_urls": ["https://g.example/"],
                        "query": "q", "headline": "q"}]
            srows = report.search_rows(stories, Path(t))
            self.assertEqual(len(srows), 6)
            self.assertTrue(all(r["status"] == "missing" and r["error"] for r in srows))


if __name__ == "__main__":
    unittest.main()
