import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tinytest import gold  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def make_db(directory: Path) -> Path:
    path = directory / "ledger.db"
    con = sqlite3.connect(path)
    con.executescript((FIX / "gold_fixture.sql").read_text())
    con.commit()
    con.close()
    return path


class GoldBuild(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = make_db(self.tmp)
        self.content = FIX / "content"

    def tearDown(self):
        self._tmp.cleanup()

    def test_counts_and_eligibility(self):
        summary = gold.build(self.db, self.content, self.tmp / "out")
        self.assertEqual(summary["links"], 6)
        self.assertEqual(summary["by_role"], {"coverage": 2, "original": 3, "seed": 1})
        self.assertEqual(summary["stories_with_links"], 3)
        self.assertEqual(summary["stories_in_ledger"], 4)  # f4 has no links
        self.assertEqual(summary["by_month"], {"2026-08": 2, "2026-09": 4})
        self.assertEqual(summary["by_host"]["news.example.com"], 2)
        # github.com original is feed-native => excluded; coverage/seed excluded by role
        search = gold.read_jsonl(self.tmp / "out" / "gold_search.jsonl")
        self.assertEqual(sorted(r["fingerprint"] for r in search), ["f1", "f2"])

    def test_row_fields_and_query(self):
        gold.build(self.db, self.content, self.tmp / "out")
        rows = gold.read_jsonl(self.tmp / "out" / "gold.jsonl")
        f2 = next(r for r in rows if r["fingerprint"] == "f2" and r["role"] == "original")
        self.assertEqual(f2["query"], "Big Corp breach")  # no CVEs (default '[]')
        self.assertEqual(f2["host"], "corp-report.example.org")
        f1 = next(r for r in rows if r["fingerprint"] == "f1" and r["role"] == "original")
        # dedupe.item_key keeps www.; the ?utm_ param is removed
        self.assertEqual(f1["url"], "https://www.acme-sec.example/blog/vpn-flaw")
        self.assertIn("utm_source", f1["url_raw"])
        # _query_for: '-' -> ' ', then CVE ids appended space-separated
        self.assertEqual(f1["query"], "Acme VPN flaw exploited CVE-2026-0001 CVE-2026-0002")
        f3 = next(r for r in rows if r["fingerprint"] == "f3" and r["role"] == "original")
        self.assertEqual(f3["query"], "Repo malware wave")  # NULL cve_ids tolerated

    def test_query_for(self):
        self.assertEqual(gold.query_for("a-b c", '["CVE-1"]'), "a b c CVE-1")
        self.assertEqual(gold.query_for("a-b", "[]"), "a b")
        self.assertEqual(gold.query_for("a-b", None), "a b")

    def test_byte_identical_across_runs(self):
        gold.build(self.db, self.content, self.tmp / "a")
        gold.build(self.db, self.content, self.tmp / "b")
        for name in ("gold.jsonl", "gold_search.jsonl", "gold_summary.json"):
            self.assertEqual((self.tmp / "a" / name).read_bytes(),
                             (self.tmp / "b" / name).read_bytes(), name)

    def test_crosscheck_flags_mdx_only_url(self):
        summary = gold.build(self.db, self.content, self.tmp / "out")
        cc = summary["crosscheck"]
        self.assertEqual(cc["mdx_files"], 1)
        self.assertEqual(cc["in_mdx_not_ledger_sample"], ["https://extra.example.net/post"])
        self.assertEqual(cc["in_mdx_not_ledger_count"], 1)

    def test_discrepancies_reported_against_ab_plan(self):
        summary = gold.build(self.db, self.content, self.tmp / "out")
        self.assertTrue(any(d.startswith("links:") for d in summary["discrepancies_vs_ab_plan"]))

    def test_db_opened_read_only(self):
        before = self.db.read_bytes()
        gold.read_ledger(self.db)
        self.assertEqual(before, self.db.read_bytes())
        with self.assertRaises(sqlite3.OperationalError):
            gold.read_ledger(self.tmp / "missing.db")

    def test_summary_is_valid_json(self):
        gold.build(self.db, self.content, self.tmp / "out")
        json.loads((self.tmp / "out" / "gold_summary.json").read_text())


if __name__ == "__main__":
    unittest.main()
