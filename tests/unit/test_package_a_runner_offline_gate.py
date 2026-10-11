"""Guarantee the default 56-suite registry cannot access the live pipeline DB.

Static checks only: NEVER import tests.integration.test_pipeline (its import
itself reads the hard-coded production SQLite path).
"""
import ast
from pathlib import Path
import unittest


class TestPackageAOfflineRunnerSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parents[2]
        cls.runner=root/"tests"/"run_all.py"
        cls.pipeline=root/"tests"/"integration"/"test_pipeline.py"
        cls.code=cls.runner.read_text(encoding="utf-8")
        cls.tree=ast.parse(cls.code)
        cls.entries=[ast.literal_eval(n.value) for n in cls.tree.body
                     if isinstance(n,ast.Assign)
                     and any(isinstance(x,ast.Name) and x.id=="TESTS"
                             for x in n.targets)]
        cls.live=[ast.literal_eval(n.value) for n in cls.tree.body
                  if isinstance(n,ast.Assign)
                  and any(isinstance(x,ast.Name) and x.id=="LIVE_ONLY_SUITES"
                          for x in n.targets)]

    def test_registry_contains_every_suite_once(self):
        self.assertEqual(len(self.entries),1)
        self.assertEqual(len(self.entries[0]),70)
        self.assertEqual(len({n for n,c in self.entries[0]}),70)
        self.assertEqual(len({c for n,c in self.entries[0]}),70)

    def test_pipeline_is_explicitly_designated_live_only(self):
        self.assertEqual(len(self.live),1)
        self.assertEqual(self.live[0],{"Integration: Pipeline"})
        self.assertEqual(
            [c for n,c in self.entries[0] if n=="Integration: Pipeline"],
            ["python3 tests/integration/test_pipeline.py"])

    def test_old_pipeline_still_has_hardcoded_host_path(self):
        source=self.pipeline.read_text(encoding="utf-8")
        self.assertIn('/home/ignotus/btc-research',source)
        self.assertIn('LATEST_DATE = latest_daily_date()',source)
        self.assertNotIn('from tests.integration',self.code)

    def test_live_suite_skip_must_precede_subprocess_run(self):
        tree=self.tree
        loops=[n for n in tree.body if isinstance(n,ast.For)
               and isinstance(n.target,ast.Tuple)
               and any(isinstance(e,ast.Name) and e.id=="cmd" for e in n.target.elts)]
        self.assertEqual(len(loops),1)
        main_loop=loops[0]
        skip=[n for n in main_loop.body if isinstance(n,ast.If)
              and isinstance(n.test,ast.Compare)
              and any(isinstance(x,ast.Name) and x.id=="LIVE_ONLY_SUITES"
                      for x in n.test.comparators)]
        self.assertEqual(len(skip),1)
        self.assertTrue(any(isinstance(n,ast.Continue) for n in ast.walk(skip[0])))
        pos=main_loop.body.index(skip[0])
        calls=[i for i,n in enumerate(main_loop.body)
               if any(isinstance(x,ast.Call)
                      and isinstance(x.func,ast.Attribute)
                      and isinstance(x.func.value,ast.Name)
                      and x.func.value.id=="subprocess" and x.func.attr=="run"
                      for x in ast.walk(n))]
        self.assertTrue(calls)
        self.assertGreater(min(calls),pos)

    def test_runner_reports_explicitly_skipped_not_fake_pass(self):
        for label in ("OFFLINE_SUITES_EXECUTED","OFFLINE_SUITES_PASSED",
                      "LIVE_SUITES_SKIPPED","LIVE_STATUS=NOT_TESTED_NOT_AUTHORIZED"):
            self.assertIn(label,self.code)
        self.assertIn('sys.exit(0 if all_ok else 1)',self.code)

    def test_no_true_or_implicit_user_authorization_in_suite_gate(self):
        self.assertNotIn("os.environ.get('ALLOW_LIVE'",self.code)
        self.assertNotIn('BTC_MARKET_LAB_LIVE_INTEGRATION_APPROVED',self.code)
        self.assertNotIn('runpy.run_path',self.code)

    def test_runner_includes_safety_unit_suite_exactly_once(self):
        self.assertEqual(sum("test_package_a_runner_offline_gate" in c
                             for n,c in self.entries[0]),1)


if __name__=="__main__":
    unittest.main()
