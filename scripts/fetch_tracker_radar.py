#!/usr/bin/env python3
"""Fetch the DuckDuckGo Tracker Radar entries needed to label a batch's scripts.

    python scripts/fetch_tracker_radar.py results/full_eu_20261122 \
        --db data/full_eu_20261122/crawl.sqlite --out reference/tracker_radar_subset.json

Two sources, both pinned so labels are reproducible:
  * Tracker Radar per-domain files (github.com/duckduckgo/tracker-radar, at a fixed
    commit) for every fingerprinting script domain. They carry the usage categories
    (Advertising, Analytics, Fraud Prevention, Ad Fraud, CDN, ...), the 0-3
    fingerprinting score, the owner and prevalence. Regions are tried EU-first
    because the batch used a German exit.
  * The DuckDuckGo blocklist built from Tracker Radar (extension-tds.json) for every
    third-party request/cookie domain, giving categories and the domain->owner map
    used to spot same-owner "third parties" (e.g. fbcdn.net on facebook.com).

Tracker Radar's fingerprinting score is the likelihood of fingerprinting-API use,
not the purpose; categories are the purpose signal. Data licence: CC BY-NC-SA 4.0
(Tracker Radar). Only the fields used for labelling are stored.
"""
import argparse
import csv
import datetime as dt
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TR_COMMIT = "a358daca7f84a2d8275f0b8dca460231dd902886"   # tracker-radar main, 2026-11-23
TR_URL = "https://raw.githubusercontent.com/duckduckgo/tracker-radar/{commit}/domains/{region}/{domain}.json"
TDS_URL = "https://staticcdn.duckduckgo.com/trackerblocking/v6/current/extension-tds.json"
REGIONS = ("DE", "NL", "FR", "GB", "CH", "NO", "US", "CA", "AU")
UA = "trackmeasure-research-client"


def get_json(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
        time.sleep(1 + attempt)
    return None


def compact(entry, region):
    owner = entry.get("owner") or {}
    resources = entry.get("resources") or []
    return {
        "region": region,
        "owner": owner.get("displayName") or owner.get("name"),
        "categories": entry.get("categories") or [],
        "fingerprinting": entry.get("fingerprinting"),
        "prevalence": entry.get("prevalence"),
        "sites": entry.get("sites"),
        "cookies": entry.get("cookies"),
        "max_resource_fingerprinting": max((r.get("fingerprinting") or 0 for r in resources), default=None),
    }


def script_domains(results):
    with (Path(results) / "fp_scripts.csv").open(newline="", encoding="utf-8") as f:
        return sorted({r["script_domain"] for r in csv.DictReader(f)
                       if r["script_domain"] and not r["script_domain"].startswith("(")})


def request_domains(db):
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?immutable=1", uri=True)
    req = {d for (d,) in con.execute("SELECT DISTINCT domain FROM requests WHERE third_party=1") if d}
    ck = {d.lstrip(".") for (d,) in con.execute("SELECT DISTINCT domain FROM cookies") if d}
    con.close()
    return req | ck


def tds_lookup(tds, domain):
    """Walk up subdomains, as the blocklist keys trackers by registrable domain."""
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in tds["trackers"] or cand in tds["domains"]:
            return cand
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results")
    ap.add_argument("--db", help="crawl.sqlite, for third-party request and cookie domains")
    ap.add_argument("--out", default="reference/tracker_radar_subset.json")
    a = ap.parse_args()

    radar = {}
    for domain in script_domains(a.results):
        for region in REGIONS:
            entry = get_json(TR_URL.format(commit=TR_COMMIT, region=region, domain=domain))
            if entry:
                radar[domain] = compact(entry, region)
                break
        else:
            radar[domain] = None
        print(f"{domain:35} {'missing' if radar[domain] is None else radar[domain]['categories']}",
              file=sys.stderr)

    tds = get_json(TDS_URL)
    blocklist, owners = {}, {}
    wanted = request_domains(a.db) if a.db else set()
    wanted |= {d for d in radar}
    for domain in sorted(wanted):
        key = tds_lookup(tds, domain)
        if not key:
            continue
        if key in tds["trackers"]:
            t = tds["trackers"][key]
            blocklist[key] = {"owner": (t.get("owner") or {}).get("displayName") or (t.get("owner") or {}).get("name"),
                              "categories": t.get("categories") or [],
                              "fingerprinting": t.get("fingerprinting"),
                              "prevalence": t.get("prevalence"), "default": t.get("default")}
        if key in tds["domains"]:
            owners[key] = tds["domains"][key]

    out = {
        "_meta": {
            "fetched": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tracker_radar_commit": TR_COMMIT,
            "tracker_radar_url": TR_URL,
            "tds_url": TDS_URL,
            "tds_built_with": tds.get("_builtWith"),
            "licence": "Tracker Radar data: CC BY-NC-SA 4.0, DuckDuckGo. Subset for research use.",
        },
        "tracker_radar": radar,
        "blocklist": blocklist,
        "owners": owners,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    found = sum(v is not None for v in radar.values())
    print(f"Tracker Radar: {found}/{len(radar)} script domains; blocklist entries: {len(blocklist)}; "
          f"owner map: {len(owners)} -> {a.out}")


if __name__ == "__main__":
    main()
