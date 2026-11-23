#!/usr/bin/env python3
"""Create a privacy-reduced, shareable bundle from a completed study batch.

Example:
    python scripts/export_public_results.py results/full_eu_20261122 \
        --sites data/full_eu_20261122/sites.csv

The exporter intentionally omits raw logs and databases, drops script URLs
(which may contain session tokens), removes the exact VPN exit IP, and excludes
VPN profile hashes. It never modifies the source batch.
"""
import argparse
import csv
import json
import re
from pathlib import Path


COPY_FILES = (
    "comparison.csv",
    "consent_coverage.csv",
    "paired_uncertainty.json",
)
URL_PATTERN = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sanitize_fingerprint_scripts(source, destination):
    with source.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"Missing CSV header: {source}")
        fields = [field for field in reader.fieldnames if field != "script_url"]
        if "script_domain" not in fields or "technique" not in fields:
            raise ValueError(f"Unexpected fingerprint script schema: {source}")
        with destination.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for row in reader:
                writer.writerow({field: row.get(field, "") for field in fields})


def sanitize_per_site(source, destination):
    with source.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"Missing CSV header: {source}")
        with destination.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=reader.fieldnames, lineterminator="\n")
            writer.writeheader()
            for row in reader:
                for field in ("error", "measurement_error"):
                    if row.get(field):
                        row[field] = URL_PATTERN.sub("[URL]", row[field])
                writer.writerow(row)


def export(batch, sites, destination, overwrite=False):
    if destination.exists():
        if not destination.is_dir():
            raise FileExistsError(f"Destination exists and is not a directory: {destination}")
        if not overwrite:
            raise FileExistsError(f"Destination already exists: {destination}; pass --overwrite to refresh it")
    missing = [name for name in COPY_FILES + ("fp_scripts.csv", "manifest.json", "vpn_provenance.json")
               if not (batch / name).is_file()]
    if not sites.is_file():
        missing.append(str(sites))
    if missing:
        raise FileNotFoundError("Missing required source files: " + ", ".join(missing))

    destination.mkdir(parents=True, exist_ok=True)
    for name in COPY_FILES:
        (destination / name).write_bytes((batch / name).read_bytes())
    (destination / "sites.csv").write_bytes(sites.read_bytes())
    sanitize_per_site(batch / "per_site.csv", destination / "per_site.csv")
    sanitize_fingerprint_scripts(batch / "fp_scripts.csv", destination / "fp_scripts.csv")

    manifest = read_json(batch / "manifest.json")
    exit_info = manifest.get("exit")
    if isinstance(exit_info, dict):
        exit_info.pop("ip", None)
        exit_info["ip"] = "redacted"
    write_json(destination / "manifest.json", manifest)

    provenance = read_json(batch / "vpn_provenance.json")
    public_provenance = {
        key: provenance[key]
        for key in ("provider", "server", "protocol", "expected_country")
        if key in provenance
    }
    change = provenance.get("resolver_change")
    if isinstance(change, dict):
        public_provenance["resolver_change"] = {
            key: change[key]
            for key in ("before", "after", "reason")
            if key in change
        }
    write_json(destination / "vpn_provenance.json", public_provenance)

    readme = """# Public results bundle — full_eu_20261122

This directory contains selected aggregate and per-site outputs for the completed study batch.

## Files

- `per_site.csv`: measurement status and per-site metrics for each condition; URLs in error messages are redacted.
- `comparison.csv`: site-paired domain–technique pair and third-party-domain comparisons.
- `paired_uncertainty.json`: paired bootstrap estimates and intervals.
- `consent_coverage.csv`: attempted, confirmed, failed, and unknown consent outcomes.
- `fp_scripts.csv`: detected script domains and techniques; script URLs are deliberately omitted because their paths or query strings can contain session tokens.
- `sites.csv`: frozen list of domains used as input.
- `manifest.json`: crawl settings and source hashes; exact VPN exit IP removed.
- `vpn_provenance.json`: public provider/region and resolver-change summary; credential-bearing profile hashes and exact timestamps omitted.

Raw crawl databases and crawl logs are not included. The database can contain cookie values and the logs may contain request details. Error-message URLs are also redacted. Use the repository scripts and the private local batch to reproduce or inspect raw observations. These results describe the accessible sites and this crawler configuration; heuristic API detections are not ground-truth proof of tracking.
"""
    (destination / "README.md").write_text(readme, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path, help="Completed private batch directory")
    parser.add_argument("--sites", type=Path, required=True, help="Frozen input sites CSV")
    parser.add_argument("--out", type=Path, default=Path("public_results/full_eu_20261122"),
                        help="Public bundle directory")
    parser.add_argument("--overwrite", action="store_true",
                        help="Refresh files in an existing destination directory")
    args = parser.parse_args()
    export(args.batch, args.sites, args.out, overwrite=args.overwrite)
    print(f"Wrote privacy-reduced results bundle to {args.out}")


if __name__ == "__main__":
    main()
