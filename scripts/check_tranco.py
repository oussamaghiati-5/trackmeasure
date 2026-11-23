#!/usr/bin/env python3
"""Cross-check sites.csv against a Tranco top-1M list.

Downloads the latest list from tranco-list.eu (or uses a local zip),
prints the list ID (record it in the paper for reproducibility),
annotates each domain with its Tranco rank, and writes sites_ranked.csv.
Sites absent from the list or beyond --max-rank are dropped and listed.

Usage:
  python scripts/check_tranco.py                       # download latest
  python scripts/check_tranco.py --list-file tranco_1M.csv.zip
  python scripts/check_tranco.py --max-rank 500000 --out sites_filtered.csv
"""
import argparse
import csv
import io
import json
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))
from common import etld1, load_sites   # noqa: E402  (after path setup)

TRANCO_API = "https://tranco-list.eu/api/lists/date/{date}"
TRANCO_ZIP = "https://tranco-list.eu/top-1m.csv.zip"
TRANCO_ID = "https://tranco-list.eu/top-1m-id"
UA = "trackmeasure-research/1.0 (academic measurement study)"


def _get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_list_id(for_date: str) -> str:
    """Return the Tranco list ID for a given date (YYYY-MM-DD), or '' on error."""
    try:
        data = json.loads(_get(TRANCO_API.format(date=for_date.replace("-", "")), timeout=15))
        return data.get("list_id") or data.get("listId") or ""
    except Exception:
        return ""


def download_tranco() -> tuple[str, bytes]:
    """Download latest Tranco zip. Returns (list_id, raw_bytes)."""
    today = date.today().isoformat()
    list_id = _get(TRANCO_ID, timeout=15).decode().strip()
    if not list_id:
        raise RuntimeError("Tranco did not return a permanent list ID")
    print(f"Downloading Tranco list (date={today}) ...", file=sys.stderr)
    raw = _get(TRANCO_ZIP)
    if _get(TRANCO_ID, timeout=15).decode().strip() != list_id:
        raise RuntimeError("Tranco's latest list changed during download; retry")
    return list_id, raw


def load_ranks(data: bytes) -> dict[str, int]:
    """Parse a Tranco CSV zip into {domain: rank}."""
    ranks: dict[str, int] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = z.namelist()[0]
        with z.open(name) as f:
            for line in io.TextIOWrapper(f, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                rank_s, domain = line.split(",", 1)
                ranks[domain.lower()] = int(rank_s)
    return ranks


def rank_of(domain: str, ranks: dict[str, int]) -> int | None:
    """Try exact domain, then eTLD+1, then www-stripped."""
    d = domain.lower()
    return (ranks.get(d)
            or ranks.get(etld1(d))
            or ranks.get(d.removeprefix("www.")))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sites", default="sites.csv",
                    help="input site list (default: sites.csv)")
    ap.add_argument("--list-file",
                    help="local Tranco zip (skips download; list ID not available)")
    ap.add_argument("--max-rank", type=int, default=1_000_000,
                    help="drop sites with rank > this (default: 1,000,000)")
    ap.add_argument("--out", default="sites_ranked.csv",
                    help="output CSV (default: sites_ranked.csv)")
    ap.add_argument("--save-zip", default="tranco_latest.zip",
                    help="save downloaded zip here (default: tranco_latest.zip)")
    args = ap.parse_args()

    if args.list_file:
        with open(args.list_file, "rb") as f:
            raw = f.read()
        list_id = "(local file)"
    else:
        list_id, raw = download_tranco()
        Path(args.save_zip).write_bytes(raw)
        print(f"Saved to {args.save_zip}", file=sys.stderr)

    ranks = load_ranks(raw)
    print(f"Loaded {len(ranks):,} domains from Tranco list", file=sys.stderr)

    if list_id:
        print(f"\n*** TRANCO LIST ID: {list_id} ***", file=sys.stderr)
        print("*** Record this in your paper for reproducibility. ***\n", file=sys.stderr)
    else:
        print("WARNING: could not determine list ID from Tranco API; "
              "note the download date manually.\n", file=sys.stderr)

    sites = load_sites(HERE / args.sites)
    kept, dropped = [], []

    with open(args.out, "w", newline="") as fout:
        w = csv.writer(fout)
        w.writerow(["domain", "category", "tranco_rank"])
        for domain, cat in sites:
            r = rank_of(domain, ranks)
            if r and r <= args.max_rank:
                w.writerow([domain, cat, r])
                kept.append((domain, r))
            else:
                dropped.append((domain, r or "not in list"))

    print(f"Kept:    {len(kept)}", file=sys.stderr)
    print(f"Dropped: {len(dropped)}", file=sys.stderr)
    if dropped:
        print("Dropped domains:", file=sys.stderr)
        for d, r in sorted(dropped, key=lambda x: (x[1] == "not in list", x[1])):
            print(f"  {d:40s}  rank={r}", file=sys.stderr)
    print(f"\nWritten to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
