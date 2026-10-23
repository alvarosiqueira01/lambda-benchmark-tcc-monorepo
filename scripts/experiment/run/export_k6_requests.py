#!/usr/bin/env python3
"""Export per-request k6 samples and timing covariables to CSV."""
import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

TIMING_METRICS = {
    "bench_connect_ms": "connect_ms",
    "bench_tls_ms": "tls_ms",
    "bench_sending_ms": "sending_ms",
    "bench_waiting_ms": "waiting_ms",
    "bench_receiving_ms": "receiving_ms",
}

FIELDS = [
    "run_id", "request_sequence", "sample_id", "iteration",
    "scheduled_offset_ms", "scheduled_at_utc", "started_at_utc", "start_epoch_ms",
    "offset_ms", "phase", "latency_ms", "status_code", "error_code", "error",
    "timeout", "request_sent",
    "connect_ms", "tls_ms", "sending_ms", "waiting_ms", "receiving_ms",
    "new_connection", "new_tls", "scenario", "t0_epoch_ms",
    "sample_t0_epoch_ms", "sample_t0_source",
]


def parse_time_ms(value):
    if not value:
        return None
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--t0-epoch-ms", type=int, default=None)
    ap.add_argument("--summary-json", default=None)
    ap.add_argument("--warmup-seconds", type=int, default=120)
    ap.add_argument("--total-seconds", type=int, default=240)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    samples = {}
    with open(a.raw, encoding="utf-8") as handle:
        for line in handle:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "Point":
                continue
            metric = obj.get("metric")
            if metric not in ("bench_request_latency", *TIMING_METRICS):
                continue
            data = obj.get("data", {})
            tags = data.get("tags") or {}
            sample_id = tags.get("sample_id") or tags.get("iteration")
            if sample_id is None:
                continue
            sample = samples.setdefault(sample_id, {"tags": tags, "timings": {}})
            if metric == "bench_request_latency":
                value = float(data.get("value") or 0)
                sampled_ms = parse_time_ms(data.get("time"))
                sample["latency_ms"] = value
                sample["end_ms"] = sampled_ms
                sample["start_epoch_ms"] = (
                    sampled_ms - round(value) if sampled_ms is not None else None
                )
            else:
                sample["timings"][TIMING_METRICS[metric]] = float(data.get("value") or 0)

    if not samples:
        raise SystemExit(
            "No bench_request_latency samples; check k6 version/output format and custom Trend."
        )

    sample_t0_values = {
        int(sample["tags"]["t0_epoch_ms"])
        for sample in samples.values()
        if sample["tags"].get("t0_epoch_ms")
    }
    sample_t0_sources = {
        sample["tags"]["t0_source"]
        for sample in samples.values()
        if sample["tags"].get("t0_source")
    }
    if len(sample_t0_values) > 1:
        raise SystemExit(
            "Inconsistent t0_epoch_ms tags across request samples: "
            f"{sorted(sample_t0_values)}. The k6 fallback T0 is VU-local; "
            "rerun with a valid exec.scenario.startTime."
        )
    if len(sample_t0_sources) > 1:
        raise SystemExit(
            "Inconsistent t0_source tags across request samples: "
            f"{sorted(sample_t0_sources)}"
        )

    t0 = a.t0_epoch_ms
    t0_source = "argument"
    if t0 is None:
        tag_values = sorted(sample_t0_values)
        if tag_values:
            t0 = tag_values[0]
            t0_source = "k6_sample_tag"
    if t0 is not None and sample_t0_values and int(t0) != next(iter(sample_t0_values)):
        raise SystemExit(
            f"T0 argument {t0} differs from request sample tag "
            f"{next(iter(sample_t0_values))}"
        )
    if t0 is None and a.summary_json and Path(a.summary_json).exists():
        summary = json.loads(Path(a.summary_json).read_text())
        t0 = summary.get("t0_epoch_ms")
        t0_source = "k6_summary"
    if t0 is None:
        starts = [
            sample["start_epoch_ms"]
            for sample in samples.values()
            if sample.get("start_epoch_ms") is not None
        ]
        t0 = min(starts) if starts else None
        t0_source = "first_request_fallback"
    if t0 is None:
        raise SystemExit("Could not determine T0 from argument, sample tags or summary")
    t0 = int(t0)

    rows = []
    for sample in samples.values():
        tags = sample["tags"]
        start_ms = sample.get("start_epoch_ms")
        if start_ms is None:
            continue
        timings = sample["timings"]
        offset = start_ms - t0
        phase = tags.get("phase")
        if not phase:
            if offset < a.warmup_seconds * 1000:
                phase = "warmup"
            elif offset < a.total_seconds * 1000:
                phase = "measurement"
            else:
                phase = "outside_run"
        connect_ms = timings.get("connect_ms", 0.0)
        tls_ms = timings.get("tls_ms", 0.0)
        scheduled_offset = tags.get("scheduled_offset_ms")
        rows.append({
            "run_id": tags.get("run_id", ""),
            "request_sequence": None,
            "sample_id": tags.get("sample_id", ""),
            "iteration": tags.get("iteration", ""),
            "scheduled_offset_ms": scheduled_offset,
            "scheduled_at_utc": (
                datetime.fromtimestamp(
                    (t0 + float(scheduled_offset)) / 1000, UTC
                ).isoformat()
                if scheduled_offset not in (None, "") else None
            ),
            "started_at_utc": datetime.fromtimestamp(start_ms / 1000, UTC).isoformat(),
            "start_epoch_ms": start_ms,
            "offset_ms": offset,
            "phase": phase,
            "latency_ms": sample.get("latency_ms"),
            "status_code": tags.get("status_code", ""),
            "error_code": tags.get("error_code", "0"),
            "error": tags.get("error", ""),
            "timeout": tags.get("timeout", "false"),
            "request_sent": tags.get("request_sent", "true"),
            "connect_ms": connect_ms,
            "tls_ms": tls_ms,
            "sending_ms": timings.get("sending_ms", 0.0),
            "waiting_ms": timings.get("waiting_ms", 0.0),
            "receiving_ms": timings.get("receiving_ms", 0.0),
            "new_connection": connect_ms > 0,
            "new_tls": tls_ms > 0,
            "scenario": tags.get("scenario", ""),
            "t0_epoch_ms": t0,
            "sample_t0_epoch_ms": tags.get("t0_epoch_ms", ""),
            "sample_t0_source": tags.get("t0_source", ""),
        })
    rows.sort(key=lambda row: row["start_epoch_ms"])
    for index, row in enumerate(rows, 1):
        row["request_sequence"] = index

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"{len(rows)} request samples -> {out} "
        f"(t0={t0} ms, source={t0_source})"
    )


if __name__ == "__main__":
    main()
