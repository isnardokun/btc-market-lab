#!/usr/bin/env python3
"""
Discover all available series from bitview.space API.
Tests each series with a 7-day window. Saves working ones to all_series_valid.txt.
"""
import sys, os, json, time, urllib.request, concurrent.futures
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config

API = config.API_BASE
INPUT  = "/home/ignotus/btc-research/all_series.txt"
OUTPUT = "/home/ignotus/btc-research/all_series_valid.txt"
LOG    = "/home/ignotus/btc-research/discover_log.txt"

def fetch_json(path, retries=2):
    url = f"{API}{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        return None

def test_series(name):
    """Test a series. Returns (name, index) if valid, None if not."""
    # Try 1d first, then 1w, then 1mo
    for idx in ["1d", "1w", "1mo"]:
        r = fetch_json(f"/series/{name}/{idx}?start=2026-10-01&end=2026-10-07")
        if r and "data" in r and len(r.get("data", [])) > 0:
            return (name, r.get("index", idx), r.get("type", ""))
    # Try height-indexed
    r = fetch_json(f"/series/{name}/height?start=865000&end=865005")
    if r and "data" in r and len(r.get("data", [])) > 0:
        return (name, "height", r.get("type", ""))
    return None

def main():
    # Load names
    with open(INPUT) as f:
        names = [l.strip() for l in f if l.strip()]
    print(f"Testing {len(names)} series...")

    valid = []
    errors = 0
    lock_file = open(LOG, "w")

    # Concurrent: 30 workers
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as ex:
        futs = {ex.submit(test_series, n): n for n in names}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            done += 1
            name = futs[fut]
            try:
                result = fut.result()
            except Exception as e:
                result = None

            if result:
                valid.append(result)
                lock_file.write(f"OK  {result[0]}: {result[1]} {result[2]}\n")
            else:
                errors += 1
                lock_file.write(f"ERR {name}\n")
                lock_file.flush()

            if done % 200 == 0:
                print(f"  {done}/{len(names)} — valid: {len(valid)}, errors: {errors}")

    lock_file.close()

    # Save valid series
    with open(OUTPUT, "w") as f:
        for name, idx, dtype in sorted(valid):
            f.write(f"{name}\t{idx}\t{dtype}\n")

    print(f"\nDone: {len(valid)} valid / {errors} failed / {len(names)} total")
    print(f"Valid saved to: {OUTPUT}")
    print(f"Log saved to: {LOG}")

if __name__ == "__main__":
    main()
