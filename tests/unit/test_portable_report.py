#!/usr/bin/env python3
"""Offline HTML and Hermes sendDocument regression tests (no network, no DB)."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rendering.portable_report import export, make_portable
from scripts import send_report


class PortableReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / "daily_report_2026-10-08.html"
        self.gate = self.root / "gate_2026-10-08.json"
        self.output = self.root / "portable" / self.report.name
        self.html = (
            '<!doctype html><html><head><link rel="stylesheet" '
            'href="https://fonts.googleapis.com/css2?family=Manrope">'
            '<style>body{font-family:Manrope,sans-serif}</style></head>'
            '<body><svg><line x1="0" y1="0"/></svg>'
            '<a href="https://example.org/article">Fuente</a></body></html>'
        )
        self.report.write_text(self.html, encoding="utf-8")
        self.make_gate()

    def make_gate(self, passed=True, digest=None):
        content = self.report.read_bytes()
        self.gate.write_text(json.dumps({
            "date": "2026-10-08",
            "report": str(self.report),
            "report_sha256": digest or hashlib.sha256(content).hexdigest(),
            "pass": passed,
            "critical_count": 0 if passed else 1,
            "score_total": 99,
        }), encoding="utf-8")

    def test_offline_export_keeps_svg_and_hyperlinks(self):
        export(self.report, self.gate, self.output)
        text = self.output.read_text(encoding="utf-8")
        self.assertIn("<svg>", text)
        self.assertIn('href="https://example.org/article"', text)
        self.assertNotIn("fonts.googleapis.com", text)
        manifest = json.loads(Path(str(self.output) + ".manifest.json").read_text())
        self.assertEqual(manifest["portable_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())

    def test_rejected_gate_blocks_portable_file(self):
        self.make_gate(passed=False)
        with self.assertRaisesRegex(ValueError, "rechazó"):
            export(self.report, self.gate, self.output)
        self.assertFalse(self.output.exists())

    def test_modified_report_blocks_export(self):
        self.make_gate(digest="0" * 64)
        with self.assertRaisesRegex(ValueError, "hash"):
            export(self.report, self.gate, self.output)

    def test_external_image_blocks_portable_file(self):
        self.assertRaises(ValueError, make_portable,
                          '<html><head><style>body{color:red}</style></head>'
                          '<body><img src="https://example.org/p.png"></body></html>')

    def test_telegram_receipt_and_multipart_without_network(self):
        export(self.report, self.gate, self.output)
        with patch.object(send_report, "PORTABLE_DIR", self.output.parent):
            meta, content = send_report.validate_attachment(self.output)
        body, content_type = send_report.create_multipart("123", self.output.name, content, "Resumen")
        self.assertIn("multipart/form-data", content_type)
        self.assertIn(b"Content-Type: text/html", body)
        self.assertIn(b"chat_id", body)
        self.assertEqual(meta["report_date"], "2026-10-08")

    def test_edited_portable_file_cannot_be_sent(self):
        export(self.report, self.gate, self.output)
        self.output.write_bytes(self.output.read_bytes() + b"tampered")
        with patch.object(send_report, "PORTABLE_DIR", self.output.parent):
            with self.assertRaisesRegex(ValueError, "manifest"):
                send_report.validate_attachment(self.output)


if __name__ == "__main__":
    unittest.main()
