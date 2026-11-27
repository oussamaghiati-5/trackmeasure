#!/usr/bin/env python3
"""Find cookie values re-issued across isolated browser profiles.

The main crawler creates and deletes a fresh persistent Chromium profile for every
(run, site).  Consequently, a value that occurs in the final cookie jars of two
runs was not copied by the crawler from one run to the other.  This script uses
that invariant as an observational relinking test:

    python scripts/cookie_isolation.py data/BATCH/crawl.sqlite results/BATCH

An *isolated-profile re-issue candidate* is an identifier-like token that:

* occurs for the same site in two fresh-profile runs;
* occurs on no other measured site (cross-site constants are excluded);
* is not a timestamp or a consent-cookie token.

The result is evidence that two isolated profiles received the same value.  It is
not proof that a fingerprint produced the value: publisher constants, login state,
the common exit IP, server-side state and identifier parsing can explain a match.
The dedicated A1/A2/B protocol in respawn.py is required to test those causes.

Outputs deliberately contain no cookie values:

* cookie_isolation.csv: per-site counts and cookie slots for each run pair;
* cookie_isolation.json: aggregate counts, design invariant and limitations.
"""
import argparse
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from common import etld1  # noqa: E402
from id_sharing import CONSENT_COOKIE, candidate_tokens  # noqa: E402

RUN_PAIRS = (
    ("baseline", "baseline2"),
    ("baseline", "ubol"),
    ("baseline", "pbadger"),
    ("baseline", "optout"),
    ("baseline", "reject"),
    ("baseline", "gpc"),
    ("baseline", "accept"),
)
COLUMNS = (
    "run_a", "run_b", "site", "candidate_tokens", "same_slot_candidates",
    "two_profile_only_candidates", "multi_profile_candidates", "cookie_names",
    "cookie_owners", "two_profile_only_cookie_names", "multi_profile_cookie_names",
)
DEFINITION = (
    "An identifier-like token (>=10 URL-safe characters, includes a digit, not a recent "
    "timestamp or consent cookie) present for one site in both final cookie jars of a run "
    "pair, while absent from every other measured site. Each (run, site) starts in a newly "
    "created Chromium profile that is deleted afterwards. Values are never exported."
)
LIMITATIONS = (
    "A match proves value re-issuance across the crawler's isolated profiles, not fingerprint "
    "derivation or tracking. Site-scoped constants, the shared exit IP, server-side state, "
    "content variation, accounts outside browser state and token parsing can cause matches. "
    "Only final cookie jars are available, so creation time and Cookie request headers are "
    "not observed. The runs were collected sequentially, not randomised. Confirm candidates "
    "with respawn.py using cookie-clear, all-state-clear and changed-fingerprint controls."
)


def collect(cookies, now):
    """Return token/profile slots plus diagnostics without retaining raw values.

    cookies is an iterable of (run, site, name, domain, value).  A slot is
    (cookie name, registrable owner).  Consent-cookie rows are ignored before
    tokenisation so encoded consent strings cannot become candidates.
    """
    occurrences = defaultdict(lambda: defaultdict(set))
    token_sites = defaultdict(set)
    consent_rows = 0
    candidate_rows = 0
    for run, site, name, domain, value in cookies:
        if CONSENT_COOKIE.search(name or ""):
            consent_rows += 1
            continue
        tokens = candidate_tokens(value, now)
        if tokens:
            candidate_rows += 1
        slot = (name or "(unnamed)", etld1(domain or "") or "(unknown)")
        for token in tokens:
            profile = (run, site)
            occurrences[token][profile].add(slot)
            token_sites[token].add(site)
    diagnostics = {
        "cookie_rows_with_identifier_like_token": candidate_rows,
        "consent_cookie_rows_excluded": consent_rows,
        "cross_site_tokens_excluded": sum(len(sites) > 1 for sites in token_sites.values()),
    }
    return occurrences, token_sites, diagnostics


def find_candidates(cookies, now, eligible, pairs=RUN_PAIRS):
    """Build privacy-reduced per-site rows for run pairs.

    eligible maps run -> set(site), normally from comparison_eligible in
    per_site.csv.  Token strings are used only as in-memory grouping keys and
    never appear in the returned frame.
    """
    occurrences, token_sites, diagnostics = collect(cookies, now)
    rows = defaultdict(lambda: {
        "tokens": set(), "same": set(), "two": set(), "multi": set(),
        "names": set(), "owners": set(), "two_names": set(), "multi_names": set(),
    })
    available_pairs = []
    for run_a, run_b in pairs:
        common = eligible.get(run_a, set()) & eligible.get(run_b, set())
        if not common:
            continue
        available_pairs.append((run_a, run_b))
        for token, profiles in occurrences.items():
            if len(token_sites[token]) != 1:
                continue
            site = next(iter(token_sites[token]))
            pa, pb = (run_a, site), (run_b, site)
            if site not in common or pa not in profiles or pb not in profiles:
                continue
            item = rows[run_a, run_b, site]
            item["tokens"].add(token)
            slots_a, slots_b = profiles[pa], profiles[pb]
            if slots_a & slots_b:
                item["same"].add(token)
            if len(profiles) == 2:
                item["two"].add(token)
                item["two_names"].update(name for name, _ in slots_a | slots_b)
            else:
                item["multi"].add(token)
                item["multi_names"].update(name for name, _ in slots_a | slots_b)
            for name, owner in slots_a | slots_b:
                item["names"].add(name)
                item["owners"].add(owner)

    output = []
    for (run_a, run_b, site), item in sorted(rows.items()):
        output.append({
            "run_a": run_a,
            "run_b": run_b,
            "site": site,
            "candidate_tokens": len(item["tokens"]),
            "same_slot_candidates": len(item["same"]),
            "two_profile_only_candidates": len(item["two"]),
            "multi_profile_candidates": len(item["multi"]),
            "cookie_names": ",".join(sorted(item["names"])),
            "cookie_owners": ",".join(sorted(item["owners"])),
            "two_profile_only_cookie_names": ",".join(sorted(item["two_names"])),
            "multi_profile_cookie_names": ",".join(sorted(item["multi_names"])),
        })
    return pd.DataFrame(output, columns=COLUMNS), diagnostics, available_pairs


def summarize(rows, eligible, pairs, diagnostics):
    pair_summary = {}
    for run_a, run_b in pairs:
        common = eligible.get(run_a, set()) & eligible.get(run_b, set())
        selected = rows[(rows.run_a == run_a) & (rows.run_b == run_b)]
        key = f"{run_a}__{run_b}"
        n_sites = int(selected.site.nunique())
        name_counts = Counter()
        owner_counts = Counter()
        for record in selected.itertuples():
            for name in filter(None, record.cookie_names.split(",")):
                name_counts[name] += 1
            for owner in filter(None, record.cookie_owners.split(",")):
                owner_counts[owner] += 1
        pair_summary[key] = {
            "run_a": run_a,
            "run_b": run_b,
            "common_eligible_sites": len(common),
            "sites_with_candidate": n_sites,
            "site_rate": n_sites / len(common) if common else None,
            "sites_with_same_slot_candidate": int((selected.same_slot_candidates > 0).sum()),
            "sites_with_two_profile_only_candidate": int(
                (selected.two_profile_only_candidates > 0).sum()),
            "candidate_tokens": int(selected.candidate_tokens.sum()),
            "same_slot_candidates": int(selected.same_slot_candidates.sum()),
            "two_profile_only_candidates": int(selected.two_profile_only_candidates.sum()),
            "multi_profile_candidates": int(selected.multi_profile_candidates.sum()),
            "candidate_sites_with_fingerprinting_in_both_runs": int(
                selected.get("fp_both_runs", pd.Series(dtype=int)).sum()),
            "candidate_sites_with_tracking_fingerprinting_in_both_runs": int(
                selected.get("tracking_fp_both_runs", pd.Series(dtype=int)).sum()),
            "candidate_sites_where_cookie_owner_is_fp_script_domain": int(
                selected.get("cookie_owner_is_fp_script_domain", pd.Series(dtype=int)).sum()),
            "top_cookie_names_by_site": dict(name_counts.most_common(10)),
            "top_cookie_owners_by_site": dict(owner_counts.most_common(10)),
        }
    return {
        "definition": DEFINITION,
        "isolation_invariant": (
            "crawler.py creates tempfile.mkdtemp(prefix='tm_profile_') inside crawl_site and "
            "removes that directory in finally; crawl_site is invoked once per (run, site)."
        ),
        "interpretation": (
            "Use baseline__baseline2 as the repeat/noise observation. A candidate is a lead for "
            "the controlled respawn protocol, not a fingerprinting attribution."
        ),
        "limitations": LIMITATIONS,
        "diagnostics": diagnostics,
        "pairs": pair_summary,
    }


def add_fingerprinting_context(rows, results):
    """Relate cookie leads to site-level FP observations without claiming causality."""
    rows = rows.copy()
    fp_path = results / "fp_scripts.csv"
    purpose_path = results / "script_purposes.csv"
    fp_counts = Counter()
    fp_domains = defaultdict(set)
    if fp_path.is_file():
        fp = pd.read_csv(fp_path).fillna("")
        fp = fp[fp.phase_group == "post"]
        for (run, site), group in fp.groupby(["run", "site"]):
            fp_counts[run, site] = len(set(zip(group.script_domain, group.technique)))
            fp_domains[run, site] = set(group.script_domain) - {"", "(inline/unknown)"}

    tracking_counts = Counter()
    if purpose_path.is_file():
        purposes = pd.read_csv(purpose_path).fillna("")
        purposes = purposes[purposes.harm_class == "Tracking"]
        for record in purposes.itertuples():
            for run in filter(None, str(record.runs).split(",")):
                tracking_counts[run, record.site] += 1

    context = []
    for record in rows.itertuples(index=False):
        key_a, key_b = (record.run_a, record.site), (record.run_b, record.site)
        a, b = fp_counts[key_a], fp_counts[key_b]
        ta, tb = tracking_counts[key_a], tracking_counts[key_b]
        owners = set(filter(None, record.cookie_owners.split(",")))
        context.append((a, b, int(a > 0 and b > 0), ta, tb, int(ta > 0 and tb > 0),
                        int(bool(owners & (fp_domains[key_a] | fp_domains[key_b])))))
    columns = ("fp_pairs_run_a", "fp_pairs_run_b", "fp_both_runs",
               "tracking_fp_scripts_run_a", "tracking_fp_scripts_run_b",
               "tracking_fp_both_runs", "cookie_owner_is_fp_script_domain")
    for index, column in enumerate(columns):
        rows[column] = [item[index] for item in context]
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", type=Path)
    ap.add_argument("results", type=Path)
    args = ap.parse_args()
    per_site = pd.read_csv(args.results / "per_site.csv")
    eligible = {
        run: set(group[group.comparison_eligible == 1].site)
        for run, group in per_site.groupby("run")
    }
    con = sqlite3.connect(args.db.resolve().as_uri() + "?immutable=1", uri=True)
    started = [row[0] for row in con.execute(
        "SELECT started FROM visits WHERE started IS NOT NULL")]
    if not started:
        raise ValueError("The crawl database contains no visit timestamps")
    now = statistics.median(started)
    cookies = list(con.execute("SELECT run, site, name, domain, value FROM cookies"))
    con.close()
    rows, diagnostics, pairs = find_candidates(cookies, now, eligible)
    rows = add_fingerprinting_context(rows, args.results)
    summary = summarize(rows, eligible, pairs, diagnostics)
    args.results.mkdir(parents=True, exist_ok=True)
    rows.to_csv(args.results / "cookie_isolation.csv", index=False)
    (args.results / "cookie_isolation.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
