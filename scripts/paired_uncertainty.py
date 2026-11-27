#!/usr/bin/env python3
"""Bootstrap paired site-level differences against the repeat baseline.

Usage: python scripts/paired_uncertainty.py results/BATCH/comparison.csv
"""
import argparse
import csv
import json
import random
import statistics
from pathlib import Path


def survival(rows):
    denominator = sum(int(row["fp_pairs_baseline"]) for row in rows)
    return (sum(int(row["fp_pairs_surviving"]) for row in rows) / denominator
            if denominator else None)


def summarize(rows, reference, repetitions=2000):
    domain_deltas = [int(row["tp_domains_run"]) -
                     int(reference[row["site"]]["tp_domains_run"])
                     for row in rows if row["site"] in reference]
    domain_ci = None
    if domain_deltas:
        domain_rng = random.Random(1)
        domain_draws = sorted(statistics.median(
            [domain_deltas[domain_rng.randrange(len(domain_deltas))]
             for _ in domain_deltas]) for _ in range(repetitions))
        domain_ci = [domain_draws[int(.025 * repetitions)],
                     domain_draws[int(.975 * repetitions) - 1]]
    matched = [(reference[row["site"]], row) for row in rows
               if row["site"] in reference and
               int(row["fp_pairs_baseline"]) > 0 and
               int(reference[row["site"]]["fp_pairs_baseline"]) > 0]
    if not matched:
        return {"sites": 0, "survival": None, "repeat_survival": None,
                "difference_pp": None, "ci95_pp": None,
                "tp_domain_sites": len(domain_deltas),
                "tp_domain_median_delta": statistics.median(domain_deltas)
                if domain_deltas else None, "tp_domain_ci95": domain_ci}
    repeat_rate = survival([pair[0] for pair in matched])
    condition_rate = survival([pair[1] for pair in matched])
    rng = random.Random(0)
    draws = []
    for _ in range(repetitions):
        sample = [matched[rng.randrange(len(matched))] for _ in matched]
        draws.append(100 * (survival([pair[1] for pair in sample]) -
                            survival([pair[0] for pair in sample])))
    draws.sort()
    return {"sites": len(matched), "survival": condition_rate,
            "repeat_survival": repeat_rate,
            "difference_pp": 100 * (condition_rate - repeat_rate),
            "ci95_pp": [draws[int(.025 * repetitions)],
                        draws[int(.975 * repetitions) - 1]],
            "tp_domain_sites": len(domain_deltas),
            "tp_domain_median_delta": statistics.median(domain_deltas),
            "tp_domain_ci95": domain_ci}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comparison_csv", type=Path)
    args = parser.parse_args()
    with args.comparison_csv.open(newline="", encoding="utf-8") as stream:
        data = list(csv.DictReader(stream))
    reference = {row["site"]: row for row in data if row["run"] == "baseline2"}
    result = {
        "_meta": {
            "pair_definition": ["site", "script_domain", "technique"],
            "note": "Generic purpose-agnostic cross-check. The paper uses purpose_results.json, "
                    "whose pair key also includes purpose_primary; its All-FP estimates differ "
                    "slightly by construction."},
        **{run: summarize([row for row in data if row["run"] == run], reference)
           for run in sorted({row["run"] for row in data} - {"baseline2"})}}
    output = args.comparison_csv.with_name("paired_uncertainty.json")
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
