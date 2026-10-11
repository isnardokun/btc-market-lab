"""Package A direct library writer + whole daily report lock coordination.

One outer lock for an entire report avoids nested flock when its library
news archival inserts into SQLite. All disk writes are synthetic local
TemporaryDirectory fixtures. No Yahoo, Exa, FRED, Bitview or real DB is used.
"""
import ast
import contextlib
from io import StringIO
import os
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from analysis import daily_report as report
from ingestion import news_archive as news
from scripts.market_r10e11_writer_exclusion import (
    hold_writer_lock, WriterLockBusy, WriterLockUnsafe
)
from scripts.package_a_writer_guard import GLOBAL_WRITER_NAMESPACE as NAMESPACE

HOLDER = """
import sys
from scripts.market_r10e11_writer_exclusion import hold_writer_lock
with hold_writer_lock(sys.argv[1],namespace=sys.argv[2]):
    print("HELD",flush=True)
    sys.stdin.readline()
"""
SAMPLE={"BTC":[{"url":"https://example.com/market-news","title":"Synthetic BTC report",
                "source_type":"fixture","source":"offline","date":"2026-10-11"}]}


class PackageAReportLibraryWriterLockTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix="package-a-news-fixture-")
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.db=self.root/"news_synthetic.db"
        self.db.touch()
        self.locks=self.root/"locks"
        self.locks.mkdir()
        self.repo=Path(__file__).resolve().parents[2]

    def holder(self):
        env=dict(os.environ)
        env["PYTHONPATH"]=str(self.repo)+os.pathsep+env.get("PYTHONPATH","")
        p=subprocess.Popen(
            [sys.executable,"-c",HOLDER,str(self.locks),NAMESPACE],
            cwd=str(self.repo),env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,text=True,bufsize=1)
        def clean():
            if p.poll() is None: p.kill()
            p.communicate(timeout=5)
        self.addCleanup(clean)
        with selectors.DefaultSelector() as sel:
            sel.register(p.stdout,selectors.EVENT_READ)
            self.assertTrue(sel.select(timeout=5))
        self.assertEqual(p.stdout.readline().strip(),"HELD")
        return p

    def test_news_library_optin_creates_fixture_schema_under_single_lock(self):
        count=news.archive_news(
            self.db,"2026-10-11",SAMPLE,lock_directory=self.locks)
        self.assertEqual(count,1)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM research_news_archive").fetchone()[0],1)
        self.assertEqual((self.locks/(NAMESPACE+".lock")).stat().st_mode & 0o777,0o600)

    def test_news_library_replay_idempotent_under_same_lock(self):
        news.archive_news(self.db,"2026-10-11",SAMPLE,lock_directory=self.locks)
        self.assertEqual(
            news.archive_news(self.db,"2026-10-11",SAMPLE,lock_directory=self.locks),0)

    def test_news_library_busy_must_not_open_sqlite_or_access_path(self):
        with hold_writer_lock(self.locks,namespace=NAMESPACE):
            with patch.object(news.sqlite3,"connect",
                              side_effect=AssertionError("Must not open database")):
                with self.assertRaises(WriterLockBusy):
                    news.archive_news(self.db,"2026-10-11",SAMPLE,
                                      lock_directory=self.locks)
        with sqlite3.connect(self.db) as conn:
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name='research_news_archive'").fetchone())

    def test_news_library_child_process_busy_raises_not_fake_count(self):
        child=self.holder()
        with self.assertRaises(WriterLockBusy):
            news.archive_news(self.db,"2026-10-11",SAMPLE,lock_directory=self.locks)
        self.assertIsNone(child.poll())
        child.stdin.write("\n")
        child.stdin.flush()
        self.assertEqual(child.wait(timeout=5),0)
        self.assertEqual(
            news.archive_news(self.db,"2026-10-11",SAMPLE,lock_directory=self.locks),1)

    def test_news_library_invalid_lock_directory_fails_before_sql(self):
        missing=self.root/"never-created"
        with self.assertRaises(WriterLockUnsafe):
            news.archive_news(self.db,"2026-10-11",SAMPLE,lock_directory=missing)
        self.assertFalse(missing.exists())

    def test_news_library_legacy_default_retained_explicitly_unprotected(self):
        with hold_writer_lock(self.locks,namespace=NAMESPACE):
            count=news.archive_news(self.db,"2026-10-11",SAMPLE)
        self.assertEqual(count,1)

    def test_whole_report_cli_locks_before_any_delegate_http_sql_or_files(self):
        def worker(*a,**kw):
            with self.assertRaises(WriterLockBusy):
                with hold_writer_lock(self.locks,namespace=NAMESPACE):
                    pass
            return 27
        with patch.object(report,"_generate_daily_report",side_effect=worker) as task:
            self.assertEqual(report.main(["--global-lock-dir",str(self.locks)]),27)
        task.assert_called_once()

    def test_whole_report_cli_busy_rc3_before_delegate(self):
        with hold_writer_lock(self.locks,namespace=NAMESPACE):
            with patch.object(report,"_generate_daily_report",
                              side_effect=AssertionError("Report must not start")) as task:
                with contextlib.redirect_stderr(StringIO()) as output:
                    status=report.main(["--global-lock-dir",str(self.locks)])
        self.assertEqual(status,3)
        self.assertIn("BLOCKED_WRITER_BUSY",output.getvalue())
        task.assert_not_called()

    def test_whole_report_cli_busy_child_process(self):
        child=self.holder()
        with patch.object(report,"_generate_daily_report",
                          side_effect=AssertionError("Must not start")) as task:
            with contextlib.redirect_stderr(StringIO()):
                self.assertEqual(report.main(["--global-lock-dir",str(self.locks)]),3)
        task.assert_not_called()
        child.stdin.write("\n")
        child.stdin.flush()
        self.assertEqual(child.wait(timeout=5),0)

    def test_whole_report_parent_lock_covers_real_nested_news_library_without_relocking(self):
        def fake_report():
            # The underlying daily report's archive_news call does NOT pass
            # another lock directory, because the parent owns that lock.
            with self.assertRaises(WriterLockBusy):
                with hold_writer_lock(self.locks,namespace=NAMESPACE):
                    pass
            return news.archive_news(self.db,"2026-10-11",SAMPLE)
        with patch.object(report,"_generate_daily_report",side_effect=fake_report):
            self.assertEqual(report.main(["--global-lock-dir",str(self.locks)]),1)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM research_news_archive").fetchone()[0],1)

    def test_whole_report_legacy_unflagged_mode_retains_prior_behavior(self):
        with hold_writer_lock(self.locks,namespace=NAMESPACE):
            with patch.object(report,"_generate_daily_report",return_value=19):
                self.assertEqual(report.main([]),19)

    def test_whole_report_absent_path_blocks_before_http(self):
        missing=self.root/"missing"
        with patch.object(report,"_generate_daily_report",
                          side_effect=AssertionError("Unexpected report")) as task:
            with self.assertRaises(WriterLockUnsafe):
                report.main(["--global-lock-dir",str(missing)])
        task.assert_not_called()

    def test_news_invalid_fields_preserves_original_url_and_title_filtering(self):
        bad={"BTC":[{"url":"http://example.org/insecure","title":"Must be rejected"},
                    {"url":"https://example.org/ok","title":""}]}
        self.assertEqual(news.archive_news(self.db,"2026-10-11",bad,
                                          lock_directory=self.locks),0)

    def test_report_full_pipeline_not_running_without_explicit_user_action(self):
        text=(self.repo/"scripts"/"daily.sh").read_text(encoding="utf-8")
        self.assertNotIn("--global-lock-dir",text)
        self.assertIn("python3 analysis/daily_report.py",text)
        source=(self.repo/"analysis"/"daily_report.py").read_text(encoding="utf-8")
        self.assertIn("run_with_optional_writer_lock(",source)
        self.assertIn("--global-lock-dir",source)
        # No lock flag is forwarded to nested news_archive inside the
        # parent's report worker; avoid self-deadlock from a second flock.
        tree=ast.parse(source)
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)
               and isinstance(n.func,ast.Name) and n.func.id=="archive_news"]
        self.assertEqual(len(calls),1)
        self.assertFalse(any(kw.arg=="lock_directory" for kw in calls[0].keywords))

    def test_runner_registers_report_library_test_once(self):
        tree=ast.parse((self.repo/"tests"/"run_all.py").read_text(encoding="utf-8"))
        rows=[ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
              and any(isinstance(v,ast.Name) and v.id=="TESTS" for v in n.targets)]
        self.assertEqual(len(rows),1)
        self.assertEqual(sum("test_package_a_report_library_lock" in cmd
                             for _,cmd in rows[0]),1)
        self.assertGreaterEqual(len(rows[0]),61)


if __name__=="__main__":
    unittest.main()
