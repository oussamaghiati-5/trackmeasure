#!/usr/bin/env python3
"""Print the batch's stored TCF consent strings with our decoding, for check_tcf_official.mjs.

    python scripts/dump_tc_strings.py data/full_eu_20261122/crawl.sqlite > tc_strings.json

The output contains consent cookie values from the crawl. Keep it private.
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import consent  # noqa: E402


def main(db):
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?immutable=1", uri=True)
    rows = con.execute("SELECT run, site, value FROM cookies WHERE name='euconsent-v2'").fetchall()
    out = [dict(run=r, site=s, tc=v, ours=consent.decode_tcf(v)) for r, s, v in rows if consent.decode_tcf(v)]
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
