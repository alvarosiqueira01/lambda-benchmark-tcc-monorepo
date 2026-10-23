#!/usr/bin/env python3
"""Collect Lambda platform START/REPORT records with wait/poll and count checks."""
import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import csv_write, load_yaml, utc_now, write_json

START_RE = re.compile(r"START RequestId: ([0-9a-fA-F-]+) Version: (\S+)")
REPORT_RE = re.compile(
    r"REPORT RequestId: ([0-9a-fA-F-]+).*?"
    r"Duration: ([0-9.]+) ms.*?"
    r"Billed Duration: ([0-9]+) ms.*?"
    r"Memory Size: ([0-9]+) MB.*?"
    r"Max Memory Used: ([0-9]+) MB"
)
INIT_RE = re.compile(r"Init Duration: ([0-9.]+) ms")

FIELDS = [
    "run_id", "function_name", "version", "request_id",
    "start_timestamp_ms", "start_timestamp_utc",
    "report_timestamp_ms", "report_timestamp_utc",
    "duration_ms", "billed_duration_ms", "memory_mb", "configured_memory_mb",
    "max_memory_used_mb", "init_duration_ms", "cold_start", "report_found",
    "phase", "architecture",
]


def iso_ms(ms):
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def fetch_events(logs, group, start_ms, end_ms):
    events = []
    token = None
    while True:
        kwargs = {
            "logGroupName": group,
            "startTime": start_ms,
            "endTime": end_ms,
            "startFromHead": True,
        }
        if token:
            kwargs["nextToken"] = token
        response = logs.filter_log_events(**kwargs)
        events.extend(response.get("events", []))
        next_token = response.get("nextToken")
        if not next_token or next_token == token:
            break
        token = next_token
    return events


def parse_events(events, fn, expected_versions, architecture, configured_memory,
                 t0_ms, warmup_ms, total_ms):
    starts = {}
    reports = {}
    for event in events:
        message = event.get("message", "")
        match = START_RE.search(message)
        if match:
            starts[match.group(1)] = {
                "version": match.group(2),
                "timestamp_ms": int(event["timestamp"]),
            }
        match = REPORT_RE.search(message)
        if match:
            init_match = INIT_RE.search(message)
            reports[match.group(1)] = {
                "duration_ms": float(match.group(2)),
                "billed_duration_ms": int(match.group(3)),
                "memory_mb": int(match.group(4)),
                "max_memory_used_mb": int(match.group(5)),
                "init_duration_ms": float(init_match.group(1)) if init_match else None,
                "report_timestamp_ms": int(event["timestamp"]),
            }
    rows = []
    for request_id, start in starts.items():
        if start["version"] not in expected_versions.get(fn, {start["version"]}):
            continue
        start_ms = start["timestamp_ms"]
        if start_ms < t0_ms:
            phase = "pre_run"
        elif start_ms < t0_ms + warmup_ms:
            phase = "warmup"
        elif start_ms < t0_ms + total_ms:
            phase = "measurement"
        else:
            phase = "post_run"
        report = reports.get(request_id, {})
        rows.append({
            "run_id": "",
            "function_name": fn,
            "version": start["version"],
            "request_id": request_id,
            "start_timestamp_ms": start_ms,
            "start_timestamp_utc": iso_ms(start_ms),
            "report_timestamp_ms": report.get("report_timestamp_ms"),
            "report_timestamp_utc": (
                iso_ms(report["report_timestamp_ms"])
                if report.get("report_timestamp_ms") is not None else None
            ),
            "duration_ms": report.get("duration_ms"),
            "billed_duration_ms": report.get("billed_duration_ms"),
            "memory_mb": report.get("memory_mb"),
            "configured_memory_mb": configured_memory.get(fn),
            "max_memory_used_mb": report.get("max_memory_used_mb"),
            "init_duration_ms": report.get("init_duration_ms"),
            "cold_start": report.get("init_duration_ms") is not None,
            "report_found": bool(report),
            "phase": phase,
            "architecture": architecture.get(fn, ""),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="experiments/harness-config.yaml")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--start-ms", required=True, type=int, help="T0 reported by k6")
    ap.add_argument("--end-ms", required=True, type=int)
    ap.add_argument("--collect-from-ms", type=int, default=None,
                    help="Start of the log query; defaults to T0-120s. Use reset time to detect pre-T0 invocations.")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--function", action="append", required=True)
    ap.add_argument("--versions-json", required=True)
    ap.add_argument("--expected-invocations", type=int, default=None)
    ap.add_argument("--shortfall-allowed", type=int, default=0,
                    help="Expected invocations that may be missing because HTTP requests failed.")
    ap.add_argument("--composition", choices=["monolithic", "decomposed"], default=None)
    ap.add_argument("--wait-seconds", type=int, default=None)
    ap.add_argument("--poll-seconds", type=int, default=None)
    ap.add_argument("--warmup-seconds", type=int, default=120)
    ap.add_argument("--total-seconds", type=int, default=240)
    ap.add_argument("--query-margin-seconds", type=int, default=None)
    a = ap.parse_args()
    cfg = load_yaml(a.config)
    wait_seconds = int(
        a.wait_seconds if a.wait_seconds is not None else cfg["aws"].get("log_wait_seconds", 180)
    )
    poll_seconds = int(
        a.poll_seconds if a.poll_seconds is not None else cfg["aws"].get("log_poll_seconds", 10)
    )

    import boto3

    session = boto3.Session(
        profile_name=cfg["aws"].get("profile"), region_name=cfg["aws"]["region"]
    )
    logs = session.client("logs")
    vdoc = json.loads(Path(a.versions_json).read_text())
    functions = [x for x in vdoc.get("functions", []) if x["function_name"] in a.function]
    expected_versions = {x["function_name"]: {str(x["version"])} for x in functions}
    architecture = {x["function_name"]: x.get("architecture", "") for x in functions}
    configured_memory = {x["function_name"]: x.get("memory_mb") for x in functions}
    query_margin_seconds = int(
        a.query_margin_seconds
        if a.query_margin_seconds is not None
        else cfg["aws"].get("log_query_margin_seconds", 120)
    )
    query_margin_ms = query_margin_seconds * 1000
    collect_from = (
        a.collect_from_ms
        if a.collect_from_ms is not None
        else a.start_ms - query_margin_ms
    )
    query_end = a.end_ms + query_margin_ms

    rows_by_function = {}
    issues = []
    polls = 0
    last_total = -1
    stable_polls = 0
    deadline = time.time() + wait_seconds
    while True:
        polls += 1
        for fn in a.function:
            group = f"/aws/lambda/{fn}"
            try:
                events = fetch_events(logs, group, collect_from, query_end)
            except logs.exceptions.ResourceNotFoundException:
                issues.append(f"log group missing: {group}")
                rows_by_function[fn] = []
                continue
            rows_by_function[fn] = parse_events(
                events, fn, expected_versions, architecture, configured_memory,
                a.start_ms, a.warmup_seconds * 1000, a.total_seconds * 1000,
            )
        rows = [row for fn in a.function for row in rows_by_function.get(fn, [])]
        for row in rows:
            row["run_id"] = a.run_id
        missing = sum(not row["report_found"] for row in rows)
        count_ok = (
            a.expected_invocations is None
            or len(rows) + a.shortfall_allowed >= a.expected_invocations
        )
        if rows and missing == 0 and count_ok:
            break
        if time.time() >= deadline:
            break
        if len(rows) == last_total:
            stable_polls += 1
        else:
            stable_polls = 0
        last_total = len(rows)
        if a.expected_invocations is None and stable_polls >= 2:
            break
        time.sleep(poll_seconds)

    if len(rows) != last_total and last_total >= 0:
        stable_polls = 0
    missing = sum(not row["report_found"] for row in rows)
    if missing:
        issues.append(f"{missing} START records have no paired REPORT")
    if a.expected_invocations is not None and len(rows) + a.shortfall_allowed < a.expected_invocations:
        issues.append(
            f"collected {len(rows)} invocations, expected {a.expected_invocations} "
            f"(shortfall allowed by failed HTTP requests: {a.shortfall_allowed})"
        )
    per_function = {}
    expected_per_function = None
    shortfall_per_function = 0
    if a.composition and a.expected_invocations is not None:
        divisor = 1 if a.composition == "monolithic" else 4
        expected_per_function = a.expected_invocations // divisor
        shortfall_per_function = a.shortfall_allowed // divisor
    for fn in a.function:
        count = len(rows_by_function.get(fn, []))
        below = (
            expected_per_function is not None
            and count + shortfall_per_function < expected_per_function
        )
        per_function[fn] = {
            "collected": count,
            "expected": expected_per_function,
            "shortfall_allowed": shortfall_per_function,
            "below_expected": below,
        }
        if below:
            issues.append(
                f"{fn}: collected {count} invocations, expected {expected_per_function} "
                f"(shortfall allowed: {shortfall_per_function})"
            )
    status = "COMPLETE" if rows and missing == 0 and not issues else "INCOMPLETE"
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_write(out / "lambda-invocations.csv", rows, FIELDS)
    write_json(out / "lambda-collection-status.json", {
        "run_id": a.run_id,
        "collected_at_utc": utc_now(),
        "records": len(rows),
        "reports_missing": missing,
        "expected_invocations": a.expected_invocations,
        "shortfall_allowed": a.shortfall_allowed,
        "per_function": per_function,
        "wait_seconds": wait_seconds,
        "poll_seconds": poll_seconds,
        "polls": polls,
        "issues": issues,
        "status": status,
        "selection": (
            f"log query from reset through T0+240s plus {query_margin_seconds}s "
            "ingestion margin, filtered by the versions published in "
            "cold-reset.json; platform START/REPORT lines do not contain the "
            "application run_id. The margin is for START/REPORT pairing only: "
            "collect_cost.py attributes cost by START inside [T0, T0+240)."
        ),
        "query_margin_seconds": query_margin_seconds,
    })
    print(
        f"Collected {len(rows)} invocations; missing REPORT={missing}; "
        f"expected={a.expected_invocations}; status={status}; issues={issues}"
    )


if __name__ == "__main__":
    main()
