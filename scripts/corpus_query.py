#!/usr/bin/env python3
"""Offline, read-only access to the approved research methodology corpus."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.knowledge_rag import (
    ALLOWED_FAMILIES, corpus_manifest, evidence_bundle,
)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Consult the versioned Research Studio methodology corpus; no API/SQLite writes"
    )
    p.add_argument("--query", help="Topic, metric ID or method question")
    p.add_argument("--family", action="append", choices=sorted(ALLOWED_FAMILIES),
                   help="Restrict research domain; repeat to include multiple")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--manifest", action="store_true",
                   help="Print manifest with version, records and SHA256")
    args = p.parse_args(argv)
    if not args.query and not args.manifest:
        p.error("Use --query or --manifest")
    if not 1 <= args.limit <= 20:
        p.error("--limit must be between 1 and 20")
    output = evidence_bundle(args.query, args.limit, args.family) if args.query else corpus_manifest()
    print(json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
