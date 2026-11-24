#!/usr/bin/env python3
"""Score the manual validation sample: inter-annotator agreement and rule precision.

    python scripts/purpose_agreement.py results/full_eu_20261122/validation/annotation_sheet.csv \
        results/full_eu_20261122/validation/annotation_key.csv

Two team members fill annotation_sheet.csv independently (see
docs/annotation_guide.md), without looking at annotation_key.csv:

  a1_is_fp, a2_is_fp          yes / no / unsure   -- is this fingerprinting behaviour?
  a1_purpose, a2_purpose      Advertising, Analytics, Anti-fraud/security,
                              Functional, CDN, Unknown
  adjudicated_is_fp, adjudicated_purpose   filled together after discussing disagreements

Reports Cohen's kappa for both questions, detector precision per technique
(share of sampled detections judged fingerprinting) and the rule labels'
precision per purpose against the adjudicated purpose. Recall cannot be
estimated: the sample only contains scripts the detector flagged.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd


def cohen_kappa(a, b):
    """Cohen's kappa for two equal-length label sequences (pairs with a missing label dropped)."""
    pairs = [(x, y) for x, y in zip(a, b) if isinstance(x, str) and x and isinstance(y, str) and y]
    n = len(pairs)
    if n == 0:
        return None, 0
    po = sum(x == y for x, y in pairs) / n
    ca, cb = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return (1.0 if pe == 1 else (po - pe) / (1 - pe)), n


def norm(s):
    return s.fillna("").astype(str).str.strip()


def score(sheet, key):
    df = sheet.merge(key, on="sample_id", how="left", suffixes=("", "_key"))
    for c in ("a1_is_fp", "a2_is_fp", "a1_purpose", "a2_purpose", "adjudicated_is_fp", "adjudicated_purpose"):
        df[c] = norm(df[c]) if c in df else ""
    for c in ("a1_is_fp", "a2_is_fp", "adjudicated_is_fp"):
        df[c] = df[c].str.lower()
    out = {"n_rows": len(df)}
    k, n = cohen_kappa(df.a1_is_fp, df.a2_is_fp)
    out["kappa_is_fingerprinting"] = {"kappa": k, "n": n}
    k, n = cohen_kappa(df.a1_purpose, df.a2_purpose)
    out["kappa_purpose"] = {"kappa": k, "n": n}

    # Reference label: adjudicated value, or the annotators' shared value when they agree.
    def ref(adj, a1, a2):
        return [x if x else (y if y and y == z else "") for x, y, z in zip(adj, a1, a2)]
    df["ref_is_fp"] = ref(df.adjudicated_is_fp, df.a1_is_fp, df.a2_is_fp)
    df["ref_purpose"] = ref(df.adjudicated_purpose, df.a1_purpose, df.a2_purpose)

    per_tech = {}
    for tech in sorted({t for ts in df.techniques.fillna("") for t in ts.split(",") if t}):
        sub = df[df.techniques.fillna("").str.split(",").map(lambda ts: tech in ts) & df.ref_is_fp.isin(["yes", "no"])]
        per_tech[tech] = {"n_judged": len(sub),
                          "precision": (sub.ref_is_fp == "yes").mean() if len(sub) else None}
    out["detector_precision_by_technique"] = per_tech

    judged = df[df.ref_purpose != ""]
    per_purpose = {}
    for p in sorted(set(judged.purpose_primary)):
        sub = judged[judged.purpose_primary == p]
        per_purpose[p] = {"n_judged": len(sub), "precision": (sub.ref_purpose == p).mean()}
    out["rule_precision_by_purpose"] = per_purpose
    out["rule_accuracy_overall"] = (judged.ref_purpose == judged.purpose_primary).mean() if len(judged) else None
    out["confusion_rule_vs_reference"] = (pd.crosstab(judged.purpose_primary, judged.ref_purpose).to_dict()
                                          if len(judged) else {})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheet")
    ap.add_argument("key")
    ap.add_argument("--out", help="write the JSON summary here (default: next to the sheet)")
    a = ap.parse_args()
    res = score(pd.read_csv(a.sheet), pd.read_csv(a.key))
    out = Path(a.out) if a.out else Path(a.sheet).with_name("agreement.json")
    out.write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
