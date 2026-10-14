#!/usr/bin/env python3
"""Extract Duration statistics from CloudWatch REPORT events."""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

DUR_RE = re.compile(r"Duration:\s+([\d.]+)\s+ms")
BILLED_RE = re.compile(r"Billed Duration:\s+([\d.]+)\s+ms")
MEM_SIZE_RE = re.compile(r"Memory Size:\s+(\d+)\s+MB")
MAX_MEM_RE = re.compile(r"Max Memory Used:\s+(\d+)\s+MB")
INIT_RE = re.compile(r"Init Duration:\s+([\d.]+)\s+ms")


def percentile(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize(values):
    if not values:
        return {}
    return {
        "min": min(values),
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
        "mean": statistics.fmean(values),
        "stdev": statistics.pstdev(values) if len(values) > 1 else 0.0,
    }


def extract(events):
    durations = []
    warm_durations = []
    billed = []
    max_mem = []
    init_dur = []
    cold_count = 0

    for ev in events:
        msg = ev.get("message", "")
        m = DUR_RE.search(msg)
        if not m:
            continue
        d = float(m.group(1))
        durations.append(d)

        b = BILLED_RE.search(msg)
        if b:
            billed.append(float(b.group(1)))

        mm = MAX_MEM_RE.search(msg)
        if mm:
            max_mem.append(int(mm.group(1)))

        if INIT_RE.search(msg):
            init_dur.append(float(INIT_RE.search(msg).group(1)))
            cold_count += 1
        else:
            warm_durations.append(d)

    if not durations:
        return {"count": 0, "error": "no REPORT lines found"}

    total_dur = sum(durations)
    total_init = sum(init_dur) if init_dur else 0.0

    result = {
        "count": len(durations),
        "warm_count": len(warm_durations),
        "cold_count": cold_count,
        "cold_start_rate": cold_count / len(durations),
        "cold_start_fraction_of_total": (total_init / total_dur) if total_dur > 0 else 0.0,
        "duration_ms": summarize(durations),
        "billed_duration_ms": {
            "mean": statistics.fmean(billed) if billed else 0.0,
            "max": max(billed) if billed else 0.0,
        },
        "max_memory_used_mb": {
            "max": max(max_mem) if max_mem else 0,
            "mean": statistics.fmean(max_mem) if max_mem else 0.0,
        },
        "init_duration_ms": {
            "count": len(init_dur),
            "mean": statistics.fmean(init_dur) if init_dur else 0.0,
            "max": max(init_dur) if init_dur else 0.0,
        },
    }

    if warm_durations:
        result["duration_ms_warm_only"] = summarize(warm_durations)

    return result


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        data = json.loads(Path(path).read_text())
    else:
        data = json.load(sys.stdin)
    events = data.get("events", [])
    print(json.dumps(extract(events), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())