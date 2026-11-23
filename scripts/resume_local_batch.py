#!/usr/bin/env python3
"""Resume a frozen batch using a stable-data Compose overlay.

The overlay keeps the live SQLite file outside a cloud-synced bind mount.
"""
import argparse
import hashlib
import json
import subprocess

from run_pilot import MODES, run
from run_study import SOURCE_FILES
from verify_vpn import BASE, VPN, ROOT, expected_country, trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--modes", nargs="+", choices=MODES, required=True)
    args = parser.parse_args()
    if "docker-compose.volume.yml" not in BASE:
        parser.error("configure MEASUREMENT_DATA_OVERLAY in .env first")
    results = ROOT / "results" / args.batch
    manifest = json.loads((results / "manifest.json").read_text())
    network = VPN if manifest["network"] == "vpn" else BASE
    for name in SOURCE_FILES:
        current = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if current != manifest["source_sha256"][name]:
            raise RuntimeError(f"Frozen source changed: {name}")
    exit_info = manifest["exit"]
    if manifest["network"] == "vpn":
        subprocess.run(VPN + ["up", "-d", "--wait", "--wait-timeout", "120", "gluetun"],
                       cwd=ROOT, check=True)
        if exit_info["country"] != expected_country().upper():
            raise RuntimeError("VPN exit country differs from the configured country")
    if trace(network) != exit_info:
        raise RuntimeError("Network exit changed from the frozen batch")
    for mode in args.modes:
        command = network + ["run", "--rm", "-T", "crawler", "xvfb-run", "-a", "python", "-u",
                          "crawler.py", "--sites", f"data/{args.batch}/successful_sites.csv",
                          "--out", f"data/{args.batch}/crawl.sqlite", "--run", mode,
                          "--consent-artifacts", f"results/{args.batch}/consent",
                          *manifest["settings"], *MODES[mode],
                          "--reuse-urls-from", f"data/{args.batch}/crawl.sqlite:baseline"]
        run(command, results / f"{mode}.log")
        if trace(network) != exit_info:
            raise RuntimeError("Network exit changed during the condition")
        offline = BASE + ["-f", "docker-compose.test.yml"]
        run(offline + ["run", "--rm", "-T", "crawler", "python", "analyze.py",
                       f"data/{args.batch}/crawl.sqlite", "--baseline", "baseline",
                       "--outdir", f"results/{args.batch}"], results / "analysis.log")
    print(f"Study outputs: {results}")


if __name__ == "__main__":
    main()
