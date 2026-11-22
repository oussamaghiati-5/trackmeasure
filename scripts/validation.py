#!/usr/bin/env python3
"""Manual validation of the fingerprinting heuristics (precision per technique).

1. Draw a stratified random sample of baseline detections to label by hand:
     python scripts/validation.py sample results/fp_scripts.csv --per-technique 15 --out results/validation_sample.csv
2. Open each script_url, decide whether it fingerprints, and fill `label` with
   fp / not_fp / unsure (and `notes`). Two group members should label independently.
3. Score it:
     python scripts/validation.py score results/validation_sample.csv
Reports precision = fp / (fp + not_fp) with a Wilson 95% interval; unsure rows are excluded.
Recall cannot be estimated this way: the heuristics only show what they detect.
"""
import argparse
import math

import pandas as pd

LABELS = ("fp", "not_fp", "unsure")


def wilson(k, n, z=1.96):
    if not n:
        return math.nan, math.nan
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def sample(fp_scripts, run, k, seed):
    det = fp_scripts[fp_scripts.run == run]
    uniq = det.groupby(["technique", "script_url"], dropna=False).agg(
        script_domain=("script_domain", "first"), third_party=("third_party", "first"),
        example_site=("site", "first"), sites=("site", "nunique")).reset_index()
    parts = [g.sample(min(k, len(g)), random_state=seed) for _, g in uniq.groupby("technique")]
    out = pd.concat(parts, ignore_index=True) if parts else uniq
    return out.assign(label="", notes="")


def score(labelled):
    lab = labelled.assign(label=labelled["label"].fillna("").str.strip().str.lower())
    bad = sorted(set(lab.label) - set(LABELS) - {""})
    if bad:
        raise SystemExit(f"Unknown labels {bad}; use one of {LABELS}")
    rows = []
    for tech, g in [*lab.groupby("technique"), ("ALL", lab)]:
        k, n = int((g.label == "fp").sum()), int(g.label.isin(["fp", "not_fp"]).sum())
        lo, hi = wilson(k, n)
        rows.append(dict(technique=tech, labelled=n, unsure=int((g.label == "unsure").sum()),
                         unlabelled=int((g.label == "").sum()), precision=k / n if n else math.nan,
                         ci_low=lo, ci_high=hi))
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("fp_scripts", nargs="?", default="results/fp_scripts.csv")
    s.add_argument("--run", default="baseline")
    s.add_argument("--per-technique", type=int, default=15)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--out", default="results/validation_sample.csv")
    c = sub.add_parser("score")
    c.add_argument("labelled", nargs="?", default="results/validation_sample.csv")
    a = ap.parse_args()
    if a.cmd == "sample":
        out = sample(pd.read_csv(a.fp_scripts), a.run, a.per_technique, a.seed)
        out.to_csv(a.out, index=False)
        print(f"{len(out)} detections to label written to {a.out}")
    else:
        print(score(pd.read_csv(a.labelled)).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
