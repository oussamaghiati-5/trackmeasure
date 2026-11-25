#!/usr/bin/env python3
"""Re-analyse a batch by fingerprinting *purpose* (run scripts/label_purposes.py first).

    python scripts/purpose_results.py results/full_eu_20261122 \
        --sites public_results/full_eu_20261122/sites.csv

Writes to the results directory:
  purpose_results.json          headline numbers used in the paper
  purpose_prevalence.csv        baseline pairs and sites by purpose x technique x party
  purpose_survival.csv          blocker / consent survival by purpose group, with
                                site-bootstrap intervals against the repeat baseline
  purpose_consent_sites.csv     per-site fingerprinting by purpose after verified choices
  harm_survival.csv             the same survival analysis by harm class (Tracking = the
                                study's focus, with an upper bound; see purpose.harm_class)
  validation/annotation_sheet.csv, validation/annotation_key.csv
                                stratified 80-script manual-validation sample (private)

A pair is (site, script domain, purpose, technique), as in analyze.compare but
split by purpose; only post-consent phases and comparison-eligible sites count.
"""
import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import purpose  # noqa: E402
from paired_uncertainty import summarize  # noqa: E402
from purpose_agreement import cohen_kappa  # noqa: E402

GROUPS = {
    "Security": {purpose.SECURITY},
    "Ads/analytics": {purpose.ADS, purpose.ANALYTICS},
    "Other/unknown": {purpose.FUNCTIONAL, purpose.CDN, purpose.UNKNOWN},
}
KEY = ["site", "script_domain", "template"]


def group_of(p):
    return next(g for g, members in GROUPS.items() if p in members)


def wilson(k, n, z=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def fisher_two_sided(a, b, c, d):
    """Fisher's exact test for [[a, b], [c, d]]."""
    n, r1, c1 = a + b + c + d, a + b, a + c

    def p(x):
        return math.comb(c1, x) * math.comb(n - c1, r1 - x) / math.comb(n, r1)
    obs = p(a)
    lo, hi = max(0, r1 + c1 - n), min(r1, c1)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= obs * (1 + 1e-9)))


def load(results):
    r = Path(results)
    ps = pd.read_csv(r / "per_site.csv")
    fp = pd.read_csv(r / "fp_scripts.csv")
    lab = pd.read_csv(r / "script_purposes.csv").fillna({"vendor": "", "purpose_secondary": ""})
    fp["script_url"] = fp.script_url.fillna("")
    fp["template"] = fp.script_url.map(lambda u: purpose.path_template(u.split("?")[0].split("#")[0]))
    det = fp.merge(lab[KEY + ["purpose_primary", "party", "confidence", "vendor", "harm_class", "capability",
                              "sends_attributes", "cross_site"]], on=KEY, how="left")
    assert det.purpose_primary.notna().all(), "unlabelled detections: re-run label_purposes.py"
    det["group"] = det.purpose_primary.map(group_of)
    # Deterministic row order: pair-level de-duplication keeps the first row.
    det = det.sort_values(["run", "site", "phase_group", "script_domain", "technique", "script_url"],
                          kind="stable").reset_index(drop=True)
    return ps, det, lab


def pair_sets(det, run, sites, groups=None, col="group"):
    d = det[(det.run == run) & (det.phase_group == "post") & det.site.isin(sites)]
    if groups:
        d = d[d[col].isin(groups)]
    out = defaultdict(set)
    for r in d.itertuples():
        out[r.site].add((r.script_domain, r.purpose_primary, r.technique))
    return out


def survival_table(ps, det, views=None, col="group"):
    views = views or [("All", None)] + [(g, {g}) for g in GROUPS]
    elig = ps[ps.comparison_eligible == 1]
    sites_of = {run: set(elig[elig.run == run].site) for run in elig.run.unique()}
    rows, summary = [], {}
    # reject and gpc separate the two signals of optout (batches after 2026-11-22).
    for run in [r for r in ("ubol", "pbadger", "optout", "reject", "gpc", "accept") if r in sites_of]:
        common = sites_of["baseline"] & sites_of[run] & sites_of["baseline2"]
        for gname, groups in views:
            base = pair_sets(det, "baseline", common, groups, col)
            cond = pair_sets(det, run, common, groups, col)
            rep = pair_sets(det, "baseline2", common, groups, col)
            cond_rows = [dict(site=s, fp_pairs_baseline=len(base[s]), fp_pairs_surviving=len(base[s] & cond[s]),
                              tp_domains_run=0) for s in sorted(common)]
            ref = {s: dict(site=s, fp_pairs_baseline=len(base[s]), fp_pairs_surviving=len(base[s] & rep[s]),
                           tp_domains_run=0) for s in sorted(common)}
            res = summarize(cond_rows, ref)
            b = sum(len(base[s]) for s in common)
            rows.append(dict(
                run=run, group=gname, sites_with_pairs=res["sites"], pairs_baseline=b,
                pairs_surviving=sum(len(base[s] & cond[s]) for s in common),
                pairs_surviving_repeat=sum(len(base[s] & rep[s]) for s in common),
                pairs_new=sum(len(cond[s] - base[s]) for s in common),
                survival=res["survival"], repeat_survival=res["repeat_survival"],
                difference_pp=res["difference_pp"], ci95_pp=res["ci95_pp"]))
            summary.setdefault(run, {})[gname] = {k: rows[-1][k] for k in (
                "sites_with_pairs", "pairs_baseline", "pairs_surviving", "survival", "repeat_survival",
                "difference_pp", "ci95_pp", "pairs_new")}
    return pd.DataFrame(rows), summary


def prevalence(ps, det, sites_csv):
    elig = set(ps[(ps.run == "baseline") & (ps.comparison_eligible == 1)].site)
    b = det[(det.run == "baseline") & det.site.isin(elig)]
    pairs = b.drop_duplicates(["site", "script_domain", "purpose_primary", "technique"])
    table = (pairs.groupby(["purpose_primary", "party", "technique"]).size()
             .rename("pairs").reset_index())
    by_group_sites = {g: int(b[b.group == g].site.nunique()) for g in GROUPS}
    pre = b[b.phase_group == "pre"]
    strong = pairs.technique.isin(purpose.STRONG)
    out = {
        "usable_baseline_sites": len(elig),
        "sites_with_fp": int(b.site.nunique()),
        "sites_by_group": by_group_sites,
        "sites_only_security": int(b.groupby("site").group.agg(lambda g: set(g) == {"Security"}).sum()),
        "sites_with_ads_analytics_fp_before_consent": int(pre[pre.group == "Ads/analytics"].site.nunique()),
        "sites_with_security_fp_before_consent": int(pre[pre.group == "Security"].site.nunique()),
        "pairs_total": len(pairs),
        "pairs_by_purpose": pairs.purpose_primary.value_counts().to_dict(),
        "pairs_by_party": pairs.party.value_counts().to_dict(),
        "strong_pairs_by_group": pairs[strong].group.value_counts().to_dict(),
        "weak_pairs_by_group": pairs[~strong].group.value_counts().to_dict(),
        "labelled_share_of_pairs": round(float((pairs.purpose_primary != purpose.UNKNOWN).mean()), 4),
        "labelled_share_of_strong_pairs": round(float((pairs[strong].purpose_primary != purpose.UNKNOWN).mean()), 4),
        "confidence_of_labelled_pairs": pairs[pairs.purpose_primary != purpose.UNKNOWN].confidence.value_counts().to_dict(),
    }
    if sites_csv and Path(sites_csv).exists():
        cat = pd.read_csv(sites_csv).set_index("domain").category
        rows = {}
        for c in sorted(set(cat)):
            s = {x for x in elig if cat.get(x) == c}
            bc = b[b.site.isin(s)]
            rows[c] = {"usable": len(s), "with_fp": int(bc.site.nunique()),
                       "with_security_fp": int(bc[bc.group == "Security"].site.nunique()),
                       "with_ads_analytics_fp": int(bc[bc.group == "Ads/analytics"].site.nunique()),
                       "only_security": int(bc.groupby("site").group.agg(lambda g: set(g) == {"Security"}).sum())}
        out["by_category"] = rows
    return table, out


# Harm views (purpose.harm_class). "Tracking" is the study's focus; the upper bound
# adds dual-use scripts and scripts of unknown purpose with high tracking capability.
HARM_VIEWS = [
    ("Tracking", {purpose.TRACKING}),
    ("Tracking, upper bound", {purpose.TRACKING, purpose.DUAL_USE, purpose.UNDETERMINED_HIGH}),
    ("Service", {purpose.SERVICE}),
    ("Undetermined", {purpose.UNDETERMINED_HIGH, purpose.UNDETERMINED_LOW}),
]


HARM_RANK = (purpose.TRACKING, purpose.DUAL_USE, purpose.UNDETERMINED_HIGH, purpose.SERVICE,
             purpose.UNDETERMINED_LOW)


def harm(ps, det, lab):
    """Prevalence and verified-consent results for harmful (tracking) fingerprinting."""
    ok = ps[ps.comparison_eligible == 1]
    elig = set(ok[ok.run == "baseline"].site)
    b = det[(det.run == "baseline") & det.site.isin(elig)]
    # Two scripts can give the same pair with different capability (an Unknown script
    # at High and at Low): the pair takes the higher-risk class, so it never depends
    # on row order and the upper bound stays an upper bound.
    pairs = (b.assign(_rank=b.harm_class.map({c: i for i, c in enumerate(HARM_RANK)}))
             .sort_values("_rank", kind="stable")
             .drop_duplicates(["site", "script_domain", "purpose_primary", "technique"]))
    out = {"definition": "Tracking = purpose Advertising/Analytics (not dual-use). Upper bound adds "
                         "Dual-use and Undetermined scripts with High capability.",
           "pairs_by_class": pairs.harm_class.value_counts().to_dict(),
           "sites_by_class": {c: int(b[b.harm_class == c].site.nunique()) for c in purpose.HARM_CLASSES},
           "strong_pairs_by_class": pairs[pairs.technique.isin(purpose.STRONG)].harm_class.value_counts().to_dict()}
    for name, classes in HARM_VIEWS[:2]:
        bv = b[b.harm_class.isin(classes)]
        pre = bv[bv.phase_group == "pre"]
        out[name] = {
            "sites": int(bv.site.nunique()),
            "pairs": int(len(pairs[pairs.harm_class.isin(classes)])),
            "strong_pairs": int(len(pairs[pairs.harm_class.isin(classes) & pairs.technique.isin(purpose.STRONG)])),
            "sites_before_consent": int(pre.site.nunique()),
            "sites_before_consent_high_capability": int(pre[pre.capability == "High"].site.nunique()),
        }
    tl = lab[lab.harm_class == purpose.TRACKING]
    out["tracking_scripts"] = {
        "scripts": len(tl), "sites": int(tl.site.nunique()),
        "capability": tl.capability.value_counts().to_dict(),
        "cross_site": int(tl.cross_site.sum()), "sends_attributes": int(tl.sends_attributes.sum()),
        "writes_storage": int(tl.writes_storage.sum()),
        "vendors": tl.vendor.replace("", pd.NA).fillna(tl.script_domain).value_counts().to_dict(),
        "attribute_senders": sorted({f"{v or d} ({s})" for v, d, s in zip(
            tl.vendor, tl.script_domain, tl.site) if tl.loc[(tl.vendor == v) & (tl.site == s), "sends_attributes"].any()})}
    rej = set(ok[(ok.run == "optout") & (ok.consent_outcome == "confirmed")].site)
    acc = set(ok[(ok.run == "accept") & (ok.consent_outcome == "confirmed")].site)
    both = rej & acc
    for name, classes in HARM_VIEWS[:2]:
        r = det[(det.run == "optout") & (det.phase_group == "post") & det.site.isin(rej) & det.harm_class.isin(classes)]
        a = det[(det.run == "accept") & (det.phase_group == "post") & det.site.isin(both) & det.harm_class.isin(classes)]
        rb = r[r.site.isin(both)]
        key = ["site", "script_domain", "purpose_primary", "technique"]
        out[name]["after_verified_reject"] = {
            "sites": int(r.site.nunique()), "of_verified_reject_sites": len(rej),
            "sites_high_capability": int(r[r.capability == "High"].site.nunique()),
            "sites_strong_technique": int(r[r.technique.isin(purpose.STRONG)].site.nunique()),
            "vendors": sorted(set(r.vendor.replace("", pd.NA).fillna(r.script_domain)))}
        out[name]["dual_verified"] = {"sites": len(both),
                                      "pairs_accept": int(len(a.drop_duplicates(key))),
                                      "pairs_reject": int(len(rb.drop_duplicates(key))),
                                      "sites_accept": int(a.site.nunique()), "sites_reject": int(rb.site.nunique())}
    return out


def consent(ps, det):
    ok = ps[ps.comparison_eligible == 1]
    rej = set(ok[(ok.run == "optout") & (ok.consent_outcome == "confirmed")].site)
    acc = set(ok[(ok.run == "accept") & (ok.consent_outcome == "confirmed")].site)
    both = sorted(rej & acc)
    rows = []
    for run, sites in (("optout", rej), ("accept", acc)):
        d = det[(det.run == run) & (det.phase_group == "post") & det.site.isin(sites)]
        for site in sorted(sites):
            ds = d[d.site == site]
            row = dict(run=run, site=site, dual_verified=int(site in both))
            for g in GROUPS:
                dg = ds[ds.group == g]
                row[f"pairs_{g}"] = len(dg.drop_duplicates(["script_domain", "purpose_primary", "technique"]))
                row[f"vendors_{g}"] = ";".join(sorted(set(dg.vendor.replace("", pd.NA).dropna())
                                                      | set(dg[dg.vendor == ""].script_domain)))
            rows.append(row)
    per = pd.DataFrame(rows)
    w = ok.pivot_table(index="site", columns="run", values=["tp_domains", "tp_cookies_final"])

    def med(col, run, sites):
        return float(w[col].loc[sites, run].median()) if sites else None
    out = {"verified_reject_sites": len(rej), "verified_accept_sites": len(acc), "dual_verified_sites": len(both),
           "reject_source": ok[(ok.run == "optout") & (ok.consent_outcome == "confirmed")].consent_reason.value_counts().to_dict(),
           "accept_source": ok[(ok.run == "accept") & (ok.consent_outcome == "confirmed")].consent_reason.value_counts().to_dict()}
    r = per[per.run == "optout"]
    for g in GROUPS:
        out[f"after_reject_sites_with_{g}"] = int((r[f"pairs_{g}"] > 0).sum())
    out["after_reject_sites_with_any_fp"] = int((r[[f"pairs_{g}" for g in GROUPS]].sum(axis=1) > 0).sum())
    out["after_reject_ads_analytics_sites"] = sorted(r[r["pairs_Ads/analytics"] > 0].site)
    out["after_reject_ads_analytics_vendors"] = sorted({v for x in r["vendors_Ads/analytics"] for v in x.split(";") if v})
    pd_both = per[per.dual_verified == 1]
    for g in GROUPS:
        a = pd_both[pd_both.run == "accept"].set_index("site")[f"pairs_{g}"]
        o = pd_both[pd_both.run == "optout"].set_index("site")[f"pairs_{g}"]
        out[f"dual_pairs_{g}"] = {"accept": int(a.sum()), "reject": int(o.sum()),
                                  "sites_accept": int((a > 0).sum()), "sites_reject": int((o > 0).sum())}
    out["dual_tp_domains_median"] = {"accept": med("tp_domains", "accept", both), "reject": med("tp_domains", "optout", both)}
    out["dual_tp_cookies_median"] = {"accept": med("tp_cookies_final", "accept", both),
                                     "reject": med("tp_cookies_final", "optout", both)}
    if both:
        diff = (w["tp_domains"].loc[both, "accept"] - w["tp_domains"].loc[both, "optout"])
        out["dual_tp_domains_accept_gt_reject_sites"] = int((diff > 0).sum())
    return per, out


def validity(lab, radar):
    out = {}
    # (a) Agreement between two independent label sources on third-party domains:
    #     our vendor signatures vs Tracker Radar's categories (non-CDN purposes only).
    tr = radar.get("tracker_radar", {})
    pairs = {}
    for r in lab[(lab.third_party == 1) & (lab.same_owner == 0)].itertuples():
        sig = purpose.match_signature(r.example_url)
        trp = [p for p in purpose.tr_purposes((tr.get(r.script_domain) or {}).get("categories")) if p != purpose.CDN]
        if sig and trp:
            pairs[r.script_domain] = (sig[1], trp[0])
    k, n = cohen_kappa([a for a, _ in pairs.values()], [b for _, b in pairs.values()])
    out["signature_vs_tracker_radar"] = {
        "domains": n, "kappa": None if k is None else round(k, 3),
        "raw_agreement": round(sum(a == b for a, b in pairs.values()) / n, 3) if n else None,
        "disagreements": {d: {"signature": a, "tracker_radar": b} for d, (a, b) in pairs.items() if a != b}}
    out["tracker_radar_coverage"] = {
        "third_party_script_domains": int(lab[lab.third_party == 1].script_domain.nunique()),
        "found": int(lab[(lab.third_party == 1) & (lab.tr_found == 1)].script_domain.nunique()),
        "with_purpose_category": int(lab[(lab.third_party == 1) & lab.tr_categories.fillna("").map(
            lambda c: bool([p for p in purpose.tr_purposes(c.split("|")) if p != purpose.CDN]))].script_domain.nunique())}
    # (b) Convergent validity: automation probes by purpose, using only labels that
    #     did not themselves use behaviour or page-context evidence.
    ind = lab[lab.label_source == "independent"]
    probe = {}
    for g in GROUPS:
        sub = ind[ind.purpose_primary.map(group_of) == g]
        k_, n_ = int(sub.reads_webdriver.sum()), len(sub)
        probe[g] = {"scripts": n_, "read_navigator_webdriver": k_,
                    "rate": round(k_ / n_, 3) if n_ else None, "ci95": wilson(k_, n_)}
    sec, ads = probe["Security"], probe["Ads/analytics"]
    probe["fisher_p_security_vs_ads"] = fisher_two_sided(
        sec["read_navigator_webdriver"], sec["scripts"] - sec["read_navigator_webdriver"],
        ads["read_navigator_webdriver"], ads["scripts"] - ads["read_navigator_webdriver"])
    out["webdriver_probe_by_group"] = probe
    strong = {}
    for g in GROUPS:
        sub = ind[ind.purpose_primary.map(group_of) == g]
        has = sub.techniques.map(lambda t: bool(set(t.split(",")) & purpose.STRONG)).astype(bool)
        strong[g] = {"scripts": len(sub), "with_strong_technique": int(has.sum()),
                     "rate": round(float(has.mean()), 3) if len(sub) else None}
    out["strong_technique_by_group"] = strong
    trs = lab[lab.tr_found == 1].drop_duplicates("script_domain")
    out["tracker_radar_fp_score_by_purpose"] = {
        p: round(float(g.tr_fingerprinting.mean()), 2) for p, g in trs.groupby("purpose_primary")}
    return out


def annotation_sample(lab, outdir, n=80, seed=7):
    """Stratified by strongest technique x party, proportional with >= 1 per stratum."""
    rng = random.Random(seed)
    lab = lab.copy()
    lab["stratum"] = lab.strongest + " | " + lab.party
    sizes = lab.stratum.value_counts()
    alloc = {s: 1 for s in sizes.index}
    rest = n - len(alloc)
    share = {s: (c - 1) for s, c in sizes.items()}
    total = sum(share.values())
    for s in sizes.index:
        alloc[s] += int(rest * share[s] / total) if total else 0
    for s in sorted(sizes.index, key=lambda s: -(rest * share[s] / total - int(rest * share[s] / total))):
        if sum(alloc.values()) >= n:
            break
        if alloc[s] < sizes[s]:
            alloc[s] += 1
    picks = []
    for s, k in alloc.items():
        idx = sorted(lab[lab.stratum == s].index)
        picks += rng.sample(idx, min(k, len(idx)))
    sample = lab.loc[sorted(picks)].reset_index(drop=True)
    sample.insert(0, "sample_id", [f"S{i + 1:03d}" for i in range(len(sample))])
    sheet = sample[["sample_id", "site", "script_domain", "example_url", "techniques", "page_context",
                    "runs", "stratum"]].copy()
    for c in ("a1_is_fp", "a1_purpose", "a1_notes", "a2_is_fp", "a2_purpose", "a2_notes",
              "adjudicated_is_fp", "adjudicated_purpose"):
        sheet[c] = ""
    key = sample[["sample_id", "purpose_primary", "purpose_secondary", "party", "confidence", "evidence", "vendor"]]
    vdir = Path(outdir) / "validation"
    vdir.mkdir(exist_ok=True)
    sheet.to_csv(vdir / "annotation_sheet.csv", index=False)
    key.to_csv(vdir / "annotation_key.csv", index=False)
    return {"n": len(sample), "strata": {s: int(k) for s, k in sample.stratum.value_counts().items()},
            "rule_labels_in_sample": sample.purpose_primary.value_counts().to_dict()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results")
    ap.add_argument("--sites", help="sites.csv with domain,category")
    ap.add_argument("--radar", default=str(ROOT / "reference" / "tracker_radar_subset.json"))
    a = ap.parse_args()
    out = Path(a.results)
    ps, det, lab = load(a.results)
    radar = json.loads(Path(a.radar).read_text(encoding="utf-8"))

    table, prev = prevalence(ps, det, a.sites)
    surv, surv_summary = survival_table(ps, det)
    hsurv, hsurv_summary = survival_table(ps, det, HARM_VIEWS, col="harm_class")
    harm_res = harm(ps, det, lab)
    harm_res["survival"] = hsurv_summary
    per, cons = consent(ps, det)
    val = validity(lab, radar)
    sample = annotation_sample(lab, out)
    clusters = pd.read_csv(out / "hidden_vendor_clusters.csv")
    hidden = lab[lab.party == "Hidden first-party vendor"]
    res = {
        "prevalence": prev,
        "survival": surv_summary,
        "harm": harm_res,
        "consent": cons,
        "validity": val,
        "hidden_first_party_vendors": {
            "scripts": len(hidden), "sites": int(hidden.site.nunique()),
            "vendors": hidden.vendor.replace("", "unattributed").value_counts().to_dict(),
            "clusters": clusters[["template", "n_sites", "vendor", "purpose"]].to_dict("records")},
        "labels": {"scripts": len(lab), "by_purpose": lab.purpose_primary.value_counts().to_dict(),
                   "by_confidence": lab.confidence.value_counts().to_dict(),
                   "by_party": lab.party.value_counts().to_dict()},
        "annotation_sample": sample,
    }
    table.to_csv(out / "purpose_prevalence.csv", index=False)
    surv.to_csv(out / "purpose_survival.csv", index=False)
    hsurv.to_csv(out / "harm_survival.csv", index=False)
    per.to_csv(out / "purpose_consent_sites.csv", index=False)
    (out / "purpose_results.json").write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
