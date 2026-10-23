#!/usr/bin/env python3
"""Classify a run as valid, invalid-apparatus or valid-system-anomaly."""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import load_yaml, parse_utc, utc_now, write_json


def metric_count(metrics, name):
    metric = (metrics or {}).get(name) or {}
    values = metric.get("values") or {}
    if "count" in values:
        return values["count"]
    if "value" in values:
        return values["value"]
    return 0


def as_bool(value):
    return str(value).lower() in ("true", "1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="experiments/experiment-plan.yaml")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--expected-requests", type=int, default=None)
    ap.add_argument("--pattern", default=None,
                    choices=["constant", "burst", "sparse_miniburst"])
    ap.add_argument("--composition", required=True,
                    choices=["monolithic", "decomposed"])
    ap.add_argument("--target-verification", required=True)
    ap.add_argument("--concurrent-run-detected", action="store_true")
    ap.add_argument("--retention-days", type=int, default=None)
    ap.add_argument("--max-clock-offset-ms", type=float, default=50.0)
    a = ap.parse_args()
    plan = load_yaml(a.plan)
    d = Path(a.run_dir)
    checks = []
    issues = []
    warnings = []

    def check(name, ok, detail, severity="invalid"):
        checks.append({"name": name, "passed": bool(ok), "detail": detail,
                       "severity": severity})
        if not ok:
            target = issues if severity == "invalid" else warnings
            target.append({"check": name, "detail": detail, "severity": severity})

    required = [
        "manifest.json", "requests.csv", "k6-raw.json", "k6-summary.json",
        "loadgen-summary.json", "generator-timing.json", "schedule-verification.json",
        "lambda-invocations.csv", "lambda-collection-status.json", "cold-reset.json",
        "cost.json", "target-url.json", "load-test-status.json",
        "generator-resources.csv", "generator-resources-status.json",
        "preflight.json", "concurrency-check.json",
        "cloudwatch-metrics.csv", "cloudwatch-collection-status.json",
    ]
    for filename in required:
        check(f"artifact:{filename}", (d / filename).exists(), f"{filename} exists")

    manifest = {}
    if (d / "manifest.json").exists():
        manifest = json.loads((d / "manifest.json").read_text())
    condition = manifest.get("condition", {}) or {}
    pattern = a.pattern or condition.get("load_pattern")
    check("condition_composition", condition.get("composition") == a.composition,
          f"manifest={condition.get('composition')} vs CLI={a.composition}")
    check("condition_pattern_present", pattern in plan["load_patterns"],
          f"pattern={pattern}")
    if pattern not in plan["load_patterns"]:
        pattern = "constant"

    target_info = json.loads(Path(a.target_verification).read_text()) \
        if Path(a.target_verification).exists() else None
    target_ok = bool(target_info and target_info.get("ok"))
    target_composition_ok = bool(
        target_info and target_info.get("composition") == a.composition
    )
    check("target_verification_ok", target_ok, f"verify_target ok={target_ok}")
    check("target_composition_match", target_composition_ok,
          f"target-verification composition="
          f"{target_info.get('composition') if target_info else None} "
          f"vs condition={a.composition}")
    preflight = json.loads((d / "preflight.json").read_text()) \
        if (d / "preflight.json").exists() else {}
    check("preflight_ok", preflight.get("ok") is True,
          f"ok={preflight.get('ok')}, checks={preflight.get('checks')}")
    check("preflight_phase_matches", preflight.get("phase") == manifest.get("phase"),
          f"preflight={preflight.get('phase')}, manifest={manifest.get('phase')}")
    equivalence = preflight.get("functional_equivalence") or {}
    check("functional_equivalence_verified", equivalence.get("ok") is True,
          f"equivalence_ok={equivalence.get('ok')}")

    concurrency = json.loads((d / "concurrency-check.json").read_text()) \
        if (d / "concurrency-check.json").exists() else {}
    check("concurrent_run_artifact_present", bool(concurrency),
          "concurrency-check.json is required")
    check("no_concurrent_run",
          not a.concurrent_run_detected
          and concurrency.get("ok") is True
          and concurrency.get("concurrent_run_detected") is False,
          f"cli_flag={a.concurrent_run_detected}, "
          f"artifact_ok={concurrency.get('ok')}, "
          f"matches={concurrency.get('matches')}")

    retention = manifest.get("log_retention") or {}
    expected_retention = a.retention_days or int(
        (manifest.get("log_retention_policy") or {}).get("expected_days", 90)
    )
    retention_values = [
        item.get("retention_in_days") for item in retention.values()
        if isinstance(item, dict) and not item.get("error")
    ]
    check("log_retention_configured",
          bool(retention)
          and len(retention_values) == len(retention)
          and all(value == expected_retention for value in retention_values),
          f"expected={expected_retention}, groups={retention}")

    cloudwatch_status = json.loads(
        (d / "cloudwatch-collection-status.json").read_text()
    ) if (d / "cloudwatch-collection-status.json").exists() else {}
    api_metrics = cloudwatch_status.get("api") or {}
    check("cloudwatch_metrics_present",
          cloudwatch_status.get("status") in ("COMPLETE", "PARTIAL"),
          f"status={cloudwatch_status.get('status')}")
    check("api_gateway_metrics_present", api_metrics.get("available") is True,
          f"api={api_metrics}", severity="warning")

    reset = json.loads((d / "cold-reset.json").read_text()) \
        if (d / "cold-reset.json").exists() else {}
    condition_memory = condition.get("memory_mb")
    condition_architecture = condition.get("architecture")
    reset_functions = reset.get("functions", [])
    check("cold_reset_present", bool(reset_functions),
          f"{len(reset_functions)} functions reset")
    for function in reset_functions:
        name = function.get("function_name")
        check(f"reset_version_is_new:{name}", function.get("version_is_new") is True,
              f"previous={function.get('previous_version')}, "
              f"published={function.get('version')}")
        check(f"reset_memory_matches:{name}",
              int(function.get("memory_mb", -1)) == int(condition_memory or -2),
              f"reset={function.get('memory_mb')}, condition={condition_memory}")
        check(f"reset_architecture_matches:{name}",
              function.get("architecture") == condition_architecture,
              f"reset={function.get('architecture')}, condition={condition_architecture}")
        check(f"reset_alias_matches:{name}",
              str(function.get("version")) == str(function.get("alias_version")),
              f"version={function.get('version')}, alias={function.get('alias_version')}")

    collection_status = json.loads((d / "lambda-collection-status.json").read_text()) \
        if (d / "lambda-collection-status.json").exists() else {}
    check("lambda_collection_complete",
          collection_status.get("status") == "COMPLETE",
          str(collection_status.get("status")))
    check("lambda_reports_paired", collection_status.get("reports_missing", 1) == 0,
          f"reports_missing={collection_status.get('reports_missing')}")

    generator_timing = json.loads((d / "generator-timing.json").read_text()) \
        if (d / "generator-timing.json").exists() else {}
    timing_clock = generator_timing.get("clock") or {}
    clock_checks = timing_clock.get("checks") or {}
    check("clock_offset_verified",
          timing_clock.get("ok") is True
          and clock_checks.get("offset_within_tolerance") is True
          and float(timing_clock.get("max_observed_offset_ms") or 0)
          <= float(a.max_clock_offset_ms),
          f"clock={timing_clock}")

    t0_ms = generator_timing.get("t0_epoch_ms")
    t0_source = generator_timing.get("t0_source") or ""
    valid_t0_sources = {
        "k6_exec_scenario_startTime", "k6_raw_gauge", "k6_raw_sample_tag",
    }
    check("t0_recorded_by_generator",
          bool(t0_ms) and t0_source in valid_t0_sources,
          f"t0={t0_ms}, source={t0_source}, accepted={sorted(valid_t0_sources)}",
          severity="invalid")
    reset_completed = reset.get("reset_completed_at_utc")
    if t0_ms and reset_completed:
        check("t0_after_cold_reset",
              int(t0_ms) >= int(parse_utc(reset_completed).timestamp() * 1000),
              f"t0={t0_ms}, reset_completed={reset_completed}", severity="invalid")

    requests = pd.read_csv(d / "requests.csv").fillna("") \
        if (d / "requests.csv").exists() else pd.DataFrame()
    if not requests.empty:
        if "request_sent" in requests:
            started = requests[
                requests["request_sent"].astype(str).str.lower().isin(["true", "1"])
            ]
        else:
            started = requests
        n_actual = len(started)
    else:
        started = requests
        n_actual = 0
    if not requests.empty and "sample_t0_epoch_ms" in requests:
        sample_t0 = {
            str(value) for value in requests["sample_t0_epoch_ms"].dropna()
            if str(value).strip() not in ("", "nan")
        }
        check("t0_tag_consistent_across_samples", len(sample_t0) <= 1,
              f"distinct sample t0 tags={sorted(sample_t0)}", severity="invalid")
    if not requests.empty and "sample_t0_source" in requests:
        sample_sources = {
            str(value) for value in requests["sample_t0_source"].dropna()
            if str(value).strip() not in ("", "nan")
        }
        check("t0_source_consistent_across_samples", len(sample_sources) <= 1,
              f"distinct sample t0 sources={sorted(sample_sources)}",
              severity="invalid")
    expected = a.expected_requests or int(
        plan["load_patterns"][pattern]["expected_requests_total"]
    )

    schedule = json.loads((d / "schedule-verification.json").read_text()) \
        if (d / "schedule-verification.json").exists() else {}
    check("schedule_verification_ok", bool(schedule.get("ok")),
          f"ok={schedule.get('ok')}, observed={schedule.get('observed_total')}, "
          f"expected={schedule.get('expected_total')}")
    if not schedule:
        tolerance = float(plan["arrival_schedule"].get("constant_tolerance_fraction", 0.05))
        low = int(expected * (1 - tolerance))
        high = int(expected * (1 + tolerance))
        check("arrival_count", low <= n_actual <= high,
              f"observed={n_actual}, expected={expected}, allowed=[{low},{high}]")

    summary = json.loads((d / "k6-summary.json").read_text()) \
        if (d / "k6-summary.json").exists() else {}
    loadgen = json.loads((d / "loadgen-summary.json").read_text()) \
        if (d / "loadgen-summary.json").exists() else {}
    dropped = loadgen.get("dropped_iterations")
    if dropped is None:
        dropped = metric_count(summary.get("metrics", {}), "dropped_iterations")
    maximum_dropped = int(plan["arrival_schedule"].get("maximum_dropped_iterations", 0))
    check("dropped_iterations", int(dropped or 0) <= maximum_dropped,
          f"observed={dropped}, maximum={maximum_dropped}")

    generator_resources = json.loads(
        (d / "generator-resources-status.json").read_text()
    ) if (d / "generator-resources-status.json").exists() else {}
    resources_ok = bool(
        generator_resources.get("ok")
        and generator_resources.get("covers_window")
        and int(generator_resources.get("samples") or 0) >= 2
    )
    check("generator_resources_recorded", resources_ok,
          f"samples={generator_resources.get('samples')}, "
          f"covers_window={generator_resources.get('covers_window')}, "
          f"issues={generator_resources.get('issues', [])}")
    check("generator_not_saturated",
          resources_ok and not generator_resources.get("saturated"),
          f"saturated={generator_resources.get('saturated')}, "
          f"summary={generator_resources.get('summary', {})}",
          severity="warning")

    lambdas = pd.read_csv(d / "lambda-invocations.csv").fillna("") \
        if (d / "lambda-invocations.csv").exists() else pd.DataFrame()

    check("requests_nonempty", not started.empty,
          f"request rows={len(requests)}, started={len(started)}")
    system_anomaly = False
    successful = pd.Series(dtype=bool)
    if not started.empty:
        statuses = started.get("status_code", pd.Series(dtype=str)).astype(str)
        timeouts = started.get("timeout", pd.Series(dtype=str)).astype(str).str.lower()
        timeout_mask = timeouts.isin(["true", "1"])
        if "error_code" in started:
            error_codes = pd.to_numeric(
                started["error_code"], errors="coerce"
            ).fillna(0)
            timeout_mask = timeout_mask | error_codes.isin([1050, 1211])
        errors = ~statuses.str.fullmatch(r"2\d\d")
        successful = ~(errors | timeout_mask)
        system_anomaly = bool(errors.any() or timeout_mask.any())
        check("successful_http_response_present", bool(successful.any()),
              f"2xx={int(successful.sum())}, errors={int((errors | timeout_mask).sum())}")
    measurement = started[started["phase"] == "measurement"] \
        if "phase" in started else started
    p95 = None
    if not measurement.empty:
        success_measurement = (
            measurement[successful.loc[measurement.index]]
            if len(successful) == len(started) else measurement
        )
        latencies = pd.to_numeric(
            success_measurement.get("latency_ms"), errors="coerce"
        ).dropna()
        if len(latencies):
            p95 = float(latencies.quantile(0.95))
    lmax = float(plan["latency_constraint"]["primary_lmax_ms"])
    if p95 is not None and p95 > lmax:
        system_anomaly = True

    if not lambdas.empty:
        for function in sorted(lambdas["function_name"].astype(str).unique()):
            group = lambdas[lambdas["function_name"].astype(str) == function].copy()
            group["start_timestamp_ms"] = pd.to_numeric(
                group["start_timestamp_ms"], errors="coerce"
            )
            group = group.sort_values("start_timestamp_ms")
            first = group.iloc[0]
            check(f"first_invocation_is_cold:{function}",
                  as_bool(first.get("cold_start")),
                  f"first request_id={first.get('request_id')}, "
                  f"init_duration_ms={first.get('init_duration_ms')}")
            check(f"report_configuration_matches:{function}",
                  int(first.get("memory_mb", -1)) == int(condition_memory or -2)
                  and (str(first.get("architecture", "")) == str(condition_architecture)),
                  f"report_memory={first.get('memory_mb')}, "
                  f"report_arch={first.get('architecture')}, "
                  f"condition_memory={condition_memory}, "
                  f"condition_arch={condition_architecture}")
        pre_run = lambdas[lambdas["phase"].astype(str) == "pre_run"] \
            if "phase" in lambdas else pd.DataFrame()
        check("no_invocation_after_reset_before_t0", pre_run.empty,
              f"{len(pre_run)} invocation(s) started before T0")
        invoked = set(lambdas["function_name"].astype(str))
        for function in reset_functions:
            name = function.get("function_name")
            check(f"function_invoked:{name}", name in invoked,
                  f"invoked={name in invoked}", severity="warning" if system_anomaly else "invalid")
    else:
        check("lambda_invocations_present", False, "lambda-invocations.csv has no rows")

    cost = json.loads((d / "cost.json").read_text()) \
        if (d / "cost.json").exists() else {}
    init_audit = cost.get("init_duration_validation", {})
    check("billed_includes_init_duration",
          init_audit.get("ok", False) is True,
          f"checked={init_audit.get('records_checked')}, "
          f"violations={len(init_audit.get('violations', []))}")
    measurement_cost = cost.get("measurement", {})
    check("measurement_cost_has_requests",
          (measurement_cost.get("external_requests_counted") or 0) > 0,
          f"n={measurement_cost.get('external_requests_counted')}")

    if issues:
        final = "invalid-apparatus"
    elif system_anomaly:
        final = "valid-system-anomaly"
    else:
        final = "valid"
    result = {
        "run_id": d.name,
        "status": final,
        "composition": a.composition,
        "pattern": pattern,
        "condition": condition,
        "checks": checks,
        "issues": issues,
        "warnings": warnings,
        "system_anomaly_observed": system_anomaly,
        "observed_requests": n_actual,
        "successful_requests": int(successful.sum()) if len(successful) else 0,
        "clock": timing_clock,
        "cloudwatch_status": cloudwatch_status,
        "expected_requests": expected,
        "p95_measurement_ms": p95,
        "primary_lmax_ms": lmax,
        "dropped_iterations": dropped,
        "generator_resources": generator_resources,
        "decision_timestamp_utc": utc_now(),
        "target": target_info,
        "note": (
            "HTTP errors/timeouts and p95 above Lmax are retained as system outcomes; "
            "configuration/collection/schedule failures are apparatus failures."
        ),
    }
    write_json(d / "validation.json", result)
    print(json.dumps(result, indent=2))
    if final == "invalid-apparatus":
        raise SystemExit("Run classified as invalid-apparatus")


if __name__ == "__main__":
    main()
