#!/usr/bin/env python3
"""Select the lowest-cost feasible configuration for each load pattern and Lmax."""
import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import yaml


def utc_now():
    return datetime.now(UTC).isoformat()


def select_for_limit(data, lmax):
    eligible = data[data["p95_ms_ci95_high"] <= lmax].copy()
    if eligible.empty:
        return None, eligible
    eligible = eligible.sort_values(
        ["c_req_lambda_usd_median", "p95_ms_ci95_high"], ascending=[True, True]
    )
    return eligible.iloc[0], eligible


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="experiments/aggregate/condition-summary.csv")
    parser.add_argument("--plan", default="experiments/experiment-plan.yaml")
    parser.add_argument("--out-dir", default="experiments/aggregate")
    args = parser.parse_args()
    plan = yaml.safe_load(Path(args.plan).read_text(encoding="utf-8"))
    data = pd.read_csv(args.input)
    required = [
        "composition", "architecture", "memory_mb", "load_pattern",
        "p95_ms_ci95_high", "c_req_lambda_usd_median",
    ]
    missing = [column for column in required if column not in data]
    if missing:
        raise SystemExit(f"condition-summary.csv missing columns: {missing}")

    primary = float(plan["latency_constraint"]["primary_lmax_ms"])
    limits = [primary, *[float(x) for x in plan["latency_constraint"].get(
        "sensitivity_lmax_ms", [])]]
    rows = []
    detail = []
    for load_pattern, group in data.groupby("load_pattern"):
        for lmax in limits:
            best, eligible = select_for_limit(group, lmax)
            row = {
                "load_pattern": load_pattern,
                "lmax_ms": lmax,
                "primary_limit": lmax == primary,
                "eligible_configurations": len(eligible),
                "empty_set": best is None,
                "selected_composition": None if best is None else best["composition"],
                "selected_architecture": None if best is None else best["architecture"],
                "selected_memory_mb": None if best is None else int(best["memory_mb"]),
                "selected_p95_upper_ci_ms": None if best is None
                else float(best["p95_ms_ci95_high"]),
                "selected_c_req_lambda_median": None if best is None
                else float(best["c_req_lambda_usd_median"]),
                "selected_c_req_lambda_ci95_low": None if best is None
                else float(best.get("c_req_lambda_usd_ci95_low", float("nan"))),
                "selected_c_req_lambda_ci95_high": None if best is None
                else float(best.get("c_req_lambda_usd_ci95_high", float("nan"))),
                "n_runs": None if best is None else int(best.get("n_runs", 0)),
            }
            rows.append(row)
            for _, item in eligible.iterrows():
                detail.append({
                    "load_pattern": load_pattern,
                    "lmax_ms": lmax,
                    "composition": item["composition"],
                    "architecture": item["architecture"],
                    "memory_mb": int(item["memory_mb"]),
                    "p95_upper_ci_ms": float(item["p95_ms_ci95_high"]),
                    "c_req_lambda_median": float(item["c_req_lambda_usd_median"]),
                })
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "selection.csv", index=False)
    pd.DataFrame(detail).to_csv(out / "selection-eligible.csv", index=False)
    payload = {
        "created_at_utc": utc_now(),
        "primary_lmax_ms": primary,
        "sensitivity_lmax_ms": [float(x) for x in plan["latency_constraint"].get(
            "sensitivity_lmax_ms", [])],
        "rule": "upper 95% CI of run-level p95 <= Lmax; minimum median Creq among eligible",
        "selection": rows,
        "empty_sets": [row for row in rows if row["empty_set"]],
        "note": (
            "An empty set means no tested configuration met the limit; the protocol "
            "must not relax the limit after observing results."
        ),
    }
    (out / "selection.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
