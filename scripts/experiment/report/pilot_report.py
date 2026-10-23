#!/usr/bin/env python3
"""Build pilot-validation evidence from archived run directories."""
import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import csv_read, load_yaml


def utc_now():
    return datetime.now(UTC).isoformat()


def load_json(path):
    return json.loads(path.read_text()) if path.exists() else None


def as_bool(value):
    return str(value).lower() in ("true", "1")


def percentile(values, fraction):
    values = sorted(float(value) for value in values if value is not None)
    if not values:
        return None
    index = min(len(values) - 1, max(0, round((len(values) - 1) * fraction)))
    return values[index]


def p95(rows):
    return percentile(
        [row.get("latency_ms") for row in rows if row.get("phase") == "measurement"],
        0.95,
    )


def independent_cost(run, cfg):
    """Recompute the run cost from raw rows and the frozen price snapshot."""
    manifest = run["manifest"]
    prices = manifest.get("pricing") or {}
    cost_cfg = cfg.get("cost") or {}
    if not prices or not run.get("cost"):
        return {"ok": False, "detail": "pricing snapshot or cost.json missing"}
    t0 = (run.get("timing") or {}).get("t0_epoch_ms")
    if t0 is None:
        return {"ok": False, "detail": "generator-timing has no t0"}
    warmup_ms = int((manifest.get("protocol") or {}).get("warmup_seconds") or 0) * 1000
    total_ms = int((manifest.get("protocol") or {}).get("total_duration_seconds") or 0) * 1000
    architecture = (manifest.get("condition") or {}).get("architecture")
    duration_usd = 0.0
    lambda_requests = 0
    for row in run.get("lambda_invocations") or []:
        if not as_bool(row.get("report_found")):
            continue
        start = row.get("start_timestamp_ms")
        try:
            start_ms = int(float(start))
        except (TypeError, ValueError):
            continue
        if not (t0 <= start_ms < t0 + total_ms):
            continue
        billed = float(row.get("billed_duration_ms") or 0)
        memory = float(row.get("memory_mb") or 0)
        arch = row.get("architecture") or architecture
        rate = (
            prices.get("lambda_gb_second_usd_arm64")
            if arch == "arm64" else prices.get("lambda_gb_second_usd_x86_64")
        )
        if rate is None:
            return {"ok": False, "detail": "Lambda price missing"}
        duration_usd += billed / 1000 * memory / 1024 * float(rate)
        lambda_requests += 1
    lambda_usd = (
        duration_usd
        + lambda_requests * float(prices.get("lambda_request_usd_per_million") or 0) / 1e6
    )
    started = [
        row for row in run.get("requests") or []
        if as_bool(row.get("request_sent"))
        and (row.get("phase") in ("warmup", "measurement"))
        and row.get("start_epoch_ms") not in (None, "")
        and t0 <= int(float(row["start_epoch_ms"])) < t0 + total_ms
    ]
    n = len(started)
    api_usd = n * float(prices.get("http_api_usd_per_million_requests_first_tier") or 0) / 1e6
    rru = float(cost_cfg.get("ddb_rru_per_external_request", 0)) * n
    wru = float(cost_cfg.get("ddb_wru_per_external_request", 0)) * n
    ddb_usd = (
        rru * float(prices.get("dynamodb_standard_usd_per_million_rru") or 0) / 1e6
        + wru * float(prices.get("dynamodb_standard_usd_per_million_wru") or 0) / 1e6
    )
    computed = lambda_usd + api_usd + ddb_usd
    reported = ((run.get("cost") or {}).get("full_run") or {}).get(
        "app_total_estimated_usd"
    )
    if reported is None:
        return {"ok": False, "detail": "cost.json full_run total missing"}
    delta = abs(computed - float(reported))
    return {
        "ok": delta <= max(1e-9, abs(float(reported)) * 1e-6),
        "computed_usd": computed,
        "reported_usd": float(reported),
        "delta_usd": delta,
        "lambda_usd": lambda_usd,
        "api_usd": api_usd,
        "dynamodb_usd": ddb_usd,
        "external_requests": n,
        "lambda_invocations": lambda_requests,
        "warmup_ms_used_for_anchor": warmup_ms,
    }


def steady_state(run):
    measurement = [
        row for row in run.get("requests") or []
        if row.get("phase") == "measurement" and row.get("latency_ms") not in (None, "")
    ]
    if len(measurement) < 10:
        return {"ok": False, "detail": "fewer than 10 measurement samples"}
    middle = len(measurement) // 2
    first = percentile([row.get("latency_ms") for row in measurement[:middle]], 0.95)
    second = percentile([row.get("latency_ms") for row in measurement[middle:]], 0.95)
    if first is None or second is None:
        return {"ok": False, "detail": "p95 could not be computed"}
    ratio = second / first if first else None
    return {
        "ok": ratio is not None and ratio <= 1.5,
        "first_half_p95_ms": first,
        "second_half_p95_ms": second,
        "ratio": ratio,
    }


def error_summary(run):
    requests = run.get("requests") or []
    started = [row for row in requests if as_bool(row.get("request_sent"))]
    errors = [
        row for row in started
        if str(row.get("status_code", "")).isdigit()
        and not (200 <= int(row["status_code"]) < 300)
    ]
    timeouts = [
        row for row in started
        if as_bool(row.get("timeout"))
        or str(row.get("error_code", "0")) in ("1050", "1211")
    ]
    cloudwatch = run.get("cloudwatch") or {}
    throttles = sum(
        float(row.get("sum") or 0)
        for row in run.get("cloudwatch_metrics") or []
        if row.get("metric") == "Throttles"
        and row.get("namespace") == "AWS/Lambda"
    )
    return {
        "started": len(started),
        "errors": len(errors),
        "timeouts": len(timeouts),
        "status_codes": {
            str(code): sum(1 for row in started if str(row.get("status_code")) == str(code))
            for code in sorted({str(row.get("status_code")) for row in started})
        },
        "throttles": throttles,
        "cloudwatch_status": cloudwatch.get("status"),
    }


def render_markdown(result):
    lines = [
        f"# Pilot validation — {result.get('campaign_id') or 'unset'}",
        "",
        f"Decision: **{result.get('decision')}**",
        f"Reviewed at: `{result.get('reviewed_at_utc')}`",
        f"Runs reviewed: {len(result.get('runs_reviewed', []))}",
        "",
        "## Checks",
        "",
        "| Check | Passed |",
        "|---|---|",
    ]
    for name, passed in (result.get("checks") or {}).items():
        lines.append(f"| `{name}` | {'yes' if passed else 'NO'} |")
    lines.extend(["", "## Pending actions", ""])
    pending = result.get("pending_actions") or []
    lines.extend(f"- `{item}`" for item in pending) if pending else lines.append("None.")
    lines.extend(["", "## Runs", "", "| Run | Status | Condition |", "|---|---|---|"])
    for run in result.get("runs_reviewed", []):
        condition = run.get("condition") or {}
        label = ", ".join(
            f"{key}={condition.get(key)}"
            for key in ("composition", "architecture", "memory_mb", "load_pattern")
            if condition.get(key) is not None
        )
        lines.append(f"| `{run.get('run_id')}` | {run.get('status')} | {label} |")
    lines.extend(["", "## Failed or partial runs", ""])
    failed = result.get("failed_or_partial_runs") or []
    if not failed:
        lines.append("None.")
    else:
        lines.extend(
            f"- `{item.get('run_id')}`: status={item.get('status')}, "
            f"validation={item.get('validation_status')}, error={item.get('error')}"
            for item in failed
        )
    lines.extend(["", "## Issues", ""])
    issues = result.get("issues") or []
    if not issues:
        lines.append("None.")
    else:
        lines.extend(
            f"- `{item.get('run_id')}` `{item.get('check')}`: {item.get('detail')}"
            for item in issues
        )
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", default="experiments/runs/pilot")
    parser.add_argument("--template", default="experiments/metadata/pilot-validation-template.json")
    parser.add_argument("--deploy-validation",
                        default="experiments/metadata/deploy-validation.json")
    parser.add_argument("--out", default="experiments/pilot-validation.json")
    parser.add_argument("--markdown-out", default=None)
    parser.add_argument("--config", default="experiments/harness-config.yaml")
    parser.add_argument("--pilot-conditions",
                        default="experiments/pilot-conditions.csv")
    parser.add_argument("--plan", default="experiments/experiment-plan.yaml")
    parser.add_argument("--campaign-id", default=None)
    args = parser.parse_args()
    template = load_json(Path(args.template)) or {
        "campaign_id": None, "checks": {}, "issues": [],
    }
    campaign_id = args.campaign_id or template.get("campaign_id")
    plan = load_yaml(args.plan) if Path(args.plan).exists() else {}
    cfg = load_yaml(args.config) if Path(args.config).exists() else {}
    pilot_path = Path(args.pilot_conditions)
    pilot_rows = csv_read(pilot_path) if pilot_path.exists() else []
    pilot_ids = {row["condition_id"] for row in pilot_rows if row.get("condition_id")}
    deploy = load_json(Path(args.deploy_validation)) or {}
    route_by_composition = {
        route.get("composition"): route for route in deploy.get("routes", [])
        if route.get("composition")
    }
    all_runs = []
    for directory in sorted(Path(args.runs_dir).iterdir()):
        if not directory.is_dir():
            continue
        manifest = load_json(directory / "manifest.json")
        if not manifest:
            continue
        if campaign_id and manifest.get("campaign_id") != campaign_id:
            continue
        validation = load_json(directory / "validation.json")
        if not validation:
            validation = {
                "run_id": directory.name,
                "status": "invalid-apparatus",
                "checks": [],
                "issues": [{
                    "check": "validation_missing",
                    "detail": (
                        "validation.json missing; run was interrupted or not archived"
                    ),
                }],
                "warnings": [],
            }
        all_runs.append({
            "run_id": directory.name,
            "manifest": manifest,
            "validation": validation,
            "validation_present": (directory / "validation.json").exists(),
            "schedule": load_json(directory / "schedule-verification.json"),
            "collection": load_json(directory / "lambda-collection-status.json"),
            "cost": load_json(directory / "cost.json"),
            "target": load_json(directory / "target-verification.json"),
            "generator_resources": load_json(
                directory / "generator-resources-status.json"
            ),
            "loadgen": load_json(directory / "loadgen-summary.json"),
            "cloudwatch": load_json(directory / "cloudwatch-collection-status.json"),
            "cloudwatch_metrics": csv_read(directory / "cloudwatch-metrics.csv")
            if (directory / "cloudwatch-metrics.csv").exists() else [],
            "preflight": load_json(directory / "preflight.json"),
            "concurrency": load_json(directory / "concurrency-check.json"),
            "timing": load_json(directory / "generator-timing.json"),
            "requests": csv_read(directory / "requests.csv")
            if (directory / "requests.csv").exists() else [],
            "lambda_invocations": csv_read(directory / "lambda-invocations.csv")
            if (directory / "lambda-invocations.csv").exists() else [],
        })

    ok_statuses = ("valid", "valid-system-anomaly")

    def condition_id(run):
        return (run["manifest"].get("condition") or {}).get("condition_id") \
            or run["run_id"]

    if pilot_ids:
        runs = [run for run in all_runs if condition_id(run) in pilot_ids]
    else:
        runs = all_runs
    runs_excluded = len(all_runs) - len(runs)

    failed_or_partial_runs = [
        {
            "run_id": run["run_id"],
            "status": run["manifest"].get("status"),
            "validation_status": run["validation"].get("status"),
            "validation_present": run["validation_present"],
            "failure_stage": run["manifest"].get("failure_stage"),
            "error": run["manifest"].get("error"),
            "uploaded": run["manifest"].get("artifacts_uploaded"),
            "upload_error": run["manifest"].get("upload_error"),
        }
        for run in runs
        if run["manifest"].get("status") == "FAILED_OR_PARTIAL"
        or not run["validation_present"]
        or run["validation"].get("status") == "invalid-apparatus"
    ]

    def sort_key(run):
        return str(
            run["manifest"].get("finished_at_utc")
            or run["manifest"].get("created_at_utc")
            or ""
        )

    def effective_runs(run_list):
        groups = {}
        for run in run_list:
            groups.setdefault(condition_id(run), []).append(run)
        chosen = []
        for group in groups.values():
            valid = [
                run for run in group
                if run["validation"].get("status") in ok_statuses
            ]
            chosen.append(max(valid or group, key=sort_key))
        return chosen

    # One effective run per condition: a valid substitute supersedes an older
    # invalid-apparatus attempt for the same condition.
    runs = effective_runs(runs)

    def runs_for(pattern=None, composition=None, statuses=ok_statuses):
        selected = [
            run for run in runs if run["validation"].get("status") in statuses
        ]
        if pattern:
            selected = [
                run for run in selected
                if (run["manifest"].get("factors") or {}).get("load_pattern") == pattern
            ]
        if composition:
            selected = [
                run for run in selected
                if (run["manifest"].get("condition") or {}).get("composition")
                == composition
            ]
        return selected

    def all_check(run_list, name):
        evidence = []
        ok = bool(run_list)
        for run in run_list:
            check = next(
                (item for item in run["validation"].get("checks", [])
                 if item.get("name") == name),
                None,
            )
            passed = bool(check and check.get("passed"))
            ok = ok and passed
            evidence.append({"run_id": run["run_id"], "passed": passed,
                             "detail": check.get("detail") if check else None})
        return ok, evidence

    checks = {}
    evidence = {}
    issues = []

    def factors_of(run):
        return run["manifest"].get("factors") or {}

    selected_ids = {condition_id(run) for run in runs}
    valid_ids = {
        condition_id(run) for run in runs
        if run["validation"].get("status") in ok_statuses
    }
    if pilot_ids:
        missing = sorted(pilot_ids - selected_ids)
        missing_valid = sorted(pilot_ids - valid_ids)
        checks["pilot_conditions_complete"] = not missing
        evidence["pilot_conditions_complete"] = {
            "file": str(pilot_path),
            "planned": len(pilot_ids),
            "observed": len(selected_ids & pilot_ids),
            "missing": missing,
            "excluded_runs_outside_pilot": runs_excluded,
        }
        checks["pilot_conditions_valid"] = not missing_valid
        evidence["pilot_conditions_valid"] = {"missing_valid": missing_valid}
        for missing_id in missing_valid:
            issues.append({
                "run_id": missing_id,
                "check": "pilot_coverage",
                "detail": f"no valid pilot run for condition {missing_id}",
            })
    else:
        checks["pilot_conditions_complete"] = False
        checks["pilot_conditions_valid"] = False
        evidence["pilot_conditions_complete"] = {
            "file": str(pilot_path), "exists": False,
            "hint": "run scripts/experiment/planning/planner.py to generate the 12-run pilot",
        }
        issues.append({
            "run_id": None,
            "check": "pilot_coverage",
            "detail": f"pilot conditions file not found: {pilot_path}",
        })

    factor_spec = plan.get("factors") or {}
    if factor_spec:
        observed_compositions = {factors_of(run).get("composition") for run in runs}
        observed_architectures = {factors_of(run).get("architecture") for run in runs}
        observed_patterns = {factors_of(run).get("load_pattern") for run in runs}
        observed_memories = {
            int(factors_of(run)["memory_mb"])
            for run in runs if factors_of(run).get("memory_mb") is not None
        }
        memories = sorted(int(value) for value in factor_spec.get("memory_mb", []))
        checks["pilot_compositions_covered"] = set(
            factor_spec.get("composition", [])
        ) <= observed_compositions
        checks["pilot_architectures_covered"] = set(
            factor_spec.get("architecture", [])
        ) <= observed_architectures
        checks["pilot_load_patterns_covered"] = set(
            factor_spec.get("load_pattern", [])
        ) <= observed_patterns
        checks["pilot_memory_extremes_covered"] = bool(memories) and (
            memories[0] in observed_memories and memories[-1] in observed_memories
        )
        evidence["pilot_factor_coverage"] = {
            "compositions": sorted(value for value in observed_compositions if value),
            "architectures": sorted(value for value in observed_architectures if value),
            "load_patterns": sorted(value for value in observed_patterns if value),
            "memories_mb": sorted(observed_memories),
            "required_memory_extremes_mb": memories[:1] + memories[-1:],
        }
    else:
        checks["pilot_factor_coverage"] = False
        issues.append({
            "run_id": None,
            "check": "pilot_factor_coverage",
            "detail": f"plan not found or has no factors: {args.plan}",
        })

    checks["decomposed_pilot_present"] = any(
        factors_of(run).get("composition") == "decomposed" for run in runs
    )

    checks["aliases_verified"] = bool(deploy.get("functions"))
    evidence["aliases_verified"] = deploy.get("functions", [])
    for composition, key in (
        ("monolithic", "api_mono_route_integration_qualified"),
        ("decomposed", "api_decomp_route_integration_qualified"),
    ):
        route = route_by_composition.get(composition)
        checks[key] = bool(route and route.get("qualifier_in_uri") == "benchmark")
    evidence["api_integration"] = deploy.get("routes", [])

    for composition, key in (
        ("monolithic", "api_mono_target_verified_per_run"),
        ("decomposed", "api_decomp_target_verified_per_run"),
    ):
        selected = runs_for(composition=composition)
        ok = bool(selected) and all(
            (run["target"] or {}).get("ok") is True for run in selected
        )
        checks[key] = ok
        evidence[key] = [
            {"run_id": run["run_id"], "target_ok": (run["target"] or {}).get("ok")}
            for run in selected
        ]

    ok, proof = all_check(runs_for(), "no_invocation_after_reset_before_t0")
    checks["no_invocation_after_reset_before_T0"] = ok
    evidence["no_invocation_after_reset_before_T0"] = proof

    for pattern, key in (
        ("constant", "constant_arrival_schedule_verified"),
        ("burst", "burst_schedule_verified"),
        ("sparse_miniburst", "sparse_miniburst_schedule_verified"),
    ):
        selected = runs_for(pattern=pattern)
        ok = bool(selected) and all(
            (run["schedule"] or {}).get("ok") is True for run in selected
        )
        checks[key] = ok
        evidence[key] = [
            {"run_id": run["run_id"], "schedule_ok": (run["schedule"] or {}).get("ok"),
             "dropped_iterations": (run["schedule"] or {}).get("dropped_iterations")}
            for run in selected
        ]

    selected = runs_for()
    dropped = [
        (run["schedule"] or {}).get("dropped_iterations", 0) for run in selected
        if run.get("schedule")
    ]
    checks["generator_capacity_verified"] = bool(dropped) and all(
        int(value or 0) == 0 for value in dropped
    )
    evidence["generator_capacity_verified"] = {"dropped_iterations": dropped}

    resource_checks = []
    for run in selected:
        resources = run.get("generator_resources") or {}
        summary = resources.get("summary") or {}
        resource_checks.append({
            "run_id": run["run_id"],
            "present": bool(resources),
            "ok": bool(resources.get("ok")),
            "covers_window": bool(resources.get("covers_window")),
            "saturated": bool(resources.get("saturated")),
            "samples": resources.get("samples"),
            "cpu_max_percent": (summary.get("cpu") or {}).get("percent_max"),
            "memory_max_used_percent": (
                summary.get("memory") or {}
            ).get("max_used_percent"),
            "network_rx_bytes_per_s_max": (
                summary.get("network") or {}
            ).get("rx_bytes_per_s_max"),
            "network_tx_bytes_per_s_max": (
                summary.get("network") or {}
            ).get("tx_bytes_per_s_max"),
            "issues": resources.get("issues", []),
        })
    checks["generator_resources_recorded"] = bool(resource_checks) and all(
        item["present"] and item["ok"] and item["covers_window"]
        for item in resource_checks
    )
    checks["generator_not_saturated"] = bool(resource_checks) and all(
        not item["saturated"] for item in resource_checks
    )
    evidence["generator_resources"] = resource_checks

    request_checks = []
    for run in selected:
        schedule = run.get("schedule") or {}
        counter_check = next(
            (item for item in schedule.get("checks", [])
             if item.get("name") == "k6_started_counter_matches_csv"),
            None,
        )
        request_checks.append({
            "run_id": run["run_id"],
            "passed": bool(counter_check and counter_check.get("passed")),
        })
    checks["request_level_csv_verified"] = bool(request_checks) and all(
        item["passed"] for item in request_checks
    )
    evidence["request_level_csv_verified"] = request_checks

    pairing = [
        {"run_id": run["run_id"],
         "reports_missing": (run["collection"] or {}).get("reports_missing")}
        for run in selected
    ]
    checks["lambda_START_REPORT_pairing_verified"] = bool(pairing) and all(
        int(item["reports_missing"] or 0) == 0 for item in pairing
    )
    evidence["lambda_START_REPORT_pairing_verified"] = pairing

    worker_checks = []
    for run in runs_for(composition="decomposed"):
        for check in run["validation"].get("checks", []):
            if check.get("name", "").startswith("first_invocation_is_cold:"):
                worker_checks.append({
                    "run_id": run["run_id"],
                    "check": check["name"],
                    "passed": check.get("passed"),
                    "detail": check.get("detail"),
                })
    checks["decomposed_workers_show_init_duration_on_first_invocation"] = bool(
        worker_checks
    ) and all(item["passed"] for item in worker_checks)
    evidence["decomposed_workers_show_init_duration_on_first_invocation"] = worker_checks

    window_checks = []
    for run in selected:
        check = next(
            (item for item in run["validation"].get("checks", [])
             if item.get("name") == "schedule_verification_ok"),
            None,
        )
        window_checks.append({"run_id": run["run_id"],
                              "passed": bool(check and check.get("passed"))})
    checks["measurement_window_verified"] = bool(window_checks) and all(
        item["passed"] for item in window_checks
    )
    evidence["measurement_window_verified"] = window_checks

    cost_checks = [
        {"run_id": run["run_id"],
         "ok": ((run.get("cost") or {}).get("init_duration_validation") or {}).get("ok")}
        for run in selected
    ]
    checks["cost_calculation_verified"] = bool(cost_checks) and all(
        item["ok"] for item in cost_checks
    )
    evidence["cost_calculation_verified"] = cost_checks

    upload_checks = [
        {"run_id": run["run_id"],
         "uploaded": run["manifest"].get("artifacts_uploaded")}
        for run in selected
    ]
    checks["raw_artifacts_uploaded"] = bool(upload_checks) and all(
        item["uploaded"] for item in upload_checks
    )
    evidence["raw_artifacts_uploaded"] = upload_checks

    preflight_evidence = [
        {
            "run_id": run["run_id"],
            "ok": (run.get("preflight") or {}).get("ok"),
            "checks": (run.get("preflight") or {}).get("checks"),
        }
        for run in selected
    ]
    checks["preflight_verified"] = bool(preflight_evidence) and all(
        item["ok"] is True for item in preflight_evidence
    )
    evidence["preflight_verified"] = preflight_evidence

    equivalence_evidence = [
        {
            "run_id": run["run_id"],
            "ok": (
                ((run.get("preflight") or {}).get("functional_equivalence") or {})
                .get("ok")
            ),
        }
        for run in selected
    ]
    checks["functional_equivalence_verified"] = bool(equivalence_evidence) and all(
        item["ok"] is True for item in equivalence_evidence
    )
    evidence["functional_equivalence_verified"] = equivalence_evidence

    concurrency_evidence = [
        {
            "run_id": run["run_id"],
            "ok": (run.get("concurrency") or {}).get("ok"),
            "matches": (run.get("concurrency") or {}).get("matches"),
        }
        for run in selected
    ]
    checks["concurrency_verified"] = bool(concurrency_evidence) and all(
        item["ok"] is True for item in concurrency_evidence
    )
    evidence["concurrency_verified"] = concurrency_evidence

    clock_evidence = [
        {
            "run_id": run["run_id"],
            "ok": ((run.get("timing") or {}).get("clock") or {}).get("ok"),
            "max_observed_offset_ms": (
                ((run.get("timing") or {}).get("clock") or {})
                .get("max_observed_offset_ms")
            ),
        }
        for run in selected
    ]
    checks["clock_offset_verified"] = bool(clock_evidence) and all(
        item["ok"] is True for item in clock_evidence
    )
    evidence["clock_offset_verified"] = clock_evidence

    generator_cfg = cfg.get("generator") or {}
    generator_evidence = []
    for run in selected:
        generator = run["manifest"].get("generator") or {}
        loadgen = run.get("loadgen") or {}
        expected_credits = generator_cfg.get("cpu_credits")
        credits_ok = (
            expected_credits is None
            or generator.get("cpu_credits") == expected_credits
        )
        ok = (
            generator.get("instance_type") == generator_cfg.get("instance_type")
            and credits_ok
            and loadgen.get("k6_version") == generator_cfg.get("k6_expected_version")
        )
        generator_evidence.append({
            "run_id": run["run_id"],
            "ok": ok,
            "instance_type": generator.get("instance_type"),
            "expected_instance_type": generator_cfg.get("instance_type"),
            "cpu_credits": generator.get("cpu_credits"),
            "expected_cpu_credits": expected_credits,
            "k6_version": loadgen.get("k6_version"),
            "expected_k6_version": generator_cfg.get("k6_expected_version"),
        })
    checks["generator_configuration_verified"] = bool(generator_evidence) and all(
        item["ok"] for item in generator_evidence
    )
    evidence["generator_configuration_verified"] = generator_evidence

    vus_evidence = [
        {
            "run_id": run["run_id"],
            "pre_allocated_vus": (run.get("loadgen") or {}).get("pre_allocated_vus"),
            "max_vus": (run.get("loadgen") or {}).get("max_vus"),
            "dropped_iterations": (run.get("loadgen") or {}).get("dropped_iterations"),
        }
        for run in selected
    ]
    checks["vus_recorded"] = bool(vus_evidence) and all(
        item["pre_allocated_vus"] is not None
        and item["max_vus"] is not None
        and int(item["dropped_iterations"] or 0) == 0
        for item in vus_evidence
    )
    evidence["vus_recorded"] = vus_evidence

    retention_evidence = []
    expected_retention = int((cfg.get("aws") or {}).get("log_retention_days", 90))
    for run in selected:
        groups = run["manifest"].get("log_retention") or {}
        values = [
            item.get("retention_in_days") for item in groups.values()
            if isinstance(item, dict) and not item.get("error")
        ]
        retention_evidence.append({
            "run_id": run["run_id"],
            "ok": bool(groups) and len(values) == len(groups)
            and all(value == expected_retention for value in values),
            "expected_days": expected_retention,
            "groups": groups,
        })
    checks["log_retention_verified"] = bool(retention_evidence) and all(
        item["ok"] for item in retention_evidence
    )
    evidence["log_retention_verified"] = retention_evidence

    error_evidence = [
        {"run_id": run["run_id"], **error_summary(run)} for run in selected
    ]
    checks["errors_timeouts_throttles_recorded"] = bool(error_evidence) and all(
        item["cloudwatch_status"] in ("COMPLETE", "PARTIAL")
        and "errors" in item and "timeouts" in item
        for item in error_evidence
    )
    evidence["errors_timeouts_throttles_recorded"] = error_evidence

    stability_evidence = [
        {"run_id": run["run_id"], **steady_state(run)} for run in selected
    ]
    checks["warmup_stability_verified"] = bool(stability_evidence) and all(
        item["ok"] is True for item in stability_evidence
    )
    evidence["warmup_stability_verified"] = stability_evidence

    cost_independent = [
        {"run_id": run["run_id"], **independent_cost(run, cfg)}
        for run in selected
    ]
    checks["cost_independent_estimate_matches"] = bool(cost_independent) and all(
        item["ok"] is True for item in cost_independent
    )
    evidence["cost_independent_estimate_matches"] = cost_independent

    api_evidence = [
        {
            "run_id": run["run_id"],
            "api": (run.get("cloudwatch") or {}).get("api"),
        }
        for run in selected
    ]
    checks["api_gateway_metrics_recorded"] = bool(api_evidence) and all(
        (item["api"] or {}).get("available") is True for item in api_evidence
    )
    evidence["api_gateway_metrics_recorded"] = api_evidence

    worker_env_evidence = []
    for run in selected:
        target = run.get("target") or {}
        composition = (run["manifest"].get("condition") or {}).get("composition")
        if composition == "decomposed":
            env = target.get("environment_checks") or {}
            ok = bool(env) and all(
                item.get("ok") is True
                for key, item in env.items()
                if not key.endswith("_expected")
            )
        else:
            reset_checks = target.get("reset_function_checks") or {}
            ok = bool(reset_checks) and all(
                item.get("alias_ok") is True for item in reset_checks.values()
            )
        worker_env_evidence.append({"run_id": run["run_id"], "ok": ok})
    checks["worker_environment_verified"] = bool(worker_env_evidence) and all(
        item["ok"] for item in worker_env_evidence
    )
    evidence["worker_environment_verified"] = worker_env_evidence

    for run in runs:
        for issue in run["validation"].get("issues", []):
            issues.append({
                "run_id": run["run_id"],
                "check": "validation",
                "detail": str(issue),
            })
        if run["validation"].get("status") == "invalid-apparatus":
            issues.append({
                "run_id": run["run_id"],
                "check": "run_status",
                "detail": "invalid-apparatus",
            })

    decision = "APPROVED" if all(checks.values()) and not issues else "NOT_APPROVED"
    result = {
        **template,
        "campaign_id": campaign_id
        or (runs[0]["manifest"].get("campaign_id") if runs else None),
        "pilot_status": "REVIEWED",
        "pilot_conditions_file": str(pilot_path),
        "pilot_conditions_planned": len(pilot_ids),
        "runs_excluded_outside_pilot": runs_excluded,
        "reviewed_at_utc": utc_now(),
        "runs_reviewed": [
            {"run_id": run["run_id"],
             "status": run["validation"].get("status"),
             "condition": run["manifest"].get("condition")}
            for run in runs
        ],
        "failed_or_partial_runs": failed_or_partial_runs,
        "checks": checks,
        "evidence": evidence,
        "issues": issues,
        "decision": decision,
        "pending_actions": [key for key, value in checks.items() if not value],
        "note": (
            "APPROVED here means all required evidence passed; human sign-off "
            "(approved_by/approved_at_utc) is still required before the main campaign."
        ),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n",
                   encoding="utf-8")
    markdown_out = Path(args.markdown_out) if args.markdown_out else out.with_suffix(".md")
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    markdown_out.write_text(render_markdown(result) + "\n", encoding="utf-8")
    print(json.dumps({"decision": decision, "pending_actions": result["pending_actions"]},
                     indent=2))


if __name__ == "__main__":
    main()
