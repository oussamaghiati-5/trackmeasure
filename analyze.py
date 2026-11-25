#!/usr/bin/env python3
"""Per-site API metrics, rule candidates and run-vs-baseline comparison.

  python analyze.py data/crawl.sqlite --baseline baseline --outdir results/

Rules are implemented in fingerprinting.py; API use is not proof of tracking.
New Canvas traces require successful draw-before-read on the same document,
canvas and script. Legacy records are explicitly unlinked.
  canvas        >=10 distinct characters, then non-lossy image read >=16x16
  canvas_font   measureText with >=50 distinct fonts
  webrtc        createDataChannel + createOffer
  audio         createOscillator + createDynamicsCompressor + a readback
  webgl         getParameter(UNMASKED_VENDOR/RENDERER_WEBGL)  [extra, not in E&N]
  attr_enum     >=12 distinct navigator/screen attributes read  [weak signal]

Also written: fp_exfil.csv (canvas/WebGL values found in later requests), id_sharing.csv
(identifier-like cookie values sent to another registrable domain) and behavior.csv
(scripts registering input/motion listeners or Sensor APIs). See exfil.py.
"""
import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd

import exfil as exfil_mod
from navigation import measurement_error
from consent import consent_record
from fingerprinting import arguments, find_candidates, legacy_techniques, dataset_identity

PRE_PHASES = {"landing"}
POST_PHASES = {"after_consent", "home_reload", "subpage"}
TECHNIQUES = ["canvas", "canvas_font", "webrtc", "audio", "webgl", "attr_enum"]
COMPARISON_COLUMNS = [
    "run", "site", "fp_pairs_baseline", "fp_pairs_surviving", "fp_pairs_new", "survival",
    "tp_domains_baseline", "tp_domains_run", "fp_calls_baseline", "fp_calls_run",
    "blocked_requests", "consent_method", "consent_action", "consent_outcome", "gpc",
    "exfil_pairs_baseline", "exfil_pairs_surviving", "exfil_survival",
]
BEHAVIOR_FAMILY = {
    "mousemove": "pointer", "mousedown": "pointer", "mouseup": "pointer", "pointermove": "pointer",
    "pointerdown": "pointer", "keydown": "keyboard", "keyup": "keyboard", "keypress": "keyboard",
    "touchstart": "touch", "touchmove": "touch", "touchend": "touch", "wheel": "scroll", "scroll": "scroll",
    "devicemotion": "motion", "deviceorientation": "motion",
}
BEHAVIOR_COLUMNS = ["run", "site", "phase_group", "script_domain", "third_party", "families", "n_families", "events"]
ANIMATION = ("Canvas2D.save", "Canvas2D.restore", "Canvas.addEventListener")


def category(api):
    if api.startswith(("Storage.", "IndexedDB.", "Document.cookie")):
        return "storage"
    if api == "Navigator.sendBeacon":
        return "beacon"
    if api.startswith(("Event.", "Sensor.")):
        return "behavior"
    if api.startswith("Instrumentation."):
        return "metadata"
    if api.startswith(("Event.", "Sensor.")):
        return "behavior"
    if api in ("Canvas.getContext", "Canvas.width.set", "Canvas.height.set", "Canvas2D.clearRect",
               "Canvas2D.reset", *ANIMATION):
        return "other"
    return "fingerprinting"


def behavior_family(api):
    if api.startswith("Sensor."):
        return "motion"
    return BEHAVIOR_FAMILY.get(api[len("Event."):]) if api.startswith("Event.") else None


def techniques(calls):
    """Compatibility entry point for old tuple-only traces."""
    return legacy_techniques(calls)


def load(dbs):
    frames = {k: [] for k in ("visits", "requests", "api", "cookies", "done")}
    seen = set()
    for path in dbs:
        con = sqlite3.connect(path)
        keys = set(con.execute("SELECT DISTINCT run,site FROM visits"))
        if seen & keys:
            con.close()
            raise ValueError("Duplicate run/site measurements across databases; analyze batches separately: " + str(sorted(seen & keys)))
        seen.update(keys)
        identity = dataset_identity(con, path)
        frames["visits"].append(pd.read_sql("SELECT * FROM visits", con))
        frames["requests"].append(pd.read_sql(
            "SELECT r.visit_id, r.domain, r.third_party, r.failure, v.run, v.site, v.phase "
            "FROM requests r JOIN visits v ON v.id=r.visit_id", con))
        frames["api"].append(pd.read_sql(
            "SELECT a.rowid AS call_id, a.ts, a.visit_id, a.frame_url, a.script_url, a.script_domain, a.third_party, a.api, a.args, "
            "v.run, v.site, v.phase FROM api_calls a JOIN visits v ON v.id=a.visit_id", con))
        frames["cookies"].append(pd.read_sql("SELECT * FROM cookies", con))
        frames["done"].append(pd.read_sql("SELECT * FROM sites_done", con))
        for group in frames.values():
            group[-1]["database_id"] = identity
        con.close()
    return {k: pd.concat(v, ignore_index=True) if v else pd.DataFrame() for k, v in frames.items()}


def detect(api):
    """Candidate patterns; raw calls and weak signals are not proven tracking."""
    fields = ["run", "site", "phase_group", "script_domain", "script_url", "third_party", "technique",
              "finding_id", "rule_version", "evidence_level", "document_id", "object_id", "reason"]
    found = find_candidates(api.to_dict("records"))
    return pd.DataFrame([{k: f.get(k) for k in fields} for f in found if f["counts_as_candidate"]], columns=fields)


def behavior(api):
    """Scripts registering behavioural (input/motion) listeners or Sensor APIs."""
    if not len(api):
        return pd.DataFrame(columns=BEHAVIOR_COLUMNS)
    b = api.assign(family=api["api"].map(behavior_family)).dropna(subset=["family"])
    if not len(b):
        return pd.DataFrame(columns=BEHAVIOR_COLUMNS)
    b = b.assign(phase_group=b["phase"].map(lambda p: "pre" if p in PRE_PHASES else "post"),
                 script_domain=b["script_domain"].replace("", "(inline/unknown)").fillna("(inline/unknown)"))
    out = b.groupby(["run", "site", "phase_group", "script_domain", "third_party"]).agg(
        families=("family", lambda s: ",".join(sorted(set(s)))), n_families=("family", "nunique"),
        events=("api", lambda s: ",".join(sorted({a.split(".", 1)[1] for a in s})))).reset_index()
    return out[BEHAVIOR_COLUMNS]


def per_site(d, det, exfil=None, sharing=None, beh=None):
    req, api, ck = d["requests"], d["api"], d["cookies"]
    api = api.assign(cat=api["api"].map(category))
    g = ["run", "site"]
    out = d["done"][["run", "site", "ok", "error", "seconds"]].set_index(g)
    argd = api.get("args", pd.Series(index=api.index, dtype=object)).map(arguments)
    capped = argd.map(lambda a: a.get("capped") is True).astype(bool)
    trace_meta = argd.map(lambda a: a.get("_tm") if isinstance(a.get("_tm"), dict) else {})
    out["trace_cap_markers"] = api[capped].groupby(g).size()
    out["failed_api_operations"] = api[trace_meta.map(lambda m: m.get("success") is False).astype(bool)].groupby(g).size()
    out["async_api_requests"] = api[trace_meta.map(lambda m: m.get("status") == "async_requested").astype(bool)].groupby(g).size()
    api = api[~capped]  # cap notices are metadata, not additional detailed calls
    problems = {}
    for key, visits in d["visits"].groupby(g, sort=False):
        # SQL NULLs in numeric columns become NaN in pandas. Restore None for
        # the shared validity check, especially for unfinished visits.
        records = visits.astype(object).where(visits.notna(), None).to_dict("records")
        problems[key] = measurement_error(records)
    out["measurement_error"] = [problems.get(key, "No visits recorded") for key in out.index]
    out["measurement_ok"] = out["measurement_error"].isna().astype(int)
    out["requests"] = req.groupby(g).size()
    blocked = req.failure.fillna("").str.contains("BLOCKED_BY_CLIENT")
    tp = req[(req.third_party == 1) & ~blocked]  # contacted, i.e. not blocked by the extension
    out["tp_requests"] = tp.groupby(g).size()
    out["tp_domains"] = tp.groupby(g)["domain"].nunique()
    out["blocked_requests"] = req[blocked].groupby(g).size()
    out["fp_calls"] = api[api.cat == "fingerprinting"].groupby(g).size()
    # Old fp_* columns remain compatibility aliases, not tracking verdicts.
    out["monitored_api_calls"] = api[api.cat != "metadata"].groupby(g).size()
    out["nonstorage_api_calls"] = api[~api.cat.isin(["metadata", "storage"])].groupby(g).size()
    out["fp_apis_distinct"] = api[api.cat == "fingerprinting"].groupby(g)["api"].nunique()
    out["tp_fp_calls"] = api[(api.cat == "fingerprinting") & (api.third_party == 1)].groupby(g).size()
    out["storage_ops"] = api[api.cat == "storage"].groupby(g).size()
    out["cookie_writes_js"] = api[api.api == "Document.cookie.set"].groupby(g).size()
    out["cookies_final"] = ck.groupby(g).size()
    out["tp_cookies_final"] = ck[ck.third_party == 1].groupby(g).size()
    out["fp_scripts"] = det.groupby(g)["script_url"].nunique() if len(det) else 0
    out["candidate_scripts"] = out["fp_scripts"]
    out["candidate_findings"] = det.groupby(g).size() if len(det) else 0
    levels = det.get("evidence_level", pd.Series("legacy_unlinked", index=det.index))
    out["weak_signals"] = det[levels == "weak_signal"].groupby(g).size() if len(det) else 0
    out["legacy_unlinked_findings"] = det[levels == "legacy_unlinked"].groupby(g).size() if len(det) else 0
    for t in TECHNIQUES:
        sub = det[det.technique == t] if len(det) else det
        out[t] = sub.groupby(g)["script_domain"].nunique() if len(sub) else 0
    pre = det[det.phase_group == "pre"] if len(det) else det
    out["fp_scripts_pre_consent"] = pre.groupby(g)["script_url"].nunique() if len(pre) else 0
    ex = exfil if exfil is not None and len(exfil) else None
    out["fp_exfil_receivers"] = ex.groupby(g)["receiver_domain"].nunique() if ex is not None else 0
    out["fp_exfil_tp_receivers"] = (ex[ex.receiver_third_party == 1].groupby(g)["receiver_domain"].nunique()
                                    if ex is not None else 0)
    sh = sharing if sharing is not None and len(sharing) else None
    out["id_sharing_receivers"] = sh.groupby(g)["receiver_domain"].nunique() if sh is not None else 0
    bh = beh if beh is not None and len(beh) else None
    if bh is not None:
        tpb = bh[bh.third_party == 1]
        per_domain = tpb.groupby(g + ["script_domain"])["families"].agg(
            lambda s: len(set(",".join(s).split(","))))
        out["behavior_tp_domains"] = tpb.groupby(g)["script_domain"].nunique()
        out["behavior_tp_multi_family"] = (per_domain >= 2).groupby(level=g).sum()
    else:
        out["behavior_tp_domains"] = 0
        out["behavior_tp_multi_family"] = 0
    cons = d["visits"][d["visits"].phase == "after_consent"].set_index(g)["consent"]
    records = cons.map(consent_record).reindex(out.index).map(consent_record)
    for field in ("method", "action", "outcome", "reason"):
        out["consent_" + field] = records.map(lambda r: r.get(field))
    out["consent_obstacle"] = records.map(lambda r: r.get("obstacle") or "not_observed")
    out["consent_cmp_signals"] = records.map(lambda r: ",".join(r.get("cmp_signals") or []))
    out["consent_scope"] = records.map(lambda r: r.get("scope") or "legacy_unknown")
    out["consent_reason_code"] = records.map(lambda r: r.get("reason_code") or "legacy_unknown")
    out["consent_pay_or_ok"] = records.map(lambda r: int(r.get("pay_or_ok") is True))
    out["gpc"] = records.map(lambda r: {True: "enabled", False: "disabled"}.get(r.get("gpc"), "unknown"))
    out["comparison_eligible"] = ((out["ok"] == 1) & (out["measurement_ok"] == 1)
                                  & out["consent_outcome"].isin(["confirmed", "not_requested"])).astype(int)
    out["tcf_purposes"] = records.map(lambda r: r.get("tcf")).map(
        lambda t: ",".join(map(str, t.get("purposesConsented", []))) if isinstance(t, dict) else t)
    num = [c for c in out.columns if c not in (
        "ok", "error", "seconds", "measurement_error", "consent_method", "consent_action",
        "consent_outcome", "consent_reason", "consent_obstacle", "consent_cmp_signals", "consent_scope",
        "consent_reason_code", "gpc", "tcf_purposes")]
    out[num] = out[num].fillna(0).astype(int)
    return out.reset_index()


def compare(ps, det, baseline, exfil=None):
    """Survival of (script eTLD+1, technique) pairs vs. the baseline, post-consent phases.

    exfil_survival does the same for (receiver eTLD+1, value kind) pairs: fingerprint
    values that actually left the browser."""
    key = lambda df: set(zip(df.script_domain, df.technique))
    post = det[det.phase_group == "post"] if len(det) else det
    ex = exfil[exfil.phase != "landing"] if exfil is not None and len(exfil) else None

    def ex_key(run, site):
        if ex is None:
            return set()
        sub = ex[(ex.run == run) & (ex.site == site)]
        return set(zip(sub.receiver_domain, sub.kind))
    ok = ps[ps.comparison_eligible == 1]
    rows = []
    for run in sorted(set(ok.run) - {baseline}):
        both = set(ok[ok.run == baseline].site) & set(ok[ok.run == run].site)
        for site in sorted(both):
            b = key(post[(post.run == baseline) & (post.site == site)]) if len(post) else set()
            r = key(post[(post.run == run) & (post.site == site)]) if len(post) else set()
            pb = ps[(ps.run == baseline) & (ps.site == site)].iloc[0]
            pr = ps[(ps.run == run) & (ps.site == site)].iloc[0]
            eb, er = ex_key(baseline, site), ex_key(run, site)
            rows.append(dict(run=run, site=site, fp_pairs_baseline=len(b), fp_pairs_surviving=len(b & r),
                             exfil_pairs_baseline=len(eb), exfil_pairs_surviving=len(eb & er),
                             exfil_survival=(len(eb & er) / len(eb)) if eb else None,
                             fp_pairs_new=len(r - b),
                             survival=(len(b & r) / len(b)) if b else None,
                             tp_domains_baseline=pb.tp_domains, tp_domains_run=pr.tp_domains,
                             fp_calls_baseline=pb.fp_calls, fp_calls_run=pr.fp_calls,
                             blocked_requests=pr.blocked_requests, consent_method=pr.consent_method,
                             consent_action=pr.consent_action, consent_outcome=pr.consent_outcome, gpc=pr.gpc))
    return pd.DataFrame(rows, columns=COMPARISON_COLUMNS)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dbs", nargs="+")
    ap.add_argument("--baseline", default="baseline")
    ap.add_argument("--outdir", default="results")
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)

    d = load(a.dbs)
    det = detect(d["api"]) if len(d["api"]) else pd.DataFrame(
        columns=["run", "site", "phase_group", "script_domain", "script_url", "third_party", "technique"])
    ex_rows, sh_rows = [], []
    for path in a.dbs:
        e, s = exfil_mod.analyze_db(path)
        ex_rows += e
        sh_rows += s
    ex = pd.DataFrame(ex_rows, columns=exfil_mod.EXFIL_COLUMNS)
    sh = pd.DataFrame(sh_rows, columns=exfil_mod.SHARING_COLUMNS)
    beh = behavior(d["api"])
    ps = per_site(d, det, exfil=ex, sharing=sh, beh=beh)
    cmp_ = compare(ps, det, a.baseline, exfil=ex)
    ps.to_csv(out / "per_site.csv", index=False)
    det.drop_duplicates().to_csv(out / "fp_scripts.csv", index=False)
    cmp_.to_csv(out / "comparison.csv", index=False)
    ex.to_csv(out / "fp_exfil.csv", index=False)
    sh.to_csv(out / "id_sharing.csv", index=False)
    beh.to_csv(out / "behavior.csv", index=False)
    coverage = ps.groupby(["run", "consent_action", "consent_outcome", "consent_obstacle"], dropna=False).agg(
        site_runs=("site", "size"), usable_navigation=("measurement_ok", "sum"),
        eligible_site_runs=("comparison_eligible", "sum"),
        pay_or_ok=("consent_pay_or_ok", "sum")).reset_index()
    coverage.to_csv(out / "consent_coverage.csv", index=False)
    print("\n== Consent outcomes (all site/runs, including exclusions) ==")
    print(coverage.to_string(index=False))

    ok = ps[(ps.ok == 1) & (ps.measurement_ok == 1)]
    excluded = len(ps) - len(ok)
    if excluded:
        print(f"\nExcluded {excluded} incomplete/failed site-run measurement(s); "
              "see ok/error and measurement_ok/measurement_error in per_site.csv.")
    # Keep valid observations with failed/unknown consent in per_site.csv and
    # the coverage table, but don't present them as verified consent results.
    ok = ps[ps.comparison_eligible == 1]
    print("\n== Per-run summary (eligible site/runs) ==")
    summ = ok.groupby("run").agg(sites=("site", "nunique"), median_tp_domains=("tp_domains", "median"),
                                 median_fp_calls=("fp_calls", "median"),
                                 sites_with_fp=("fp_scripts", lambda s: int((s > 0).sum())),
                                 sites_fp_pre_consent=("fp_scripts_pre_consent", lambda s: int((s > 0).sum())),
                                 blocked_requests=("blocked_requests", "sum"),
                                 sites_fp_exfil=("fp_exfil_tp_receivers", lambda s: int((s > 0).sum())),
                                 sites_id_sharing=("id_sharing_receivers", lambda s: int((s > 0).sum())),
                                 sites_behavior_tp=("behavior_tp_domains", lambda s: int((s > 0).sum())),
                                 median_nonstorage_api_calls=("nonstorage_api_calls", "median"),
                                 sites_with_candidates=("candidate_scripts", lambda s: int((s > 0).sum())),
                                 sites_candidates_pre_consent=("fp_scripts_pre_consent", lambda s: int((s > 0).sum())))
    for t in TECHNIQUES:
        summ[f"sites_{t}"] = ok.groupby("run")[t].apply(lambda s: int((s > 0).sum()))
    print(summ.to_string())
    usable_det = det.merge(ok[["run", "site"]].drop_duplicates(), on=["run", "site"], how="inner")
    if len(usable_det):
        print("\n== Top candidate script domains (includes weak signals; not tracking verdicts) ==")
        top = usable_det[usable_det.run == a.baseline] if a.baseline in set(usable_det.run) else usable_det
        print(top.groupby("script_domain")["site"].nunique().sort_values(ascending=False).head(15).to_string())
    if len(cmp_):
        print(f"\n== vs. {a.baseline}: candidate (script domain, technique) pairs surviving, post-consent ==")
        agg = cmp_.groupby("run").agg(sites=("site", "count"), pairs_baseline=("fp_pairs_baseline", "sum"),
                                      pairs_surviving=("fp_pairs_surviving", "sum"), pairs_new=("fp_pairs_new", "sum"),
                                      median_tp_domains_run=("tp_domains_run", "median"))
        agg["survival_rate"] = (agg.pairs_surviving / agg.pairs_baseline.where(agg.pairs_baseline > 0)).round(3)
        ex_agg = cmp_.groupby("run")[["exfil_pairs_baseline", "exfil_pairs_surviving"]].sum()
        agg["exfil_survival_rate"] = (ex_agg.exfil_pairs_surviving
                                      / ex_agg.exfil_pairs_baseline.where(ex_agg.exfil_pairs_baseline > 0)).round(3)
        print(agg.to_string())
    print(f"\nCSV written to {out}/ (per_site.csv, fp_scripts.csv, comparison.csv, consent_coverage.csv, "
          "fp_exfil.csv, id_sharing.csv, behavior.csv)")


if __name__ == "__main__":
    main()
