#!/usr/bin/env python3
"""A fresh one-site consent check, with local diagnostics and no pilot overwrite."""
import argparse
import csv
import datetime
import hashlib
import json
import sqlite3
import subprocess
from urllib.parse import urlparse

from run_pilot import run
from verify_vpn import ROOT, VPN, expected_country, trace


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site", required=True)
    ap.add_argument("--action", choices=("reject", "accept"), default="reject")
    args = ap.parse_args()
    url = args.site if "://" in args.site else "https://" + args.site
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        ap.error("Use a website hostname or HTTP(S) URL without credentials")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    data_rel = "data/consent-check/" + stamp
    results_rel = "results/consent-check/" + stamp
    data, results = ROOT / data_rel, ROOT / results_rel
    data.mkdir(parents=True)
    results.mkdir(parents=True)
    with (data / "sites.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["domain", "category"])
        writer.writerow([args.site, "consent-check"])
    subprocess.run(VPN + ["up", "-d", "--wait", "--wait-timeout", "120", "gluetun"], cwd=ROOT, check=True)
    exit_info = trace(VPN)
    if exit_info["country"] != expected_country().upper():
        raise RuntimeError("VPN country does not match .env; no site visit started")
    command = VPN + ["run", "--rm", "-T", "crawler", "xvfb-run", "-a", "python", "-u", "crawler.py",
                     "--sites", data_rel + "/sites.csv", "--out", data_rel + "/crawl.sqlite",
                     "--run", args.action, "--consent", args.action, "--workers", "1", "--headful",
                     "--subpages", "0", "--dwell", "5", "--timeout", "45", "--site-timeout", "180",
                     "--consent-wait", "20", "--locale", "en-US", "--timezone", "Europe/Amsterdam",
                     "--consent-artifacts", results_rel]
    if args.action == "reject":
        command.append("--gpc")
    metadata = dict(site=args.site, action=args.action, exit=exit_info, command=command,
                    consent_sha256=hashlib.sha256((ROOT / "consent.py").read_bytes()).hexdigest(),
                    note="One-site diagnostic check; no tracking-effectiveness comparison")
    (results / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    run(command, results / "crawl.log")
    if trace(VPN) != exit_info:
        raise RuntimeError("VPN exit changed during this check; inspect before interpreting")
    with sqlite3.connect(data / "crawl.sqlite") as db:
        execution = db.execute("SELECT ok,error FROM sites_done").fetchone()
        row = db.execute("SELECT consent FROM visits WHERE phase='after_consent'").fetchone()
    record = json.loads(row[0]) if row and row[0] else {}
    print("Navigation:", "completed" if execution and execution[0] else execution)
    print("Consent:", record.get("outcome", "not reached"), "—", record.get("reason", ""))
    print("Evidence:", results)


if __name__ == "__main__":
    main()
