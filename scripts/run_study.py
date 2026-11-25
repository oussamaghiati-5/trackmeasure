#!/usr/bin/env python3
"""Run a reproducible browser measurement batch.

Example: python scripts/run_study.py --sites data/full_eu_20261122/sites.csv \
             --batch full_eu_20261126 --workers 2 --modes baseline baseline2
The input list, browser settings and VPN exit are frozen in data/BATCH and
results/BATCH. Use a new --batch name for a different experiment.

Every database read runs in the crawler container, so the same commands work
whether data/ is a bind mount or the live database stays on a Docker volume
(MEASUREMENT_DATA_OVERLAY=docker-compose.volume.yml, needed on Windows, where
SQLite on a bind mount was corrupted). With the volume, a copy is exported to
data/BATCH/crawl.sqlite after the requested conditions finish.
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import time

from run_pilot import MODES, run
from verify_vpn import ROOT, BASE, VPN, DATA_OVERLAY, VPN_OVERLAY, expected_country, trace

SETTINGS = ["--headful", "--subpages", "1", "--dwell", "5",
            "--timeout", "45", "--site-timeout", "180", "--consent-wait", "20",
            "--locale", "en-US", "--seed", "0"]
# The browser time zone follows the verified exit country, so the IP and the
# browser agree (the 2026-11-22 batch used Europe/Amsterdam behind a German exit).
TIMEZONES = {"AT": "Europe/Vienna", "BE": "Europe/Brussels", "CH": "Europe/Zurich",
             "DE": "Europe/Berlin", "DK": "Europe/Copenhagen", "ES": "Europe/Madrid",
             "FI": "Europe/Helsinki", "FR": "Europe/Paris", "GB": "Europe/London",
             "IE": "Europe/Dublin", "IT": "Europe/Rome", "NL": "Europe/Amsterdam",
             "NO": "Europe/Oslo", "PL": "Europe/Warsaw", "PT": "Europe/Lisbon",
             "SE": "Europe/Stockholm"}
SOURCE_FILES = ("crawler.py", "instrument.js", "consent.py", "analyze.py",
                "respawn.py", "common.py", "navigation.py", "extension_state.py",
                "requirements.txt", "extensions.lock.json", "Dockerfile",
                "docker-compose.yml", VPN_OVERLAY, *([DATA_OVERLAY] if DATA_OVERLAY else []),
                "scripts/run_study.py", "scripts/run_pilot.py")
OFFLINE = BASE + ["-f", "docker-compose.test.yml"]     # data mounts, no network
MARKER = "STUDY_DB "
QUERY = r'''
import json, os, sqlite3, sys
path, sql = sys.argv[1], sys.argv[2]
rows = None
if os.path.exists(path):
    con = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    rows = con.execute(sql).fetchall()
    con.close()
print("STUDY_DB " + json.dumps(rows), flush=True)
'''
EXPORT = r'''
import sqlite3, sys
src = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()
'''


NETWORK_ERRORS = ("net::ERR_", "Timeout", "TimeoutError")
RETRY_PAUSE = 60        # seconds, so a short VPN DNS outage can end before the retry


def network_failures(rows):
    """Sites whose failure was the network, not the site's answer (sites_done rows)."""
    return [site for site, error in rows or [] if error and any(k in error for k in NETWORK_ERRORS)]


def exit_timezone(country):
    try:
        return TIMEZONES[country.upper()]
    except KeyError:
        raise RuntimeError(f"No browser time zone set for exit country {country}; add it to TIMEZONES")


def container_python(code, *argv, capture=False):
    return subprocess.run(OFFLINE + ["run", "--rm", "--no-deps", "-T", "crawler", "python", "-c", code, *argv],
                          cwd=ROOT, check=True, text=True, capture_output=capture)


def query(batch, sql):
    """Rows of a read-only query on the batch database, or None if it does not exist yet."""
    out = container_python(QUERY, f"data/{batch}/crawl.sqlite", sql, capture=True).stdout
    line = next(line for line in out.splitlines() if line.startswith(MARKER))
    return json.loads(line[len(MARKER):])


def prepare_volume(batch):
    """A new Docker volume is root-owned (755); hand it to the crawler's pwuser once.
    Only CHOWN is added, for this one-off command, on top of the dropped capabilities."""
    subprocess.run(OFFLINE + ["run", "--rm", "--no-deps", "-T", "--user", "root", "--cap-add", "CHOWN",
                              "--entrypoint", "chown", "crawler", "pwuser:pwuser", f"data/{batch}"],
                   cwd=ROOT, check=True)


def export(batch):
    """Copy the live database from the Docker volume to data/BATCH/crawl.sqlite on the host."""
    staged = ROOT / "data" / f"{batch}-export.sqlite"
    staged.unlink(missing_ok=True)
    container_python(EXPORT, f"data/{batch}/crawl.sqlite", f"data/{batch}-export.sqlite")
    os.replace(staged, ROOT / "data" / batch / "crawl.sqlite")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
    # docker-compose.volume.yml names the batch's volume after this variable.
    os.environ["STUDY_BATCH"] = args.batch
    if os.name == "nt":
        # Keep Windows from sleeping while the runner is alive (ES_CONTINUOUS |
        # ES_SYSTEM_REQUIRED); the request ends with this process.
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    source = (ROOT / args.sites).resolve()
    if not source.is_file():
        ap.error(f"site list not found: {source}")
    with source.open(newline="", encoding="utf-8") as f:
        sites = [row for row in csv.DictReader(f) if row.get("domain") and not row["domain"].startswith("#")]
    if not sites or len({row["domain"] for row in sites}) != len(sites):
        ap.error("site list must contain unique domains")
    data = ROOT / "data" / args.batch
    results = ROOT / "results" / args.batch
    inputs = results / "inputs"         # visible to the container in both data setups
    for path in (data, results, inputs):
        path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["docker", "compose", "build", "crawler"], cwd=ROOT, check=True)
    if DATA_OVERLAY:
        prepare_volume(args.batch)
    network = VPN if args.network == "vpn" else BASE
    if args.network == "vpn":
        subprocess.run(VPN + ["up", "-d", "--wait", "--wait-timeout", "120", "gluetun"],
                       cwd=ROOT, check=True)
    exit_info = trace(network)
    if args.network == "vpn" and exit_info["country"] != expected_country().upper():
        raise RuntimeError("VPN exit country does not match configuration")
    settings += ["--timezone", exit_timezone(exit_info["country"])]
    manifest = {"sites_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "site_count": len(sites), "settings": settings, "exit": exit_info,
                "network": args.network, "data_volume": bool(DATA_OVERLAY),
                "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                  for name in SOURCE_FILES}}
    manifest_path = results / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        changed = [key for key, value in manifest.items() if previous.get(key) != value]
        if changed:
            raise RuntimeError(f"Batch {', '.join(changed)} changed; use a new --batch")
    else:
        manifest["started_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    for frozen in (data / "sites.csv", inputs / "sites.csv"):
        if frozen.exists() and frozen.read_bytes() != source.read_bytes():
            raise RuntimeError("Frozen site list differs; use a new --batch")
        if source != frozen.resolve():
            shutil.copyfile(source, frozen)
    for mode in args.modes:
        if trace(network) != exit_info:
            raise RuntimeError("Network exit changed before run")
        input_path = f"results/{args.batch}/inputs/sites.csv"
        if mode != "baseline":
            rows = query(args.batch, "SELECT site FROM sites_done WHERE run='baseline' AND ok=1")
            if rows is None:
                raise RuntimeError("Run baseline before comparison conditions")
            successful = {row[0] for row in rows}
            if not successful:
                raise RuntimeError("No successful baseline sites; diagnose the baseline first")
            selected = [row for row in sites if row["domain"] in successful]
            for path in (data / "successful_sites.csv", inputs / "successful_sites.csv"):
                with path.open("w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=["domain", "category"], extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(selected)
            input_path = f"results/{args.batch}/inputs/successful_sites.csv"
        def crawl(site_list):
            command = network + ["run", "--rm", "-T", "crawler", "xvfb-run", "-a", "python", "-u",
                                 "crawler.py", "--sites", site_list,
                                 "--out", f"data/{args.batch}/crawl.sqlite", "--run", mode,
                                 "--consent-artifacts", f"results/{args.batch}/consent", *settings, *MODES[mode]]
            if mode != "baseline":
                command += ["--reuse-urls-from", f"data/{args.batch}/crawl.sqlite:baseline"]
            run(command, results / f"{mode}.log")
            if trace(network) != exit_info:
                raise RuntimeError("Network exit changed during run; do not compare this batch")

        crawl(input_path)
        # One retry pass for network failures (VPN DNS timeouts, dropped connections).
        # HTTP answers such as 403 bot blocks are measurements and are not retried.
        failures = query(args.batch, f"SELECT site, error FROM sites_done WHERE run='{mode}' AND ok=0")
        retry = network_failures(failures)
        if retry:
            print(f"{mode}: retrying {len(retry)} sites after network errors: {', '.join(retry)}", flush=True)
            retry_path = inputs / f"retry_{mode}.csv"
            with retry_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["domain", "category"], extrasaction="ignore")
                writer.writeheader()
                writer.writerows(row for row in sites if row["domain"] in set(retry))
            time.sleep(RETRY_PAUSE)
            crawl(f"results/{args.batch}/inputs/{retry_path.name}")
        (ok, failed), = query(args.batch, "SELECT COALESCE(SUM(ok),0), COALESCE(SUM(1-ok),0) "
                                          f"FROM sites_done WHERE run='{mode}'")
        print(f"{mode}: {ok} completed, {failed} failed; inspect {results / (mode + '.log')}", flush=True)
        run(OFFLINE + ["run", "--rm", "-T", "crawler", "python", "analyze.py",
                       f"data/{args.batch}/crawl.sqlite", "--baseline", "baseline",
                       "--outdir", f"results/{args.batch}"], results / "analysis.log")
    if DATA_OVERLAY:
        export(args.batch)
        print(f"Database copy: {data / 'crawl.sqlite'}", flush=True)
    print(f"Study outputs: {results}")


if __name__ == "__main__":
    main()
