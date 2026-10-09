#!/usr/bin/env python3
"""
tests/run_all.py
================
Runner centralizado: unit + integration tests.
Ejecutar con: python3 tests/run_all.py
"""
import subprocess, sys, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)

TESTS = [
    ("Unit: Indicators", "python3 tests/unit/test_indicators.py"),
    ("Unit: Metric Registry", "python3 tests/unit/test_metric_registry.py"),
    ("Unit: Portable HTML / Telegram", "python3 tests/unit/test_portable_report.py"),
    ("Unit: Hermes Skill", "python3 tests/unit/test_hermes_skill.py"),
    ("Unit: Hermes Bridge", "python3 tests/unit/test_hermes_bridge.py"),
    ("Unit: Financial Integrity", "python3 -m unittest -v tests.unit.test_financial_integrity"),
    ("Unit: Editorial Integrity", "python3 -m unittest -v tests.unit.test_editorial_integrity"),
    ("Unit: Ingest/News Quality", "python3 -m unittest -v tests.unit.test_ingest_and_news_quality"),
    ("Unit: Completed Daily Candles", "python3 -m unittest -v tests.unit.test_daily_close_cutoff"),
    ("Unit: 52-week/Price Archive", "python3 -m unittest -v tests.unit.test_52week_and_partial_archive"),
    ("Unit: Archive/UTC Reconciliation", "python3 -m unittest -v tests.unit.test_archive_reconciliation"),
    ("Integration: Pipeline", "python3 tests/integration/test_pipeline.py"),
]

results = []
for name, cmd in TESTS:
    print(f"\n{'='*60}")
    print(f"  {name}")
    print('='*60)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    ok = r.returncode == 0
    results.append((name, ok))
    if ok:
        print(f"✅ PASS")
    else:
        print(f"❌ FAIL\n{r.stderr[-500:]}")
        # Print last 20 lines of output
        lines = r.stdout.strip().split('\n')
        print("Output:")
        for l in lines[-20:]:
            print(f"  {l}")

print(f"\n{'='*60}")
print("  RESULTS")
print('='*60)
for name, ok in results:
    status = "✅ PASS" if ok else "❌ FAIL"
    print(f"  {status}  {name}")

all_ok = all(ok for _, ok in results)
print(f"\n{'✅ All tests passed' if all_ok else '❌ Some tests failed'}")
sys.exit(0 if all_ok else 1)
