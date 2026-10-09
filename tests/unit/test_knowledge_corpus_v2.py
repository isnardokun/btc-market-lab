"""Offline reproducibility, attribution and adversarial retrieval tests for RAG v2."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from analysis import knowledge_rag as rag


class KnowledgeCorpusV2Tests(unittest.TestCase):
    def test_manifest_counts_families_and_sha(self):
        manifest = rag.corpus_manifest()
        self.assertEqual(manifest["version"], "2.0.0")
        self.assertEqual(manifest["schema_version"], 2)
        self.assertGreaterEqual(manifest["count"], 30)
        self.assertEqual(sum(manifest["families"].values()), manifest["count"])
        self.assertEqual(manifest["sha256"], hashlib.sha256(
            rag.CORPUS_PATH.read_bytes()).hexdigest())

    def test_no_duplicates_and_all_references_https_allowlisted(self):
        self.assertEqual(len(rag.CARDS), len(set(c.metric for c in rag.CARDS)))
        self.assertTrue(all(c.method and c.caveat and c.source_kind and c.url
                            for c in rag.CARDS))

    def test_exact_key_outranks_longer_names_and_preserves_old_interface(self):
        self.assertEqual(rag.retrieve("mvrv", 5)[0].metric, "mvrv")
        self.assertEqual(rag.retrieve("asopr", 5)[0].metric, "asopr")
        self.assertEqual(rag.retrieve("nupl", 5)[0].metric, "nupl")
        self.assertEqual(rag.retrieve("sth_sopr", 5)[0].metric, "sth_sopr")

    def test_new_topics_recover_domain_specific_evidence(self):
        probes = (
            ("fred_vintages", "macro"),
            ("futures_oi", "derivatives"),
            ("cpi", "macro"),
            ("supply_profit", "onchain"),
            ("point_in_time", "quality"),
            ("rbf", "protocol"),
        )
        for query, family in probes:
            with self.subTest(query=query):
                rows = rag.retrieve(query)
                self.assertEqual(rows[0].metric, query)
                self.assertEqual(rows[0].family, family)

    def test_family_filter_prevents_cross_domain_leakage(self):
        self.assertEqual(rag.retrieve("mvrv", families=("macro",)), [])
        result = rag.retrieve("inflación IPC", families=("macro",))
        self.assertTrue(result)
        self.assertTrue(all(c.family == "macro" for c in result))
        with self.assertRaises(ValueError):
            rag.retrieve("mvrv", families=("unknown",))

    def test_no_evidence_when_query_missing(self):
        self.assertEqual(rag.retrieve("zzqv99nothing"), [])
        self.assertEqual(rag.retrieve(""), [])
        self.assertEqual(rag.retrieve("mvrv", limit=0), [])

    def test_fts_query_injection_cannot_escape_literal_tokens(self):
        results = rag.retrieve('" OR fts5() ; DROP TABLE knowledge --', limit=4)
        self.assertIsInstance(results, list)
        self.assertLessEqual(len(results), 4)
        self.assertEqual(rag.retrieve("mvrv")[0].metric, "mvrv")

    def test_fts_fallback_without_extension(self):
        with patch.object(rag.sqlite3, "connect", side_effect=sqlite3.OperationalError):
            result = rag.retrieve("costo base sth")
        self.assertTrue(result)
        self.assertTrue(any(c.metric == "sth_cost" for c in result))

    def test_structured_evidence_has_sources_and_no_market_snapshot(self):
        bundle = rag.evidence_bundle("cpi", families=("macro",))
        self.assertEqual(bundle["corpus"]["count"], len(rag.CARDS))
        self.assertTrue(bundle["results"])
        self.assertTrue(all(row["methodology_url"].startswith("https://")
                            and row["caveat"] for row in bundle["results"]))
        self.assertNotIn("price", bundle["results"][0])
        self.assertIn("no observaciones de mercado", bundle["disclaimer"])

    def test_corpus_is_not_a_market_observation(self):
        text = rag.render_research_note({})
        self.assertEqual(text, "")
        self.assertEqual(rag.explain_snapshot({"mvrv": None}), [])
        text = rag.render_research_note({"mvrv": 1.2})
        self.assertIn("Consultar metodología", text)
        self.assertNotIn("probabilidad de", text)

    def test_reject_duplicate_slugs(self):
        payload = json.loads(rag.CORPUS_PATH.read_text())
        payload["records"].append(dict(payload["records"][0]))
        self._bad(payload, "Duplicate")

    def test_reject_foreign_lookalike_hostname(self):
        payload = json.loads(rag.CORPUS_PATH.read_text())
        payload["records"][0]["url"] = "https://researchbitcoin.net.attacker.org/fake"
        self._bad(payload, "Unapproved")

    def test_reject_non_https_or_embedded_credentials(self):
        for url in ("http://researchbitcoin.net/metrics/mvrv/",
                    "https://user:pass@researchbitcoin.net/metrics/mvrv/"):
            with self.subTest(url=url):
                payload = json.loads(rag.CORPUS_PATH.read_text())
                payload["records"][0]["url"] = url
                self._bad(payload, "Unapproved")

    def test_reject_blank_caveat_and_unknown_category(self):
        for field, val in (("caveat", ""), ("source_kind", "rumor"),
                           ("family", "speculation")):
            with self.subTest(field=field):
                payload = json.loads(rag.CORPUS_PATH.read_text())
                payload["records"][0][field] = val
                self._bad(payload, "")

    def test_json_sha_changes_without_modifying_original(self):
        payload = json.loads(rag.CORPUS_PATH.read_text())
        payload["records"][0]["method"] += " Verificación editorial adicional."
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "changed.json"
            p.write_text(json.dumps(payload, ensure_ascii=False))
            manifest = rag.corpus_manifest(p)
        self.assertNotEqual(manifest["sha256"], rag.corpus_manifest()["sha256"])
        self.assertEqual(manifest["count"], len(rag.CARDS))

    def test_rendered_html_carries_matching_corpus_sha_and_gate_rejects_tamper(self):
        import datetime as dt
        from validation.content_checks import audit_report_html
        html = ('<meta name="market-design-system" '
                'content="mercados-research-studio-v2">'
                + rag.render_research_note({"mvrv": 1.5}))
        self.assertIn('data-corpus-version="2.0.0"', html)
        self.assertIn('data-corpus-sha256="' + rag.corpus_manifest()["sha256"], html)
        day = dt.date(2026, 10, 9)
        errors = audit_report_html(html, report_day=day)
        self.assertFalse(any("RAG:" in issue for issue in errors), errors)
        changed = html.replace('data-corpus-sha256="' + rag.corpus_manifest()["sha256"],
                               'data-corpus-sha256="' + "0" * 64)
        issues = audit_report_html(changed, report_day=day)
        self.assertTrue(any("RAG: corpus publicado no coincide" in issue
                            for issue in issues), issues)
        missing = html.replace(' data-corpus-version="2.0.0"', "")
        issues = audit_report_html(missing, report_day=day)
        self.assertTrue(any("RAG: bloque metodológico sin versión" in issue
                            for issue in issues), issues)

    def _bad(self, payload, expected):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "corpus.json"
            p.write_text(json.dumps(payload, ensure_ascii=False))
            with self.assertRaisesRegex(ValueError, expected):
                rag.load_corpus(p)


if __name__ == "__main__":
    unittest.main()
