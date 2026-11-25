#!/usr/bin/env python3
"""Run a small diagnostic pilot; keep all artifacts separate from the full study.

Usage: python3 scripts/run_pilot.py --modes baseline
       python3 scripts/run_pilot.py --modes baseline2 optout accept ubol pbadger
"""
import argparse
import csv
import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

from verify_vpn import ROOT, VPN, trace

MODES = {
    "baseline": [], "baseline2": [],
    "optout": ["--consent", "reject", "--gpc"],
    # Separate the two signals combined in optout.
    "reject": ["--consent", "reject"],
    "gpc": ["--gpc"],
    "accept": ["--consent", "accept"],
    "ubol": ["--extension", "ext/ubol"],
    "pbadger": ["--extension", "ext/privacybadger"],
}


def run(command, log):
    # Stream to the terminal and keep the complete diagnostic log locally.
    with log.open("a") as out:
        out.write("\n=== " + datetime.datetime.now(datetime.timezone.utc).isoformat() + " ===\n")
        proc = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        try:
            for line in proc.stdout:
                print(line, end="", flush=True)
                out.write(line)
                out.flush()
            if proc.wait():
                raise RuntimeError(f"Command failed; see {log}")
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=30)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--modes", choices=list(MODES), nargs="+", default=["baseline"])
    args = ap.parse_args()
    data = ROOT / "data/pilot"
    results = ROOT / "results/pilot"
    data.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    source = ROOT / "pilot_sites.csv"
    with source.open() as f:
        sites = list(csv.DictReader(f))
    assert len(sites) == 5 and len({s["domain"] for s in sites}) == 5
    settings = ["--workers", "1", "--headful", "--subpages", "1", "--dwell", "5",
                "--timeout", "45", "--site-timeout", "180", "--consent-wait", "15",
                "--locale", "en-US", "--timezone", "Europe/Amsterdam", "--seed", "0"]
    subprocess.run(VPN + ["up", "-d", "--wait", "--wait-timeout", "120", "gluetun"],
                   cwd=ROOT, check=True)
    exit_info = trace(VPN)
    if exit_info["country"] != "NL":
        raise RuntimeError("Pilot requires the verified Netherlands exit")
    manifest = {"kind": "diagnostic pilot, convenience sample; not study findings",
                "sites_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "settings": settings, "exit": exit_info}
    manifest_path = results / "manifest.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if any(previous.get(k) != v for k, v in manifest.items()):
            raise RuntimeError("Pilot settings/exit changed. Preserve this batch and use a new directory.")
    else:
        manifest["started_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    (data / "sites.csv").write_bytes(source.read_bytes())
    db_path = data / "crawl.sqlite"
    for mode in args.modes:
        if trace(VPN) != exit_info:
            raise RuntimeError("VPN exit changed before run")
        input_path = "data/pilot/sites.csv"
        if mode != "baseline":
            if not db_path.exists():
                raise RuntimeError("Run baseline first")
            with sqlite3.connect(db_path) as db:
                successful = {r[0] for r in db.execute(
                    "SELECT site FROM sites_done WHERE run='baseline' AND ok=1")}
            selected = [s for s in sites if s["domain"] in successful]
            if not selected:
                raise RuntimeError("No successful baseline sites; diagnose failures before continuing")
            with (data / "successful_sites.csv").open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["domain", "category"])
                writer.writeheader()
                writer.writerows(selected)
            input_path = "data/pilot/successful_sites.csv"
        command = VPN + ["run", "--rm", "-T", "crawler", "xvfb-run", "-a", "python", "-u",
                         "crawler.py", "--sites", input_path, "--out", "data/pilot/crawl.sqlite",
                         "--run", mode, *settings, *MODES[mode]]
        if mode != "baseline":
            command += ["--reuse-urls-from", "data/pilot/crawl.sqlite:baseline"]
        print(f"\nPilot condition: {mode}; exit {exit_info['ip']} (NL)", flush=True)
        run(command, results / f"{mode}.log")
        if trace(VPN) != exit_info:
            raise RuntimeError("VPN exit changed during run; do not compare this batch")
        # Analyze locally in Docker, with external networking disabled.
        offline = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.test.yml"]
        run(offline + ["run", "--rm", "-T", "crawler", "python", "analyze.py",
                       "data/pilot/crawl.sqlite", "--baseline", "baseline", "--outdir", "results/pilot"],
            results / "analysis.log")
    print(f"\nPilot artifacts: {results}", flush=True)


if __name__ == "__main__":
    main()
