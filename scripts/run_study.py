#!/usr/bin/env python3
"""Run a reproducible browser measurement batch.

Example: python scripts/run_study.py --sites sites_ranked.csv --modes baseline baseline2
The input list, browser settings and VPN exit are frozen in data/study and
results/study. Use a new --batch name for a different experiment.
"""
import argparse
import csv
import datetime
import hashlib
import json
import shutil
import sqlite3
import subprocess

from run_pilot import MODES, run
from verify_vpn import ROOT, BASE, VPN, expected_country, trace

SETTINGS = ["--headful", "--subpages", "1", "--dwell", "5",
            "--timeout", "45", "--site-timeout", "180", "--consent-wait", "20",
            "--locale", "en-US", "--timezone", "Europe/Amsterdam", "--seed", "0"]
SOURCE_FILES = ("crawler.py", "instrument.js", "consent.py", "analyze.py",
                "respawn.py", "common.py", "navigation.py", "extension_state.py",
                "requirements.txt", "extensions.lock.json", "Dockerfile",
                "docker-compose.yml", "docker-compose.vpn.yml", "scripts/run_study.py")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sites", required=True, help="final CSV with domain,category")
    ap.add_argument("--batch", default="study", help="name under data/ and results/")
    ap.add_argument("--network", choices=("vpn", "direct"), default="vpn",
                    help="direct is for an exploratory batch without GDPR claims")
    ap.add_argument("--workers", type=int, choices=(1, 2, 3, 4), default=1)
    ap.add_argument("--modes", nargs="+", choices=list(MODES), required=True)
    args = ap.parse_args()
    settings = ["--workers", str(args.workers), *SETTINGS]
    if not args.batch.replace("-", "").replace("_", "").isalnum():
        ap.error("--batch must contain only letters, digits, hyphens or underscores")
    source = (ROOT / args.sites).resolve()
    if not source.is_file():
        ap.error(f"site list not found: {source}")
    with source.open(newline="", encoding="utf-8") as f:
        sites = [row for row in csv.DictReader(f) if row.get("domain") and not row["domain"].startswith("#")]
    if not sites or len({row["domain"] for row in sites}) != len(sites):
        ap.error("site list must contain unique domains")
    data = ROOT / "data" / args.batch
    results = ROOT / "results" / args.batch
    data.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    subprocess.run(["docker", "compose", "build", "crawler"], cwd=ROOT, check=True)
    network = VPN if args.network == "vpn" else BASE
    if args.network == "vpn":
        subprocess.run(VPN + ["up", "-d", "--wait", "--wait-timeout", "120", "gluetun"],
                       cwd=ROOT, check=True)
    exit_info = trace(network)
    if args.network == "vpn" and exit_info["country"] != expected_country().upper():
        raise RuntimeError("VPN exit country does not match configuration")
    manifest = {"sites_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "site_count": len(sites), "settings": settings, "exit": exit_info,
                "network": args.network,
                "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                  for name in SOURCE_FILES}}
    manifest_path = results / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if any(previous.get(key) != value for key, value in manifest.items()):
            raise RuntimeError("Batch input, settings or VPN exit changed; use a new --batch")
    else:
        manifest["started_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    frozen = data / "sites.csv"
    if frozen.exists() and frozen.read_bytes() != source.read_bytes():
        raise RuntimeError("Frozen site list differs; use a new --batch")
    if source != frozen.resolve():
        shutil.copyfile(source, frozen)
    db = data / "crawl.sqlite"
    for mode in args.modes:
        if trace(network) != exit_info:
            raise RuntimeError("Network exit changed before run")
        input_path = f"data/{args.batch}/sites.csv"
        if mode != "baseline":
            if not db.exists():
                raise RuntimeError("Run baseline before comparison conditions")
            with sqlite3.connect(db) as con:
                successful = {row[0] for row in con.execute(
                    "SELECT site FROM sites_done WHERE run='baseline' AND ok=1")}
            if not successful:
                raise RuntimeError("No successful baseline sites; diagnose the baseline first")
            selected = [row for row in sites if row["domain"] in successful]
            with (data / "successful_sites.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["domain", "category"], extrasaction="ignore")
                writer.writeheader()
                writer.writerows(selected)
            input_path = f"data/{args.batch}/successful_sites.csv"
        command = network + ["run", "--rm", "-T", "crawler", "xvfb-run", "-a", "python", "-u",
                         "crawler.py", "--sites", input_path,
                         "--out", f"data/{args.batch}/crawl.sqlite", "--run", mode,
                         "--consent-artifacts", f"results/{args.batch}/consent", *settings, *MODES[mode]]
        if mode != "baseline":
            command += ["--reuse-urls-from", f"data/{args.batch}/crawl.sqlite:baseline"]
        run(command, results / f"{mode}.log")
        if trace(network) != exit_info:
            raise RuntimeError("Network exit changed during run; do not compare this batch")
        with sqlite3.connect(db) as con:
            ok, failed = con.execute(
                "SELECT COALESCE(SUM(ok),0), COALESCE(SUM(1-ok),0) FROM sites_done WHERE run=?",
                (mode,)).fetchone()
        print(f"{mode}: {ok} completed, {failed} failed; inspect {results / (mode + '.log')}", flush=True)
        offline = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.test.yml"]
        run(offline + ["run", "--rm", "-T", "crawler", "python", "analyze.py",
                       f"data/{args.batch}/crawl.sqlite", "--baseline", "baseline",
                       "--outdir", f"results/{args.batch}"], results / "analysis.log")
    print(f"Study outputs: {results}")


if __name__ == "__main__":
    main()
