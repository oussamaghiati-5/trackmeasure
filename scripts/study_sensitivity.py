#!/usr/bin/env python3
"""Post-hoc sensitivity checks for recorded run incidents and degraded visits.

This script never changes primary eligibility. It quantifies whether the accept restart, the
Privacy Badger suspension outlier, and a conservative GPC visit-quality flag alter the headline
results. Output contains no cookie values or URLs.
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import purpose  # noqa: E402
import purpose_results as pr  # noqa: E402
from paired_uncertainty import summarize  # noqa: E402


RESULT = re.compile(r"\[accept\] \d+/\d+ ([^ ]+) (?:ok|FAIL)")
RESTART = re.compile(r"\[accept\] \d+ sites to crawl")


def accept_retry_sites(text):
    """Return unique sites attempted after the first accept run was restarted."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if RESTART.search(line)]
    if len(starts) < 2:
        return []
    sites = []
    for line in lines[starts[1] + 1:]:
        match = RESULT.search(line)
        if match and match.group(1) not in sites:
            sites.append(match.group(1))
    return sites


def medians(ps, sites, runs, metrics):
    keyed = ps.set_index(["run", "site"])
    return {metric: {run: float(pd.Series([keyed.loc[(run, site), metric]
                                           for site in sorted(sites)]).median())
                     for run in runs} for metric in metrics}


def pair_summary(det, sites, run, groups=None, col="group"):
    base = pr.pair_sets(det, "baseline", sites, groups, col)
    repeat = pr.pair_sets(det, "baseline2", sites, groups, col)
    condition = pr.pair_sets(det, run, sites, groups, col)
    rows = [dict(site=site, fp_pairs_baseline=len(base[site]),
                 fp_pairs_surviving=len(base[site] & condition[site]), tp_domains_run=0)
            for site in sorted(sites)]
    reference = {site: dict(site=site, fp_pairs_baseline=len(base[site]),
                            fp_pairs_surviving=len(base[site] & repeat[site]), tp_domains_run=0)
                 for site in sorted(sites)}
    estimate = summarize(rows, reference)
    site_differences = [len(base[site] & condition[site]) - len(base[site] & repeat[site])
                        for site in sites]
    positive = sum(value > 0 for value in site_differences)
    negative = sum(value < 0 for value in site_differences)
    discordant = positive + negative
    exact_sign_p = (min(1.0, 2 * sum(math.comb(discordant, k)
                                     for k in range(min(positive, negative) + 1)) /
                         (2 ** discordant)) if discordant else 1.0)
    return {
        "baseline_pairs": sum(len(base[site]) for site in sites),
        "surviving_pairs": sum(len(base[site] & condition[site]) for site in sites),
        "condition_only_pairs": sum(len(condition[site] - base[site]) for site in sites),
        "difference_pp_vs_repeat": estimate["difference_pp"],
        "ci95_pp": estimate["ci95_pp"],
        "site_sign_test": {"positive": positive, "negative": negative,
                           "p_two_sided": exact_sign_p},
    }


def identifier_counts(share, sites, run):
    post = share[(share.phase != "landing") & (share.flow != "third_to_first")]
    known = post[(post.receiver_known_tracker == 1) & post.site.isin(sites)]
    receivers = post[(post.run == run) & post.site.isin(sites)].groupby("site").receiver_domain.nunique() \
        .reindex(sorted(sites), fill_value=0)
    return {
        "sites_to_known_tracker": int(known[known.run == run].site.nunique()),
        "median_receivers": float(receivers.median()) if len(receivers) else None,
        "cookie_sync_sites": int(post[(post.run == run) & post.site.isin(sites) &
                                      (post.flow == "third_to_third")].site.nunique()),
    }


def audit(results):
    results = Path(results)
    ps, det, _ = pr.load(results)
    eligible_rows = ps[ps.comparison_eligible == 1]
    eligible = {run: set(eligible_rows[eligible_rows.run == run].site)
                for run in eligible_rows.run.unique()}
    share = pd.read_csv(results / "id_sharing.csv")

    retry = accept_retry_sites((results / "accept.log").read_text(encoding="utf-8")) \
        if (results / "accept.log").is_file() else []
    accept_sites = eligible["baseline"] & eligible["baseline2"] & eligible.get("accept", set())
    dual_sites = eligible.get("optout", set()) & eligible.get("accept", set())
    accept_kept, dual_kept = accept_sites - set(retry), dual_sites - set(retry)

    pbadger = ps[(ps.run == "pbadger") & (ps.measurement_ok == 1)]
    duration_median = float(pbadger.seconds.median())
    duration_outliers = set(pbadger[pbadger.seconds > 10 * duration_median].site)
    pbadger_common = eligible["baseline"] & eligible["baseline2"] & eligible.get("pbadger", set())

    keyed = ps.set_index(["run", "site"])
    degraded_gpc = set()
    for site in eligible.get("gpc", set()) & eligible["baseline"] & eligible["baseline2"]:
        req = keyed.loc[("gpc", site), "requests"]
        base_req = keyed.loc[("baseline", site), "requests"]
        repeat_req = keyed.loc[("baseline2", site), "requests"]
        domains = keyed.loc[("gpc", site), "tp_domains"]
        base_domains = keyed.loc[("baseline", site), "tp_domains"]
        repeat_domains = keyed.loc[("baseline2", site), "tp_domains"]
        if ((req < .25 * base_req and req < .25 * repeat_req) or
                (domains == 0 and base_domains > 0 and repeat_domains > 0)):
            degraded_gpc.add(site)
    gpc_common = eligible["baseline"] & eligible["baseline2"] & eligible.get("gpc", set())

    cookie_only_optout_outliers = set()
    optout_rows = ps[(ps.run == "optout") & (ps.comparison_eligible == 1) &
                     (ps.consent_outcome == "confirmed") &
                     (ps.consent_outcome_live == "unknown")]
    for row in optout_rows.itertuples():
        baseline_domains = keyed.loc[("baseline", row.site), "tp_domains"]
        repeat_domains = keyed.loc[("baseline2", row.site), "tp_domains"]
        if row.tp_domains > 2 * max(baseline_domains, repeat_domains):
            cookie_only_optout_outliers.add(row.site)

    def accept_block(sites):
        return {
            "sites": len(sites),
            "tracking": pair_summary(det, sites, "accept", {purpose.TRACKING}, "harm_class"),
            "tp_domain_delta_median_vs_repeat": float(pd.Series([
                keyed.loc[("accept", site), "tp_domains"] -
                keyed.loc[("baseline2", site), "tp_domains"] for site in sites]).median()),
            "identifiers_baseline": identifier_counts(share, sites, "baseline"),
            "identifiers_accept": identifier_counts(share, sites, "accept"),
        }

    def dual_block(sites):
        return {
            "sites": len(sites),
            "medians": medians(ps, sites, ["baseline", "baseline2", "optout", "accept"],
                               ["tp_domains", "tp_cookies_final"]),
            "tracking_under_optout": pair_summary(
                det, sites, "optout", {purpose.TRACKING}, "harm_class"),
        }

    def pbadger_deltas(sites):
        return {metric: float(pd.Series([
            keyed.loc[("pbadger", site), metric] - keyed.loc[("baseline2", site), metric]
            for site in sites]).median()) for metric in ("tp_domains", "tp_cookies_final", "requests")}

    def gpc_block(sites):
        return {
            "sites": len(sites),
            "all": pair_summary(det, sites, "gpc"),
            "service": pair_summary(det, sites, "gpc", {purpose.SERVICE}, "harm_class"),
            "tracking": pair_summary(det, sites, "gpc", {purpose.TRACKING}, "harm_class"),
        }

    return {
        "definitions": {
            "accept_restart": "All sites attempted after the first accept-run restart.",
            "duration_outlier": "Measurement-ok Privacy Badger visit over 10x its condition median.",
            "degraded_gpc": "Eligible GPC visit with requests under 25% of both baselines, or zero third-party domains when both baselines are non-zero.",
        },
        "accept_restart": {
            "retried_sites": sorted(retry),
            "retried_sites_n": len(retry),
            "verified_accept_sites_retried": len(accept_sites & set(retry)),
            "dual_verified_sites_retried": len(dual_sites & set(retry)),
            "primary": accept_block(accept_sites),
            "excluding_retried": accept_block(accept_kept),
            "dual_primary": dual_block(dual_sites),
            "dual_excluding_retried": dual_block(dual_kept),
        },
        "pbadger_duration": {
            "condition_median_seconds": duration_median,
            "outlier_sites": sorted(duration_outliers),
            "primary_delta_medians_vs_repeat": pbadger_deltas(pbadger_common),
            "excluding_outlier_delta_medians_vs_repeat": pbadger_deltas(pbadger_common - duration_outliers),
            "outlier_id_sharing_rows": int(len(share[(share.run == "pbadger") &
                                                       share.site.isin(duration_outliers)])),
        },
        "gpc_visit_quality": {
            "flagged_sites": sorted(degraded_gpc),
            "primary": gpc_block(gpc_common),
            "excluding_flagged": gpc_block(gpc_common - degraded_gpc),
        },
        "cookie_only_optout_outlier": {
            "definition": "Confirmed optout using cookie-only evidence, live state unknown, and third-party domains over 2x both baselines.",
            "flagged_sites": sorted(cookie_only_optout_outliers),
            "primary_dual": dual_block(dual_sites),
            "excluding_flagged_dual": dual_block(dual_sites - cookie_only_optout_outliers),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    output = audit(args.results)
    path = args.results / "sensitivity_audit.json"
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
