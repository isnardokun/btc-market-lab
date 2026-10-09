#!/usr/bin/env python3
"""Offline GitHub bridge regressions; no gh or network required."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import hermes_bridge as bridge


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "reports" / "portable").mkdir(parents=True)
        (self.base / "reports" / "bridge").mkdir(parents=True)
        self.log = self.base / "command.log"
        self.log.write_text("tests: FAIL\nFRED_API_KEY=0123456789abcdef0123456789abcdef\n"
                            "Sensitive bot token 123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop\n"
                            "/home/test-user/btc-research\n",
                            encoding="utf-8")
        self.html = self.base / "reports" / "portable" / "daily_report_2026-10-08.html"
        self.html.write_text("<!doctype html><html><head><style>body{color:red}</style></head>"
                             "<body><svg></svg><a href='https://example.org'>Noticia</a></body></html>",
                             encoding="utf-8")

    def fake_machine(self):
        return {"git_commit": "a" * 40, "git_branch": "master", "db_present": False}

    def test_scrub_known_secrets(self):
        raw = self.log.read_text()
        s = bridge.scrub(raw)
        self.assertNotIn("0123456789abcdef0123456789abcdef", s)
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnop", s)
        self.assertNotIn("/home/test-user", s)
        bridge.safe(s, "log")
        with self.assertRaises(ValueError):
            bridge.safe(raw, "raw")

    def test_create_failed_bundle_includes_html_when_requested(self):
        with patch.object(bridge, "ROOT", self.base), patch.object(bridge, "OUT", self.base / "reports" / "bridge"), \
             patch.object(bridge, "machine", self.fake_machine):
            result = bridge.prepare(self.log, 7, True, self.html, False)
        self.assertTrue((result / "report.html").exists())
        obj = json.loads((result / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(obj["result"], "FAIL")
        self.assertEqual(obj["exit_code"], 7)
        self.assertEqual(obj["report"]["kind"], "manual")
        self.assertEqual(obj["report"]["source_sha256"], bridge.sha256(self.html.read_bytes()))
        self.assertEqual(obj["machine"]["git_commit"], "a" * 40)
        self.assertIn("[REDACTED]", (result / "log.txt").read_text())
        self.assertNotIn("test-user", (result / "log.txt").read_text())

    def test_no_html_uploaded_by_default(self):
        with patch.object(bridge, "ROOT", self.base), patch.object(bridge, "OUT", self.base / "reports" / "bridge"), \
             patch.object(bridge, "machine", self.fake_machine):
            packet = bridge.prepare(self.log, 0, False, self.html, False)
        self.assertFalse((packet / "report.html").exists())

    def test_html_with_secret_rejected(self):
        self.html.write_text("<html>token=abcdefghijklmnopqrstuvwxyz123456789012345</html>")
        with patch.object(bridge, "ROOT", self.base), patch.object(bridge, "OUT", self.base / "reports" / "bridge"), \
             patch.object(bridge, "machine", self.fake_machine):
            with self.assertRaisesRegex(ValueError, "Potencial"):
                bridge.prepare(self.log, 1, True, self.html, False)

    def test_publication_requires_explicit_approval(self):
        with self.assertRaisesRegex(ValueError, "approve-public"):
            bridge.publish(self.base, approve_public=False)

    def test_shell_wrapper_syntax(self):
        script = Path(__file__).resolve().parents[2] / "scripts" / "hermes_review_cycle.sh"
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_secrets_committed_in_scraping_docs(self):
        text = (Path(__file__).resolve().parents[2] / "docs" / "SCRAPING_TOOLS.md").read_text()
        bridge.safe(text, "docs/SCRAPING_TOOLS.md")


if __name__ == "__main__":
    unittest.main()
