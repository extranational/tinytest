import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tinytest import metrics  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


class ContentOverlap(unittest.TestCase):
    def test_hand_computed_value(self):
        # native has 3 qualifying sentences ("Read more." is < 40 chars and ignored);
        # the TinyFish HTML contains sentences 1 and 2 (2 differs only by punctuation/<b>)
        # and sentence 3 only inside a <script>, which must not count => 2/3.
        native = (FIX / "native_article.txt").read_text()
        tf_text = metrics.strip_html((FIX / "tinyfish_article.html").read_text())
        self.assertEqual(len(metrics.sentences(native)), 3)
        self.assertAlmostEqual(metrics.content_overlap(native, tf_text), 2 / 3)

    def test_undefined_without_native_sentences(self):
        self.assertIsNone(metrics.content_overlap("Too short.", "anything at all"))

    def test_full_and_zero(self):
        native = (FIX / "native_article.txt").read_text()
        self.assertEqual(metrics.content_overlap(native, native), 1.0)
        self.assertEqual(metrics.content_overlap(native, "unrelated text"), 0.0)

    def test_link_url_overlap(self):
        got = metrics.link_url_overlap(
            ["https://a.example/x/", "https://b.example/y"],
            ["https://a.example/x?utm_source=z"],
        )
        self.assertEqual(got, 0.5)
        self.assertIsNone(metrics.link_url_overlap([], ["https://a.example"]))

    def test_link_url_overlap_accepts_native_dict_links(self):
        native = [
            {"url": "https://a.example/x/", "anchor": "a", "sentence": "S.", "page_sentences": []},
            {"url": "https://b.example/y", "anchor": "", "sentence": "", "page_sentences": []},
        ]
        self.assertEqual(metrics.link_url_overlap(native, ["https://a.example/x"]), 0.5)
        self.assertEqual(metrics.link_url_overlap(native, native), 1.0)
        self.assertIsNone(metrics.link_url_overlap([{"url": ""}], ["https://a.example"]))


class FailureClasses(unittest.TestCase):
    def test_non_html_is_classified_not_crashed(self):
        self.assertEqual(
            metrics.classify_failure(True, content_type="application/pdf", text=""), "non_html"
        )

    def test_classes(self):
        c = metrics.classify_failure
        self.assertEqual(c(True, text="body"), "ok")
        self.assertEqual(c(True, text="  "), "empty_body")
        self.assertEqual(c(False, status=403), "anti_bot")
        self.assertEqual(c(False, status=404), "http_404")
        self.assertEqual(c(False, error="ReadTimeout: timed out"), "timeout")
        self.assertEqual(c(False, error="gaierror: getaddrinfo failed"), "dns")
        self.assertEqual(c(False, error="empty extraction"), "empty_body")
        self.assertEqual(c(False, error="non-html content-type: application/pdf"), "non_html")
        self.assertEqual(c(False, status=404, error="HTTP 404"), "http_404")
        self.assertEqual(c(False, error="boom"), "other_error")


class SearchMetrics(unittest.TestCase):
    def test_hit_at_rank_3_gives_mrr_one_third(self):
        results = ["https://a.example/1", "https://b.example/2",
                   "https://Gold.example/post?utm_source=x", "https://d.example/4"]
        rank = metrics.rank_of_gold(results, ["https://gold.example/post"])
        self.assertEqual(rank, 3)
        self.assertAlmostEqual(metrics.reciprocal_rank(rank), 1 / 3)
        self.assertEqual(metrics.recall_at_k(rank, 5), 1)
        self.assertEqual(metrics.recall_at_k(rank, 2), 0)

    def test_miss(self):
        rank = metrics.rank_of_gold(["https://a.example/1"], ["https://gold.example/p"])
        self.assertIsNone(rank)
        self.assertEqual(metrics.reciprocal_rank(rank), 0.0)
        self.assertEqual(metrics.recall_at_k(rank, 10), 0)


class Helpers(unittest.TestCase):
    def test_percentile_nearest_rank(self):
        vals = list(range(1, 11))
        self.assertEqual(metrics.percentile(vals, 50), 5)
        self.assertEqual(metrics.percentile(vals, 90), 9)
        self.assertEqual(metrics.percentile(vals, 95), 10)
        self.assertIsNone(metrics.percentile([], 50))

    def test_normalize_url(self):
        # dedupe.item_key: fragment dropped, trailing "/" stripped, utm_/ref= removed,
        # lowercased; www. kept, scheme untouched, params unsorted
        n = metrics.normalize_url
        self.assertEqual(n(" http://WWW.Ex.com/A/?utm_x=1&b=2&a=1#f "),
                         "http://www.ex.com/a/&b=2&a=1")  # real quirk: leading ?utm_ removed
        self.assertEqual(n("https://ex.com/a?b=1&ref=tw&c=2"), "https://ex.com/a?b=1&c=2")
        self.assertEqual(n("https://ex.com/"), "https://ex.com")
        self.assertEqual(n("https://ex.com/a?utm_x=1"), "https://ex.com/a")
        self.assertEqual(n(""), "")
        self.assertNotEqual(n("https://www.ex.com/a"), n("https://ex.com/a"))
        self.assertNotEqual(n("http://ex.com/a"), n("https://ex.com/a"))
        self.assertEqual(n(n("https://ex.com/a/")), n("https://ex.com/a/"))


if __name__ == "__main__":
    unittest.main()
