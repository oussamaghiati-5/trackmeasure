#!/usr/bin/env python3
"""Re-run the whole offline analysis of a completed batch, in order, with this Python.

    python scripts/reproduce_results.py --batch full_eu_20261122

Needs only the private crawl database (data/<batch>/crawl.sqlite) and the committed
Tracker Radar subset; no crawl, no network. Steps:

  1. analyze.py              detection, per-site metrics, consent re-assessment, comparisons
  2. paired_uncertainty.py   site-bootstrap intervals against the repeat baseline
  3. label_purposes.py       script inventory, purpose labels, capability, harm class
  4. purpose_results.py      results by purpose and by harm class, validity, validation sheet
  5. export_public_results   privacy-reduced bundle in public_results/<batch> (with --export)

Each step's output goes to results/<batch>/<step>.log; the run stops at the first failure.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", default="full_eu_20261122")
    ap.add_argument("--export", action="store_true", help="also refresh public_results/<batch>")
    a = ap.parse_args()
    db = ROOT / "data" / a.batch / "crawl.sqlite"
    res = ROOT / "results" / a.batch
    sites = ROOT / "data" / a.batch / "sites.csv"
    if not db.is_file():
        sys.exit(f"Missing private database: {db}")
    res.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    steps = [
        ("analyze", [py, "analyze.py", str(db), "--baseline", "baseline", "--outdir", str(res)]),
        ("uncertainty", [py, "scripts/paired_uncertainty.py", str(res / "comparison.csv")]),
        ("label_purposes", [py, "scripts/label_purposes.py", str(db), str(res),
                            "--radar", "reference/tracker_radar_subset.json"]),
        ("purpose_results", [py, "scripts/purpose_results.py", str(res), "--sites", str(sites)]),
    ]
    if a.export:
        steps.append(("export", [py, "scripts/export_public_results.py", str(res), "--sites", str(sites),
                                 "--out", str(ROOT / "public_results" / a.batch), "--overwrite"]))
    for name, cmd in steps:
        started = time.monotonic()
        log = res / f"{name}.log"
        with log.open("w", encoding="utf-8") as out:
            code = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT).returncode
        print(f"{name:16} {'ok' if code == 0 else 'FAILED'}  {time.monotonic() - started:6.1f}s  -> {log}")
        if code:
            sys.exit(code)
    print(f"All steps done. Headline numbers: {res / 'purpose_results.json'}")


if __name__ == "__main__":
    main()
