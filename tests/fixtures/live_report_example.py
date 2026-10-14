"""Export a synthetic native live report for local visual checks only."""
import argparse
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).absolute().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tests.unit.test_installed_live_pipeline import LivePipelineTests


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args(argv)
    if a.output_dir.exists() or a.output_dir.is_symlink():p.error('New output directory required')
    case=LivePipelineTests()
    try:
        m,root,db,plan,cap,client=case.fixture(True)
        result=m.run_live_pipeline(plan,capability=cap,apply=True,transport=client)
        if result['gate']['state']!='OFFLINE_LIVE_BINDING_LOCAL_PASS':raise ValueError('NATIVE_SYNTHETIC_REPORT_BLOCKED')
        a.output_dir.mkdir(mode=0o700,parents=True)
        for name in ('report.html','gate.json'):
            target=a.output_dir/name;target.write_bytes((root/'live-run-native-test'/name).read_bytes());target.chmod(0o600)
        print('NATIVE_SYNTHETIC_REPORT_EXPORTED_NO_PUBLICATION')
    finally:case.doCleanups()
    return 0

if __name__=='__main__':raise SystemExit(main())
