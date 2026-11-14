#!/usr/bin/env python3
"""Select respawn candidates from the baseline cookies table.

A site is a candidate if it set at least one cookie that is:
  - ID-like (≥8 alnum chars including a digit, low entropy of unique chars,
             not a pure timestamp), AND one of:
  - Third-party (etld1(cookie.domain) != site's eTLD+1), OR
  - Long-expiry first-party (expires > now + --min-expiry-days days)

Writes respawn_sites.csv (domain, category) for up to --max-sites sites,
ordered by number of candidate cookies descending (best candidates first).

Usage:
  python scripts/pick_respawn_sites.py data/crawl.sqlite --run baseline
  python scripts/pick_respawn_sites.py data/crawl.sqlite --run baseline \
      --max-sites 30 --out respawn_sites.csv
"""
import argparse
import csv
import re
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))
from common import load_sites   # noqa: E402

TOKEN = re.compile(r"[A-Za-z0-9]{8,}")
NOW = time.time()


def is_id_like(value: str | None) -> bool:
    if not value:
        return False
    for tok in TOKEN.findall(value[:500]):
        if not any(ch.isdigit() for ch in tok):
            continue
        if len(set(tok)) <= 3:          # too low entropy
            continue
        # Reject pure timestamps (Unix seconds or milliseconds, ±5 years)
        if tok.isdigit() and 9 <= len(tok) <= 13:
            v = int(tok)
            if (abs(v - NOW) < 5 * 365 * 86400
                    or abs(v / 1000 - NOW) < 5 * 365 * 86400):
                continue
        return True
    return False


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", help="crawl SQLite (e.g. data/crawl.sqlite)")
    ap.add_argument("--run", default="baseline", help="run label to read from (default: baseline)")
    ap.add_argument("--min-expiry-days", type=int, default=90,
                    help="min days until expiry for first-party cookie inclusion (default: 90)")
    ap.add_argument("--max-sites", type=int, default=50,
                    help="max sites to write (default: 50)")
    ap.add_argument("--out", default="respawn_sites.csv")
    args = ap.parse_args()

    min_expires = NOW + args.min_expiry_days * 86400

    con = sqlite3.connect(args.db)
    rows = con.execute(
        "SELECT site, name, domain, value, expires, third_party "
        "FROM cookies WHERE run=?",
        (args.run,),
    ).fetchall()
    con.close()

    if not rows:
        sys.exit(f"No cookies found for run='{args.run}' in {args.db}. "
                 "Did the baseline crawl complete?")

    # Count candidate cookies per site
    candidates: dict[str, int] = {}
    for site, name, domain, value, expires, third_party in rows:
        if not is_id_like(value):
            continue
        long_expiry = expires and float(expires) > min_expires
        if third_party or long_expiry:
            candidates[site] = candidates.get(site, 0) + 1

    if not candidates:
        print("No candidate sites found. Consider lowering --min-expiry-days.", file=sys.stderr)
        sys.exit(0)

    # Order by candidate count descending (best bets first)
    ordered = sorted(candidates.items(), key=lambda x: x[1], reverse=True)

    # Retrieve original categories from sites.csv if available
    orig_cat: dict[str, str] = {}
    sites_csv = HERE / "sites.csv"
    if sites_csv.exists():
        orig_cat = {d: c for d, c in load_sites(sites_csv)}

    out_sites = ordered[:args.max_sites]
    with open(args.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["domain", "category"])
        for site, n_cookies in out_sites:
            w.writerow([site, orig_cat.get(site, "")])

    print(f"{len(out_sites)} sites written to {args.out}", file=sys.stderr)
    print(f"Total candidates found: {len(ordered)}", file=sys.stderr)
    if len(ordered) > args.max_sites:
        print(f"({len(ordered) - args.max_sites} more excluded; "
              "increase --max-sites to include them)", file=sys.stderr)
    print("\nTop 10 candidate sites by cookie count:", file=sys.stderr)
    for site, n in out_sites[:10]:
        print(f"  {n:3d} cookies  {site}", file=sys.stderr)


if __name__ == "__main__":
    main()
