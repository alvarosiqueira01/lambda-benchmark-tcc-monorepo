#!/usr/bin/env python3
"""Collect supplementary Lambda and generator EC2 CloudWatch metrics."""
import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import csv_write, load_yaml, utc_now, write_json

LAMBDA_METRICS = {
    "Invocations": ["Sum"],
    "Errors": ["Sum"],
    "Throttles": ["Sum"],
    "Duration": ["Average", "Maximum"],
    "ConcurrentExecutions": ["Maximum"],
}

API_METRICS = {
    "Count": ["Sum"],
    "Latency": ["Average", "Maximum"],
    "IntegrationLatency": ["Average", "Maximum"],
    "4xx": ["Sum"],
    "5xx": ["Sum"],
}

EC2_METRICS = {
    "CPUUtilization": ["Average", "Maximum"],
    "NetworkIn": ["Sum", "Average"],
    "NetworkOut": ["Sum", "Average"],
    "CPUCreditBalance": ["Average", "Minimum"],
}

FIELDS = [
    "run_id", "namespace", "scope", "resource", "function_name", "metric",
    "timestamp_utc", "unit", "sum", "average", "maximum", "minimum",
]


def collect(client, run_id, namespace, resource, function_name, metric,
            dimensions, statistics, start, end, period, rows, raw):
    try:
        response = client.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric,
            Dimensions=dimensions,
            StartTime=start,
            EndTime=end,
            Period=period,
            Statistics=statistics,
        )
    except Exception as exc:  # noqa: BLE001 - report metric availability, keep run
        raw.append({
            "namespace": namespace,
            "metric": metric,
            "dimensions": dimensions,
            "period": period,
            "statistics": statistics,
            "error": str(exc),
        })
        return 0
    raw.append({
        "namespace": namespace,
        "metric": metric,
        "dimensions": dimensions,
        "period": period,
        "statistics": statistics,
        "response": response,
    })
    for point in response.get("Datapoints", []):
        rows.append({
            "run_id": run_id,
            "namespace": namespace,
            "scope": (
                "generator" if namespace == "AWS/EC2"
                else "api" if namespace == "AWS/ApiGateway"
                else "application"
            ),
            "resource": resource,
            "function_name": function_name,
            "metric": metric,
            "timestamp_utc": point["Timestamp"].isoformat(),
            "unit": point.get("Unit"),
            "sum": point.get("Sum"),
            "average": point.get("Average"),
            "maximum": point.get("Maximum"),
            "minimum": point.get("Minimum"),
        })
    return len(response.get("Datapoints", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="experiments/harness-config.yaml")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--start-ms", required=True, type=int)
    ap.add_argument("--end-ms", required=True, type=int)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--function", action="append", default=[])
    ap.add_argument("--generator-instance-id", default=None)
    ap.add_argument("--api-id", default=None)
    ap.add_argument("--api-stage", default='$default')
    ap.add_argument("--period-seconds", type=int, default=60)
    a = ap.parse_args()
    cfg = load_yaml(a.config)

    import boto3

    session = boto3.Session(
        profile_name=cfg["aws"].get("profile"), region_name=cfg["aws"]["region"]
    )
    cloudwatch = session.client("cloudwatch")
    rows = []
    raw = []
    start = datetime.fromtimestamp(a.start_ms / 1000, UTC)
    end = datetime.fromtimestamp(a.end_ms / 1000, UTC)

    lambda_datapoints = 0
    for function_name in a.function:
        for metric, statistics in LAMBDA_METRICS.items():
            lambda_datapoints += collect(
                cloudwatch, a.run_id, "AWS/Lambda", function_name, function_name,
                metric, [{"Name": "FunctionName", "Value": function_name}],
                statistics, start, end, a.period_seconds, rows, raw,
            )

    api = {
        "api_id": a.api_id,
        "stage": a.api_stage,
        "queried": False,
        "available": False,
        "datapoints": 0,
        "metrics": sorted(API_METRICS),
    }
    if a.api_id:
        api["queried"] = True
        for metric, statistics in API_METRICS.items():
            api["datapoints"] += collect(
                cloudwatch, a.run_id, "AWS/ApiGateway", a.api_id, "",
                metric,
                [
                    {"Name": "ApiId", "Value": a.api_id},
                    {"Name": "Stage", "Value": a.api_stage},
                ],
                statistics, start, end, a.period_seconds, rows, raw,
            )
        api["available"] = api["datapoints"] > 0
        if not api["available"]:
            api["reason"] = (
                "No AWS/ApiGateway datapoints returned; confirm the API ID, "
                "stage dimension, metric delay and that the route was invoked."
            )
    else:
        api["reason"] = "api_id not configured"

    generator_id = a.generator_instance_id or (
        cfg.get("generator") or {}
    ).get("instance_id")
    generator = {
        "instance_id": generator_id,
        "queried": False,
        "available": False,
        "datapoints": 0,
        "metrics": sorted(EC2_METRICS),
        "period_seconds": a.period_seconds,
    }
    if generator_id:
        generator["queried"] = True
        for metric, statistics in EC2_METRICS.items():
            generator["datapoints"] += collect(
                cloudwatch, a.run_id, "AWS/EC2", str(generator_id), "",
                metric, [{"Name": "InstanceId", "Value": str(generator_id)}],
                statistics, start, end, a.period_seconds, rows, raw,
            )
        generator["available"] = generator["datapoints"] > 0
        if not generator["available"]:
            generator["reason"] = (
                "No AWS/EC2 datapoints returned; confirm detailed monitoring, "
                "metric delay and the InstanceId dimension."
            )
    else:
        generator["reason"] = "generator.instance_id is not configured"
    generator["memory_note"] = (
        "AWS/EC2 does not expose memory; the SSM /proc sampler in "
        "load_test.py records generator memory for each run."
    )

    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "cloudwatch-raw.json", raw)
    csv_write(out / "cloudwatch-metrics.csv", rows, FIELDS)
    unavailable = [
        name for name, item in (("api", api), ("generator", generator))
        if item.get("queried") and not item.get("available")
    ]
    if rows and unavailable:
        status = "PARTIAL"
    elif rows:
        status = "COMPLETE"
    else:
        status = "EMPTY"
    write_json(out / "cloudwatch-collection-status.json", {
        "run_id": a.run_id,
        "collected_at_utc": utc_now(),
        "records": len(rows),
        "functions": a.function,
        "lambda_datapoints": lambda_datapoints,
        "api": api,
        "generator": generator,
        "status": status,
        "unavailable_scopes": unavailable,
        "note": (
            "CloudWatch metrics are supplementary and may be delayed/coarse; "
            "k6 request samples and Lambda REPORT logs remain primary. Generator "
            "memory is recorded by the SSM /proc sampler in load_test.py."
        ),
    })


if __name__ == "__main__":
    main()
