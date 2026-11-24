#!/usr/bin/env python3
"""Build the fingerprinting-script inventory of a batch and label each script's purpose.

    python scripts/label_purposes.py data/full_eu_20261122/crawl.sqlite results/full_eu_20261122 \
        --radar reference/tracker_radar_subset.json

Unit of analysis: a *script* is (site, script domain, path template). Templates
collapse random path segments, so a bot-manager script whose URL changes on every
visit is still one script. For each script the inventory records:

  * techniques detected (analyze.py rules) and the runs it was detected in
  * page context: on which crawled page types it ran (home, subpage + subpage type)
  * behaviour: reads of navigator.webdriver / Permissions.query (automation probes)
  * POST requests to the script's domain, number of sites its domain appears on
  * vendor cookies set on the site (Akamai, HUMAN, DataDome, Kasada, ...)
  * Tracker Radar categories, fingerprinting score, owner, prevalence
  * path clusters: the same path template served from >= 2 domains / sites

Labels come from purpose.label(). Outputs (private; they contain script URLs):
  script_inventory.csv, script_purposes.csv, hidden_vendor_clusters.csv
"""
import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import purpose  # noqa: E402

HOME = {"landing", "after_consent", "home_reload"}
STRENGTH = ["canvas", "canvas_font", "webrtc", "audio", "attr_enum", "webgl"]


def strip(url):
    return (url or "").split("?")[0].split("#")[0]


def load(db):
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?immutable=1", uri=True)
    visits = pd.read_sql("SELECT id AS visit_id, run, site, phase, url FROM visits", con)
    present = pd.read_sql("SELECT DISTINCT visit_id, script_url, script_domain FROM api_calls", con)
    probes = pd.read_sql("SELECT DISTINCT visit_id, script_url, api FROM api_calls "
                         "WHERE api IN ('Navigator.webdriver', 'Permissions.query')", con)
    posts = pd.read_sql("SELECT DISTINCT visit_id, domain FROM requests WHERE method='POST'", con)
    cookies = pd.read_sql("SELECT DISTINCT run, site, name FROM cookies", con)
    con.close()
    return visits, present, probes, posts, cookies


def owner_of(domain, owners):
    parts = (domain or "").split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in owners:
            return owners[cand]
    return None


def build(db, results, radar_path):
    fp = pd.read_csv(Path(results) / "fp_scripts.csv")
    fp["script_url"] = fp["script_url"].fillna("")
    fp["base"] = fp.script_url.map(strip)
    fp["template"] = fp.base.map(purpose.path_template)
    radar = json.loads(Path(radar_path).read_text(encoding="utf-8")) if radar_path else {}
    tr, owners = radar.get("tracker_radar", {}), radar.get("owners", {})

    visits, present, probes, posts, cookies = load(db)
    vinfo = visits.set_index("visit_id")
    present = present.join(vinfo[["run", "site", "phase", "url"]], on="visit_id")
    present["base"] = present.script_url.map(strip)
    present["template"] = present.base.map(purpose.path_template)
    present["script_domain"] = present.script_domain.map(lambda d: d or "(inline/unknown)")
    probes = probes.join(vinfo[["site"]], on="visit_id")
    probes["template"] = probes.script_url.map(lambda u: purpose.path_template(strip(u)))
    posts = posts.join(vinfo[["site"]], on="visit_id")

    key = ["site", "script_domain", "template"]
    # Page context: on which crawled pages did the script run at all (any API call)?
    pages = defaultdict(set)
    sub_types = defaultdict(set)
    for r in present.merge(fp[key].drop_duplicates(), on=key).itertuples():
        if r.phase in HOME:
            pages[(r.site, r.script_domain, r.template)].add("home")
        elif r.phase == "subpage":
            pages[(r.site, r.script_domain, r.template)].add("subpage")
            sub_types[(r.site, r.script_domain, r.template)].add(purpose.page_type(r.url))
    probe_set = defaultdict(set)
    for r in probes.itertuples():
        probe_set[(r.site, r.template)].add(r.api)
    post_set = set(zip(posts.site, posts.domain))
    site_cookies = cookies.groupby("site").name.apply(set).to_dict()
    domain_sites = fp[fp.script_domain != "(inline/unknown)"].groupby("script_domain").site.nunique()

    rows = []
    for (site, dom, tpl), g in fp.groupby(key):
        k = (site, dom, tpl)
        pg = pages.get(k, set())
        if pg == {"home", "subpage"}:
            ctx = "All pages"
        elif pg == {"home"}:
            ctx = "Home only"
        elif pg == {"subpage"}:
            types = sorted(sub_types.get(k, {"Other"}))
            ctx = f"{types[0]} only" if len(types) == 1 else "Subpage only"
        else:
            ctx = "Unobserved"
        techs = set(g.technique)
        api = probe_set.get((site, tpl), set())
        entry = tr.get(dom)
        site_owner, dom_owner = owner_of(site, owners), owner_of(dom, owners)
        rows.append(dict(
            site=site, script_domain=dom, template=tpl, example_url=g.base.iloc[0], n_urls=g.base.nunique(),
            third_party=int(g.third_party.max()),
            same_owner=int(bool(site_owner and dom_owner and site_owner == dom_owner)),
            techniques=",".join(t for t in STRENGTH if t in techs),
            strongest=next(t for t in STRENGTH if t in techs),
            runs=",".join(sorted(set(g.run))),
            pre_consent=int((g.phase_group == "pre").any()),
            page_context=ctx, subpage_types=",".join(sorted(sub_types.get(k, set()))),
            reads_webdriver=int("Navigator.webdriver" in api), reads_permissions=int("Permissions.query" in api),
            posts_to_domain=int((site, dom) in post_set),
            domain_sites_in_crawl=int(domain_sites.get(dom, 0)),
            site_cookie_vendors=",".join(sorted(purpose.cookie_vendors(site_cookies.get(site, ())))),
            tr_found=int(entry is not None),
            tr_categories="|".join((entry or {}).get("categories") or []),
            tr_fingerprinting=(entry or {}).get("fingerprinting"),
            tr_prevalence=(entry or {}).get("prevalence"),
            tr_owner=(entry or {}).get("owner") or dom_owner or "",
        ))
    inv = pd.DataFrame(rows)

    # Path clusters: one template served from >= 2 different domains on >= 2 sites.
    clusters = []
    generic = {"/", "", "/{hex}", "/{n}", "/{rand}"}
    for tpl, g in inv[~inv.template.isin(generic)].groupby("template"):
        if g.site.nunique() >= 2 and g.script_domain.nunique() >= 2:
            clusters.append(dict(template=tpl, n_sites=g.site.nunique(), n_domains=g.script_domain.nunique(),
                                 sites=",".join(sorted(set(g.site))), domains=",".join(sorted(set(g.script_domain))),
                                 first_party_members=int((g.third_party == 0).sum())))
    clusters = pd.DataFrame(clusters, columns=["template", "n_sites", "n_domains", "sites", "domains",
                                               "first_party_members"])
    cl = {r.template: r._asdict() for r in clusters.itertuples()}

    # Vendors matched by signatures on each site, in any run.
    site_vendors = defaultdict(set)
    for r in inv.itertuples():
        sig = purpose.match_signature(r.example_url)
        if sig:
            site_vendors[r.site].add(sig[0])

    # An /akam/ beacon on the site is first-party evidence of Akamai Bot Manager,
    # used like a vendor cookie to attribute its random-path sensor script.
    akam_sites = set(inv[inv.template.str.startswith("/akam/")].site)

    labels = []
    for r in inv.itertuples():
        script = dict(url=r.example_url, third_party=r.third_party, techniques=r.techniques.split(","),
                      page_context=r.page_context, reads_webdriver=r.reads_webdriver)
        c = cl.get(r.template)
        lab = purpose.label(script, radar=tr.get(r.script_domain), same_owner=bool(r.same_owner),
                            site_cookie_vendors=set(filter(None, r.site_cookie_vendors.split(",")))
                            | ({"Akamai"} if r.site in akam_sites else set()),
                            cluster=c, site_vendors=site_vendors.get(r.site, ()))
        labels.append(lab)
    lab = pd.concat([inv, pd.DataFrame(labels)], axis=1)
    # "independent": the label rests on vendor/Tracker Radar/cookie evidence, so the
    # behavioural validity checks in purpose_results.py are not circular.
    lab["label_source"] = lab.evidence.map(
        lambda e: "none" if e == "none" else
        "independent" if any(s in e for s in ("signature", "Tracker Radar", "cookie", "co-occurrence"))
        else "behaviour/context only")

    # Name each cluster's vendor by majority label of its members.
    if len(clusters):
        clusters["vendor"] = [
            (lab[lab.template == t].vendor.replace("", pd.NA).dropna().mode().iloc[0]
             if lab[lab.template == t].vendor.replace("", pd.NA).notna().any() else "unattributed")
            for t in clusters.template]
        clusters["purpose"] = [lab[lab.template == t].purpose_primary.mode().iloc[0] for t in clusters.template]
    return lab, clusters


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db")
    ap.add_argument("results")
    ap.add_argument("--radar", default="reference/tracker_radar_subset.json")
    a = ap.parse_args()
    lab, clusters = build(a.db, a.results, a.radar)
    out = Path(a.results)
    inv_cols = [c for c in lab.columns if c not in ("purpose_primary", "purpose_secondary", "party",
                                                    "confidence", "evidence", "vendor", "label_source")]
    lab[inv_cols].to_csv(out / "script_inventory.csv", index=False)
    lab.to_csv(out / "script_purposes.csv", index=False)
    clusters.to_csv(out / "hidden_vendor_clusters.csv", index=False)
    base = lab[lab.runs.str.contains("baseline")]
    print(f"{len(lab)} scripts ({len(base)} in baseline runs) on {lab.site.nunique()} sites")
    print(pd.crosstab(lab.purpose_primary, lab.confidence, margins=True).to_string())
    print(pd.crosstab(lab.purpose_primary, lab.party).to_string())
    print(clusters.to_string(index=False))


if __name__ == "__main__":
    main()
