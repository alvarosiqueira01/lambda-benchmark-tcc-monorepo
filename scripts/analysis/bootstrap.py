#!/usr/bin/env python3
"""Run-level bootstrap utilities and condition summaries."""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def bootstrap_ci(values, statistic=np.median, n_boot=10000, seed=42):
    x = np.asarray(pd.Series(values).dropna(), dtype=float)
    if len(x) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    samples = np.empty(n_boot)
    for i in range(n_boot):
        samples[i] = statistic(rng.choice(x, size=len(x), replace=True))
    return tuple(np.quantile(samples, [.025, .975]))


def paired_bootstrap_contrast(left, right, statistic=None, n_boot=10000, seed=42):
    """Bootstrap a paired contrast after aligning observations by index.

    left and right must be Series/arrays with the same index (e.g. block_id).
    The default statistic is the difference of medians (left - right).
    """
    left = pd.Series(left)
    right = pd.Series(right)
    paired = pd.concat([left.rename("left"), right.rename("right")], axis=1).dropna()
    if len(paired) < 2:
        return (float("nan"), float("nan"), 0)
    if statistic is None:
        statistic = lambda a, b: float(np.median(a) - np.median(b))
    rng = np.random.default_rng(seed)
    values = np.empty(n_boot)
    a = paired["left"].to_numpy(dtype=float)
    b = paired["right"].to_numpy(dtype=float)
    for i in range(n_boot):
        indices = rng.integers(0, len(paired), size=len(paired))
        values[i] = statistic(a[indices], b[indices])
    return (*np.quantile(values, [.025, .975]), len(paired))


def paired_bootstrap_ratio(left, right, n_boot=10000, seed=42):
    def ratio(a, b):
        denominator = np.median(b)
        return float(np.median(a) / denominator) if denominator else float("nan")

    return paired_bootstrap_contrast(left, right, statistic=ratio, n_boot=n_boot, seed=seed)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="experiments/aggregate/runs.csv")
    parser.add_argument("--out", default="experiments/aggregate/condition-summary.csv")
    parser.add_argument("--n-boot", type=int, default=10000)
    args = parser.parse_args()
    df = pd.read_csv(args.input)
    factors = ["composition", "architecture", "memory_mb", "load_pattern"]
    rows = []
    for key, group in df.groupby(factors, dropna=False):
        row = dict(zip(factors, key))
        row["n_runs"] = len(group)
        for metric in ["p95_ms", "c_req_lambda_usd", "c_req_app_usd",
                       "error_rate", "cold_start_rate", "dropped_iterations"]:
            if metric not in group:
                continue
            values = group[metric].dropna()
            row[f"{metric}_median"] = values.median() if len(values) else None
            row[f"{metric}_mean"] = values.mean() if len(values) else None
            low, high = bootstrap_ci(values, n_boot=args.n_boot)
            row[f"{metric}_ci95_low"] = low
            row[f"{metric}_ci95_high"] = high
        rows.append(row)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.out, index=False)


if __name__ == "__main__":
    main()
