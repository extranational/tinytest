"""Pin check and subprocess plumbing of the native arm. No CyberPulse, no network."""

import dataclasses
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tinytest import fetch_ab, native_adapter  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class PinCheck(unittest.TestCase):
    def test_pinned_head_parses_and_ignores_todo(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "PINNED.md"
            p.write_text("- cyberpulse_head: TODO\n")
            self.assertIsNone(native_adapter.pinned_head(p))
            p.write_text("- cyberpulse_head: ABCDEF1234\n")
            self.assertEqual(native_adapter.pinned_head(p), "abcdef1234")

    def test_committed_pins_are_present_and_well_formed(self):
        pin = native_adapter.pinned_head()
        self.assertEqual(pin, "368e2722de832d6e82711b08b5c7c49da9d2c46e")
        text = native_adapter.PINNED_MD.read_text()
        for key in ("enrich_py_sha256", "websearch_py_sha256"):
            m = re.search(rf"^- {key}: ([0-9a-f]{{64}})$", text, re.MULTILINE)
            self.assertIsNotNone(m, key)
        self.assertNotIn("TODO", text.split("- cyberpulse_head:")[1].split("\n\n")[0])

    def test_main_refuses_when_checkout_head_differs_from_pin(self):
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            git(repo, "init", "-q")
            (repo / "f").write_text("x")
            git(repo, "add", "f")
            git(repo, "commit", "-q", "-m", "c")
            self.assertFalse(git(repo, "rev-parse", "HEAD").startswith("368e2722"))
            with mock.patch.dict(os.environ, {"CYBERPULSE_PATH": str(repo)}), \
                    mock.patch.object(native_adapter, "fetch") as fetch:
                self.assertEqual(native_adapter.main(["https://a.example"]), 2)
                fetch.assert_not_called()

    def test_unset_pin_refuses(self):
        with self.assertRaises(RuntimeError):
            native_adapter.check_pin(Path("."), None)

    def test_head_match_and_mismatch(self):
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            git(repo, "init", "-q")
            (repo / "f").write_text("x")
            git(repo, "add", "f")
            git(repo, "commit", "-q", "-m", "c")
            head = git(repo, "rev-parse", "HEAD")
            self.assertEqual(native_adapter.check_pin(repo, head[:10]), head)
            with self.assertRaises(RuntimeError) as ctx:
                native_adapter.check_pin(repo, "0" * 10)
            self.assertIn("refusing", str(ctx.exception))

    def test_main_refuses_with_exit_2(self):
        with mock.patch.object(native_adapter, "pinned_head", return_value=None):
            self.assertEqual(native_adapter.main(["https://a.example"]), 2)
        self.assertEqual(native_adapter.main([]), 2)

    def test_link_dict_carries_citation_fields(self):
        @dataclasses.dataclass
        class PageLink:
            url: str
            anchor: str
            sentence: str
            page_sentences: list

        d = native_adapter._link_dict(PageLink("https://x.example", "anc", "A sentence.", ["A sentence."]))
        self.assertEqual(set(d), {"url", "anchor", "sentence", "page_sentences"})
        self.assertEqual(d["anchor"], "anc")


class NativeFetchPlumbing(unittest.TestCase):
    def run_fake(self, returncode, stdout="", stderr=""):
        proc = subprocess.CompletedProcess([], returncode, stdout, stderr)
        with mock.patch.object(fetch_ab.subprocess, "run", return_value=proc) as run:
            return run, fetch_ab.native_fetch("https://a.example/p")

    def test_parses_one_json_object(self):
        rec = {"ok": True, "status": 200, "text": "t", "latency_ms": 5.0,
               "links": [{"url": "u", "anchor": "a", "sentence": "s", "page_sentences": []}]}
        run, out = self.run_fake(0, json.dumps(rec))
        self.assertEqual(out, rec)
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:3], ["-m", "tinytest.native_adapter"])
        self.assertEqual(argv[3], "https://a.example/p")

    def test_refusal_aborts_run(self):
        with self.assertRaises(fetch_ab.NativeRefused):
            self.run_fake(2, "", "HEAD mismatch")

    def test_garbage_output_is_a_failure_record(self):
        _, out = self.run_fake(1, "not json", "Traceback...")
        self.assertFalse(out["ok"])
        self.assertIn("adapter exit 1", out["error"])

    def test_timeout_is_a_failure_record(self):
        with mock.patch.object(fetch_ab.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("x", 1)):
            out = fetch_ab.native_fetch("https://a.example")
        self.assertFalse(out["ok"])
        self.assertIn("timeout", out["error"])


class BaselineDict(unittest.TestCase):
    def test_fetch_failures_dict_is_counted(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as t:
            db = Path(t) / "c.db"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE runs (at TEXT, kind TEXT, summary TEXT)")
            con.execute("INSERT INTO runs VALUES ('1','digest',?)",
                        (json.dumps({"fetch_failures": {"firstpost.com": 1, "redd.it": 2}}),))
            con.execute("INSERT INTO runs VALUES ('2','digest',?)",
                        (json.dumps({"fetch_failures": {"firstpost.com": 3}}),))
            con.execute("INSERT INTO runs VALUES ('3','digest','{}')")
            con.commit()
            con.close()
            out = fetch_ab.native_failure_baseline(db)
        self.assertEqual(out["total_failures"], 6)
        self.assertEqual(out["per_host"], {"firstpost.com": 4, "redd.it": 2})
        self.assertEqual(out["runs_with_fetch_failures"], 2)


if __name__ == "__main__":
    unittest.main()
