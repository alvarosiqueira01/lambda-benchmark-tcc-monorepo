"""Idempotent CloudWatch Logs retention configuration.

The benchmark stack does not own the Lambda log groups: they are created by the
Lambda service on first invocation. Managing them as CloudFormation resources
would collide with the AWS-created groups. This module applies the retention
policy operationally instead, and the deployment/preflight paths verify it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import is_placeholder


def managed_function_names(cfg: dict) -> list[str]:
    names = [
        cfg["functions"]["monolithic"]["name"],
        cfg["functions"]["orchestrator"]["name"],
        *[worker["name"] for worker in cfg["functions"].get("workers", [])],
    ]
    crud = cfg["functions"].get("decomposed_crud", {}).get("name")
    if crud:
        names.append(crud)
    return [name for name in names if not is_placeholder(name)]


def group_for(function_name: str) -> str:
    return f"/aws/lambda/{function_name}"


def describe(session, cfg: dict) -> dict[str, Any]:
    logs = session.client("logs")
    names = managed_function_names(cfg)
    result = {}
    for function_name in names:
        group = group_for(function_name)
        try:
            groups = logs.describe_log_groups(logGroupNamePrefix=group)["logGroups"]
            match = next((g for g in groups if g.get("logGroupName") == group), None)
            result[group] = {
                "function_name": function_name,
                "exists": match is not None,
                "retention_in_days": match.get("retentionInDays") if match else None,
                "stored_bytes": match.get("storedBytes") if match else None,
            }
        except Exception as exc:  # noqa: BLE001 - report, do not hide
            result[group] = {"function_name": function_name, "error": str(exc)}
    return result


def ensure(session, cfg: dict, days: int | None = None) -> dict[str, Any]:
    expected = int(days or cfg["aws"].get("log_retention_days", 90))
    logs = session.client("logs")
    result = {"expected_days": expected, "groups": {}, "ok": True}
    for function_name in managed_function_names(cfg):
        group = group_for(function_name)
        try:
            logs.put_retention_policy(logGroupName=group, retentionInDays=expected)
            info = logs.describe_log_groups(logGroupNamePrefix=group)["logGroups"]
            match = next((g for g in info if g.get("logGroupName") == group), None)
            actual = match.get("retentionInDays") if match else None
            result["groups"][group] = {
                "function_name": function_name,
                "retention_in_days": actual,
                "ok": actual == expected,
            }
            result["ok"] = result["ok"] and actual == expected
        except Exception as exc:  # noqa: BLE001
            result["groups"][group] = {"function_name": function_name, "error": str(exc), "ok": False}
            result["ok"] = False
    return result


def verify(session, cfg: dict, days: int | None = None) -> dict[str, Any]:
    expected = int(days or cfg["aws"].get("log_retention_days", 90))
    groups = describe(session, cfg)
    result = {
        "expected_days": expected,
        "groups": groups,
        "ok": bool(groups) and all(
            g.get("exists") is True and g.get("retention_in_days") == expected
            for g in groups.values()
        ),
    }
    return result
