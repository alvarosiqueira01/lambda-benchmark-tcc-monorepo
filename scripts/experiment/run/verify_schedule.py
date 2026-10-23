#!/usr/bin/env python3
"""Verify arrival counts, per-window counts, offsets and dropped iterations."""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import load_yaml, utc_now, write_json


def metric_count(metrics, name):
    metric = (metrics or {}).get(name) or {}
    values = metric.get("values") or {}
    if "count" in values:
        return values["count"]
    if "value" in values:
        return values["value"]
    return 0


def load_requests(path):
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key in ("offset_ms", "start_epoch_ms", "latency_ms", "status_code"):
            try:
                row[key] = float(row[key])
            except (KeyError, TypeError, ValueError):
                row[key] = None
    return rows


def add_check(checks, name, ok, detail):
    checks.append({"name": name, "passed": bool(ok), "detail": detail})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="experiments/experiment-plan.yaml")
    ap.add_argument("--requests-csv", required=True)
    ap.add_argument("--summary-json", required=True)
    ap.add_argument("--pattern", required=True,
                    choices=["constant", "burst", "sparse_miniburst"])
    ap.add_argument("--t0-epoch-ms", type=int, default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    plan = load_yaml(a.plan)
    spec = plan["load_patterns"][a.pattern]
    schedule = plan.get("arrival_schedule", {})
    rows = load_requests(a.requests_csv)
    summary = json.loads(Path(a.summary_json).read_text())
    metrics = summary.get("metrics", {})

    t0 = a.t0_epoch_ms or summary.get("t0_epoch_ms")
    if t0 is None:
        t0_values = [row.get("t0_epoch_ms") for row in rows if row.get("t0_epoch_ms")]
        t0 = int(t0_values[0]) if t0_values else None
    if t0 is None and rows:
        t0 = int(min(row["start_epoch_ms"] - row["latency_ms"] for row in rows))
    if t0 is None:
        raise SystemExit("Could not determine T0 from requests or k6 summary")
    t0 = int(t0)

    for row in rows:
        if row.get("offset_ms") is None and row.get("start_epoch_ms") is not None:
            row["offset_ms"] = row["start_epoch_ms"] - t0

    expected_total = int(spec["expected_requests_total"])
    expected_measurement = int(spec["expected_requests_measurement"])
    tolerance = float(schedule.get("constant_tolerance_fraction", 0.05))
    checks = []
    windows = []
    active_jitter_ms = None

    observed_total = len(rows)
    observed_measurement = sum(1 for row in rows if row.get("phase") == "measurement")
    if observed_measurement == 0:
        warmup = int(plan["warmup_seconds"]) * 1000
        total = int(plan["total_duration_seconds"]) * 1000
        observed_measurement = sum(
            1 for row in rows
            if row.get("offset_ms") is not None and warmup <= row["offset_ms"] < total
        )

    def within(observed, expected, fraction):
        margin = math.ceil(expected * fraction)
        return expected - margin <= observed <= expected + margin, margin

    ok, margin = within(observed_total, expected_total, tolerance)
    add_check(checks, "arrival_total", ok,
              f"observed={observed_total}, expected={expected_total}, tolerance=+/-{margin}")
    ok, margin = within(observed_measurement, expected_measurement, tolerance)
    add_check(checks, "arrival_measurement", ok,
              f"observed={observed_measurement}, expected={expected_measurement}, tolerance=+/-{margin}")

    started_metric = metric_count(metrics, "external_http_started")
    add_check(checks, "k6_started_counter_matches_csv", int(started_metric) == observed_total,
              f"metric={int(started_metric)}, csv={observed_total}")

    sample_t0_tags = {
        str(row.get("sample_t0_epoch_ms"))
        for row in rows
        if row.get("sample_t0_epoch_ms") not in (None, "", "nan")
    }
    add_check(checks, "t0_tag_consistent_across_samples", len(sample_t0_tags) <= 1,
              f"distinct sample t0 tags={sorted(sample_t0_tags)}")

    dropped = int(metric_count(metrics, "dropped_iterations"))
    maximum_dropped = int(schedule.get("maximum_dropped_iterations", 0))
    add_check(checks, "dropped_iterations", dropped <= maximum_dropped,
              f"observed={dropped}, maximum={maximum_dropped}")
    scheduled = int(metric_count(metrics, "scheduled_iterations"))

    if a.pattern == "constant":
        rate = int(spec["rate_per_second"])
        buckets = {}
        for row in rows:
            offset = row.get("offset_ms")
            if offset is None or offset < 0:
                continue
            bucket = int(offset // 1000)
            buckets[bucket] = buckets.get(bucket, 0) + 1
        bad_windows = []
        for bucket in range(int(plan["total_duration_seconds"])):
            observed = buckets.get(bucket, 0)
            window_ok, window_margin = within(observed, rate, tolerance)
            if not window_ok:
                bad_windows.append({"second": bucket, "observed": observed,
                                    "expected": rate, "margin": window_margin})
        for bucket in sorted(buckets):
            windows.append({"second": bucket, "observed": buckets[bucket], "expected": rate})
        add_check(checks, "constant_per_second_windows", not bad_windows,
                  f"{len(bad_windows)} seconds outside tolerance: {bad_windows[:10]}")
        add_check(checks, "no_requests_outside_run",
                  all(0 <= row["offset_ms"] < int(plan["total_duration_seconds"]) * 1000
                      for row in rows if row.get("offset_ms") is not None),
                  "all request starts inside [T0, T0+240s)")

    elif a.pattern == "burst":
        cycle_ms = int(spec["cycle_period_seconds"]) * 1000
        active_ms = int(spec["active_duration_seconds"]) * 1000
        # The last scheduled request is at 990 ms. Network/timer jitter can push
        # it past the nominal 1,000 ms boundary without breaking the schedule,
        # so use the same frozen jitter tolerance as the sparse pattern.
        active_jitter_ms = int(schedule.get("sparse_timing_jitter_ms", 50))
        active_limit_ms = active_ms + active_jitter_ms
        expected_per_cycle = int(spec["expected_requests_per_cycle"])
        window_tolerance = float(schedule.get("burst_per_window_tolerance_fraction", tolerance))
        cycles = {}
        idle_violations = []
        offset_violations = []
        for row in rows:
            offset = row.get("offset_ms")
            if offset is None or offset < 0:
                continue
            cycle = int(offset // cycle_ms)
            relative = offset - cycle * cycle_ms
            cycles.setdefault(cycle, {"active": 0, "idle": 0})
            if relative < active_limit_ms:
                cycles[cycle]["active"] += 1
            else:
                cycles[cycle]["idle"] += 1
                idle_violations.append({"offset_ms": offset, "cycle": cycle})
        bad_windows = []
        for cycle in range(int(spec["total_cycles"])):
            observed = cycles.get(cycle, {}).get("active", 0)
            window_ok, window_margin = within(observed, expected_per_cycle, window_tolerance)
            if not window_ok:
                bad_windows.append({"cycle": cycle, "observed": observed,
                                    "expected": expected_per_cycle, "margin": window_margin})
            windows.append({"cycle": cycle, "active_observed": observed,
                            "active_expected": expected_per_cycle,
                            "idle_observed": cycles.get(cycle, {}).get("idle", 0)})
        add_check(checks, "burst_active_windows", not bad_windows,
                  f"{len(bad_windows)} active windows outside tolerance: {bad_windows[:10]}")
        add_check(checks, "burst_idle_windows_empty", not idle_violations,
                  f"{len(idle_violations)} requests in idle windows "
                  f"(active window + {active_jitter_ms} ms jitter)")
        add_check(checks, "burst_offsets_inside_active_window", not offset_violations,
                  f"{len(offset_violations)} offsets outside active window")
        add_check(checks, "no_requests_outside_run",
                  all(0 <= row["offset_ms"] < int(plan["total_duration_seconds"]) * 1000
                      for row in rows if row.get("offset_ms") is not None),
                  "all request starts inside [T0, T0+240s)")

    else:
        cycle_ms = int(spec["cycle_period_seconds"]) * 1000
        active_ms = int(spec["active_duration_seconds"]) * 1000
        expected_per_cycle = int(spec["requests_per_active_window"])
        expected_offsets = [int(x) for x in spec["offsets_within_window_ms"]]
        jitter = int(schedule.get("sparse_timing_jitter_ms", 50))
        count_tolerance = float(schedule.get("sparse_count_tolerance_fraction", 0.0))
        cycles = {}
        idle_violations = []
        timing_violations = []
        for row in rows:
            offset = row.get("offset_ms")
            if offset is None or offset < 0:
                continue
            cycle = int(offset // cycle_ms)
            relative = offset - cycle * cycle_ms
            cycles.setdefault(cycle, {"offsets": [], "idle": 0})
            if relative < active_ms:
                cycles[cycle]["offsets"].append(relative)
            else:
                cycles[cycle]["idle"] += 1
                idle_violations.append({"offset_ms": offset, "cycle": cycle})
        bad_windows = []
        for cycle in range(int(spec["total_cycles"])):
            observed_offsets = sorted(cycles.get(cycle, {}).get("offsets", []))
            observed = len(observed_offsets)
            window_ok, window_margin = within(observed, expected_per_cycle, count_tolerance)
            if not window_ok:
                bad_windows.append({"cycle": cycle, "observed": observed,
                                    "expected": expected_per_cycle, "margin": window_margin})
            if len(observed_offsets) == len(expected_offsets):
                for actual, expected in zip(observed_offsets, expected_offsets):
                    if abs(actual - expected) > jitter:
                        timing_violations.append({
                            "cycle": cycle, "actual_offset_ms": round(actual, 3),
                            "expected_offset_ms": expected, "jitter_ms": jitter,
                        })
            windows.append({"cycle": cycle, "observed_offsets_ms": observed_offsets,
                            "expected_offsets_ms": expected_offsets,
                            "idle_observed": cycles.get(cycle, {}).get("idle", 0)})
        add_check(checks, "sparse_windows_count", not bad_windows,
                  f"{len(bad_windows)} cycles outside tolerance: {bad_windows[:10]}")
        add_check(checks, "sparse_idle_windows_empty", not idle_violations,
                  f"{len(idle_violations)} requests in idle windows")
        add_check(checks, "sparse_offsets_within_jitter", not timing_violations,
                  f"{len(timing_violations)} offsets outside +/-{jitter} ms: {timing_violations[:10]}")
        add_check(checks, "no_requests_outside_run",
                  all(0 <= row["offset_ms"] < int(plan["total_duration_seconds"]) * 1000
                      for row in rows if row.get("offset_ms") is not None),
                  "all request starts inside [T0, T0+240s)")

    result = {
        "created_at_utc": utc_now(),
        "pattern": a.pattern,
        "t0_epoch_ms": t0,
        "t0_source": summary.get("t0_source"),
        "expected_total": expected_total,
        "observed_total": observed_total,
        "expected_measurement": expected_measurement,
        "observed_measurement": observed_measurement,
        "dropped_iterations": dropped,
        "scheduled_iterations": scheduled,
        "active_window_jitter_ms": active_jitter_ms,
        "checks": checks,
        "windows": windows,
        "ok": all(check["passed"] for check in checks),
    }
    write_json(a.out, result)
    print(json.dumps({k: result[k] for k in (
        "pattern", "t0_epoch_ms", "observed_total", "expected_total",
        "observed_measurement", "expected_measurement", "dropped_iterations", "ok",
    )}, indent=2))
    if not result["ok"]:
        raise SystemExit("Arrival schedule verification failed")


if __name__ == "__main__":
    main()
