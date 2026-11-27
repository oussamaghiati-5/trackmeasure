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
# Purpose-labelling outputs (scripts/label_purposes.py, scripts/purpose_results.py).
# Optional: copied when present. Script URLs and per-site path templates are dropped
# from the label table; cluster templates are shared by >= 2 sites and kept.
PURPOSE_COPY = ("purpose_results.json", "purpose_survival.csv", "purpose_prevalence.csv",
                "purpose_consent_sites.csv", "hidden_vendor_clusters.csv", "harm_survival.csv",
                "sensitivity_audit.json")
PURPOSE_DROP = ("example_url", "template")
# Cookie evidence: names/domains and aggregate counts only, no values or URLs.
EVIDENCE_COPY = ("id_sharing.json", "id_sharing.csv", "id_sharing_strict.csv",
                 "cookie_isolation.json", "cookie_isolation.csv")
INCIDENT_KEYS = ("condition", "description", "handling")


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


def sanitize_purposes(source, destination):
    with source.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fields = [field for field in reader.fieldnames or () if field not in PURPOSE_DROP]
        with destination.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for row in reader:
                writer.writerow({field: row.get(field, "") for field in fields})


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
    purpose_files = [name for name in PURPOSE_COPY + EVIDENCE_COPY if (batch / name).is_file()]
    for name in purpose_files:
        (destination / name).write_bytes((batch / name).read_bytes())
    if (batch / "script_purposes.csv").is_file():
        sanitize_purposes(batch / "script_purposes.csv", destination / "script_purposes.csv")
    # Validation sample: scores only. The sheets hold script URLs and stay private.
    validation = batch / "validation"
    if (validation / "agreement.json").is_file():
        method = validation / "method.json"
        write_json(destination / "validation_agreement.json",
                   {"method": read_json(method) if method.is_file() else None,
                    **read_json(validation / "agreement.json")})

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
    # Collection incidents (network loss, host suspension) and how they were handled.
    incidents = [{key: item[key] for key in INCIDENT_KEYS if key in item}
                 for item in provenance.get("incidents", ()) if isinstance(item, dict)]
    if incidents:
        public_provenance["incidents"] = incidents
    write_json(destination / "vpn_provenance.json", public_provenance)

    readme = f"""# Public results bundle — {batch.resolve().name}

This directory contains selected aggregate and per-site outputs for the completed study batch.

## Files

- `analyze.md` (when present): the written analysis of this batch.

- `per_site.csv`: measurement status and per-site metrics for each condition; URLs in error messages are redacted.
- `comparison.csv`: site-paired domain–technique pair and third-party-domain comparisons.
- `paired_uncertainty.json`: generic purpose-agnostic paired bootstrap estimates, using
  `(site, script domain, technique)`; its `_meta` block distinguishes it from paper results.
- `consent_coverage.csv`: attempted, confirmed, failed, and unknown consent outcomes. Outcomes combine the stored CMP API state (re-assessed with the current rules) with the consent cookies stored at the end of the visit; `confirmed_at_crawl` shows the crawl-time count.
- `fp_scripts.csv`: detected script domains and techniques; script URLs are deliberately omitted because their paths or query strings can contain session tokens.
- `sites.csv`: frozen list of domains used as input.
- `script_purposes.csv`: one row per fingerprinting script (site, script domain, path template) with its purpose label, party (first-party, third-party or hidden first-party vendor), page context, confidence and evidence; script URLs and path templates omitted.
- `hidden_vendor_clusters.csv`: path templates served by two or more sites, attributed to vendors (e.g. bot managers behind first-party URLs).
- `purpose_prevalence.csv`, `purpose_survival.csv`, `purpose_consent_sites.csv`, `purpose_results.json`: results split by purpose (security/anti-fraud, ads/analytics, other/unknown), using `(site, script domain, purpose, technique)`. These are the paper's headline estimates.
- `harm_survival.csv` and the `harm` block of `purpose_results.json`: the same analysis by harm class. Tracking means an advertising or analytics purpose; Service means security or functional. The inclusive sensitivity scenario adds Dual-use and high-capability Undetermined scripts, but is not a formal upper bound. `script_purposes.csv` gives each script's `harm_class`, `capability`, `cross_site`, `sends_attributes` and `writes_storage`.
- `sensitivity_audit.json`: post-hoc checks excluding the accept restart sites, the suspended Privacy Badger duration outlier, and conservatively flagged degraded GPC visits. These checks do not silently change primary eligibility.
- `id_sharing.csv` and `id_sharing.json`: identifier-like cookie values found in requests to another party, such as an analytics client ID sent to its vendor or a cookie synced between ad companies.
  - A value counts as an identifier only if it occurs in one browser profile (one run and site).
  - `id_sharing_strict.csv` additionally requires that cookie name/owner to vary across at least two profiles; `id_sharing.json` reports both summaries and paired exact McNemar tests.
  - `receiver_known_tracker` marks receivers on DuckDuckGo's tracker list.
  - The files contain cookie names and domains, never cookie values or URLs.
- `cookie_isolation.csv` and `cookie_isolation.json`: identifier-like cookie tokens re-issued to the same site across two runs whose Chromium profiles were created independently and deleted afterwards.
  - Cross-site constants, timestamps and consent cookies are excluded; values are never exported.
  - This is evidence of cross-profile value reuse, not proof of fingerprint derivation. The shared exit IP, server state or a site-scoped constant can explain it; `respawn.py` supplies the controlled follow-up protocol.
- `validation_agreement.json` (when present): scores for the 80-script validation sample.
  - It holds the agreement between the two annotators, the detector's precision per technique, and the rule labels' precision per purpose.
  - `method` says who annotated and adjudicated. For 2026-11-25, two independent AI annotators labelled the sample and a third AI reviewer adjudicated their disagreements; none was human.
  - The sheets themselves contain script URLs and stay private.
- `manifest.json`: crawl settings and source hashes; exact VPN exit IP removed.
- `vpn_provenance.json`: public provider/region, resolver-change and collection-incident summary; credential-bearing profile hashes and exact timestamps omitted.

Raw crawl databases and crawl logs are not included. The database can contain cookie values and the logs may contain request details. Error-message URLs are also redacted. Use the repository scripts and the private local batch to reproduce or inspect raw observations. These results describe the accessible sites and this crawler configuration; heuristic API detections are not ground-truth proof of tracking, and purpose labels are evidence-based estimates (see `confidence` and `evidence`), not vendor-confirmed facts.
"""
    (destination / "README.md").write_text(readme, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path, help="Completed private batch directory")
    parser.add_argument("--sites", type=Path, required=True, help="Frozen input sites CSV")
    parser.add_argument("--out", type=Path, help="Public bundle directory (default: public_results/BATCH)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Refresh files in an existing destination directory")
    args = parser.parse_args()
    args.out = args.out or Path("public_results") / args.batch.resolve().name
    export(args.batch, args.sites, args.out, overwrite=args.overwrite)
    print(f"Wrote privacy-reduced results bundle to {args.out}")


if __name__ == "__main__":
    main()
