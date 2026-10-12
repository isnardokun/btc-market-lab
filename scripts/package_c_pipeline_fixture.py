#!/usr/bin/env python3
"""Plan by default; prepare/apply only an explicit, marked synthetic root."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.package_c_pipeline import plan_fixture_pipeline,prepare_fixture,run_fixture_pipeline

class _Parser(argparse.ArgumentParser):
    def error(self,message):
        # Never echo unexpected argv values: they may resemble credentials.
        raise ValueError('INVALID_CLI_ARGUMENTS')

def main(argv=None):
    parser=_Parser(description='Package C marked-fixture OFFLINE pipeline; no live API or publication')
    parser.add_argument('--root',type=Path)
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--prepare',action='store_true')
    action.add_argument('--apply',action='store_true')
    try:
        args=parser.parse_args(argv)
        if (args.prepare or args.apply) and args.root is None:raise ValueError('ROOT_REQUIRED')
        if args.prepare:result=prepare_fixture(args.root)
        elif args.apply:result=run_fixture_pipeline(args.root,apply=True)
        else:result=plan_fixture_pipeline()
        print(json.dumps(result,sort_keys=True,allow_nan=False))
        return 0 if result['state'] in ('PLAN_ONLY_NO_IO','PREPARED_OFFLINE_FIXTURE','COMPLETE_OFFLINE_FIXTURE_ONLY') else 3
    except (ValueError,OSError,TypeError) as error:
        reason=str(error) if str(error) in ('INVALID_CLI_ARGUMENTS','ROOT_REQUIRED','NEW_MARKED_FIXTURE_ROOT_REQUIRED') else type(error).__name__
        print(json.dumps({'state':'BLOCKED_OFFLINE_PIPELINE','reason':reason,'production_authorized':False},sort_keys=True))
        return 3

if __name__=='__main__':raise SystemExit(main())
