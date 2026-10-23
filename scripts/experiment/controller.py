#!/usr/bin/env python3
"""Sequential campaign controller with retry queue and frozen manifests."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from planning.manifest import run_manifest, stack_metadata

from common import (
    csv_read,
    is_placeholder,
    load_yaml,
    new_run_id,
    parse_utc,
    runs_root,
    s3_phase_prefix,
    sha256_file,
    utc_now,
    write_json,
)

EXPERIMENT_ROOT = Path(__file__).resolve().parent


def call(args, check=True):
    printable = " ".join(map(str, args))
    print("+", printable, flush=True)
    completed = subprocess.run(list(map(str, args)), check=False)
    if check and completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {printable}")
    return completed.returncode


def validate_pricing(path, plan):
    pricing_path = Path(path)
    if not pricing_path.exists():
        raise SystemExit(f"Pricing snapshot not found: {pricing_path}")
    data = json.loads(pricing_path.read_text(encoding="utf-8"))
    required = [
        "region", "captured_at_utc", "lambda_request_usd_per_million",
        "lambda_gb_second_usd_x86_64", "lambda_gb_second_usd_arm64",
        "http_api_usd_per_million_requests_first_tier",
        "dynamodb_standard_usd_per_million_rru",
        "dynamodb_standard_usd_per_million_wru",
    ]
    missing = [key for key in required if data.get(key) in (None, "")]
    if missing:
        raise SystemExit("Pricing snapshot missing fields: " + ", ".join(missing))
    if is_placeholder(str(data["captured_at_utc"])):
        raise SystemExit("Pricing snapshot captured_at_utc is a placeholder")
    if data["region"] != plan["region"]:
        raise SystemExit(
            f"Pricing region {data['region']} does not match plan region {plan['region']}"
        )
    return data


def verify_stack_unchanged(session, cfg, campaign):
    frozen = (campaign.get("stack") or {}).get("last_updated_time")
    current = stack_metadata(session, cfg["aws"].get("stack_name"))
    if not current.get("available"):
        print(f"WARNING: CloudFormation stack check unavailable: {current}", file=sys.stderr)
        return current
    status = current.get("stack_status") or ""
    if not status.endswith("_COMPLETE"):
        raise RuntimeError(f"CloudFormation stack is not stable: {status}")
    if frozen and current.get("last_updated_time") != frozen:
        raise RuntimeError(
            "CloudFormation stack changed after the campaign manifest was frozen: "
            f"frozen={frozen}, current={current.get('last_updated_time')}. "
            "A sam deploy would overwrite the benchmark configuration."
        )
    return current


def count_http_errors(requests_csv):
    rows = csv_read(requests_csv)
    errors = 0
    started = 0
    for row in rows:
        if str(row.get("request_sent", "")).lower() not in ("true", "1"):
            continue
        started += 1
        status = str(row.get("status_code", ""))
        timeout = str(row.get("timeout", "")).lower() in ("true", "1")
        if timeout or not (len(status) == 3 and status.startswith("2")):
            errors += 1
    return started, errors


def upload_run(s3, bucket, prefix, run_dir, manifest):
    files = [path for path in run_dir.rglob("*") if path.is_file()]
    for path in files:
        key = f"{prefix}/{path.relative_to(run_dir).as_posix()}"
        s3.upload_file(str(path), bucket, key)
    listed = set()
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix + "/")
    for page in pages:
        for obj in page.get("Contents", []):
            listed.add(obj["Key"])
    expected = {f"{prefix}/{path.relative_to(run_dir).as_posix()}" for path in files}
    missing = expected - listed
    if missing:
        raise RuntimeError(f"Upload verification failed; missing S3 objects: {sorted(missing)}")
    manifest["s3_uris"] = sorted(listed)
    manifest["artifacts_uploaded"] = True
    return sorted(listed)


def s3_run_prefix(cfg, run_id, phase=None):
    if phase:
        return f"{s3_phase_prefix(cfg, phase)}/{run_id}"
    return f'{cfg["aws"]["s3_prefix"].rstrip("/")}/runs/{run_id}'


def failure_validation(run_id, row, exc, stage):
    detail = str(exc)
    return {
        "run_id": run_id,
        "status": "invalid-apparatus",
        "composition": row.get("composition"),
        "pattern": row.get("load_pattern"),
        "condition": row,
        "checks": [],
        "issues": [{
            "check": "controller_failure",
            "detail": detail,
            "stage": stage,
            "severity": "invalid",
        }],
        "warnings": [],
        "system_anomaly_observed": False,
        "decision_timestamp_utc": utc_now(),
        "note": (
            "Run interrupted during preparation, load, collection or validation. "
            "Partial artifacts and this failure record were preserved and uploaded."
        ),
    }


def finalize_failure(run_dir, manifest, session, cfg, run_id, row, stage, exc, phase=None):
    """Preserve, classify and upload a partial run before retrying it."""
    validation_path = run_dir / "validation.json"
    validation = None
    if validation_path.exists():
        try:
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            validation = None
    if validation is None:
        validation = failure_validation(run_id, row, exc, stage)
    else:
        validation = dict(validation)
        validation.setdefault("checks", [])
        validation.setdefault("issues", [])
        validation.setdefault("warnings", [])
        validation["status"] = "invalid-apparatus"
        validation["issues"] = list(validation["issues"]) + [{
            "check": "controller_failure",
            "detail": str(exc),
            "stage": stage,
            "severity": "invalid",
        }]
        validation["controller_error"] = str(exc)
        validation["controller_failure_stage"] = stage
        validation["decision_timestamp_utc"] = utc_now()
    write_json(validation_path, validation)

    manifest.update({
        "status": "FAILED_OR_PARTIAL",
        "attempt": manifest.get("attempt"),
        "max_retries": manifest.get("max_retries"),
        "error": str(exc),
        "failure_stage": stage,
        "finished_at_utc": utc_now(),
        "validation_status": "invalid-apparatus",
        "artifacts": [
            path.relative_to(run_dir).as_posix()
            for path in run_dir.rglob("*") if path.is_file()
        ],
    })
    write_json(run_dir / "manifest.json", manifest)

    result = {
        "status": "invalid-apparatus",
        "error": str(exc),
        "failure_stage": stage,
        "issues": validation.get("issues", []),
    }
    try:
        s3 = session.client("s3")
        bucket = cfg["aws"]["s3_bucket"]
        prefix = s3_run_prefix(cfg, run_id, phase)
        upload_run(s3, bucket, prefix, run_dir, manifest)
        manifest["finished_at_utc"] = utc_now()
        manifest["status"] = "FAILED_OR_PARTIAL"
        write_json(run_dir / "manifest.json", manifest)
        s3.upload_file(
            str(run_dir / "manifest.json"), bucket, f"{prefix}/manifest.json"
        )
        print(
            f"Run {run_id} failed at {stage}; partial artifacts uploaded to "
            f"s3://{bucket}/{prefix}/",
            file=sys.stderr,
        )
    except Exception as upload_exc:  # noqa: BLE001 - never mask the original failure
        manifest["artifacts_uploaded"] = False
        manifest["upload_error"] = str(upload_exc)
        write_json(run_dir / "manifest.json", manifest)
        print(
            f"Run {run_id} failed at {stage}; S3 upload of partial artifacts also "
            f"failed: {upload_exc}",
            file=sys.stderr,
        )
    return result


def execute_run(row, attempt, campaign, plan, cfg, pricing_path, template_path,
                session, max_retries, phase, preflight_path):
    block = int(row.get("block_id", 1))
    repetition = int(row.get("replication", block))
    run_id = new_run_id(row["condition_id"], block, repetition)
    run_dir = runs_root(phase) / run_id
    run_dir.mkdir(parents=True)
    shutil.copy2(preflight_path, run_dir / "preflight.json")
    preflight = json.loads(preflight_path.read_text())
    if preflight.get("ok") is not True:
        raise RuntimeError("Preflight did not pass; refusing to start run")
    manifest = {
        "run_id": run_id,
        "phase": phase,
        "condition_id": row.get("condition_id"),
        "condition": row,
        "attempt": attempt,
        "max_retries": max_retries,
        "pricing_snapshot": str(pricing_path),
        "status": "PREPARING",
    }
    write_json(run_dir / "manifest.json", manifest)

    reset_path = run_dir / "cold-reset.json"
    target_path = run_dir / "target-verification.json"
    stage = "prepare"
    try:
        manifest = run_manifest(
            campaign, run_id, row, plan, status="PREPARING"
        )
        manifest["phase"] = phase
        manifest["preflight"] = {
            "path": str(preflight_path),
            "sha256": sha256_file(preflight_path),
            "ok": True,
        }
        manifest["attempt"] = attempt
        manifest["max_retries"] = max_retries
        manifest["pricing_snapshot"] = str(pricing_path)
        write_json(run_dir / "manifest.json", manifest)
        stage = "verify_stack"
        verify_stack_unchanged(session, cfg, campaign)
        stage = "deploy_validate"
        call([
            sys.executable, str(EXPERIMENT_ROOT / "prepare" / "deploy.py"),
            "--config", "experiments/harness-config.yaml",
            "--template", template_path, "--validate-only",
        ])
        if Path("experiments/metadata/deploy-validation.json").exists():
            shutil.copy2(
                "experiments/metadata/deploy-validation.json",
                run_dir / "deploy-validation.json",
            )

        stage = "concurrency_check"
        call([
            sys.executable, str(EXPERIMENT_ROOT / "prepare" / "check_concurrency.py"),
            "--config", "experiments/harness-config.yaml",
            "--run-id", run_id,
            "--out", run_dir / "concurrency-check.json",
        ])

        stage = "cold_reset"
        call([
            sys.executable, str(EXPERIMENT_ROOT / "prepare" / "cold_reset.py"),
            "--config", "experiments/harness-config.yaml",
            "--composition", row["composition"],
            "--architecture", row["architecture"],
            "--memory-mb", row["memory_mb"],
            "--run-id", run_id, "--out", reset_path,
        ])
        reset_data = json.loads(reset_path.read_text())
        functions = [item["function_name"] for item in reset_data["functions"]]

        stage = "verify_target"
        call([
            sys.executable, str(EXPERIMENT_ROOT / "prepare" / "verify_target.py"),
            "--config", "experiments/harness-config.yaml",
            "--composition", row["composition"],
            "--cold-reset", reset_path, "--out", target_path,
        ])
        target_data = json.loads(target_path.read_text())
        if not target_data.get("ok"):
            raise RuntimeError(f"verify_target failed for {row['composition']}: {target_data}")
        manifest = run_manifest(
            campaign, run_id, row, plan, reset=reset_data, target=target_data,
            status="RESET_APPLIED",
        )
        manifest["phase"] = phase
        manifest["preflight"] = {
            "path": str(preflight_path),
            "sha256": sha256_file(preflight_path),
            "ok": True,
        }
        manifest["attempt"] = attempt
        manifest["max_retries"] = max_retries
        manifest["pricing_snapshot"] = str(pricing_path)
        write_json(run_dir / "manifest.json", manifest)

        stage = "load"
        call([
            sys.executable, str(EXPERIMENT_ROOT / "run" / "load_test.py"),
            "--config", "experiments/harness-config.yaml",
            "--plan", "experiments/experiment-plan.yaml",
            "--composition", row["composition"],
            "--pattern", row["load_pattern"],
            "--run-id", run_id, "--output-dir", run_dir,
            "--document-id", cfg["dataset"].get("document_id", "") or get_document_id(cfg),
        ])
        timing = json.loads((run_dir / "generator-timing.json").read_text())
        t0_ms = int(timing["t0_epoch_ms"])
        end_ms = t0_ms + int(plan["total_duration_seconds"]) * 1000
        manifest = run_manifest(
            campaign, run_id, row, plan, reset=reset_data, target=target_data,
            timing=timing, status="LOAD_COMPLETE",
        )
        manifest["phase"] = phase
        manifest["attempt"] = attempt
        manifest["max_retries"] = max_retries
        manifest["pricing_snapshot"] = str(pricing_path)
        write_json(run_dir / "manifest.json", manifest)

        stage = "export_requests"
        call([
            sys.executable, str(EXPERIMENT_ROOT / "run" / "export_k6_requests.py"),
            "--raw", run_dir / "k6-raw.json",
            "--summary-json", run_dir / "k6-summary.json",
            "--t0-epoch-ms", t0_ms,
            "--warmup-seconds", plan["warmup_seconds"],
            "--total-seconds", plan["total_duration_seconds"],
            "--out", run_dir / "requests.csv",
        ])
        call([
            sys.executable, str(EXPERIMENT_ROOT / "run" / "verify_schedule.py"),
            "--plan", "experiments/experiment-plan.yaml",
            "--requests-csv", run_dir / "requests.csv",
            "--summary-json", run_dir / "k6-summary.json",
            "--pattern", row["load_pattern"],
            "--t0-epoch-ms", t0_ms,
            "--out", run_dir / "schedule-verification.json",
        ], check=False)

        n_started, n_errors = count_http_errors(run_dir / "requests.csv")
        factor = 1 if row["composition"] == "monolithic" else 4
        expected_invocations = n_started * factor
        shortfall_allowed = n_errors * factor
        reset_completed_ms = int(
            parse_utc(reset_data["reset_completed_at_utc"]).timestamp() * 1000
        )
        collect_args = [
            sys.executable, str(EXPERIMENT_ROOT / "collect" / "collect_lambda.py"),
            "--config", "experiments/harness-config.yaml",
            "--run-id", run_id,
            "--start-ms", t0_ms, "--end-ms", end_ms,
            "--collect-from-ms", reset_completed_ms,
            "--output-dir", run_dir, "--versions-json", reset_path,
            "--composition", row["composition"],
            "--expected-invocations", expected_invocations,
            "--shortfall-allowed", shortfall_allowed,
            "--warmup-seconds", plan["warmup_seconds"],
            "--total-seconds", plan["total_duration_seconds"],
        ]
        for function_name in functions:
            collect_args.extend(["--function", function_name])
        stage = "collect_lambda"
        call(collect_args)

        collect_start = reset_completed_ms
        collect_end = end_ms + 120000
        stage = "collect_cloudwatch"
        cloudwatch_args = [
            sys.executable, str(EXPERIMENT_ROOT / "collect" / "collect_cloudwatch.py"),
            "--config", "experiments/harness-config.yaml",
            "--run-id", run_id,
            "--start-ms", collect_start, "--end-ms", collect_end,
            "--output-dir", run_dir,
            "--api-id", cfg["api"]["routes"][row["composition"]]["api_id"],
            "--api-stage", str(cfg["api"].get("stage", "$default")),
            *[item for function_name in functions for item in ("--function", function_name)],
        ]
        generator_instance_id = cfg.get("generator", {}).get("instance_id")
        if generator_instance_id:
            cloudwatch_args.extend(
                ["--generator-instance-id", str(generator_instance_id)]
            )
        call(cloudwatch_args)
        stage = "collect_cost"
        call([
            sys.executable, str(EXPERIMENT_ROOT / "collect" / "collect_cost.py"),
            "--config", "experiments/harness-config.yaml",
            "--pricing", pricing_path,
            "--requests-csv", run_dir / "requests.csv",
            "--lambda-csv", run_dir / "lambda-invocations.csv",
            "--composition", row["composition"],
            "--architecture", row["architecture"],
            "--t0-epoch-ms", t0_ms,
            "--warmup-seconds", plan["warmup_seconds"],
            "--total-seconds", plan["total_duration_seconds"],
            "--output", run_dir / "cost.json",
        ])
        stage = "validate"
        call([
            sys.executable, str(EXPERIMENT_ROOT / "validate" / "validate_run.py"),
            "--plan", "experiments/experiment-plan.yaml",
            "--run-dir", run_dir,
            "--expected-requests", int(
                plan["load_patterns"][row["load_pattern"]]["expected_requests_total"]
            ),
            "--pattern", row["load_pattern"],
            "--composition", row["composition"],
            "--target-verification", target_path,
            "--retention-days", str(cfg["aws"].get("log_retention_days", 90)),
            "--max-clock-offset-ms", str(cfg["generator"].get("max_clock_offset_ms", 50)),
        ], check=False)
        validation = json.loads((run_dir / "validation.json").read_text())
        final_status = validation.get("status", "invalid-apparatus")


        stage = "upload"
        s3 = session.client("s3")
        bucket = cfg["aws"]["s3_bucket"]
        s3_prefix = s3_run_prefix(cfg, run_id, phase)
        manifest = run_manifest(
            campaign, run_id, row, plan, reset=reset_data, target=target_data,
            timing=timing, status="COLLECTED",
        )
        manifest.update({
            "phase": phase,
            "preflight": {
                "path": str(preflight_path),
                "sha256": sha256_file(preflight_path),
                "ok": True,
            },
            "attempt": attempt,
            "max_retries": max_retries,
            "pricing_snapshot": str(pricing_path),
            "validation_status": final_status,
            "validation_issues": validation.get("issues", []),
            "validation_warnings": validation.get("warnings", []),
            "collection_end_utc": utc_now(),
            "artifacts": [
                path.relative_to(run_dir).as_posix()
                for path in run_dir.rglob("*") if path.is_file()
            ],
        })
        write_json(run_dir / "manifest.json", manifest)
        upload_run(s3, bucket, s3_prefix, run_dir, manifest)
        manifest["finished_at_utc"] = utc_now()
        manifest["status"] = final_status
        write_json(run_dir / "manifest.json", manifest)
        # Re-upload the final manifest so the archived copy matches the local one.
        s3.upload_file(
            str(run_dir / "manifest.json"), bucket, f"{s3_prefix}/manifest.json"
        )
        print(f"Run {run_id} finished with status {final_status}")
        return validation, run_dir
    except Exception as exc:  # noqa: BLE001 - preserve partial run and retry
        manifest.update({
            "attempt": attempt,
            "max_retries": max_retries,
            "pricing_snapshot": str(pricing_path),
        })
        result = finalize_failure(
            run_dir, manifest, session, cfg, run_id, row, stage, exc, phase
        )
        print(
            f"Run {run_id} failed at {stage}; partial artifacts preserved: {exc}",
            file=sys.stderr,
        )
        return result, run_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", default="experiments/experiment-plan.yaml")
    parser.add_argument("--config", default="experiments/harness-config.yaml")
    parser.add_argument("--condition-file", required=True)
    parser.add_argument("--pricing", required=True)
    parser.add_argument("--template", default=None)
    parser.add_argument("--phase", choices=["pilot", "campaign"], default="campaign")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--start-index", type=int, default=0)
    args = parser.parse_args()
    plan = load_yaml(args.plan)
    cfg = load_yaml(args.config)
    validate_pricing(args.pricing, plan)
    template_path = args.template or cfg["aws"].get("template_path", "template.yaml")
    cfg["aws"]["template_path"] = template_path
    rows = csv_read(args.condition_file)[args.start_index:]
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        raise SystemExit("No conditions selected")

    if "dataset" not in cfg or not Path(cfg["dataset"]["path"]).exists():
        raise SystemExit(f"Dataset missing: {cfg.get('dataset', {}).get('path')}")

    lock = Path("experiments/.campaign.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        raise SystemExit(
            "Campaign lock exists; verify no controller/run is active before removing it."
        )

    try:
        # Pilot and campaign use separate frozen manifests. The pilot manifest
        # may be regenerated while the instrument is being corrected; the
        # campaign manifest refuses dirty commits and input changes.
        manifest_path = Path(args.manifest or (
            "experiments/metadata/pilot-manifest.json"
            if args.phase == "pilot"
            else "experiments/metadata/campaign-manifest.json"
        ))
        manifest_args = [
            sys.executable, str(EXPERIMENT_ROOT / "planning" / "manifest.py"),
            "--config", args.config,
            "--plan", args.plan,
            "--pricing", args.pricing,
            "--template", template_path,
            "--out", str(manifest_path),
            "--phase", args.phase,
        ]
        if args.phase == "pilot":
            manifest_args.append("--force")
        else:
            manifest_args.append("--require-clean")
        call(manifest_args)
        campaign = json.loads(manifest_path.read_text())
        if args.phase == "campaign" and campaign.get("git_dirty"):
            raise SystemExit(
                "Refusing to start the main campaign from a dirty Git worktree; "
                "commit the frozen code first."
            )
        import boto3

        session = boto3.Session(
            profile_name=cfg["aws"].get("profile"),
            region_name=cfg["aws"]["region"],
        )
        preflight_path = Path(f"experiments/metadata/preflight-{args.phase}.json")
        call([
            sys.executable, str(EXPERIMENT_ROOT / "prepare" / "preflight.py"),
            "--config", args.config,
            "--plan", args.plan,
            "--phase", args.phase,
            "--out", preflight_path,
        ])
        preflight = json.loads(preflight_path.read_text())
        if preflight.get("ok") is not True:
            raise SystemExit(
                "Preflight failed; refusing to start pilot/campaign: "
                + json.dumps(preflight, indent=2)
            )
        max_retries = int(cfg.get("campaign", {}).get("max_retries", 2))
        queue = [(row, 0) for row in rows]
        results = []
        while queue:
            row, attempt = queue.pop(0)
            validation, run_dir = execute_run(
                row, attempt, campaign, plan, cfg, args.pricing, template_path,
                session, max_retries, args.phase, preflight_path,
            )
            status = validation.get("status", "invalid-apparatus")
            results.append({
                "condition_id": row.get("condition_id"),
                "run_id": run_dir.name,
                "attempt": attempt,
                "status": status,
            })
            if status == "invalid-apparatus":
                if attempt < max_retries:
                    print(
                        f"Run {run_dir.name} is invalid-apparatus; queueing substitute "
                        f"({attempt + 1}/{max_retries}) for {row.get('condition_id')}",
                        file=sys.stderr,
                    )
                    queue.append((row, attempt + 1))
                else:
                    print(
                        f"Run {run_dir.name} exhausted retries for {row.get('condition_id')}",
                        file=sys.stderr,
                    )
        write_json(f"experiments/metadata/{args.phase}-controller-results.json", {
            "finished_at_utc": utc_now(),
            "results": results,
            "invalid_after_retries": [
                item for item in results if item["status"] == "invalid-apparatus"
            ],
        })
        if any(item["status"] == "invalid-apparatus" for item in results):
            raise SystemExit(2)
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def get_document_id(cfg):
    path = Path(cfg["dataset"]["path"])
    if not path.exists():
        raise FileNotFoundError(f"Dataset missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data:
        raise ValueError("Dataset is empty")
    first = data[0]
    if "id" not in first:
        raise ValueError("First dataset item has no 'id'")
    return str(first["id"])


if __name__ == "__main__":
    main()
