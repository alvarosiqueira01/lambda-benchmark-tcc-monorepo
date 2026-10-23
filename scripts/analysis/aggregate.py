#!/usr/bin/env python3
"""Aggregate valid runs into runs.csv and function_runs.csv."""
import argparse
import json
from pathlib import Path

import pandas as pd


def load_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--phase", choices=["pilot", "campaign", "all"], default="campaign")
    parser.add_argument("--out", default="experiments/aggregate")
    args = parser.parse_args()
    rows = []
    function_rows = []
    if args.runs_dir:
        roots = [Path(args.runs_dir)]
    elif args.phase == "all":
        roots = [Path("experiments/runs/pilot"), Path("experiments/runs/campaign")]
    else:
        roots = [Path("experiments/runs") / args.phase]
    directories = []
    for root in roots:
        if root.exists():
            directories.extend(path for path in sorted(root.iterdir()) if path.is_dir())
    for directory in directories:
        if not directory.is_dir():
            continue
        validation = load_json(directory / "validation.json")
        manifest = load_json(directory / "manifest.json")
        if not validation or not manifest:
            continue
        if validation.get("status") not in ("valid", "valid-system-anomaly"):
            continue
        if not (directory / "requests.csv").exists() or not (directory / "cost.json").exists():
            continue
        condition = manifest.get("condition", {})
        requests = pd.read_csv(directory / "requests.csv").fillna("")
        measurement = requests[requests["phase"] == "measurement"] \
            if "phase" in requests else requests
        started = measurement[
            measurement["request_sent"].astype(str).str.lower().isin(["true", "1"])
        ] if "request_sent" in measurement else measurement
        latencies = pd.to_numeric(started.get("latency_ms"), errors="coerce").dropna()
        statuses = started.get("status_code", pd.Series(dtype=str)).astype(str)
        timeouts = started.get("timeout", pd.Series(dtype=str)).astype(str).str.lower()
        errors = ~statuses.str.fullmatch(r"2\d\d")
        timeout_mask = timeouts.isin(["true", "1"])
        success = started[~(errors | timeout_mask)]
        success_latencies = pd.to_numeric(
            success.get("latency_ms"), errors="coerce"
        ).dropna()
        cost = load_json(directory / "cost.json", {})
        cost_measurement = cost.get("measurement", cost)
        loadgen = load_json(directory / "loadgen-summary.json", {})
        lambdas = pd.read_csv(directory / "lambda-invocations.csv").fillna("") \
            if (directory / "lambda-invocations.csv").exists() else pd.DataFrame()
        invocations = len(lambdas)
        measurement_lambdas = (
            lambdas[lambdas["phase"].astype(str) == "measurement"]
            if "phase" in lambdas else lambdas
        )
        invocations_measurement = len(measurement_lambdas)
        cold_starts = 0
        cold_starts_measurement = 0
        billed_total = 0.0
        init_total = 0.0
        if not lambdas.empty:
            cold_starts = int(
                lambdas["cold_start"].astype(str).str.lower().isin(["true", "1"]).sum()
            )
            cold_starts_measurement = int(
                measurement_lambdas["cold_start"].astype(str).str.lower()
                .isin(["true", "1"]).sum()
            )
            billed_total = pd.to_numeric(
                lambdas.get("billed_duration_ms"), errors="coerce"
            ).sum()
            init_total = pd.to_numeric(
                lambdas.get("init_duration_ms"), errors="coerce"
            ).sum()
        n_started = len(started)
        n_errors = int((errors | timeout_mask).sum()) if n_started else 0
        rows.append({
            "run_id": directory.name,
            "phase": args.phase if args.runs_dir is None else directory.parent.name,
            **condition,
            "block_id": condition.get("block_id", manifest.get("block_id")),
            "status": validation.get("status"),
            "pattern": manifest.get("factors", {}).get("load_pattern"),
            "n_requests": n_started,
            "n_errors": n_errors,
            "n_timeouts": int(timeout_mask.sum()) if n_started else 0,
            "error_rate": n_errors / n_started if n_started else None,
            "timeout_rate": int(timeout_mask.sum()) / n_started if n_started else None,
            "p50_ms": latencies.quantile(.5) if len(latencies) else None,
            "p90_ms": latencies.quantile(.9) if len(latencies) else None,
            "p95_ms": latencies.quantile(.95) if len(latencies) else None,
            "p99_ms": latencies.quantile(.99) if len(latencies) else None,
            "p95_success_ms": success_latencies.quantile(.95)
            if len(success_latencies) else None,
            "mean_ms": latencies.mean() if len(latencies) else None,
            "std_ms": latencies.std(ddof=1) if len(latencies) > 1 else None,
            "new_connection_rate": float(
                started["new_connection"].astype(str).str.lower().isin(["true", "1"]).mean()
            ) if "new_connection" in started and len(started) else None,
            "mean_connect_ms": pd.to_numeric(
                started.get("connect_ms"), errors="coerce"
            ).mean() if "connect_ms" in started else None,
            "mean_tls_ms": pd.to_numeric(
                started.get("tls_ms"), errors="coerce"
            ).mean() if "tls_ms" in started else None,
            "lambda_invocations": invocations,
            "lambda_invocations_measurement": invocations_measurement,
            "lambda_cold_starts": cold_starts,
            "lambda_cold_starts_measurement": cold_starts_measurement,
            "cold_start_rate": (
                cold_starts_measurement / invocations_measurement
                if invocations_measurement else None
            ),
            "billed_duration_ms_total": billed_total,
            "init_duration_ms_total": init_total,
            "dropped_iterations": loadgen.get("dropped_iterations"),
            "t0_source": manifest.get("t0_source"),
            "c_req_lambda_usd": cost_measurement.get("c_req_lambda_usd"),
            "c_req_app_usd": cost_measurement.get("c_req_app_usd"),
            "lambda_total_usd": cost_measurement.get("lambda_total_usd"),
        })
        if not lambdas.empty:
            for function_name, group in lambdas.groupby("function_name"):
                function_rows.append({
                    "run_id": directory.name,
                    "phase": args.phase if args.runs_dir is None else directory.parent.name,
                    **condition,
                    "function_name": function_name,
                    "version": group["version"].iloc[0],
                    "invocations": len(group),
                    "billed_duration_ms": pd.to_numeric(
                        group["billed_duration_ms"], errors="coerce"
                    ).sum(),
                    "duration_ms": pd.to_numeric(
                        group["duration_ms"], errors="coerce"
                    ).sum(),
                    "init_duration_ms": pd.to_numeric(
                        group["init_duration_ms"], errors="coerce"
                    ).sum(),
                    "cold_starts": int(
                        group["cold_start"].astype(str).str.lower().isin(["true", "1"]).sum()
                    ),
                    "max_memory_used_mb": pd.to_numeric(
                        group["max_memory_used_mb"], errors="coerce"
                    ).max(),
                })
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "runs.csv", index=False)
    pd.DataFrame(function_rows).to_csv(out / "function_runs.csv", index=False)
    print(f"Aggregated {len(rows)} valid runs to {out}")


if __name__ == "__main__":
    main()
