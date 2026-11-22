#!/usr/bin/env python3
"""Paired statistics against the noise floor (run after analyze.py).

For each condition, compares every site's survival with the same site's survival in the
noise-floor run (baseline2: an identical repeat of the baseline). Reports the median
paired difference with a bootstrap 95% CI and an exact two-sided sign test, plus each
condition's mean survival with a bootstrap 95% CI. Applied to fingerprint survival
(survival) and to exfiltration survival (exfil_survival).

  python scripts/stats.py results/comparison.csv --noise baseline2 --out results/stats.csv
"""
import argparse
import math

import numpy as np
import pandas as pd

METRICS = ("survival", "exfil_survival")


def bootstrap_ci(values, stat=np.median, n=10000, seed=0, alpha=0.05):
    v = np.asarray(values, float)
    if not len(v):
        return math.nan, math.nan
    samples = stat(np.random.default_rng(seed).choice(v, size=(n, len(v)), replace=True), axis=1)
    return float(np.quantile(samples, alpha / 2)), float(np.quantile(samples, 1 - alpha / 2))


def sign_test(diffs):
    """Exact two-sided sign test; ties are dropped."""
    d = [x for x in diffs if x != 0]
    n, k = len(d), sum(1 for x in d if x > 0)
    if not n:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(k, n - k) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired(cmp, metric, noise):
    if metric not in cmp.columns:
        return []
    base = cmp[cmp.run == noise].set_index("site")[metric].dropna()
    rows = []
    for run, g in cmp.groupby("run"):
        s = g.set_index("site")[metric].dropna()
        lo, hi = bootstrap_ci(s.values, np.mean)
        row = dict(metric=metric, run=run, sites=len(s), mean=s.mean() if len(s) else math.nan,
                   mean_ci_low=lo, mean_ci_high=hi)
        if run != noise:
            common = s.index.intersection(base.index)
            diff = (s[common] - base[common]).values
            dlo, dhi = bootstrap_ci(diff)
            row.update(paired_sites=len(common),
                       median_diff_vs_noise=float(np.median(diff)) if len(diff) else math.nan,
                       diff_ci_low=dlo, diff_ci_high=dhi, sign_test_p=sign_test(diff))
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("comparison", nargs="?", default="results/comparison.csv")
    ap.add_argument("--noise", default="baseline2", help="noise-floor run label")
    ap.add_argument("--out", default="results/stats.csv")
    a = ap.parse_args()
    cmp = pd.read_csv(a.comparison)
    if a.noise not in set(cmp.run):
        raise SystemExit(f"Noise-floor run '{a.noise}' not in {a.comparison}; run it before interpreting survival.")
    out = pd.DataFrame([r for m in METRICS for r in paired(cmp, m, a.noise)])
    out.to_csv(a.out, index=False)
    print(out.round(3).to_string(index=False))
    print(f"\nWritten to {a.out}. Negative median_diff_vs_noise = less survives than between two identical crawls.")


if __name__ == "__main__":
    main()
