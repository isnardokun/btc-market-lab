"""Export ONLY synthetic installed-schema report/gate for local visual QA.

The structural stand-in generator belongs to tests, never production DDL.
All fake payloads reuse the existing B example jobs. No native credentials.
"""
import argparse
from contextlib import ExitStack
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tests.unit.installed_fixture_helpers import new_fixture, ASOF, CAPTURE
from scripts.package_b_report_fixture import example_jobs
from scripts.installed_schema_pipeline import prepare_installed_run, run_installed_pipeline
from ingestion.compatibility_acquisition import request_from_job, FakeTransport
from rendering.portable_report import atomic_write


def main(argv=None):
    parser=argparse.ArgumentParser(description='Synthetic installed-schema visual fixture export')
    parser.add_argument('--output-dir',type=Path,required=True);args=parser.parse_args(argv)
    if args.output_dir.exists() or args.output_dir.is_symlink(): parser.error('New output directory required')
    args.output_dir.mkdir(mode=0o700,parents=True)
    case=unittest.TestCase()
    try:
        root,path,locks=new_fixture(case)
        jobs=example_jobs();requests=tuple(request_from_job(j) for j in jobs)
        transport=FakeTransport({r.sha256:((200,j.raw_bytes),) for r,j in zip(requests,jobs)})
        prepare_installed_run(root,asof=ASOF,snapshot_captured_utc=CAPTURE,requests=requests,transport=transport)
        result=run_installed_pipeline(root,apply=True,transport=transport)
        if result['state']!='COMPLETE_OFFLINE_COMPATIBILITY': raise ValueError('SYNTHETIC_DEMO_BLOCKED')
        for name in ('report.html','gate.json'):
            atomic_write(args.output_dir/name,(root/'artifacts'/name).read_bytes())
        print('INSTALLED_SYNTHETIC_REPORT_EXPORTED_NO_PUBLICATION')
    finally: case.doCleanups()
    return 0


if __name__=='__main__': raise SystemExit(main())
