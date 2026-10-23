#!/usr/bin/env python3
"""Build the frozen campaign manifest and per-run manifests."""
import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (
    git_value,
    is_placeholder,
    load_yaml,
    sha256_file,
    utc_now,
    write_json,
)

MANIFEST_VERSION = 2

RUNTIME_ARTIFACT_PATHS = (
    "experiments/.campaign.lock",
    "experiments/pilot-conditions.csv",
    "experiments/pilot-validation.json",
    "experiments/pilot-validation.md",
    "experiments/runs/",
    "experiments/aggregate/",
    "experiments/metadata/plan/",
)


def is_runtime_artifact(path):
    normalized = path.replace("\\", "/")
    if normalized == "experiments/metadata/pilot-validation-template.json":
        return False
    if normalized in RUNTIME_ARTIFACT_PATHS:
        return True
    if normalized.startswith("experiments/runs/"):
        return True
    if normalized.startswith("experiments/aggregate/"):
        return True
    if normalized.startswith("experiments/metadata/plan/"):
        return True
    if normalized.startswith("experiments/metadata/") and normalized.endswith(".json"):
        return True
    return normalized.startswith(("experiments/pilot-", "experiments/.campaign"))


def git_dirty():
    output = git_value(["status", "--porcelain", "--untracked-files=all"]) or ""
    for line in output.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if not is_runtime_artifact(path):
            return True
    return False


def tool_version(command):
    try:
        output = subprocess.check_output(
            command, text=True, stderr=subprocess.STDOUT, timeout=20
        ).strip()
        return output.splitlines()[0] if output else None
    except Exception:  # noqa: BLE001 - tool versions are diagnostic
        return None


def pricing_metadata(path):
    if not path or not Path(path).exists():
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "region": data.get("region"),
        "captured_at_utc": data.get("captured_at_utc"),
        "source_urls": data.get("source_urls"),
        "pricing_model": data.get("pricing_model"),
        "lambda_request_usd_per_million": data.get("lambda_request_usd_per_million"),
        "lambda_gb_second_usd_x86_64": data.get("lambda_gb_second_usd_x86_64"),
        "lambda_gb_second_usd_arm64": data.get("lambda_gb_second_usd_arm64"),
        "http_api_usd_per_million_requests_first_tier": data.get(
            "http_api_usd_per_million_requests_first_tier"
        ),
        "dynamodb_standard_usd_per_million_rru": data.get(
            "dynamodb_standard_usd_per_million_rru"
        ),
        "dynamodb_standard_usd_per_million_wru": data.get(
            "dynamodb_standard_usd_per_million_wru"
        ),
    }


def stack_metadata(session, stack_name):
    if is_placeholder(stack_name):
        return {"available": False, "reason": "stack_name not configured"}
    try:
        stack = session.client("cloudformation").describe_stacks(
            StackName=stack_name
        )["Stacks"][0]
        return {
            "available": True,
            "stack_name": stack.get("StackName"),
            "stack_id": stack.get("StackId"),
            "stack_status": stack.get("StackStatus"),
            "creation_time": str(stack.get("CreationTime") or ""),
            "last_updated_time": str(stack.get("LastUpdatedTime") or ""),
        }
    except Exception as exc:  # noqa: BLE001 - diagnostic
        return {"available": False, "error": str(exc)}


def generator_metadata(session, instance_id):
    if is_placeholder(instance_id):
        return {"available": False, "reason": "instance_id not configured"}
    try:
        reservations = session.client("ec2").describe_instances(
            InstanceIds=[instance_id]
        )["Reservations"]
        instance = reservations[0]["Instances"][0]
        credit = (instance.get("CreditSpecification") or {}).get("CpuCredits")
        if credit is None:
            specs = session.client("ec2").describe_instance_credit_specifications(
                InstanceIds=[instance_id]
            ).get("InstanceCreditSpecifications", [])
            if specs:
                credit = specs[0].get("CpuCredits")
        return {
            "available": True,
            "instance_id": instance_id,
            "instance_type": instance.get("InstanceType"),
            "availability_zone": instance.get("Placement", {}).get("AvailabilityZone"),
            "state": instance.get("State", {}).get("Name"),
            "image_id": instance.get("ImageId"),
            "cpu_credits": credit,
            "credit_specification": instance.get("CreditSpecification"),
        }
    except Exception as exc:  # noqa: BLE001 - diagnostic
        return {"available": False, "error": str(exc)}


def log_retention_metadata(session, function_names):
    logs = session.client("logs")
    result = {}
    for function_name in function_names:
        group = f"/aws/lambda/{function_name}"
        try:
            groups = logs.describe_log_groups(logGroupNamePrefix=group)["logGroups"]
            match = next((g for g in groups if g.get("logGroupName") == group), None)
            result[group] = {
                "exists": match is not None,
                "retention_in_days": match.get("retentionInDays") if match else None,
                "stored_bytes": match.get("storedBytes") if match else None,
            }
        except Exception as exc:  # noqa: BLE001 - diagnostic
            result[group] = {"error": str(exc)}
    return result


def campaign_metadata(cfg, plan, pricing_path=None, template_path="template.yaml",
                      plan_path="experiments/experiment-plan.yaml",
                      config_path="experiments/harness-config.yaml", phase=None):
    dataset_path = Path(cfg["dataset"]["path"])
    template = Path(template_path)
    session = None
    try:
        import boto3

        session = boto3.Session(
            profile_name=cfg["aws"].get("profile"), region_name=cfg["aws"]["region"]
        )
        account_id = session.client("sts").get_caller_identity()["Account"]
    except Exception:  # noqa: BLE001 - offline planning is allowed
        account_id = None

    function_names = [
        cfg["functions"]["monolithic"]["name"],
        cfg["functions"]["orchestrator"]["name"],
        *[worker["name"] for worker in cfg["functions"].get("workers", [])],
    ]
    if cfg["functions"].get("decomposed_crud", {}).get("name"):
        function_names.append(cfg["functions"]["decomposed_crud"]["name"])
    stack = stack_metadata(session, cfg["aws"].get("stack_name")) if session else None
    generator = (
        generator_metadata(session, cfg["generator"].get("instance_id")) if session else None
    )
    retention = log_retention_metadata(session, function_names) if session else None
    return {
        "manifest_version": MANIFEST_VERSION,
        "campaign_id": plan["campaign_id"],
        "phase": phase,
        "captured_at_utc": utc_now(),
        "account_id": account_id,
        "region": cfg["aws"]["region"],
        "runtime": plan.get("runtime"),
        "git_commit": git_value(["rev-parse", "HEAD"]),
        "git_dirty": git_dirty(),
        "dataset": {
            "path": str(dataset_path),
            "version": cfg["dataset"].get("version"),
            "document_id": cfg["dataset"].get("document_id"),
            "sha256": sha256_file(dataset_path) if dataset_path.exists() else None,
            "exists": dataset_path.exists(),
            "size_bytes": dataset_path.stat().st_size if dataset_path.exists() else None,
        },
        "plan": {"path": str(plan_path), "sha256": sha256_file(plan_path)},
        "config": {"path": str(config_path), "sha256": sha256_file(config_path)},
        "template": {
            "path": str(template),
            "sha256": sha256_file(template) if template.exists() else None,
            "exists": template.exists(),
        },
        "seed": plan.get("random_seed"),
        "pricing": pricing_metadata(pricing_path),
        "stack": stack,
        "generator": {
            **(generator or {}),
            "expected_instance_type": cfg["generator"].get("instance_type"),
            "expected_cpu_credits": cfg["generator"].get("cpu_credits"),
            "expected_k6_version": cfg["generator"].get("k6_expected_version"),
            "require_chrony": cfg["generator"].get("require_chrony", True),
            "max_clock_offset_ms": cfg["generator"].get("max_clock_offset_ms", 50),
        },
        "log_retention": retention,
        "log_retention_policy": {
            "expected_days": cfg["aws"].get("log_retention_days", 90),
            "managed_outside_cloudformation": True,
        },
        "tools": {
            "python": sys.version,
            "platform": platform.platform(),
            "aws_cli": tool_version(["aws", "--version"]),
            "sam_cli": tool_version(["sam", "--version"]),
            "k6_local": tool_version(["k6", "version"]),
            "git": tool_version(["git", "--version"]),
        },
        "protocol": {
            "random_seed": plan.get("random_seed"),
            "warmup_seconds": plan.get("warmup_seconds"),
            "measurement_seconds": plan.get("measurement_seconds"),
            "total_duration_seconds": plan.get("total_duration_seconds"),
            "repetitions_per_condition": plan.get("repetitions_per_condition"),
            "blocks": plan.get("blocks"),
            "primary_lmax_ms": plan["latency_constraint"]["primary_lmax_ms"],
            "sensitivity_lmax_ms": plan["latency_constraint"].get("sensitivity_lmax_ms"),
            "conservative_feasibility_rule": plan["latency_constraint"].get(
                "conservative_feasibility_rule"
            ),
        },
        "arrival_schedule": plan.get("arrival_schedule"),
        "validity": plan.get("validity"),
    }


def run_manifest(campaign, run_id, condition, plan, reset=None, target=None,
                 timing=None, generator=None, status="PREPARING", phase=None):
    pattern = condition.get("load_pattern")
    pattern_spec = plan.get("load_patterns", {}).get(pattern, {})
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "phase": phase or campaign.get("phase"),
        "condition_id": condition.get("condition_id"),
        "block_id": condition.get("block_id"),
        "replication": condition.get("replication"),
        "condition": condition,
        "factors": {
            "composition": condition.get("composition"),
            "architecture": condition.get("architecture"),
            "memory_mb": condition.get("memory_mb"),
            "load_pattern": pattern,
        },
        "campaign_id": campaign.get("campaign_id"),
        "created_at_utc": utc_now(),
        "status": status,
        "git_commit": campaign.get("git_commit"),
        "git_dirty": campaign.get("git_dirty"),
        "dataset": campaign.get("dataset"),
        "plan": campaign.get("plan"),
        "config": campaign.get("config"),
        "template": campaign.get("template"),
        "pricing": campaign.get("pricing"),
        "seed": campaign.get("seed"),
        "protocol": campaign.get("protocol"),
        "arrival_schedule": {
            "pattern": pattern,
            "expected_requests_total": pattern_spec.get("expected_requests_total"),
            "expected_requests_measurement": pattern_spec.get("expected_requests_measurement"),
            "tolerances": campaign.get("arrival_schedule"),
        },
        "stack": campaign.get("stack"),
        "log_retention": campaign.get("log_retention"),
        "log_retention_policy": campaign.get("log_retention_policy"),
        "generator": {
            **(campaign.get("generator") or {}),
            **(generator or {}),
        },
    }
    if reset:
        manifest["reset"] = reset
        manifest["functions"] = reset.get("functions", [])
        manifest["expected_drift"] = "cold_reset mutates configuration and aliases outside CloudFormation"
    if target:
        manifest["api_target"] = target
    if timing:
        manifest["t0_epoch_ms"] = timing.get("t0_epoch_ms")
        manifest["t0_source"] = timing.get("t0_source")
        manifest["tend_epoch_ms"] = timing.get("tend_epoch_ms")
        manifest["measurement_start_epoch_ms"] = (
            int(timing["t0_epoch_ms"]) + int(plan["warmup_seconds"]) * 1000
            if timing.get("t0_epoch_ms") is not None else None
        )
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="experiments/harness-config.yaml")
    ap.add_argument("--plan", default="experiments/experiment-plan.yaml")
    ap.add_argument("--pricing", default=None)
    ap.add_argument("--template", default=None)
    ap.add_argument("--out", default="experiments/metadata/campaign-manifest.json")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--require-clean", action="store_true")
    ap.add_argument("--phase", choices=["pilot", "campaign"], default=None)
    a = ap.parse_args()
    cfg = load_yaml(a.config)
    plan = load_yaml(a.plan)
    template = a.template or cfg["aws"].get("template_path", "template.yaml")
    metadata = campaign_metadata(cfg, plan, a.pricing, template, a.plan, a.config, a.phase)
    if a.require_clean and metadata.get("git_dirty"):
        raise SystemExit(
            "Refusing to freeze the campaign manifest from a dirty Git "
            "worktree; commit the frozen code first."
        )
    out = Path(a.out)
    if out.exists() and not a.force:
        previous = json.loads(out.read_text(encoding="utf-8"))
        frozen = ["git_commit", "region", "runtime", "seed"]
        changed = [key for key in frozen if previous.get(key) != metadata.get(key)]
        for key in ("dataset", "plan", "config", "template", "pricing"):
            old_value = (previous.get(key) or {}).get("sha256") \
                if isinstance(previous.get(key), dict) else None
            new_value = (metadata.get(key) or {}).get("sha256") \
                if isinstance(metadata.get(key), dict) else None
            if old_value != new_value:
                changed.append(f"{key}.sha256")
        if changed:
            raise SystemExit(
                "Campaign manifest is frozen and its inputs changed: "
                + ", ".join(changed)
                + ". Use --force only after explicitly versioning a new campaign."
            )
    write_json(out, metadata)
    print(out)


if __name__ == "__main__":
    main()
