#!/usr/bin/env python3
"""Analytical Lambda/API Gateway/DynamoDB cost with Init Duration audit."""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import load_yaml, utc_now, write_json


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def select_window(frame, t0_ms, lower_offset_ms, upper_offset_ms,
                  allowed_phases, time_column, label):
    """Select rows whose START/request time falls inside [T0+lower, T0+upper).

    The START timestamp is authoritative. The already classified phase is used
    only for rows without a usable timestamp or when T0 is unavailable. The
    collection window is intentionally wider than the accounting window.
    """
    mask = pd.Series(False, index=frame.index)
    usable_time = None
    if time_column in frame.columns:
        usable_time = pd.to_numeric(frame[time_column], errors="coerce")
        if t0_ms is not None and usable_time.notna().any():
            mask = usable_time.ge(t0_ms + lower_offset_ms) & usable_time.lt(
                t0_ms + upper_offset_ms
            )
        if usable_time.isna().any():
            missing_time = usable_time.isna()
        else:
            missing_time = pd.Series(False, index=frame.index)
    else:
        missing_time = pd.Series(True, index=frame.index)
    if "phase" in frame.columns:
        phase_ok = frame["phase"].astype(str).isin(allowed_phases)
        if usable_time is None or t0_ms is None or not usable_time.notna().any():
            mask = phase_ok
        else:
            mask = mask | (missing_time & phase_ok)
    if usable_time is None and "phase" not in frame.columns:
        raise SystemExit(
            f"Cannot select {label}: no {time_column!r} or 'phase' column in input"
        )
    return frame[mask]


def phase_counts(frame):
    if "phase" not in frame.columns:
        return {}
    return {
        str(key): int(value)
        for key, value in frame["phase"].astype(str).value_counts().items()
    }


def lambda_cost(rows, prices, default_architecture):
    duration = 0.0
    invocations = 0
    by_function = {}
    for row in rows:
        if str(row.get("report_found", "")).lower() not in ("true", "1"):
            continue
        billed = to_float(row.get("billed_duration_ms"))
        memory = to_float(row.get("memory_mb"))
        if billed is None or memory is None:
            continue
        architecture = row.get("architecture") or default_architecture
        rate = (
            prices["lambda_gb_second_usd_arm64"]
            if architecture == "arm64"
            else prices["lambda_gb_second_usd_x86_64"]
        )
        gb_seconds = billed / 1000 * memory / 1024
        cost = gb_seconds * rate
        duration += cost
        invocations += 1
        entry = by_function.setdefault(
            str(row.get("function_name")), {"invocations": 0, "billed_duration_ms": 0.0,
                                            "duration_usd": 0.0}
        )
        entry["invocations"] += 1
        entry["billed_duration_ms"] += billed
        entry["duration_usd"] += cost
    request = invocations * prices["lambda_request_usd_per_million"] / 1e6
    for entry in by_function.values():
        entry["request_usd"] = (
            entry["invocations"] * prices["lambda_request_usd_per_million"] / 1e6
        )
        entry["total_usd"] = entry["duration_usd"] + entry["request_usd"]
    return {
        "lambda_duration_usd": duration,
        "lambda_request_usd": request,
        "lambda_total_usd": duration + request,
        "lambda_invocations_counted": invocations,
        "lambda_by_function": by_function,
    }


def init_duration_audit(rows, tolerance_ms=1):
    checked = []
    violations = []
    for row in rows:
        init = to_float(row.get("init_duration_ms"))
        duration = to_float(row.get("duration_ms"))
        billed = to_float(row.get("billed_duration_ms"))
        if init is None or duration is None or billed is None:
            continue
        checked.append(row)
        if billed + tolerance_ms < duration + init:
            violations.append({
                "request_id": row.get("request_id"),
                "function_name": row.get("function_name"),
                "duration_ms": duration,
                "init_duration_ms": init,
                "billed_duration_ms": billed,
                "billed_minus_duration_and_init_ms": billed - duration - init,
            })
    return {
        "records_with_init_duration": len(checked),
        "records_checked": len(checked),
        "tolerance_ms": tolerance_ms,
        "violations": violations,
        "ok": not violations,
        "note": (
            "For every cold start, checks whether Billed Duration already includes "
            "Duration + Init Duration. The analytical cost uses Billed Duration only."
        ),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="experiments/harness-config.yaml")
    ap.add_argument("--pricing", required=True)
    ap.add_argument("--requests-csv", required=True)
    ap.add_argument("--lambda-csv", required=True)
    ap.add_argument("--composition", required=True, choices=["monolithic", "decomposed"])
    ap.add_argument("--architecture", required=True, choices=["x86_64", "arm64"])
    ap.add_argument("--t0-epoch-ms", type=int, default=None)
    ap.add_argument("--warmup-seconds", type=int, default=120)
    ap.add_argument("--total-seconds", type=int, default=240)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    cfg = load_yaml(a.config)
    prices = json.loads(Path(a.pricing).read_text())
    requests = pd.read_csv(a.requests_csv).fillna("")
    lambdas = pd.read_csv(a.lambda_csv).fillna("")

    def window_cost(requests_window, invocations, label):
        if "request_sent" in requests_window:
            started = requests_window[
                requests_window["request_sent"].astype(str).str.lower().isin(["true", "1"])
            ]
        else:
            started = requests_window
        n = len(started)
        lc = lambda_cost(invocations.to_dict("records"), prices, a.architecture)
        api = n * prices["http_api_usd_per_million_requests_first_tier"] / 1e6
        cost_cfg = cfg["cost"]
        rru = float(cost_cfg.get("ddb_rru_per_external_request", 0)) * n
        wru = float(cost_cfg.get("ddb_wru_per_external_request", 0)) * n
        ddb = (
            rru * prices["dynamodb_standard_usd_per_million_rru"] / 1e6
            + wru * prices["dynamodb_standard_usd_per_million_wru"] / 1e6
        )
        total = lc["lambda_total_usd"] + api + ddb
        return {
            "window": label,
            "external_requests_counted": n,
            **lc,
            "api_gateway_estimated_usd": api,
            "dynamodb_estimated_usd": ddb,
            "app_total_estimated_usd": total,
            "c_req_lambda_usd": lc["lambda_total_usd"] / n if n else None,
            "c_req_app_usd": total / n if n else None,
        }

    total_ms = int(a.total_seconds) * 1000
    warmup_ms = int(a.warmup_seconds) * 1000
    full_requests = select_window(
        requests, a.t0_epoch_ms, 0, total_ms,
        ["warmup", "measurement"], "start_epoch_ms", "full_run requests",
    )
    full_lambdas = select_window(
        lambdas, a.t0_epoch_ms, 0, total_ms,
        ["warmup", "measurement"], "start_timestamp_ms", "full_run invocations",
    )
    measurement_requests = select_window(
        requests, a.t0_epoch_ms, warmup_ms, total_ms,
        ["measurement"], "start_epoch_ms", "measurement requests",
    )
    measurement_lambdas = select_window(
        lambdas, a.t0_epoch_ms, warmup_ms, total_ms,
        ["measurement"], "start_timestamp_ms", "measurement invocations",
    )

    full = window_cost(full_requests, full_lambdas, "full_run")
    measurement = window_cost(
        measurement_requests, measurement_lambdas, "measurement"
    )
    audit = init_duration_audit(full_lambdas.to_dict("records"))
    result = {
        "created_at_utc": utc_now(),
        "composition": a.composition,
        "architecture": a.architecture,
        "full_run": full,
        "measurement": measurement,
        "accounting_windows": {
            "full_run": {
                "lower_offset_ms": 0,
                "upper_offset_ms": total_ms,
                "phases": ["warmup", "measurement"],
            },
            "measurement": {
                "lower_offset_ms": warmup_ms,
                "upper_offset_ms": total_ms,
                "phases": ["measurement"],
            },
            "policy": (
                "Attribute Lambda invocations and external requests by their START "
                "timestamp inside [T0, T0+total). Phase is only a fallback for rows "
                "without a usable timestamp. The wider log-query margin is an "
                "ingestion window, not an accounting window."
            ),
            "t0_epoch_ms": a.t0_epoch_ms,
        },
        "raw_totals": {
            "lambda_rows": len(lambdas),
            "lambda_rows_full_run": len(full_lambdas),
            "lambda_rows_measurement": len(measurement_lambdas),
            "lambda_phase_counts": phase_counts(lambdas),
            "request_rows": len(requests),
            "request_rows_full_run": len(full_requests),
            "request_rows_measurement": len(measurement_requests),
            "request_phase_counts": phase_counts(requests),
        },
        "init_duration_validation": audit,
        "pricing_snapshot": a.pricing,
        "dynamodb_estimation_assumptions": {
            "rru_per_external_request": cfg["cost"].get("ddb_rru_per_external_request"),
            "wru_per_external_request": cfg["cost"].get("ddb_wru_per_external_request"),
            "read_consistency": cfg.get("dynamodb", {}).get("read_consistency"),
            "item_size_bytes": cfg.get("dynamodb", {}).get("document_item_size_bytes"),
            "warning": (
                "DynamoDB costs are estimates until unit consumption and item "
                "size/consistency are verified against the real table."
            ),
        },
    }
    write_json(a.output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
