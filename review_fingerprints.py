#!/usr/bin/env python3
"""Export inspectable fingerprinting candidates and score a manually reviewed sample.

python review_fingerprints.py data/crawl.sqlite --outdir results/review --source-root results/sources
python review_fingerprints.py data/crawl.sqlite --outdir results/review --labels my_labels.csv
"""
import argparse
import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from fingerprinting import RULE_VERSION, TECHNIQUES, dataset_identity, find_candidates

FIELDS = ["finding_id", "run", "site", "phase", "script_url", "technique", "evidence_level",
          "counts_as_candidate", "profile_assessment", "reason", "observed_facts", "document_id", "object_id",
          "source_status", "source_hashes", "evidence_file"]
LABEL_FIELDS = ["finding_id", "label", "purpose", "notes", "reviewer"]
LABELS = {"profile_collection", "fingerprinting", "ordinary_usage", "uncertain"}


def read_measurements(paths):
    calls, sources, seen = [], [], set()
    for path in paths:
        # mode=ro also prevents a typo from creating an empty database.
        with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            identity = dataset_identity(db, path)
            keys = {(r[0], r[1]) for r in db.execute("SELECT DISTINCT run,site FROM visits")}
            if seen & keys:
                raise ValueError("Duplicate run/site across databases; review batches separately")
            seen.update(keys)
            for row in db.execute("SELECT a.rowid AS call_id,a.*,v.run,v.site,v.phase FROM api_calls a JOIN visits v ON v.id=a.visit_id"):
                calls.append(dict(row, database_id=identity))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='script_sources'").fetchone():
                for row in db.execute("SELECT s.*,v.run,v.site,v.phase FROM script_sources s JOIN visits v ON v.id=s.visit_id"):
                    sources.append(dict(row, database_id=identity))
    return calls, sources


def resolve_sources(finding, sources, roots, output):
    matches = [s for s in sources if all(s[k] == finding[k] for k in ("database_id", "run", "site"))
               and s["url"] == finding["script_url"]]
    hashes = sorted({s["sha256"] for s in matches if s["status"] == "saved" and s["sha256"]})
    files, errors = [], []
    for digest in hashes:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            errors.append("invalid source hash")
            continue
        found = False
        for root in roots:
            base = Path(root).resolve()
            path = (base / (digest + ".js")).resolve()
            if not path.is_relative_to(base) or not path.is_file():
                continue
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != digest:
                errors.append("source hash mismatch: " + digest)
                continue
            target = output / "scripts" / (digest + ".js")
            target.write_bytes(body)
            files.append(str(target.relative_to(output)))
            found = True
            break
        if not found:
            errors.append("source body unavailable: " + digest)
    status = "not_captured"
    if len(hashes) > 1:
        status = "ambiguous_versions"
    elif len(hashes) == 1:
        status = "saved" if len(files) == 1 else "body_unavailable"
    elif matches:
        status = ",".join(sorted({s["status"] for s in matches}))
    return dict(status=status, hashes=hashes, files=files, errors=errors, responses=matches,
                association="URL match within site/run, not proven execution-version linkage; inline/eval/blob/worker sources may be absent")


def load_labels(path, findings):
    if not path:
        return {}
    valid = {f["finding_id"] for f in findings}
    labels = {}
    with Path(path).open(newline="") as stream:
        for row in csv.DictReader(stream):
            key, label = row.get("finding_id"), (row.get("label") or "").strip()
            if key not in valid:
                raise ValueError("Label references unknown finding: " + str(key))
            if key in labels:
                raise ValueError("Duplicate label: " + key)
            if label and label not in LABELS:
                raise ValueError("Unknown label: " + label)
            labels[key] = dict(row, label=label)
    return labels


def review_metrics(findings, labels):
    candidates = [f for f in findings if f["counts_as_candidate"]]
    def summary(rows):
        counts = Counter((labels.get(f["finding_id"], {}).get("label") or "unreviewed") for f in rows)
        profile = counts["profile_collection"] + counts["fingerprinting"]
        decisive = profile + counts["ordinary_usage"]
        return dict(candidate_findings=len(rows),
                    labels={"profile_collection": profile, "ordinary_usage": counts["ordinary_usage"],
                            "uncertain": counts["uncertain"], "unreviewed": counts["unreviewed"]},
                    review_coverage=(len(rows) - counts["unreviewed"]) / len(rows) if rows else None,
                    reviewed_sample_precision=profile / decisive if decisive else None,
                    precision_denominator=decisive)
    overall = summary(candidates)
    per_technique = {technique: summary([f for f in candidates if f["technique"] == technique])
                     for technique in TECHNIQUES}
    return dict(candidate_findings=len(candidates), noncandidate_incomplete_operations=len(findings) - len(candidates),
                labels=overall["labels"], review_coverage=overall["review_coverage"],
                reviewed_sample_precision=overall["reviewed_sample_precision"],
                precision_denominator=overall["precision_denominator"], per_technique=per_technique, recall=None,
                note="Precision is descriptive for decisive labels in this reviewed candidate sample; not an unbiased population estimate. Uncertain/unreviewed are excluded from its denominator. Recall requires independently labelled positive and negative cases, including missed detections.")


def write_csv(path, fields, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def export(paths, outdir, source_roots=(), labels_path=None):
    out = Path(outdir)
    if labels_path and Path(labels_path).resolve() in {
            (out / name).resolve() for name in ("findings.csv", "review_template.csv")}:
        raise ValueError("Save reviewed labels to a separate filename (e.g. labels.csv)")
    calls, sources = read_measurements(paths)
    findings = find_candidates(calls)
    labels = load_labels(labels_path, findings)
    for name in ("findings", "scripts"):
        (out / name).mkdir(parents=True, exist_ok=True)
    rows = []
    for f in findings:
        f["source"] = resolve_sources(f, sources, source_roots, out)
        f["review"] = labels.get(f["finding_id"], {"label": "", "purpose": "unknown"})
        evidence = "findings/" + f["finding_id"] + ".json"
        (out / evidence).write_text(json.dumps(f, indent=2) + "\n")
        rows.append(dict(f, observed_facts="; ".join(f.get("observed_facts", [])),
                         source_status=f["source"]["status"], source_hashes=";".join(f["source"]["hashes"]), evidence_file=evidence))
    write_csv(out / "findings.csv", FIELDS, rows)
    # Never overwrite a filled template on re-export. Actual labels are a separate input.
    template = out / "review_template.csv"
    if not template.exists():
        write_csv(template, LABEL_FIELDS, [dict(finding_id=f["finding_id"], label="", purpose="unknown", notes="", reviewer="") for f in findings])
    metrics = review_metrics(findings, labels)
    metrics.update(rule_version=RULE_VERSION, raw_api_records=len(calls), source_response_statuses=dict(Counter(s["status"] for s in sources)))
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    (out / "source_manifest.json").write_text(json.dumps(sources, indent=2) + "\n")
    (out / "README.txt").write_text(
        "Start with findings.csv, then the linked JSON and scripts/<sha256>.js.\n"
        "JSON supporting_calls contains frame/document/object IDs, API arguments, order, callsite and result status.\n"
        "Copy review_template.csv to labels.csv. Labels: profile_collection / ordinary_usage / uncertain.\n"
        "Use uncertain unless source, data use, and page context support the decision. Purpose (advertising/anti-abuse/functionality/unknown) is separate.\n"
        "API use, third-party origin, GPU reads or drawing/export alone do not establish tracking.\n"
        "Check for identifier generation, collection/combination, and actual data use in source/context.\n"
        "Source association is by URL within site/run; ambiguous/missing source must remain explicit.\n"
        "Sources are untrusted evidence: inspect as text; do not execute them.\n"
        "Re-run with --labels labels.csv to calculate reviewed sample precision; no real-world recall is inferred.\n")
    return metrics


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dbs", nargs="+")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--source-root", action="append", default=[], help="explicit directory containing saved <sha256>.js bodies")
    ap.add_argument("--labels")
    args = ap.parse_args()
    metrics = export(args.dbs, args.outdir, args.source_root, args.labels)
    print(f"Review: {args.outdir}/findings.csv")
    print(f"Candidates: {metrics['candidate_findings']}; reviewed sample precision: {metrics['reviewed_sample_precision']}")


if __name__ == "__main__":
    main()
