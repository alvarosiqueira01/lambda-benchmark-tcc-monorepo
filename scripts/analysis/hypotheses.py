#!/usr/bin/env python3
"""Paired, run-level contrasts for the hypotheses described in the protocol."""
import argparse
import itertools
from pathlib import Path

import numpy as np
import pandas as pd
from bootstrap import bootstrap_ci, paired_bootstrap_contrast, paired_bootstrap_ratio


def emit(rows, hypothesis, stratum, method, estimate, low=None, high=None,
         n_pairs=None, note=None):
    rows.append({
        "hypothesis": hypothesis,
        "stratum": stratum,
        "method": method,
        "estimate": estimate,
        "ci95_low": low,
        "ci95_high": high,
        "n_pairs": n_pairs,
        "note": note,
    })


def block_pivot(frame, index, columns, value):
    if "block_id" not in frame:
        frame = frame.assign(block_id=frame.index)
    return frame.pivot_table(
        index="block_id", columns=columns, values=value, aggfunc="median"
    )


def h1(rows, data, n_boot, seed):
    for keys, group in data.groupby(["composition", "architecture", "load_pattern"]):
        pivot = block_pivot(group, "block_id", "memory_mb", "p95_ms").sort_index(axis=1)
        memories = list(pivot.columns)
        for left, right in itertools.pairwise(memories):
            if left not in pivot or right not in pivot:
                continue
            low, high, n = paired_bootstrap_contrast(
                pivot[right], pivot[left], n_boot=n_boot, seed=seed
            )
            emit(
                rows, "H1", str(keys),
                f"paired_delta_median_p95_ms {left}->{right} MB",
                float(pivot[right].median() - pivot[left].median()),
                low, high, n,
                "negative values mean lower p95 at the larger memory",
            )


def h2(rows, data, lmax, n_boot, seed):
    for keys, group in data.groupby(["load_pattern", "composition", "architecture"]):
        memory_rows = []
        for memory, frame in group.groupby("memory_mb"):
            _, high = bootstrap_ci(
                frame["p95_ms"], statistic=np.median, n_boot=n_boot, seed=seed
            )
            memory_rows.append({
                "memory_mb": memory,
                "p95_median": frame["p95_ms"].median(),
                "p95_upper_ci95": high,
                "cost_median": frame["c_req_lambda_usd"].median(),
                "n_runs": len(frame),
            })
        summary = pd.DataFrame(memory_rows).dropna(
            subset=["p95_median", "p95_upper_ci95", "cost_median"]
        )
        if summary.empty:
            continue
        eligible = summary[summary["p95_upper_ci95"] <= lmax]
        if eligible.empty:
            emit(rows, "H2", str(keys), "min median Creq among feasible memories",
                 None, note=f"empty feasible set at Lmax={lmax} ms")
            continue
        best = eligible.sort_values("cost_median").iloc[0]
        emit(
            rows, "H2", str(keys),
            "min median Creq among memories with upper 95% p95 <= Lmax",
            int(best["memory_mb"]),
            note=(
                f"Lmax={lmax} ms; p95_median={best['p95_median']:.3f}; "
                f"p95_upper_ci95={best['p95_upper_ci95']:.3f}; "
                f"cost_median={best['cost_median']:.3e}"
            ),
        )
        # Bootstrap selection frequency over blocks (run-level resampling).
        blocks = sorted(group["block_id"].dropna().unique())
        if len(blocks) < 2:
            continue
        rng = np.random.default_rng(seed)
        frequencies = {int(memory): 0 for memory in summary["memory_mb"]}
        valid_resamples = 0
        by_block = {
            memory: group[group["memory_mb"] == memory].set_index("block_id")
            for memory in summary["memory_mb"]
        }
        for _ in range(n_boot):
            sample = rng.choice(blocks, size=len(blocks), replace=True)
            candidates = []
            for memory, frame in by_block.items():
                available = [block for block in sample if block in frame.index]
                if not available:
                    continue
                frame_sample = frame.loc[available]
                p95 = frame_sample["p95_ms"].median()
                cost = frame_sample["c_req_lambda_usd"].median()
                if pd.notna(p95) and pd.notna(cost) and p95 <= lmax:
                    candidates.append((cost, int(memory)))
            if candidates:
                valid_resamples += 1
                frequencies[min(candidates)[1]] += 1
        if valid_resamples:
            top = sorted(frequencies.items(), key=lambda item: item[1], reverse=True)
            emit(
                rows, "H2", str(keys),
                "bootstrap selection frequency of the lowest-cost feasible memory",
                top[0][0],
                note=(
                    f"Lmax={lmax} ms; valid_resamples={valid_resamples}/{n_boot}; "
                    f"resample feasibility uses point median p95 <= Lmax; "
                    f"selection.csv uses the exact upper 95% CI; frequencies={top[:5]}"
                ),
            )


def h3(rows, data, n_boot, seed):
    for keys, group in data.groupby(["composition", "memory_mb", "load_pattern"]):
        pivot = block_pivot(group, "block_id", "architecture", "c_req_lambda_usd")
        if "arm64" not in pivot or "x86_64" not in pivot:
            continue
        low, high, n = paired_bootstrap_ratio(
            pivot["arm64"], pivot["x86_64"], n_boot=n_boot, seed=seed
        )
        emit(rows, "H3", str(keys), "paired median ratio Creq arm64/x86",
             float(pivot["arm64"].median() / pivot["x86_64"].median()), low, high, n)
        pivot_p95 = block_pivot(group, "block_id", "architecture", "p95_ms")
        if "arm64" in pivot_p95 and "x86_64" in pivot_p95:
            low, high, n = paired_bootstrap_ratio(
                pivot_p95["arm64"], pivot_p95["x86_64"], n_boot=n_boot, seed=seed
            )
            emit(rows, "H3", str(keys), "paired median ratio p95 arm64/x86",
                 float(pivot_p95["arm64"].median() / pivot_p95["x86_64"].median()),
                 low, high, n)


def h4(rows, data, n_boot, seed):
    for keys, group in data.groupby(["composition", "memory_mb", "load_pattern"]):
        pivot_duration = block_pivot(
            group, "block_id", "architecture", "billed_duration_ms_total"
        )
        pivot_cost = block_pivot(group, "block_id", "architecture", "c_req_lambda_usd")
        if all(column in pivot_duration for column in ("arm64", "x86_64")):
            low, high, n = paired_bootstrap_ratio(
                pivot_duration["x86_64"], pivot_duration["arm64"],
                n_boot=n_boot, seed=seed,
            )
            emit(rows, "H4", str(keys),
                 "paired ratio billed duration x86/arm64",
                 float(pivot_duration["x86_64"].median()
                       / pivot_duration["arm64"].median()), low, high, n)
        if all(column in pivot_cost for column in ("arm64", "x86_64")):
            low, high, n = paired_bootstrap_ratio(
                pivot_cost["arm64"], pivot_cost["x86_64"], n_boot=n_boot, seed=seed
            )
            emit(rows, "H4", str(keys), "paired ratio Creq arm64/x86",
                 float(pivot_cost["arm64"].median() / pivot_cost["x86_64"].median()),
                 low, high, n)


def h5_h6(rows, data, n_boot, seed):
    for keys, group in data.groupby(["architecture", "memory_mb", "load_pattern"]):
        pivot_cost = block_pivot(group, "block_id", "composition", "c_req_lambda_usd")
        pivot_p95 = block_pivot(group, "block_id", "composition", "p95_ms")
        if all(column in pivot_cost for column in ("decomposed", "monolithic")):
            low, high, n = paired_bootstrap_contrast(
                pivot_cost["decomposed"], pivot_cost["monolithic"],
                n_boot=n_boot, seed=seed,
            )
            emit(rows, "H5", str(keys), "paired delta Creq decomposed-monolithic",
                 float(pivot_cost["decomposed"].median()
                       - pivot_cost["monolithic"].median()), low, high, n)
        if all(column in pivot_p95 for column in ("decomposed", "monolithic")):
            low, high, n = paired_bootstrap_contrast(
                pivot_p95["decomposed"], pivot_p95["monolithic"],
                n_boot=n_boot, seed=seed,
            )
            emit(rows, "H6", str(keys), "paired delta p95 decomposed-monolithic",
                 float(pivot_p95["decomposed"].median()
                       - pivot_p95["monolithic"].median()), low, high, n)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="experiments/aggregate/runs.csv")
    parser.add_argument("--plan", default="experiments/experiment-plan.yaml")
    parser.add_argument("--out", default="experiments/aggregate/hypothesis-tests.csv")
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    data = pd.read_csv(args.input)
    import yaml

    plan = yaml.safe_load(Path(args.plan).read_text(encoding="utf-8"))
    lmax = float(plan["latency_constraint"]["primary_lmax_ms"])
    data = data.dropna(subset=["p95_ms", "c_req_lambda_usd"])
    rows = []
    h1(rows, data, args.n_boot, args.seed)
    h2(rows, data, lmax, args.n_boot, args.seed)
    h3(rows, data, args.n_boot, args.seed)
    h4(rows, data, args.n_boot, args.seed)
    h5_h6(rows, data, args.n_boot, args.seed)
    table = pd.DataFrame(rows)
    if not table.empty:
        table["multiple_comparison_note"] = (
            "Exploratory family; report effect sizes and intervals. Apply Holm/BH "
            "before making confirmatory claims across the full contrast family."
        )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False)
    print(f"Wrote {len(table)} contrasts to {args.out}")


if __name__ == "__main__":
    main()
