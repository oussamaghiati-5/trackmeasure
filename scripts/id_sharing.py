#!/usr/bin/env python3
"""Identifier sharing: identifier-like cookie values that leave the browser towards another party.

    python scripts/id_sharing.py data/full_eu_20261125/crawl.sqlite results/full_eu_20261125 \
        --radar reference/tracker_radar_subset.json

Adapted from exfil.find_sharing on the main branch (commit 4eb0e9b), for this branch's schema
(requests.body) and with a stricter test of what counts as an identifier.

A token of a cookie value counts as an identifier when all of these hold:
  * it has >= 10 characters from [A-Za-z0-9_-], at least one digit and > 4 distinct characters;
  * it is not a Unix timestamp (seconds or milliseconds) within 5 years of the crawl;
  * the cookie is not a consent record (its name does not contain "consent" and is not a known
    CMP cookie);
  * it occurs in exactly one profile, i.e. one (run, site). Every site visit starts from a fresh
    browser profile, so a value identifying this browser cannot repeat across sites or runs. A
    repeated value is a constant (a build, publisher or account ID), not a browser identifier.

The identifier is *shared* when it appears, raw or URL-decoded, in the URL or captured body
of a request that left the browser to another party:
  * the request was not blocked by an extension;
  * its registrable domain differs from the cookie's owner;
  * no single company owns both domains (Tracker Radar owners, or a brand-named domain).

Flows: first_to_third (a site cookie sent to a third party, e.g. an analytics client ID),
third_to_third (a third-party cookie sent to another third party: cookie syncing) and
third_to_first (a third-party cookie sent to the site).

Lower bound: the Cookie and Referer headers, hashed or encoded identifiers and truncated bodies
are not searched. Direction is not established: the receiver may have issued the value.

The primary filter is supplemented by a stricter sensitivity analysis that also requires the
same cookie name/owner to show at least two different identifier-like tokens in at least two
profiles. This drops condition-only constants at the cost of missing genuine cookies that exist
only after acceptance.

Outputs contain no cookie values, tokens or URLs:
  * id_sharing.csv: run, site, phase, cookie_name, cookie_owner, receiver_domain, flow,
    receiver_known_tracker, requests;
  * id_sharing.json: summary.
  * id_sharing_strict.csv: the stricter cookie-variation sensitivity rows.
"""
import argparse
import json
import math
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from common import etld1  # noqa: E402
from label_purposes import owner_key, owner_of, same_brand  # noqa: E402

TOKEN = re.compile(r"[A-Za-z0-9_-]{10,}")
CONSENT_COOKIE = re.compile(
    r"consent|^(eupubconsent(-v2)?|optanonalertboxclosed|cookiebot.*|usprivacy|didomi_token|"
    r"_?uc_.*|_sp_.*|__cmp.*|gpp.*|fccdcf|fcnec)$", re.I)
FIVE_YEARS = 5 * 365 * 86400
COLUMNS = ["run", "site", "phase", "cookie_name", "cookie_owner", "receiver_domain", "flow",
           "receiver_known_tracker", "requests"]
CONDITIONS = ("baseline", "baseline2", "ubol", "pbadger", "optout", "reject", "gpc", "accept")
DEFINITION = (
    "A single-profile identifier-like cookie value (>= 10 chars, a digit, not a timestamp, "
    "not a consent cookie, seen in one run/site only) found in the URL or body of an unblocked "
    "request to another registrable domain not owned by the cookie's owner. 'known tracker' = "
    "receiver listed in DuckDuckGo's tracker blocklist subset; other receivers include security, "
    "functional and unrecognised same-company domains, so 'any third party' is an upper bound. "
    "A condition-only constant can pass this primary filter; strict_variation_sensitivity also "
    "requires the cookie name/owner to vary across profiles. Counts are over comparison-eligible "
    "site/runs after the consent step unless named otherwise.")


def is_timestamp(token, now):
    if not token.isdigit() or not 9 <= len(token) <= 13:
        return False
    value = int(token)
    return abs(value - now) < FIVE_YEARS or abs(value / 1000 - now) < FIVE_YEARS


def candidate_tokens(value, now):
    """Identifier-like substrings of one cookie value (before the one-profile test)."""
    text = unquote(value or "")
    return {t for t in TOKEN.findall(text)
            if any(c.isdigit() for c in t) and len(set(t)) > 4 and not is_timestamp(t, now)}


def identifiers(cookies, now, require_cookie_variation=False):
    """cookies: iterable of (run, site, name, domain, value).
    -> {(run, site): {token: [(cookie_name, cookie_owner)]}} for single-profile tokens."""
    seen = defaultdict(set)
    cookie_tokens, cookie_profiles = defaultdict(set), defaultdict(set)
    rows = []
    for run, site, name, domain, value in cookies:
        if CONSENT_COOKIE.search(name or ""):
            continue
        owner = etld1(domain or "")
        for token in candidate_tokens(value, now):
            seen[token].add((run, site))
            cookie_tokens[name, owner].add(token)
            cookie_profiles[name, owner].add((run, site))
            rows.append((run, site, token, name, owner))
    out = defaultdict(lambda: defaultdict(list))
    for run, site, token, name, owner in rows:
        varying = (len(cookie_tokens[name, owner]) >= 2 and
                   len(cookie_profiles[name, owner]) >= 2)
        if len(seen[token]) == 1 and (not require_cookie_variation or varying):
            out[run, site][token].append((name, owner))
    return out


def same_company(a, b, owners):
    if not a or not b:
        return False
    if a == b or same_brand(a, b) or same_brand(b, a):
        return True
    oa, ob = owner_key(owner_of(a, owners)), owner_key(owner_of(b, owners))
    return bool(oa and ob and oa == ob)


def flow(site, owner, receiver):
    if owner == site:
        return "first_to_third"
    return "third_to_first" if receiver == site else "third_to_third"


def exact_mcnemar(reference_only, condition_only):
    """Two-sided exact McNemar test over discordant paired binary outcomes."""
    n = reference_only + condition_only
    if not n:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(reference_only, condition_only) + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def find_sharing(con, owners, trackers, require_cookie_variation=False):
    now = statistics.median(r[0] for r in con.execute("SELECT started FROM visits WHERE started IS NOT NULL"))
    ids = identifiers(con.execute("SELECT run, site, name, domain, value FROM cookies"), now,
                      require_cookie_variation=require_cookie_variation)
    has_body = any(r[1] == "body" for r in con.execute("PRAGMA table_info(requests)"))
    body = "r.body" if has_body else "NULL"
    counts = Counter()
    for (run, site), tokens in ids.items():
        site_domain = etld1(site)
        pattern = re.compile("|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True)))
        for url, text, receiver, failure, phase in con.execute(
                f"SELECT r.url, {body}, r.domain, r.failure, v.phase FROM requests r "
                "JOIN visits v ON v.id = r.visit_id WHERE v.run = ? AND v.site = ?", (run, site)):
            if not receiver or (failure and "BLOCKED_BY_CLIENT" in failure):
                continue
            hay = "\n".join(p for p in (url, text) if p)
            if "%" in hay:
                hay += "\n" + unquote(hay)
            for token in {m.group(0) for m in pattern.finditer(hay)}:
                for name, owner in tokens[token]:
                    if not same_company(owner, receiver, owners):
                        counts[run, site, phase, name, owner, receiver,
                               flow(site_domain, owner, receiver), int(receiver in trackers)] += 1
    rows = [dict(zip(COLUMNS, (*key, n))) for key, n in sorted(counts.items())]
    return pd.DataFrame(rows, columns=COLUMNS)


def summarize(share, per_site):
    """Counts over comparison-eligible site/runs; post = after the consent step."""
    ok = per_site[per_site.comparison_eligible == 1]
    eligible = {run: set(ok[ok.run == run].site) for run in ok.run.unique()}
    post = share[share.phase != "landing"]
    third = post[post.flow != "third_to_first"]          # an identifier reached a third party

    def sites(frame, run, sites_in=None, flows=None):
        f = frame[(frame.run == run)]
        if flows:
            f = f[f.flow.isin(flows)]
        chosen = set(f.site)
        return len(chosen & (sites_in if sites_in is not None else eligible.get(run, set())))

    out = {"definition": DEFINITION, "by_condition": {}}
    for run in [r for r in CONDITIONS if r in eligible]:
        e = eligible[run]
        f = third[(third.run == run) & third.site.isin(e)]
        receivers = f.groupby("site").receiver_domain.nunique().reindex(sorted(e), fill_value=0)
        out["by_condition"][run] = {
            "eligible_sites": len(e),
            "sites_id_to_third_party": int((receivers > 0).sum()),
            "sites_first_to_third": sites(post, run, e, ["first_to_third"]),
            "sites_cookie_sync": sites(post, run, e, ["third_to_third"]),
            "sites_to_known_tracker": len(set(f[f.receiver_known_tracker == 1].site)),
            "median_receivers_per_site": float(receivers.median()) if len(receivers) else None,
            "sites_before_consent": len(set(share[(share.run == run) & (share.phase == "landing")
                                                  & (share.flow != "third_to_first")].site) & e),
        }
    # Paired: the same sites in baseline, its repeat (noise floor) and the condition.
    known = third[third.receiver_known_tracker == 1]
    with_known = {run: set(known[known.run == run].site) for run in eligible}
    out["paired_known_tracker"] = {}
    for run in [r for r in CONDITIONS if r in eligible and r not in ("baseline", "baseline2")]:
        common = eligible.get("baseline", set()) & eligible.get("baseline2", set()) & eligible[run]
        base, repeat, condition = (with_known.get("baseline", set()) & common,
                                   with_known.get("baseline2", set()) & common,
                                   with_known[run] & common)
        base_only, cond_only_base = len(base - condition), len(condition - base)
        repeat_only, cond_only_repeat = len(repeat - condition), len(condition - repeat)
        out["paired_known_tracker"][run] = {
            "common_sites": len(common),
            "baseline": len(base), "repeat": len(repeat), "condition": len(condition),
            "baseline_vs_condition": {
                "baseline_only": base_only, "condition_only": cond_only_base,
                "exact_mcnemar_p_two_sided": exact_mcnemar(base_only, cond_only_base)},
            "repeat_vs_condition": {
                "repeat_only": repeat_only, "condition_only": cond_only_repeat,
                "exact_mcnemar_p_two_sided": exact_mcnemar(repeat_only, cond_only_repeat)}}
    base = third[(third.run == "baseline") & third.site.isin(eligible.get("baseline", set()))]
    out["top_receivers_baseline"] = base.groupby("receiver_domain").site.nunique() \
        .sort_values(ascending=False).head(15).to_dict()
    out["top_cookies_baseline"] = base[base.receiver_known_tracker == 1].groupby("cookie_name").site.nunique() \
        .sort_values(ascending=False).head(10).to_dict()
    return out


def combined(share, purposes):
    """Sites where a domain that runs tracking-class fingerprinting also receives an identifier."""
    if purposes is None or not len(share):
        return {}
    tracking = purposes[purposes.harm_class == "Tracking"][["site", "script_domain"]].drop_duplicates()
    pairs = set(zip(tracking.site, tracking.script_domain))
    out = {}
    for run in [r for r in ("baseline", "optout", "reject", "accept") if r in set(share.run)]:
        f = share[(share.run == run) & (share.phase != "landing") & (share.flow != "third_to_first")]
        hit = f[pd.Series([(s, d) in pairs for s, d in zip(f.site, f.receiver_domain)], index=f.index, dtype=bool)]
        out[run] = {"sites": int(hit.site.nunique()), "receivers": sorted(set(hit.receiver_domain))}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db")
    ap.add_argument("results")
    ap.add_argument("--radar", default=str(ROOT / "reference" / "tracker_radar_subset.json"))
    a = ap.parse_args()
    res = Path(a.results)
    radar = json.loads(Path(a.radar).read_text(encoding="utf-8")) if a.radar else {}
    owners, trackers = radar.get("owners", {}), set(radar.get("blocklist", {}))
    con = sqlite3.connect(Path(a.db).resolve().as_uri() + "?immutable=1", uri=True)
    share = find_sharing(con, owners, trackers)
    strict_share = find_sharing(con, owners, trackers, require_cookie_variation=True)
    con.close()
    share.to_csv(res / "id_sharing.csv", index=False)
    strict_share.to_csv(res / "id_sharing_strict.csv", index=False)
    per_site = pd.read_csv(res / "per_site.csv")
    summary = summarize(share, per_site)
    strict_summary = summarize(strict_share, per_site)
    strict_summary["definition"] = ("Primary identifier-like filter plus at least two distinct "
                                    "candidate tokens for the same cookie name/owner in at least "
                                    "two profiles. This removes condition-only constants but can "
                                    "miss identifiers created in only one condition.")
    summary["strict_variation_sensitivity"] = strict_summary
    path = res / "script_purposes.csv"
    summary["combined_with_tracking_fingerprinting"] = combined(
        share, pd.read_csv(path) if path.exists() else None)
    # Verified consent: identifiers still reaching third parties after a verified choice.
    ok = per_site[per_site.comparison_eligible == 1]
    verified = {run: set(ok[(ok.run == run) & (ok.consent_outcome == "confirmed")].site)
                for run in ("optout", "reject", "accept") if run in set(per_site.run)}
    both = verified.get("optout", set()) & verified.get("accept", set())
    post = share[(share.phase != "landing") & (share.flow != "third_to_first")]
    recv = lambda run, s: post[(post.run == run) & post.site.isin(s)].groupby("site").receiver_domain.nunique() \
        .reindex(sorted(s), fill_value=0)
    def after(run, s):
        f = post[(post.run == run) & post.site.isin(s)]
        known = f[f.receiver_known_tracker == 1]
        return {"verified_sites": len(s), "sites_id_to_third_party": int((recv(run, s) > 0).sum()),
                "sites_id_to_known_tracker": int(known.site.nunique()),
                "sites_cookie_sync": int(f[f.flow == "third_to_third"].site.nunique()),
                "known_tracker_receivers": known.groupby("receiver_domain").site.nunique()
                .sort_values(ascending=False).to_dict()}

    summary["consent"] = {
        **{f"after_verified_{run}": after(run, s) for run, s in verified.items()},
        "dual_verified": {"sites": len(both),
                          "median_receivers_accept": float(recv("accept", both).median()) if both else None,
                          "median_receivers_reject": float(recv("optout", both).median()) if both else None},
    }
    (res / "id_sharing.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("by_condition", "consent", "combined_with_tracking_fingerprinting")}, indent=1))


if __name__ == "__main__":
    main()
