#!/usr/bin/env python3
"""Fail-closed preflight before the first cold reset of a phase.

This is deliberately run on the control plane before any condition is executed:
it verifies that the dataset is the expected one, every DynamoDB table holds the
same document, both API routes return the same document analysis, the
90-day log retention is in place, and the generator instance is the configured
type.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from log_retention import verify as verify_log_retention

from common import load_yaml, sha256_file, utc_now, write_json

REQUIRED_DOCUMENT_FIELDS = ("id", "title", "body", "language", "tags")
VOLATILE_KEYS = {"request_id", "requestId", "trace_id", "traceId"}


def canonical(value):
    if isinstance(value, dict):
        return {
            key: canonical(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def normalize_tags(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                return value
        if isinstance(parsed, list):
            return sorted(str(item) for item in parsed)
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    return value


def normalize_document(value):
    value = canonical(value)
    if isinstance(value, dict) and "tags" in value:
        value["tags"] = normalize_tags(value["tags"])
    return value


def http_json(url, timeout=20, headers=None):
    last_error = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                return {
                    "status": response.status,
                    "content_type": response.headers.get("Content-Type"),
                    "body": body,
                    "json": json.loads(body.decode("utf-8")),
                }
        except urllib.error.HTTPError as exc:
            return {
                "status": exc.code,
                "content_type": exc.headers.get("Content-Type") if exc.headers else None,
                "body": exc.read(),
                "json": None,
                "error": str(exc),
            }
        except Exception as exc:  # noqa: BLE001 - retry transient endpoint failure
            last_error = exc
            if attempt < 2:
                time.sleep(1)
    return {"status": None, "error": str(last_error)}


def dynamodb_value(value):
    """Decode one DynamoDB AttributeValue returned by GetItem."""
    if not isinstance(value, dict):
        return value
    if "S" in value:
        return value["S"]
    if "N" in value:
        return value["N"]
    if "BOOL" in value:
        return value["BOOL"]
    if "NULL" in value:
        return None
    if "L" in value:
        return [dynamodb_value(item) for item in value["L"]]
    if "M" in value:
        return {key: dynamodb_value(item) for key, item in value["M"].items()}
    if "SS" in value:
        return sorted(value["SS"])
    if "NS" in value:
        return sorted(value["NS"])
    if "B" in value:
        return value["B"]
    return next(iter(value.values())) if value else value


def estimated_dynamodb_item_bytes(item):
    """Approximate DynamoDB item size (attribute names + values).

    DynamoDB does not expose the stored item size in GetItem. This estimate is
    recorded so the configured value can be checked against the representation
    actually returned by the table instead of the source JSON file size.
    """
    def value_size(value):
        if value is None:
            return 1
        if isinstance(value, bool):
            return 1
        if isinstance(value, (int, float)):
            return len(str(value).encode())
        if isinstance(value, str):
            return len(value.encode())
        if isinstance(value, list):
            return 3 + sum(value_size(v) for v in value)
        if isinstance(value, dict):
            return 3 + sum(len(str(k).encode()) + value_size(v) for k, v in value.items())
        return len(str(value).encode())

    return sum(len(str(key).encode()) + value_size(value) for key, value in item.items())


def check_generator(session, cfg):
    expected_type = cfg["generator"].get("instance_type")
    instance_id = cfg["generator"].get("instance_id")
    if not instance_id:
        return {"ok": False, "error": "generator.instance_id not configured"}
    try:
        ec2 = session.client("ec2")
        instance = ec2.describe_instances(
            InstanceIds=[instance_id]
        )["Reservations"][0]["Instances"][0]
        credit = (instance.get("CreditSpecification") or {}).get("CpuCredits")
        if credit is None:
            specs = ec2.describe_instance_credit_specifications(
                InstanceIds=[instance_id]
            ).get("InstanceCreditSpecifications", [])
            if specs:
                credit = specs[0].get("CpuCredits")
        checks = {
            "state_running": instance.get("State", {}).get("Name") == "running",
            "instance_type": instance.get("InstanceType") == expected_type,
            "cpu_credits": (
                expected_type != "t8i.small"
                or (cfg["generator"].get("cpu_credits") in (None, credit))
            ),
        }
        return {
            "ok": all(checks.values()),
            "instance_id": instance_id,
            "instance_type": instance.get("InstanceType"),
            "expected_instance_type": expected_type,
            "cpu_credits": credit,
            "checks": checks,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def check_dataset(cfg, plan):
    path = Path(cfg["dataset"]["path"])
    result = {
        "ok": False,
        "path": str(path),
        "expected_version": cfg["dataset"].get("version"),
        "document_id": cfg["dataset"].get("document_id"),
        "plan_id": plan.get("campaign_id"),
    }
    if not path.exists():
        result["error"] = "dataset file missing"
        return result
    try:
        documents = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"cannot parse dataset: {exc}"
        return result
    if not isinstance(documents, list) or not documents:
        result["error"] = "dataset must be a non-empty JSON array"
        return result
    by_id = {str(row.get("id")): row for row in documents if isinstance(row, dict)}
    document_id = str(cfg["dataset"].get("document_id"))
    document = by_id.get(document_id)
    result.update({
        "sha256": sha256_file(path),
        "document_count": len(documents),
        "document_found": document is not None,
        "document_fields": sorted(document) if document else [],
        "first_document_id": documents[0].get("id") if isinstance(documents[0], dict) else None,
    })
    if document is None:
        result["error"] = f"document {document_id} not found in dataset"
        return result
    missing = [field for field in REQUIRED_DOCUMENT_FIELDS if field not in document]
    result["missing_fields"] = missing
    result["document"] = document
    result["ok"] = not missing
    return result


def check_tables(session, cfg, dataset):
    document = dataset.get("document") or {}
    document_id = str(cfg["dataset"]["document_id"])
    results = {}
    all_ok = True
    for composition, table_cfg in cfg["dynamodb"]["tables"].items():
        table = table_cfg["table_name"]
        entry = {"table_name": table, "ok": False}
        try:
            response = session.client("dynamodb").get_item(
                TableName=table, Key={"id": {"S": document_id}}
            )
            item = response.get("Item")
            entry["found"] = item is not None
            if item:
                plain = {key: dynamodb_value(value) for key, value in item.items()}
                entry["item_keys"] = sorted(plain)
                entry["missing_fields"] = [
                    field for field in REQUIRED_DOCUMENT_FIELDS if field not in plain
                ]
                entry["estimated_item_size_bytes"] = estimated_dynamodb_item_bytes(plain)
                configured_size = cfg["dynamodb"].get("document_item_size_bytes")
                entry["configured_item_size_bytes"] = configured_size
                entry["size_matches_config"] = (
                    configured_size is not None
                    and abs(entry["estimated_item_size_bytes"] - configured_size)
                    <= max(1024, configured_size * 0.15)
                )
                entry["document_matches_dataset"] = all(
                    normalize_document(plain.get(field)) == normalize_document(document.get(field))
                    for field in REQUIRED_DOCUMENT_FIELDS
                    if field in plain and field in document
                )
                entry["ok"] = (
                    not entry["missing_fields"]
                    and entry["document_matches_dataset"]
                    and entry["size_matches_config"]
                )
            else:
                entry["error"] = "GetItem returned no item"
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
        results[composition] = entry
        all_ok = all_ok and entry["ok"]
    return {"ok": all_ok, "tables": results}


def check_equivalence(cfg):
    document_id = str(cfg["dataset"]["document_id"])
    responses = {}
    all_ok = True
    for composition, route in cfg["api"]["routes"].items():
        path = route["route_path_template"].replace("{id}", document_id)
        url = route["base_url"].rstrip("/") + "/" + path.lstrip("/")
        response = http_json(
            url, headers={"X-Benchmark-Run-Id": "preflight", "User-Agent": "tcc-preflight"}
        )
        body = response.get("json")
        responses[composition] = {
            "url": url,
            "status": response.get("status"),
            "content_type": response.get("content_type"),
            "error": response.get("error"),
            "document": normalize_document(body) if isinstance(body, dict) else body,
        }
        all_ok = (
            all_ok
            and response.get("status") == 200
            and isinstance(body, dict)
            and {"lexical", "readability", "keywords"} <= set(body)
        )
    if all_ok:
        first = responses[next(iter(responses))]["document"]
        all_ok = all(resp["document"] == first for resp in responses.values())
    return {"ok": all_ok, "responses": responses}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="experiments/harness-config.yaml")
    ap.add_argument("--plan", default="experiments/experiment-plan.yaml")
    ap.add_argument("--phase", choices=["pilot", "campaign"], required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cfg = load_yaml(a.config)
    plan = load_yaml(a.plan)

    import boto3

    session = boto3.Session(
        profile_name=cfg["aws"].get("profile"), region_name=cfg["aws"]["region"]
    )
    dataset = check_dataset(cfg, plan)
    tables = check_tables(session, cfg, dataset) if dataset.get("ok") else {
        "ok": False, "error": "dataset check failed"
    }
    equivalence = check_equivalence(cfg)
    generator = check_generator(session, cfg)
    retention = verify_log_retention(session, cfg)
    checks = {
        "dataset_ok": dataset.get("ok") is True,
        "tables_ok": tables.get("ok") is True,
        "functional_equivalence_ok": equivalence.get("ok") is True,
        "generator_ok": generator.get("ok") is True,
        "log_retention_ok": retention.get("ok") is True,
    }
    result = {
        "created_at_utc": utc_now(),
        "phase": a.phase,
        "campaign_id": plan.get("campaign_id"),
        "checks": checks,
        "ok": all(checks.values()),
        "dataset": {key: value for key, value in dataset.items() if key != "document"},
        "tables": tables,
        "functional_equivalence": equivalence,
        "generator": generator,
        "log_retention": retention,
    }
    write_json(a.out, result)
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit("preflight failed: " + ", ".join(k for k, v in checks.items() if not v))


if __name__ == "__main__":
    main()
