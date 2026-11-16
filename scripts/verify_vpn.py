#!/usr/bin/env python3
"""Host-side Docker integration check. Temporarily stops and restores Gluetun.

Run before crawling, never alongside an active experiment. No VPN secrets are read.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = ["docker", "compose", "-f", "docker-compose.yml"]
VPN = BASE + ["-f", "docker-compose.vpn.yml"]
MARKER = "VPN_CHECK "
PROBE = r'''
import concurrent.futures, json, socket, sys, urllib.request
def trace():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("https://www.cloudflare.com/cdn-cgi/trace", timeout=25) as r:
        fields = dict(line.split("=", 1) for line in r.read().decode().splitlines() if "=" in line)
    assert fields.get("ip") and fields.get("loc"), "Missing exit IP/country"
    return {"ip": fields["ip"], "country": fields["loc"]}
def reachable(target):
    family = socket.AF_INET6 if ":" in target else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((target, 443))
        return True
    except OSError:
        return False
def emit(value):
    print("VPN_CHECK " + json.dumps(value), flush=True)
if sys.argv[1] == "trace":
    emit(trace())
else:
    targets = ["1.1.1.1", "1.0.0.1", "2606:4700:4700::1111"]
    with concurrent.futures.ThreadPoolExecutor() as pool:
        before = dict(zip(targets, pool.map(reachable, targets)))
    emit({"exit": trace(), "direct_before": before})
    assert sys.stdin.readline().strip() == "VPN_STOPPED", "Host closed check early"
    with concurrent.futures.ThreadPoolExecutor() as pool:
        after = dict(zip(targets, pool.map(reachable, targets)))
    try:
        trace()
        https_blocked = False
    except Exception:
        https_blocked = True
    emit({"direct_after": after, "https_blocked": https_blocked})
'''


def run(args, timeout=180):
    return subprocess.run(args, cwd=ROOT, check=True, timeout=timeout)


def message(process, timeout=75):
    """Read marked JSON without waiting forever on a stuck container."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
            break
        line = process.stdout.readline()
        if not line:
            raise RuntimeError("Crawler check exited before returning a result")
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    raise TimeoutError("Crawler check did not return a result in time")


def spawn(command):
    return subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, bufsize=1)


def trace(command):
    proc = spawn(command + ["run", "--rm", "--no-deps", "-T", "crawler",
                            "python", "-u", "-c", PROBE, "trace"])
    try:
        value = message(proc)
        if proc.wait(timeout=15):
            raise RuntimeError("Exit-IP check failed")
        return value
    finally:
        proc.stdin.close()
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=15)


def expected_country():
    # The same non-secret setting works from .env or an exported variable.
    values = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return os.environ.get("VPN_EXPECTED_COUNTRY", values.get("VPN_EXPECTED_COUNTRY", "NL"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", default=expected_country())
    args = parser.parse_args()
    country = args.country.upper()
    report = {"status": "failed", "checked_at": datetime.datetime.now(
        datetime.timezone.utc).isoformat(), "expected_country": country}
    proc = None
    restore_needed = False
    try:
        print("Checking Docker configuration and starting VPN...", flush=True)
        run(VPN + ["config", "--quiet"])
        run(VPN + ["up", "-d", "--wait", "--wait-timeout", "120", "gluetun"])
        direct = trace(BASE)
        proc = spawn(VPN + ["run", "--rm", "--no-deps", "-T", "crawler",
                            "python", "-u", "-c", PROBE, "kill-switch"])
        ready = message(proc)
        report.update(ready)
        report["exit_differs_from_direct"] = ready["exit"]["ip"] != direct["ip"]
        if ready["exit"]["country"] != country:
            raise RuntimeError(f"Expected {country}, observed {ready['exit']['country']}")
        if not report["exit_differs_from_direct"]:
            raise RuntimeError("VPN exit matches the direct Docker exit")
        if not any(ready["direct_before"].values()):
            raise RuntimeError("No direct-IP probe worked before stopping VPN")
        print(f"VPN exit: {ready['exit']['ip']} ({country}). Testing VPN loss...", flush=True)
        restore_needed = True
        run(VPN + ["stop", "-t", "10", "gluetun"])
        proc.stdin.write("VPN_STOPPED\n")
        proc.stdin.flush()
        stopped = message(proc)
        report.update(stopped)
        if proc.wait(timeout=15):
            raise RuntimeError("Kill-switch probe failed")
        if any(stopped["direct_after"].values()) or not stopped["https_blocked"]:
            raise RuntimeError("Crawler retained internet access with VPN stopped")
        print("VPN loss blocked HTTPS and direct-IP connections. Restoring VPN...", flush=True)
        run(VPN + ["up", "-d", "--wait", "--wait-timeout", "120", "gluetun"])
        restore_needed = False
        recovery = trace(VPN)
        report["recovery_exit"] = recovery
        if recovery != ready["exit"]:
            raise RuntimeError("Exit IP/country changed after recovery; investigate before crawling")
        report["status"] = "passed"
        print("VPN restored with the same exit. All VPN checks passed.", flush=True)
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        if proc is not None:
            proc.stdin.close()
            if proc.poll() is None:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.wait(timeout=15)
        if restore_needed:
            run(VPN + ["up", "-d", "--wait", "--wait-timeout", "120", "gluetun"])
        out = ROOT / "results" / "vpn_verification.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Report: {out}", flush=True)


if __name__ == "__main__":
    main()
