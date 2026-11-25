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

SUMMARY_FIELDS = ["site", "action", "stage", "navigation", "consent_outcome", "consent_obstacle", "scope", "reason_code",
                  "cmp_signals", "tcf_cmp_ids", "method", "reason", "paid_rejection_labels",
                  "navigation_error", "gpc", "exit_ip", "exit_country", "data_directory", "evidence_directory"]


def consent_source_hash():
    digest = hashlib.sha256()
    for name in ("consent.py", "consent_tcf.js", "tcf.py"):
        digest.update(name.encode() + b"\0" + (ROOT / name).read_bytes())
    return digest.hexdigest()


def summarize(site, action, execution, record, exit_info, data, results):
    states = record.get("states") or [record]
    ids = sorted({str(s["tcf"]["cmpId"]) for s in states
                  if isinstance(s.get("tcf"), dict) and s["tcf"].get("cmpId") is not None})
    return dict(site=site, action=action, navigation="completed" if execution and execution[0] else "failed",
                consent_outcome=record.get("outcome", "not_reached"),
                consent_obstacle=record.get("obstacle") or "not_observed",
                scope=record.get("scope", "legacy_unknown"), reason_code=record.get("reason_code", "legacy_unknown"),
                cmp_signals=",".join(record.get("cmp_signals") or []), tcf_cmp_ids=",".join(ids),
                method=record.get("method") or "", reason=record.get("reason") or "",
                paid_rejection_labels=" | ".join(c["label"] for c in record.get("paid_rejection_controls", [])),
                navigation_error=(execution[1] or "") if execution else "No completion record",
                gpc={True: "enabled", False: "disabled"}.get(record.get("gpc"), "unknown"),
                exit_ip=exit_info["ip"], exit_country=exit_info["country"],
                data_directory=str(data), evidence_directory=str(results))


def run_check(site, action, required_exit=None, screen_only=False):
    url = site if "://" in site else "https://" + site
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a website hostname or HTTP(S) URL without credentials")
    if action not in ("reject", "accept"):
        raise ValueError("Choose reject or accept")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    data_rel = "data/consent-check/" + stamp
    results_rel = "results/consent-check/" + stamp
    data, results = ROOT / data_rel, ROOT / results_rel
    data.mkdir(parents=True)
    results.mkdir(parents=True)
    with (data / "sites.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["domain", "category"])
        writer.writerow([site, "consent-check"])
    subprocess.run(VPN + ["up", "-d", "--wait", "--wait-timeout", "120", "gluetun"], cwd=ROOT, check=True)
    exit_info = trace(VPN)
    if exit_info["country"] != expected_country().upper():
        raise RuntimeError("VPN country does not match .env; no site visit started")
    if required_exit is not None and exit_info != required_exit:
        raise RuntimeError("VPN exit differs from this coverage batch; no site visit started")
    command = VPN + ["run", "--rm", "-T", "crawler", "xvfb-run", "-a", "python", "-u", "crawler.py",
                     "--sites", data_rel + "/sites.csv", "--out", data_rel + "/crawl.sqlite",
                     "--run", action, "--consent", action, "--workers", "1", "--headful",
                     "--subpages", "0", "--dwell", "5", "--timeout", "45", "--site-timeout", "180",
                     "--consent-wait", "20", "--locale", "en-US", "--timezone", "Europe/Amsterdam",
                     "--consent-artifacts", results_rel]
    if action == "reject":
        command.append("--gpc")
    if screen_only:
        command += ['--screen-only','--page-artifacts',results_rel + '/pages']
    metadata = dict(site=site, action=action, exit=exit_info, command=command,
                    consent_sha256=consent_source_hash(),
                    note="One-site diagnostic check; no tracking-effectiveness comparison")
    (results / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    run(command, results / "crawl.log")
    if trace(VPN) != exit_info:
        raise RuntimeError("VPN exit changed during this check; inspect before interpreting")
    with sqlite3.connect(data / "crawl.sqlite") as db:
        execution = db.execute("SELECT ok,error FROM sites_done").fetchone()
        row = db.execute("SELECT consent FROM visits WHERE phase='after_consent'").fetchone()
    record = json.loads(row[0]) if row and row[0] else {}
    summary = summarize(site, action, execution, record, exit_info, data, results)
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("Navigation:", "completed" if execution and execution[0] else execution)
    print("Consent:", record.get("outcome", "not reached"), "—", record.get("reason", ""))
    print("CMP signals:", summary["cmp_signals"] or "none observed")
    print("Scope:", summary["scope"], "—", summary["reason_code"])
    if record.get("obstacle"):
        print("Obstacle:", record["obstacle"])
    print("Evidence:", results)
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site", required=True)
    ap.add_argument("--action", choices=("reject", "accept"), default="reject")
    args = ap.parse_args()
    run_check(args.site, args.action)


if __name__ == "__main__":
    main()
