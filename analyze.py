#!/usr/bin/env python3
"""Per-site metrics, fingerprinting-technique detection and run-vs-baseline comparison.

  python analyze.py data/crawl.sqlite --baseline baseline --outdir results/

Techniques (per script, per frame, per page visit), after Englehardt & Narayanan (CCS'16)
as adapted by FP-Inspector (S&P'21):
  canvas        text drawn with >=10 distinct chars AND image read back from a >=16x16
                canvas (toDataURL/toBlob non-lossy, or getImageData >=16x16)
  canvas_font   measureText with >=50 distinct fonts
  webrtc        createDataChannel + createOffer
  audio         createOscillator + createDynamicsCompressor + a readback
  webgl         getParameter(UNMASKED_VENDOR/RENDERER_WEBGL)  [extra, not in E&N]
  attr_enum     >=12 distinct navigator/screen attributes read  [weak signal]
"""
import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import pandas as pd

PRE_PHASES = {"landing"}
POST_PHASES = {"after_consent", "home_reload", "subpage"}
TECHNIQUES = ["canvas", "canvas_font", "webrtc", "audio", "webgl", "attr_enum"]
LOSSY = ("image/jpeg", "image/webp")


def category(api):
    if api.startswith(("Storage.", "IndexedDB.", "Document.cookie")):
        return "storage"
    if api == "Navigator.sendBeacon":
        return "beacon"
    if api in ("Canvas.getContext",):
        return "other"
    return "fingerprinting"


def techniques(calls):
    """calls: list of (api, args_dict) for one script in one frame/visit."""
    by = defaultdict(list)
    for api, a in calls:
        by[api].append(a or {})
    found = set()
    chars = set()
    for a in by["Canvas2D.fillText"] + by["Canvas2D.strokeText"]:
        chars |= set(a.get("text") or "")
    readback = any((a.get("w") or 0) >= 16 and (a.get("h") or 0) >= 16 and a.get("type") not in LOSSY
                   for a in by["Canvas.toDataURL"] + by["Canvas.toBlob"]) or \
        any((a.get("sw") or 0) >= 16 and (a.get("sh") or 0) >= 16 for a in by["Canvas2D.getImageData"])
    if len(chars) >= 10 and readback:
        found.add("canvas")
    if len({a.get("font") for a in by["Canvas2D.measureText"]}) >= 50:
        found.add("canvas_font")
    if by["WebRTC.createDataChannel"] and by["WebRTC.createOffer"]:
        found.add("webrtc")
    if by["Audio.createOscillator"] and by["Audio.createDynamicsCompressor"] and (
            by["Audio.startRendering"] or by["Audio.getFloatFrequencyData"] or by["Audio.getChannelData"]):
        found.add("audio")
    if any(a.get("p") in (37445, 37446) for a in by["WebGL.getParameter"]):
        found.add("webgl")
    methods = {"Navigator.sendBeacon", "Navigator.getBattery", "Navigator.getGamepads", "Navigator.javaEnabled"}
    if len([k for k in by if k.startswith(("Navigator.", "Screen.")) and k not in methods]) >= 12:
        found.add("attr_enum")
    return found


def load(dbs):
    frames = {k: [] for k in ("visits", "requests", "api", "cookies", "done")}
    for path in dbs:
        con = sqlite3.connect(path)
        frames["visits"].append(pd.read_sql("SELECT * FROM visits", con))
        frames["requests"].append(pd.read_sql(
            "SELECT r.visit_id, r.domain, r.third_party, r.failure, v.run, v.site, v.phase "
            "FROM requests r JOIN visits v ON v.id=r.visit_id", con))
        frames["api"].append(pd.read_sql(
            "SELECT a.visit_id, a.frame_url, a.script_url, a.script_domain, a.third_party, a.api, a.args, "
            "v.run, v.site, v.phase FROM api_calls a JOIN visits v ON v.id=a.visit_id", con))
        frames["cookies"].append(pd.read_sql("SELECT * FROM cookies", con))
        frames["done"].append(pd.read_sql("SELECT * FROM sites_done", con))
        con.close()
    return {k: pd.concat(v, ignore_index=True) if v else pd.DataFrame() for k, v in frames.items()}


def detect(api):
    """-> DataFrame(run, site, phase_group, script_domain, script_url, third_party, technique)"""
    rows = []
    api = api.copy()
    api["argd"] = api["args"].map(lambda s: json.loads(s) if isinstance(s, str) else {})
    for (vid, frame, script), g in api.groupby(["visit_id", "frame_url", "script_url"], dropna=False):
        found = techniques(list(zip(g["api"], g["argd"])))
        if not found:
            continue
        r0 = g.iloc[0]
        pg = "pre" if r0["phase"] in PRE_PHASES else "post"
        for t in found:
            rows.append((r0["run"], r0["site"], pg, r0["script_domain"] or "(inline/unknown)", script,
                         int(r0["third_party"]), t))
    return pd.DataFrame(rows, columns=["run", "site", "phase_group", "script_domain", "script_url", "third_party", "technique"])


def per_site(d, det):
    req, api, ck = d["requests"], d["api"], d["cookies"]
    api = api.assign(cat=api["api"].map(category))
    g = ["run", "site"]
    out = d["done"][["run", "site", "ok", "error", "seconds"]].set_index(g)
    out["requests"] = req.groupby(g).size()
    blocked = req.failure.fillna("").str.contains("BLOCKED_BY_CLIENT")
    tp = req[(req.third_party == 1) & ~blocked]  # contacted, i.e. not blocked by the extension
    out["tp_requests"] = tp.groupby(g).size()
    out["tp_domains"] = tp.groupby(g)["domain"].nunique()
    out["blocked_requests"] = req[blocked].groupby(g).size()
    out["fp_calls"] = api[api.cat == "fingerprinting"].groupby(g).size()
    out["fp_apis_distinct"] = api[api.cat == "fingerprinting"].groupby(g)["api"].nunique()
    out["tp_fp_calls"] = api[(api.cat == "fingerprinting") & (api.third_party == 1)].groupby(g).size()
    out["storage_ops"] = api[api.cat == "storage"].groupby(g).size()
    out["cookie_writes_js"] = api[api.api == "Document.cookie.set"].groupby(g).size()
    out["cookies_final"] = ck.groupby(g).size()
    out["tp_cookies_final"] = ck[ck.third_party == 1].groupby(g).size()
    out["fp_scripts"] = det.groupby(g)["script_url"].nunique() if len(det) else 0
    for t in TECHNIQUES:
        sub = det[det.technique == t] if len(det) else det
        out[t] = sub.groupby(g)["script_domain"].nunique() if len(sub) else 0
    pre = det[det.phase_group == "pre"] if len(det) else det
    out["fp_scripts_pre_consent"] = pre.groupby(g)["script_url"].nunique() if len(pre) else 0
    cons = d["visits"][d["visits"].phase == "after_consent"].set_index(g)["consent"]
    out["consent_method"] = cons.map(lambda s: (json.loads(s) or {}).get("method") if isinstance(s, str) else None)
    out["tcf_purposes"] = cons.map(lambda s: (json.loads(s) or {}).get("tcf") if isinstance(s, str) else None).map(
        lambda t: ",".join(map(str, t.get("purposesConsented", []))) if isinstance(t, dict) else t)
    num = [c for c in out.columns if c not in ("ok", "error", "seconds", "consent_method", "tcf_purposes")]
    out[num] = out[num].fillna(0).astype(int)
    return out.reset_index()


def compare(ps, det, baseline):
    """Survival of (script eTLD+1, technique) pairs vs. the baseline, post-consent phases."""
    key = lambda df: set(zip(df.script_domain, df.technique))
    post = det[det.phase_group == "post"] if len(det) else det
    ok = ps[ps.ok == 1]
    rows = []
    for run in sorted(set(ok.run) - {baseline}):
        both = set(ok[ok.run == baseline].site) & set(ok[ok.run == run].site)
        for site in sorted(both):
            b = key(post[(post.run == baseline) & (post.site == site)]) if len(post) else set()
            r = key(post[(post.run == run) & (post.site == site)]) if len(post) else set()
            pb = ps[(ps.run == baseline) & (ps.site == site)].iloc[0]
            pr = ps[(ps.run == run) & (ps.site == site)].iloc[0]
            rows.append(dict(run=run, site=site, fp_pairs_baseline=len(b), fp_pairs_surviving=len(b & r),
                             fp_pairs_new=len(r - b),
                             survival=(len(b & r) / len(b)) if b else None,
                             tp_domains_baseline=pb.tp_domains, tp_domains_run=pr.tp_domains,
                             fp_calls_baseline=pb.fp_calls, fp_calls_run=pr.fp_calls,
                             blocked_requests=pr.blocked_requests, consent_method=pr.consent_method))
    return pd.DataFrame(rows)


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
    ps = per_site(d, det)
    cmp_ = compare(ps, det, a.baseline)
    ps.to_csv(out / "per_site.csv", index=False)
    det.drop_duplicates().to_csv(out / "fp_scripts.csv", index=False)
    cmp_.to_csv(out / "comparison.csv", index=False)

    ok = ps[ps.ok == 1]
    print("\n== Per-run summary (successful sites) ==")
    summ = ok.groupby("run").agg(sites=("site", "nunique"), median_tp_domains=("tp_domains", "median"),
                                 median_fp_calls=("fp_calls", "median"),
                                 sites_with_fp=("fp_scripts", lambda s: int((s > 0).sum())),
                                 sites_fp_pre_consent=("fp_scripts_pre_consent", lambda s: int((s > 0).sum())),
                                 blocked_requests=("blocked_requests", "sum"))
    for t in TECHNIQUES:
        summ[f"sites_{t}"] = ok.groupby("run")[t].apply(lambda s: int((s > 0).sum()))
    print(summ.to_string())
    if len(det):
        print("\n== Top fingerprinting script domains ==")
        top = det[det.run == a.baseline] if a.baseline in set(det.run) else det
        print(top.groupby("script_domain")["site"].nunique().sort_values(ascending=False).head(15).to_string())
    if len(cmp_):
        print(f"\n== vs. {a.baseline}: (script domain, technique) pairs surviving, post-consent ==")
        agg = cmp_.groupby("run").agg(sites=("site", "count"), pairs_baseline=("fp_pairs_baseline", "sum"),
                                      pairs_surviving=("fp_pairs_surviving", "sum"), pairs_new=("fp_pairs_new", "sum"),
                                      median_tp_domains_run=("tp_domains_run", "median"))
        agg["survival_rate"] = (agg.pairs_surviving / agg.pairs_baseline.where(agg.pairs_baseline > 0)).round(3)
        print(agg.to_string())
    print(f"\nCSV written to {out}/ (per_site.csv, fp_scripts.csv, comparison.csv)")


if __name__ == "__main__":
    main()
