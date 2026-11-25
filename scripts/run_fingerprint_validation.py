#!/usr/bin/env python3
"""Run the frozen six-family detector on real sites in four conditions.

By default this is a 5-10-site engineering/measurement validation batch.
``--final-study`` accepts the separately frozen final candidate sample. Every
batch keeps a complete code snapshot, detector hashes, site input, VPN exit
checks, request evidence and page screenshots in one timestamped directory.
"""
import argparse
import csv
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from fingerprinting import RULE_VERSION, TECHNIQUES
from run_pilot import run
from verify_vpn import VPN, expected_country, trace

CONDITIONS = {
    "baseline": [],
    "reject_gpc": ["--consent", "reject", "--gpc"],
    "accept": ["--consent", "accept"],
    "ubol": ["--extension", "ext/ubol"],
}
DETECTOR_FILES = ("instrument.js", "fingerprinting.py", "review_fingerprints.py")


def load_sites(path, final_study=False):
    with Path(path).open(newline="") as stream:
        rows = list(csv.DictReader(line for line in stream if not line.lstrip().startswith('#')))
    required = {"domain", "category"} if final_study else {"domain", "category", "consent_basis"}
    if not ((1 <= len(rows) <= 150) if final_study else (5 <= len(rows) <= 10)):
        raise ValueError('Final study requires 1-150 candidates; validation requires 5-10 sites')
    if not required <= set(rows[0]) or any(not all((row.get(key) or "").strip() for key in required) for row in rows):
        needed = "domain and category" if final_study else "domain, category and consent_basis"
        raise ValueError("Each site requires " + needed)
    if len({row["domain"] for row in rows}) != len(rows):
        raise ValueError("Validation sites must be unique")
    return rows


def snapshot_code(root, destination):
    destination.mkdir()
    sources = [*root.glob("*.py"), *root.glob("*.js"), root / "requirements.txt", root / "extensions.lock.json"]
    if (root / 'study_protocol.json').exists():
        sources.append(root / 'study_protocol.json')
    hashes = {}
    for source in sorted(sources):
        content = source.read_bytes()
        (destination / source.name).write_bytes(content)
        hashes[source.name] = hashlib.sha256(content).hexdigest()
    return hashes


def extension_digest(directory):
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(directory).as_posix().encode() + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def crawl_command(relative, condition, attribute_evidence=False):
    return VPN + [
        "run", "--rm", "--pull", "never", "-T",
        "-e", "TRACKMEASURE_GVL_CACHE=/app/data/gvl", "crawler",
        "xvfb-run", "-a", "python", "-u", relative + "/code/crawler.py",
        "--sites", relative + "/sites.csv",
        "--out", relative + "/crawl.sqlite",
        "--run", condition,
        "--workers", "1", "--headful", "--subpages", "0",
        "--dwell", "5", "--timeout", "45", "--site-timeout", "240",
        "--consent-wait", "20", "--locale", "en-US", "--timezone", "Europe/Amsterdam",
        "--seed", "0", "--script-artifacts", relative + "/sources",
        "--capture-request-payloads", "--page-artifacts", relative + "/pages",
        "--consent-artifacts", relative + "/consent",
        *(['--attribute-evidence'] if attribute_evidence else []), *CONDITIONS[condition],
    ]


def validate_frozen_inputs(batch, source_hashes, extension, extension_sha256):
    for name, expected in source_hashes.items():
        if hashlib.sha256((batch / "code" / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("Frozen code snapshot changed during validation: " + name)
    if extension_digest(extension) != extension_sha256:
        raise RuntimeError("Frozen blocker input changed during validation")


def runtime_versions():
    offline = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.test.yml"]
    probe = (
        "import json,sys,subprocess; from importlib.metadata import version; "
        "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); "
        "chromium=subprocess.check_output([p.chromium.executable_path,'--version'],text=True).strip(); p.stop(); "
        "print(json.dumps({'python':sys.version,'chromium':chromium,'playwright':version('playwright'),"
        "'pandas':version('pandas'),'tldextract':version('tldextract')}))"
    )
    output = subprocess.check_output(
        offline + ["run", "--rm", "--no-deps", "--pull", "never", "-T", "crawler", "python", "-c", probe],
        cwd=ROOT, text=True,
    )
    return json.loads(output)


def generate_report(relative, batch):
    """Run frozen analysis inside Linux; macOS SQLite read-only URI access can fail."""
    offline = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.test.yml"]
    command = offline + ["run", "--rm", "--no-deps", "--pull", "never", "-T", "crawler", "python",
                         relative + "/code/evidence_pilot_report.py", relative]
    if (batch / 'review/labels.csv').exists():
        command += ['--labels', relative + '/review/labels.csv']
    run(command, batch / "report.log")
    return json.loads((batch / "review/summary.json").read_text())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sites", type=Path, default=ROOT / "fingerprint_validation_sites.csv")
    ap.add_argument("--prepare-only", action="store_true", help="write the frozen plan without Docker or site visits")
    ap.add_argument('--attribute-evidence', action='store_true', help='capture primitive attribute values and export correspondence review cards')
    ap.add_argument('--condition-order', default=','.join(CONDITIONS), help='permutation of the four condition names')
    ap.add_argument('--resume', type=Path, help='resume an existing frozen validation directory')
    ap.add_argument('--final-study', action='store_true', help='allow the frozen final candidate sample, with eligibility determined by measurement')
    args = ap.parse_args()
    rows = load_sites(args.sites, args.final_study) if not args.resume else []
    sites = [row["domain"] for row in rows]
    extension = ROOT / "ext/ubol"
    if not (extension / "manifest.json").is_file():
        raise RuntimeError("Install the pinned blocker with make extensions")

    order = args.condition_order.split(',')
    if len(order) != len(CONDITIONS) or set(order) != set(CONDITIONS):
        raise ValueError('condition-order must contain each condition exactly once')
    if args.resume:
        batch = args.resume.resolve()
        relative = batch.relative_to(ROOT).as_posix()
        manifest_path = batch / 'experiment.json'
        manifest = json.loads(manifest_path.read_text())
        if manifest['runner_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
            raise RuntimeError('Runner changed since preparation; prepare a new study')
        source_hashes = manifest['source_hashes']
        extension_sha256 = manifest['extension_sha256']
        commands = manifest['commands']
        sites = manifest['sites']
        # JSON is saved with insertion order; execution order is explicitly frozen as conditions.
        commands = {name: commands[name] for name in manifest['conditions']}

    else:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        relative = "results/fingerprint-validation/" + stamp
        batch = ROOT / relative
        batch.mkdir(parents=True)
        source_hashes = snapshot_code(ROOT, batch / "code")
        with (batch / "sites.csv").open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["domain", "category"])
            writer.writerows((row["domain"], row["category"]) for row in rows)
        (batch / "site_provenance.csv").write_bytes(args.sites.read_bytes())
        extension_sha256 = extension_digest(extension)
        commands = {condition: crawl_command(relative, condition, args.attribute_evidence) for condition in order}
        manifest = {
            "title": "Real-site fingerprint evidence validation",
            "kind": 'final candidate study' if args.final_study else 'validation; not the final study',
            "started": stamp,
            "status": "prepared",
            "sites": sites,
            "conditions": order,
            "settings": {"attribute_evidence": args.attribute_evidence, "headful": True, "workers": 1, "dwell": 5, "subpages": 0,
                         "locale": "en-US", "timezone": "Europe/Amsterdam"},
            "detector": {"rule_version": RULE_VERSION, "techniques": TECHNIQUES,
                         "hashes": {name: source_hashes[name] for name in DETECTOR_FILES}},
            "source_hashes": source_hashes,
            "sites_sha256": hashlib.sha256(args.sites.read_bytes()).hexdigest(),
            "crawl_sites_sha256": hashlib.sha256((batch / 'sites.csv').read_bytes()).hexdigest(),
            "extension_sha256": extension_sha256,
            "commands": commands,
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "note": "Detector and inputs are frozen before visits. Eligibility requires successful navigation and confirmed Cookiebot/TCF consent for both requested consent arms.",
        }
        manifest_path = batch / "experiment.json"

    def save():
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    save()
    print("Validation directory:", batch, flush=True)
    if args.prepare_only:
        print(f"Prepared {len(sites) * len(CONDITIONS)} site-runs. No Docker calls or site visits started.")
        return

    try:
        validate_frozen_inputs(batch, source_hashes, extension, extension_sha256)
        if hashlib.sha256((batch / 'sites.csv').read_bytes()).hexdigest() != manifest['crawl_sites_sha256']:
            raise RuntimeError('Frozen crawl site list changed')
        runtime = runtime_versions()
        if manifest.get('runtime') and manifest['runtime'] != runtime:
            raise RuntimeError('Runtime changed while resuming')
        manifest['runtime'] = runtime
        for line in (batch / 'code/requirements.txt').read_text().splitlines():
            if "==" in line and not line.startswith("#"):
                package, required = line.split("==", 1)
                if manifest["runtime"].get(package) != required.strip():
                    raise RuntimeError("Cached dependency differs from requirements.txt: " + package)
        subprocess.run(VPN + ["up", "-d", "--wait", "--wait-timeout", "120", "gluetun"], cwd=ROOT, check=True)
        exit_info = trace(VPN)
        if exit_info["country"] != expected_country().upper():
            raise RuntimeError("VPN country differs from .env")
        if manifest.get('exit') and manifest['exit'] != exit_info:
            raise RuntimeError('VPN exit changed while resuming; prepare a new batch')
        manifest.update(status="running", exit=exit_info,
                        exit_checks=manifest.get('exit_checks', []),
                        completed_conditions=manifest.get('completed_conditions', []))
        save()
        for condition, command in commands.items():
            if condition in manifest['completed_conditions']:
                continue
            validate_frozen_inputs(batch, source_hashes, extension, extension_sha256)
            before = trace(VPN)
            if before != exit_info:
                raise RuntimeError("VPN exit changed before " + condition)
            print("\nValidation condition", condition, flush=True)
            run(command, batch / (condition + ".log"))
            after = trace(VPN)
            manifest["exit_checks"].append({"condition": condition, "before": before, "after": after})
            if after != exit_info:
                raise RuntimeError("VPN exit changed during " + condition)
            manifest["completed_conditions"].append(condition)
            save()
        manifest["status"] = "complete"
    except BaseException as error:
        manifest.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "invalid",
                        error=type(error).__name__ + ": " + str(error)[:500])
        raise
    finally:
        manifest["finished"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save()
        if (batch / "crawl.sqlite").exists():
            try:
                result = generate_report(relative, batch)
                print("Fully eligible sites:", len(result["fully_eligible_sites"]), "/", len(sites), flush=True)
                print("Eligible:", ", ".join(result["fully_eligible_sites"]) or "none", flush=True)
            except Exception as report_error:
                manifest['report_error'] = type(report_error).__name__ + ': ' + str(report_error)[:500]
                save()
                if manifest['status'] == 'complete':
                    raise
                print('Diagnostic report failed:', manifest['report_error'], file=sys.stderr)
    print("\nReview:", batch / "review/report.md")
    print("Copy review/pair_review_template.csv to review/labels.csv and review every candidate.")
    print("Recalculate with: python3", relative + "/code/evidence_pilot_report.py", relative,
          "--labels", relative + "/review/labels.csv")


if __name__ == "__main__":
    main()
