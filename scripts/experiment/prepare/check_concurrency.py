#!/usr/bin/env python3
"""Detect a k6/sampler process already running on the generator via SSM."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import load_yaml, utc_now, write_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="experiments/harness-config.yaml")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cfg = load_yaml(a.config)
    generator = cfg["generator"]
    instance_id = generator.get("instance_id")
    if not instance_id:
        raise SystemExit("generator.instance_id not configured")

    import boto3

    session = boto3.Session(
        profile_name=cfg["aws"].get("profile"), region_name=cfg["aws"]["region"]
    )
    ssm = session.client("ssm")
    command = (
        "set -u; self=$$; parent=$PPID; "
        "ps -eo pid=,ppid=,comm=,args= | "
        "awk -v self=\"$self\" -v parent=\"$parent\" "
        "'$1==self || $1==parent || $3==\"ps\" || $3==\"awk\" || $3==\"grep\" {next} {print}' | "
        "grep -E '(^|/)(k6|generator_sampler\\.sh)([[:space:]]|$)|"
        "k6 run|/opt/lambda-benchmark/results' || true"
    )
    response = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command], "executionTimeout": ["30"]},
        TimeoutSeconds=60,
    )
    command_id = response["Command"]["CommandId"]
    deadline = time.time() + 60
    invocation = None
    while time.time() < deadline:
        try:
            invocation = ssm.get_command_invocation(
                CommandId=command_id, InstanceId=instance_id
            )
            if invocation["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
                break
        except ssm.exceptions.InvocationDoesNotExist:
            pass
        time.sleep(2)
    stdout = (invocation or {}).get("StandardOutputContent", "")
    matches = [line.strip() for line in stdout.splitlines() if line.strip()]
    result = {
        "run_id": a.run_id,
        "checked_at_utc": utc_now(),
        "instance_id": instance_id,
        "command_id": command_id,
        "ssm_status": (invocation or {}).get("Status"),
        "matches": matches,
        "concurrent_run_detected": bool(matches),
        "ok": bool(invocation) and invocation.get("Status") == "Success" and not matches,
    }
    write_json(a.out, result)
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit("concurrent generator workload detected or SSM check failed")


if __name__ == "__main__":
    main()
