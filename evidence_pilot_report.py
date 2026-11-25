#!/usr/bin/env python3
"""Export paired-pilot evidence and compare flags with reviewed collection.

python3 evidence_pilot_report.py results/evidence-pilot/<timestamp>
python3 evidence_pilot_report.py results/evidence-pilot/<timestamp> --labels labels.csv
"""
import argparse
import csv
import hashlib
import json
import sqlite3
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import fingerprinting as fingerprinting_module
from consent import consent_record
from exfil import EXFIL_COLUMNS, find_exfil
from fingerprinting import find_candidates
from navigation import measurement_error
from review_fingerprints import export, read_measurements, write_csv
from attribute_evidence import export_cards

LABEL_FIELDS = ["finding_id", "assessment", "purpose", "evidence", "reviewer"]
ASSESSMENTS = {"supported_collection", "ordinary_usage", "uncertain"}
REPORT_VERSION = "paired-review-v8"


def pair_key(finding):
    return (finding["run"], finding["site"], finding.get("script_domain") or "(unknown)", finding["technique"])


def review_pairs(findings):
    groups = defaultdict(list)
    for finding in findings:
        groups[pair_key(finding)].append(finding)
    pairs = []
    for key, members in sorted(groups.items()):
        ids = sorted(finding["finding_id"] for finding in members)
        identifier = hashlib.sha256("\0".join(ids).encode()).hexdigest()[:24]
        pairs.append(dict(pair_id=identifier, run=key[0], site=key[1], script_domain=key[2], technique=key[3],
                          finding_ids=ids, phases=sorted({f["phase"] for f in members}),
                          script_urls=sorted({f["script_url"] for f in members if f.get("script_url")}),
                          observed_facts=sorted({fact for f in members for fact in f.get("observed_facts", [])})))
    return pairs


def read_labels(path, findings, pairs=None):
    if not path:
        return {}
    known = {finding["finding_id"] for finding in findings}
    pair_lookup = {pair["pair_id"]: pair for pair in (pairs or review_pairs(findings))}
    labels = {}
    with Path(path).open(newline="") as stream:
        reader = csv.DictReader(stream)
        pair_mode = "pair_id" in (reader.fieldnames or [])
        if not pair_mode and "finding_id" not in (reader.fieldnames or []):
            raise ValueError("Labels require finding_id or pair_id")
        for row in reader:
            key = row.get("pair_id") if pair_mode else row.get("finding_id")
            assessment = (row.get("assessment") or "").strip()
            valid = pair_lookup if pair_mode else known
            if key not in valid:
                raise ValueError("Unknown finding/pair label: " + str(key))
            target_ids = pair_lookup[key]["finding_ids"] if pair_mode else [key]
            if any(target in labels for target in target_ids):
                raise ValueError("Unknown or duplicate finding label: " + str(key))
            if assessment and assessment not in ASSESSMENTS:
                raise ValueError("Unknown assessment: " + assessment)
            if assessment and not all((row.get(k) or "").strip() for k in ("evidence", "reviewer")):
                raise ValueError("Reviewed findings require evidence notes and a reviewer: " + key)
            for target in target_ids:
                labels[target] = dict(row, assessment=assessment)
    return labels


def pair_counts(findings, labels):
    """One domain/technique vote per site/run, across post-consent phases."""
    groups = defaultdict(list)
    for finding in findings:
        if finding["counts_as_candidate"] and finding["phase_group"] == "post":
            groups[finding.get("script_domain") or "(unknown)", finding["technique"]].append(finding)
    counts = Counter()
    for members in groups.values():
        assessments = [labels.get(f["finding_id"], {}).get("assessment") or "unreviewed" for f in members]
        if "supported_collection" in assessments:
            counts["supported_collection"] += 1
        elif all(value == "ordinary_usage" for value in assessments):
            counts["ordinary_usage"] += 1
        elif "unreviewed" in assessments:
            counts["unreviewed"] += 1
        else:
            counts["uncertain"] += 1
    unresolved = counts["uncertain"] + counts["unreviewed"]
    return dict(raw_pairs=len(groups), supported_pairs=counts["supported_collection"],
                ordinary_pairs=counts["ordinary_usage"], uncertain_pairs=counts["uncertain"],
                unreviewed_pairs=counts["unreviewed"], unresolved_pairs=unresolved,
                review_complete=counts["unreviewed"] == 0,
                decisive_complete=unresolved == 0)


def comparison(baseline, treatment, eligible=True):
    result = dict(raw_reduction=None, reviewed_reduction=None,
                  reviewed_reduction_low=None, reviewed_reduction_high=None)
    if not eligible:
        return result
    if baseline["raw_pairs"]:
        result["raw_reduction"] = 1 - treatment["raw_pairs"] / baseline["raw_pairs"]
    decisive = baseline.get("decisive_complete", baseline["review_complete"])
    treatment_decisive = treatment.get("decisive_complete", treatment["review_complete"])
    if decisive and treatment_decisive and baseline["supported_pairs"]:
        result["reviewed_reduction"] = 1 - treatment["supported_pairs"] / baseline["supported_pairs"]
    if baseline["review_complete"] and treatment["review_complete"]:
        baseline_low = baseline["supported_pairs"]
        baseline_high = baseline_low + baseline.get("uncertain_pairs", 0)
        treatment_low = treatment["supported_pairs"]
        treatment_high = treatment_low + treatment.get("uncertain_pairs", 0)
        if baseline_low:
            result["reviewed_reduction_low"] = 1 - treatment_high / baseline_low
        if baseline_high:
            result["reviewed_reduction_high"] = 1 - treatment_low / baseline_high
    return result


def aggregate_comparisons(rows):
    """Aggregate paired counts within each treatment without mixing eligibility sets."""
    result = []
    for run in sorted({row["run"] for row in rows}):
        eligible = [row for row in rows if row["run"] == run and row["eligible"]]
        totals = dict(
            raw_pairs=sum(row["raw_baseline"] for row in eligible),
            supported_pairs=sum(row["supported_baseline"] for row in eligible),
            uncertain_pairs=sum(row.get("uncertain_baseline", 0) for row in eligible),
            review_complete=all(not row.get("unreviewed_baseline", row["unresolved_baseline"]) for row in eligible),
            decisive_complete=all(not row["unresolved_baseline"] for row in eligible),
        )
        treatment = dict(
            raw_pairs=sum(row["raw_run"] for row in eligible),
            supported_pairs=sum(row["supported_run"] for row in eligible),
            uncertain_pairs=sum(row.get("uncertain_run", 0) for row in eligible),
            review_complete=all(not row.get("unreviewed_run", row["unresolved_run"]) for row in eligible),
            decisive_complete=all(not row["unresolved_run"] for row in eligible),
        )
        result.append(dict(
            run=run,
            eligible_sites=len(eligible),
            raw_baseline=totals["raw_pairs"],
            raw_run=treatment["raw_pairs"],
            supported_baseline=totals["supported_pairs"],
            supported_run=treatment["supported_pairs"],
            uncertain_baseline=totals["uncertain_pairs"],
            uncertain_run=treatment["uncertain_pairs"],
            unreviewed_baseline=sum(row.get("unreviewed_baseline", row["unresolved_baseline"]) for row in eligible),
            unreviewed_run=sum(row.get("unreviewed_run", row["unresolved_run"]) for row in eligible),
            unresolved_baseline=sum(row["unresolved_baseline"] for row in eligible),
            unresolved_run=sum(row["unresolved_run"] for row in eligible),
            **comparison(totals, treatment, bool(eligible)),
        ))
    return result


def eligibility(db, run, site):
    done = db.execute("SELECT ok,error FROM sites_done WHERE run=? AND site=?", (run, site)).fetchone()
    if not done or not done["ok"]:
        return "Execution failed or missing: " + (done["error"] or "unknown") if done else "Run not recorded"
    visits = [dict(row) for row in db.execute("SELECT * FROM visits WHERE run=? AND site=? ORDER BY id", (run, site))]
    problem = measurement_error(visits)
    if problem:
        return problem
    record = consent_record(next(v["consent"] for v in visits if v["phase"] == "after_consent"))
    expected = "confirmed" if run in ("reject_gpc", "accept") else "not_requested"
    if record.get("outcome") != expected:
        return "Consent outcome: " + str(record.get("outcome"))
    requested = {"reject_gpc": "reject", "accept": "accept"}.get(run, "none")
    if record.get("action") != requested:
        return "Consent action record: " + str(record.get("action"))
    if run == "reject_gpc" and record.get("gpc") is not True:
        return "GPC was not observed enabled"
    if run == "accept" and record.get("gpc") is True:
        return "GPC was unexpectedly enabled during acceptance"
    return ""


def export_requests(db, out):
    directory = out / "requests"
    directory.mkdir(exist_ok=True)
    has_payloads = db.execute("SELECT 1 FROM sqlite_master WHERE name='request_payloads'").fetchone()
    payloads = {row["request_id"]: dict(row) for row in db.execute("SELECT * FROM request_payloads")} if has_payloads else {}
    rows = []
    for row in db.execute("SELECT r.*,v.run,v.site,v.phase FROM requests r JOIN visits v ON v.id=r.visit_id ORDER BY r.id"):
        item = dict(row)
        payload = payloads.get(item["id"], {})
        body = payload.pop("body", b"") or b""
        filename = ""
        if body:
            filename = "requests/" + str(item["id"]) + ".bin"
            (out / filename).write_bytes(body)
        item.update(payload)
        item.update(payload_status=payload.get("status", "not_captured"), body_file=filename,
                    captured_sha256=hashlib.sha256(body).hexdigest() if body else "",
                    url_may_be_truncated=len(item["url"]) >= 2000)
        rows.append(item)
    fields = ["id", "run", "site", "phase", "url", "url_may_be_truncated", "method", "rtype", "frame_url", "failure", "ts",
              "payload_status", "content_type", "original_bytes", "captured_bytes", "sha256", "captured_sha256", "body_file", "detail"]
    write_csv(out / "requests.csv", fields, rows)
    return dict(Counter(row["payload_status"] for row in rows))


def verify_detector_lock(batch, manifest):
    lock = manifest.get("detector")
    if not lock:
        return {"status": "legacy_unlocked"}
    if lock.get("rule_version") != fingerprinting_module.RULE_VERSION:
        raise ValueError("Analysis rule version differs from the frozen detector")
    checked = {}
    for name, expected in lock.get("hashes", {}).items():
        frozen = batch / "code" / name
        if not frozen.is_file() or hashlib.sha256(frozen.read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen detector file differs from its manifest: " + name)
        checked[name] = expected
    active = Path(fingerprinting_module.__file__).resolve()
    expected = lock.get("hashes", {}).get("fingerprinting.py")
    if expected and hashlib.sha256(active.read_bytes()).hexdigest() != expected:
        raise ValueError("Active fingerprinting rules differ from the frozen detector")
    return {"status": "verified", "rule_version": lock["rule_version"], "hashes": checked}


def write_pair_snippets(out, pairs, radius=500):
    """Extract bounded, inert source context around recorded callsites for review."""
    destination = out / "pair-snippets"
    destination.mkdir(exist_ok=True)
    written = {}
    evidence_root = (out / "evidence").resolve()
    for pair in pairs:
        sections = []
        seen = set()
        for finding_id in pair["finding_ids"]:
            evidence = json.loads((out / "evidence/findings" / (finding_id + ".json")).read_text())
            files = evidence.get("source", {}).get("files", [])
            calls = evidence.get("supporting_calls", [])
            for relative in files:
                source = (evidence_root / relative).resolve()
                if not source.is_relative_to(evidence_root) or not source.is_file():
                    continue
                text = source.read_text(errors="replace")
                lines = text.splitlines() or [text]
                for call in calls:
                    metadata = (call.get("args") or {}).get("_tm") or {}
                    line = int(metadata.get("line") or 1)
                    column = int(metadata.get("column") or 1)
                    key = (source.name, line, column)
                    if key in seen or not 1 <= line <= len(lines):
                        continue
                    seen.add(key)
                    content = lines[line - 1]
                    start = max(0, column - 1 - radius)
                    end = min(len(content), column - 1 + radius)
                    excerpt = content[start:end]
                    sections.append(f"finding={finding_id} source={source.name} line={line} column={column}\n{excerpt}")
                    if len(sections) >= 12:
                        break
                if len(sections) >= 12:
                    break
            if len(sections) >= 12:
                break
        relative = "pair-snippets/" + pair["pair_id"] + ".txt"
        (out / relative).write_text("\n\n---\n\n".join(sections) + ("\n" if sections else ""))
        written[pair["pair_id"]] = relative
    return written


def report(batch, labels_path=None, review_dir='review'):
    batch = Path(batch)
    manifest = json.loads((batch / "experiment.json").read_text())
    detector_lock = verify_detector_lock(batch, manifest)
    dbpath = batch / "crawl.sqlite"
    if not dbpath.exists():
        raise ValueError("No crawl database exists yet")
    if not re.fullmatch(r'review(?:-[A-Za-z0-9_-]+)?', review_dir):
        raise ValueError('Review directory must be a review-prefixed directory name')
    out = batch / review_dir
    out.mkdir(exist_ok=True)
    protected = {out / "review_template.csv", out / "pair_review_template.csv", out / "items.csv",
                 out / "pairs.csv", out / "comparison.csv", out / "coverage.csv", out / "aggregate.csv"}
    if labels_path and Path(labels_path).resolve() in {p.resolve() for p in protected}:
        raise ValueError("Copy the template to a separate labels.csv before editing")
    calls, _ = read_measurements([dbpath])
    findings = [f for f in find_candidates(calls) if f["counts_as_candidate"] and f["phase_group"] == "post"]
    pairs = review_pairs(findings)
    labels = read_labels(labels_path, findings, pairs)
    export([dbpath], out / "evidence", [batch / "sources"])
    snippet_files = write_pair_snippets(out, pairs)
    by_run_site = defaultdict(list)
    for finding in findings:
        by_run_site[finding["run"], finding["site"]].append(finding)
    coverage = []
    exclusions_file = out / "exclusions.csv"
    previous_exclusions = batch / 'review/exclusions.csv'
    if review_dir != 'review' and not exclusions_file.exists() and previous_exclusions.exists():
        shutil.copyfile(previous_exclusions, exclusions_file)
    if not exclusions_file.exists():
        write_csv(exclusions_file, ["run", "site", "reason", "reviewer"], [])
    exclusions = {}
    with exclusions_file.open(newline="") as stream:
        for row in csv.DictReader(stream):
            key = row.get("run"), row.get("site")
            if key[0] not in manifest["conditions"] or key[1] not in manifest["sites"] or key in exclusions:
                raise ValueError("Unknown or duplicate manual exclusion: " + str(key))
            if not row.get("reason") or not row.get("reviewer"):
                raise ValueError("Manual exclusions require a reason and reviewer")
            exclusions[key] = row["reason"]
    with sqlite3.connect(dbpath) as db:
        db.row_factory = sqlite3.Row
        payload_statuses = export_requests(db, out)
        exfil_rows = find_exfil(db)
        write_csv(out / "value_exfil.csv", EXFIL_COLUMNS, exfil_rows)
        attribute_cards = export_cards(db, out, pairs, findings)
        for site in manifest["sites"]:
            for run in manifest["conditions"]:
                reason = eligibility(db, run, site)
                if manifest.get("status") != "complete":
                    reason = "Batch validity not confirmed: " + manifest.get("status", "unknown")
                if (run, site) in exclusions:
                    reason = "Manual page/evidence exclusion: " + exclusions[run, site]
                counts = pair_counts(by_run_site[run, site], labels)
                coverage.append(dict(site=site, run=run, eligible=not reason, exclusion=reason, **counts))
    items = []
    pair_ids = {(pair["run"], pair["site"], pair["script_domain"], pair["technique"]): pair["pair_id"]
                for pair in pairs}
    for finding in findings:
        label = labels.get(finding["finding_id"], {})
        kinds = {"canvas": {"canvas"}, "webgl": {"webgl_vendor", "webgl_renderer"}}.get(finding["technique"], set())
        hits = sum(row["run"] == finding["run"] and row["site"] == finding["site"]
                   and row["source_domain"] == (finding.get("script_domain") or "(inline/unknown)")
                   and row["kind"] in kinds for row in exfil_rows)
        items.append(dict(finding, pair_id=pair_ids[pair_key(finding)],
                          **{k: label.get(k, "") for k in LABEL_FIELDS[1:]},
                          evidence_file="evidence/findings/" + finding["finding_id"] + ".json",
                          request_index="requests.csv", value_exfil_hits=hits,
                          value_exfil_index="value_exfil.csv"))
    write_csv(out / "items.csv", ["finding_id", "pair_id", "run", "site", "phase", "script_url", "script_domain", "technique", "evidence_level",
                                 "assessment", "purpose", "evidence", "reviewer", "value_exfil_hits",
                                 "evidence_file", "request_index", "value_exfil_index"], items)
    pair_rows = []
    for pair in pairs:
        member_labels = [labels.get(finding_id, {}) for finding_id in pair["finding_ids"]]
        assessments = {row.get("assessment", "") for row in member_labels if row.get("assessment")}
        assessment = next(iter(assessments)) if len(assessments) == 1 else ("mixed" if assessments else "")
        purpose = next((row.get("purpose", "") for row in member_labels if row.get("purpose")), "")
        evidence = next((row.get("evidence", "") for row in member_labels if row.get("evidence")), "")
        reviewer = next((row.get("reviewer", "") for row in member_labels if row.get("reviewer")), "")
        pair_rows.append(dict(pair, finding_count=len(pair["finding_ids"]), assessment=assessment,
                              purpose=purpose, evidence=evidence, reviewer=reviewer,
                              finding_ids=";".join(pair["finding_ids"]), phases=";".join(pair["phases"]),
                              script_urls=";".join(pair["script_urls"]),
                              observed_facts="; ".join(pair["observed_facts"]),
                              snippet_file=snippet_files[pair["pair_id"]],
                              attribute_card=attribute_cards.get(pair['pair_id'], ''), evidence_directory="evidence/findings"))
    pair_fields = ["pair_id", "run", "site", "script_domain", "technique", "finding_count", "phases",
                   "script_urls", "observed_facts", "assessment", "purpose", "evidence", "reviewer",
                   "finding_ids", "snippet_file", "attribute_card", "evidence_directory"]
    write_csv(out / "pairs.csv", pair_fields, pair_rows)
    template = out / "review_template.csv"
    if not template.exists():
        write_csv(template, LABEL_FIELDS, [dict(finding_id=f["finding_id"], assessment="", purpose="", evidence="", reviewer="") for f in findings])
    pair_template = out / "pair_review_template.csv"
    if not pair_template.exists():
        write_csv(pair_template, ["pair_id", "assessment", "purpose", "evidence", "reviewer"],
                  [dict(pair_id=pair["pair_id"], assessment="", purpose="", evidence="", reviewer="") for pair in pairs])
    lookup = {(row["run"], row["site"]): row for row in coverage}
    comparisons = []
    for site in manifest["sites"]:
        baseline = lookup["baseline", site]
        for run in manifest["conditions"]:
            if run == "baseline":
                continue
            treatment = lookup[run, site]
            eligible = baseline["eligible"] and treatment["eligible"]
            baseline_keys = {(f.get('script_domain') or '(unknown)', f['technique']) for f in by_run_site['baseline', site]}
            treatment_keys = {(f.get('script_domain') or '(unknown)', f['technique']) for f in by_run_site[run, site]}
            comparisons.append(dict(site=site, run=run, eligible=eligible,
                exclusion=" | ".join(filter(None, [baseline["exclusion"], treatment["exclusion"]])),
                raw_baseline=baseline["raw_pairs"], raw_run=treatment["raw_pairs"],
                raw_pairs_surviving=len(baseline_keys & treatment_keys) if eligible else None,
                raw_pairs_disappearing=len(baseline_keys - treatment_keys) if eligible else None,
                raw_pairs_new=len(treatment_keys - baseline_keys) if eligible else None,
                raw_pair_survival=len(baseline_keys & treatment_keys)/len(baseline_keys) if eligible and baseline_keys else None,
                supported_baseline=baseline["supported_pairs"], supported_run=treatment["supported_pairs"],
                uncertain_baseline=baseline["uncertain_pairs"], uncertain_run=treatment["uncertain_pairs"],
                unreviewed_baseline=baseline["unreviewed_pairs"], unreviewed_run=treatment["unreviewed_pairs"],
                unresolved_baseline=baseline["unresolved_pairs"], unresolved_run=treatment["unresolved_pairs"],
                **comparison(baseline, treatment, eligible)))
    aggregates = aggregate_comparisons(comparisons)
    fully_eligible_sites = [site for site in manifest["sites"]
                            if all(lookup[run, site]["eligible"] for run in manifest["conditions"])]
    write_csv(out / "coverage.csv", list(coverage[0]), coverage)
    write_csv(out / "comparison.csv", list(comparisons[0]), comparisons)
    write_csv(out / "aggregate.csv", list(aggregates[0]), aggregates)
    capture_counts = Counter()
    match_ids, matched_pairs, origin_statuses = set(), 0, Counter()
    for path in attribute_cards.values():
        card = json.loads((out / path).read_text())
        capture_counts.update(card['capture_statuses'])
        matched_pairs += bool(card['structured_correspondences'])
        for hit in card['structured_correspondences']:
            match_ids.add(hit['request_id'])
            origin_statuses[hit.get('network_initiator', {}).get('status', 'not_captured')] += 1
    summary = dict(report_version=REPORT_VERSION, status=manifest["status"],
                   eligible_site_runs=sum(row["eligible"] for row in coverage),
                   fully_eligible_sites=fully_eligible_sites,
                   post_consent_candidate_findings=len(findings), candidate_pairs=len(pairs),
                   labels=dict(Counter(row["assessment"] or "unreviewed" for row in items)),
                   pair_labels=dict(Counter(row["assessment"] or "unreviewed" for row in pair_rows)),
                   attribute_evidence=dict(capture_statuses=dict(capture_counts), matched_pairs=matched_pairs,
                                           unique_matching_requests=len(match_ids), matching_request_ids=sorted(match_ids),
                                           initiator_statuses_per_match=dict(origin_statuses)),
                   request_capture_statuses=payload_statuses, value_exfil_hits=len(exfil_rows),
                   comparisons=comparisons, aggregates=aggregates,
                   labels_sha256=hashlib.sha256(Path(labels_path).read_bytes()).hexdigest() if labels_path else None,
                   detector_lock=detector_lock,
                   scope="Post-consent candidate domain/technique pairs among successfully measured paired sites. Supported collection is reviewer-assessed. Detection misses and server recognition are outside this score.")
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    title = manifest.get("title", "Paired evidence pilot")
    condition_notes = []
    if "baseline2" in manifest["conditions"]:
        condition_notes.append("baseline2 repeats baseline to expose normal variation")
    if "reject_gpc" in manifest["conditions"]:
        condition_notes.append("reject_gpc is confirmed rejection plus GPC")
    if "accept" in manifest["conditions"]:
        condition_notes.append("accept is a confirmed saved acceptance choice")
    if "ubol" in manifest["conditions"]:
        condition_notes.append("ubol is the extension's observed initialized behavior with no consent action")
    lines = ["# " + title, "", "Batch status: " + manifest["status"], "",
             "; ".join(condition_notes).capitalize() + ".", "",
             "Counts use unique (script domain, technique) pairs per site/run in after_consent, home_reload and subpage phases. Landing is retained as diagnostic evidence. Each pair with any supported-collection observation counts as supported. A pair is ordinary only when every observation has an ordinary-use label. Remaining pairs are unresolved.", "",
             "## Aggregate results", "",
             "| Condition | Eligible paired sites | Raw pairs baseline/run | Supported pairs baseline/run | Uncertain baseline/run | Unreviewed baseline/run | Raw reduction | Reviewed reduction/range |",
             "| --- | ---: | --- | --- | --- | --- | --- | --- |"]
    percent = lambda value: f"{value:.1%}" if value is not None else "pending/undefined"
    def reviewed(row):
        if row["reviewed_reduction"] is not None:
            return percent(row["reviewed_reduction"])
        low, high = row["reviewed_reduction_low"], row["reviewed_reduction_high"]
        return f"{percent(low)} to {percent(high)}" if low is not None or high is not None else "pending/undefined"
    for row in aggregates:
        lines.append(f"| {row['run']} | {row['eligible_sites']} | {row['raw_baseline']}/{row['raw_run']} | {row['supported_baseline']}/{row['supported_run']} | {row.get('uncertain_baseline', 0)}/{row.get('uncertain_run', 0)} | {row.get('unreviewed_baseline', 0)}/{row.get('unreviewed_run', 0)} | {percent(row['raw_reduction'])} | {reviewed(row)} |")
    lines += ["", "## Per-site results", "",
             "| Site | Condition | Eligible | Raw pairs baseline/run | Supported pairs baseline/run | Raw reduction | Reviewed reduction |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in comparisons:
        lines.append(f"| {row['site']} | {row['run']} | {row['eligible']} | {row['raw_baseline']}/{row['raw_run']} | {row['supported_baseline']}/{row['supported_run']} | {percent(row['raw_reduction'])} | {reviewed(row)} |")
    lines += ["", "Attribute evidence: pairs.csv links attribute_card JSON files for attribute-enumeration candidates. These show bounded primitive values, capture statuses, source locations and structured request-field correspondences. Matches require two distinct named attributes read before a request in the same visit and frame URL; multiple frames can share a URL. They do not prove that this script caused the request, that a server received it, or that collection serves tracking. No matches may reflect capture limits, renamed fields or transformations. No purpose labels are assigned automatically.", "",
              "A point reviewed reduction is available only when every flagged pair in both eligible arms has a decisive label and baseline has supported collection. When all pairs are reviewed but some remain uncertain, the report gives the possible reduction range: uncertain pairs are treated as profiling in the least/most favorable combinations. One-sided undefined bounds mean baseline evidence could be zero. Until review is complete, supported counts are lower bounds. Reductions compare counts, can be negative when new pairs appear, and are distinct from pair-survival metrics. See coverage.csv for uncertainty, unreviewed counts and exclusions.", "",
              "Review pairs.csv first; it groups repeated observations using exactly the (site, condition, script domain, technique) unit counted in the result. Its snippet_file gives bounded inert source context around recorded callsites. Open the finding IDs in items.csv and the linked evidence JSON/full source when the snippet is insufficient, then value_exfil.csv, requests.csv and requests/<id>.bin. value_exfil.csv records exact/raw/hash token matches for captured Canvas and WebGL values and is strong transmission evidence when linked carefully; no match is not evidence of no transmission. Request IDs refer to this database. Requests include attempts that failed or were blocked. A nearby request, a successful API call or a source-code keyword alone cannot establish data flow or purpose. Receipt by a server is not verified. Request URLs may be truncated to 2000 characters. Saved bodies are limited to 64 KiB/request and 2 MiB/site/run. Binary bodies and unavailable/limited bodies remain explicit.", "",
              "Inspect ../pages/ screenshots before interpreting reductions. HTTP-200 bot challenges and access walls are not detected automatically. Add unusable run/site rows to exclusions.csv with reason and reviewer, then regenerate this report. The reporter preserves those exclusions. Successful consent alone does not establish that an ordinary content page loaded.", "",
              "Copy pair_review_template.csv to labels.csv for the shorter, metric-aligned review (review_template.csv remains available for observation-level labels). Assessments are supported_collection, ordinary_usage or uncertain. For supported_collection, document the measured browser values, their combination/use as an identifier or profile, and supporting source/call/request evidence. Bot profiling can qualify as collection; record anti-bot purpose separately. Every reviewed row requires evidence notes and reviewer. A pair label applies only to one site and condition; identical script bytes do not automatically establish identical behavior across runs.", "",
              "Re-run evidence_pilot_report.py with --labels <path-to-labels.csv> to calculate the reviewed comparison. The reporter preserves the template and labels. Results describe only the selected validation sites and flagged cases; missing detections, opaque server behavior and automatic bot challenges limit interpretation."]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("batch")
    ap.add_argument("--labels")
    ap.add_argument('--review-dir', default='review', help='separate derived analysis directory; preserve earlier reports')
    args = ap.parse_args()
    result = report(args.batch, args.labels, args.review_dir)
    print("Report:", Path(args.batch) / args.review_dir / 'report.md')
    print("Review labels:", result["labels"])


if __name__ == "__main__":
    main()
