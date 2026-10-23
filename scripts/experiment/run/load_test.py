#!/usr/bin/env python3
"""Run k6 through SSM, recover artifacts and record the k6-reported T0."""
import argparse
import base64
import csv
import json
import re
import shlex
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import load_yaml, sha256_file, utc_now, write_json

RESOURCE_FIELDS = [
    "run_id", "timestamp_ms", "timestamp_utc", "offset_ms", "cpu_percent",
    "mem_used_percent", "load1", "procs_running", "procs_blocked",
    "rx_bytes_per_s", "tx_bytes_per_s", "rx_packets_per_s", "tx_packets_per_s",
]


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_sampler_rows(path):
    if not path.exists():
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            row = {}
            for key, value in raw.items():
                if key == "timestamp_ms":
                    row[key] = int(float(value)) if value else None
                else:
                    row[key] = _to_float(value)
            if row.get("timestamp_ms") is not None:
                rows.append(row)
    rows.sort(key=lambda item: item["timestamp_ms"])
    return rows


def derived_samples(rows, t0_ms):
    samples = []
    cpu_fields = [
        "cpu_user", "cpu_nice", "cpu_system", "cpu_idle", "cpu_iowait",
        "cpu_irq", "cpu_softirq", "cpu_steal",
    ]
    previous = None
    for row in rows:
        sample = {
            "timestamp_ms": row["timestamp_ms"],
            "offset_ms": row["timestamp_ms"] - t0_ms if t0_ms is not None else None,
            "mem_used_percent": None,
            "cpu_percent": None,
            "load1": row.get("load1"),
            "procs_running": row.get("procs_running"),
            "procs_blocked": row.get("procs_blocked"),
            "rx_bytes_per_s": None,
            "tx_bytes_per_s": None,
            "rx_packets_per_s": None,
            "tx_packets_per_s": None,
        }
        total_mem = row.get("mem_total_kb") or 0
        available = row.get("mem_available_kb")
        if total_mem > 0 and available is not None:
            sample["mem_used_percent"] = max(
                0.0, min(100.0, 100.0 * (1.0 - available / total_mem))
            )
        if previous is not None:
            dt_s = (row["timestamp_ms"] - previous["timestamp_ms"]) / 1000
            if dt_s > 0:
                cpu_total = sum(
                    (row.get(field) or 0) - (previous.get(field) or 0)
                    for field in cpu_fields
                )
                cpu_idle = (row.get("cpu_idle") or 0) - (
                    previous.get("cpu_idle") or 0
                )
                if cpu_total > 0:
                    sample["cpu_percent"] = max(
                        0.0, min(100.0, 100.0 * (1.0 - cpu_idle / cpu_total))
                    )
                sample["rx_bytes_per_s"] = max(
                    0.0,
                    ((row.get("net_rx_bytes") or 0)
                     - (previous.get("net_rx_bytes") or 0)) / dt_s,
                )
                sample["tx_bytes_per_s"] = max(
                    0.0,
                    ((row.get("net_tx_bytes") or 0)
                     - (previous.get("net_tx_bytes") or 0)) / dt_s,
                )
                sample["rx_packets_per_s"] = max(
                    0.0,
                    ((row.get("net_rx_packets") or 0)
                     - (previous.get("net_rx_packets") or 0)) / dt_s,
                )
                sample["tx_packets_per_s"] = max(
                    0.0,
                    ((row.get("net_tx_packets") or 0)
                     - (previous.get("net_tx_packets") or 0)) / dt_s,
                )
        sample["rx_dropped_cumulative"] = row.get("net_rx_dropped")
        sample["tx_dropped_cumulative"] = row.get("net_tx_dropped")
        samples.append(sample)
        previous = row
    return samples


def summarize_samples(samples):
    def values(key):
        return [item[key] for item in samples if item.get(key) is not None]

    def stats(items, prefix):
        if not items:
            return {f"{prefix}_avg": None, f"{prefix}_max": None}
        return {
            f"{prefix}_avg": sum(items) / len(items),
            f"{prefix}_max": max(items),
        }

    cpu = values("cpu_percent")
    memory = values("mem_used_percent")
    rx = values("rx_bytes_per_s")
    tx = values("tx_bytes_per_s")
    load = values("load1")
    blocked = values("procs_blocked")
    rx_dropped = values("rx_dropped_cumulative")
    tx_dropped = values("tx_dropped_cumulative")
    return {
        "cpu": stats(cpu, "percent"),
        "memory": {"max_used_percent": max(memory) if memory else None,
                   "avg_used_percent": sum(memory) / len(memory) if memory else None},
        "network": {
            **stats(rx, "rx_bytes_per_s"),
            **stats(tx, "tx_bytes_per_s"),
            "rx_dropped_packets": (
                max(rx_dropped) - min(rx_dropped) if rx_dropped else None
            ),
            "tx_dropped_packets": (
                max(tx_dropped) - min(tx_dropped) if tx_dropped else None
            ),
        },
        "load": {"max_1m": max(load) if load else None},
        "procs": {"max_blocked": max(blocked) if blocked else None},
    }


def write_generator_resources(out, run_id, samples, t0_ms, total_ms,
                              warmup_ms, interval_seconds, sampler_sha, cfg):
    generator_cfg = cfg.get("generator") or {}
    thresholds = {
        "max_cpu_utilization_pct": float(
            generator_cfg.get("max_cpu_utilization_pct", 85)
        ),
        "max_memory_used_pct": float(
            generator_cfg.get("max_memory_used_pct", 85)
        ),
        "max_network_dropped_packets": int(
            generator_cfg.get("max_network_dropped_packets", 0)
        ),
    }
    csv_rows = []
    for sample in samples:
        csv_rows.append({
            "run_id": run_id,
            "timestamp_ms": sample["timestamp_ms"],
            "timestamp_utc": datetime.fromtimestamp(
                sample["timestamp_ms"] / 1000, UTC
            ).isoformat(),
            "offset_ms": sample["offset_ms"],
            "cpu_percent": sample["cpu_percent"],
            "mem_used_percent": sample["mem_used_percent"],
            "load1": sample["load1"],
            "procs_running": sample["procs_running"],
            "procs_blocked": sample["procs_blocked"],
            "rx_bytes_per_s": sample["rx_bytes_per_s"],
            "tx_bytes_per_s": sample["tx_bytes_per_s"],
            "rx_packets_per_s": sample["rx_packets_per_s"],
            "tx_packets_per_s": sample["tx_packets_per_s"],
        })
    csv_path = out / "generator-resources.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESOURCE_FIELDS)
        writer.writeheader()
        writer.writerows(csv_rows)

    end_ms = t0_ms + total_ms if t0_ms is not None else None
    tolerance_ms = max(2000, int(float(interval_seconds) * 2000))
    first_ms = samples[0]["timestamp_ms"] if samples else None
    last_ms = samples[-1]["timestamp_ms"] if samples else None
    covers = bool(
        samples and t0_ms is not None and first_ms is not None
        and last_ms is not None
        and first_ms <= t0_ms
        and last_ms >= end_ms - tolerance_ms
    )
    measurement = [
        item for item in samples
        if t0_ms is not None
        and t0_ms + warmup_ms <= item["timestamp_ms"] < end_ms
    ] if end_ms is not None else []
    summary = summarize_samples(samples)
    issues = []
    if not samples:
        issues.append("generator sampler produced no samples")
    elif len(samples) < 2:
        issues.append("generator sampler produced fewer than two samples")
    if samples and not covers:
        issues.append(
            "generator sampler does not cover the full run window "
            f"[{t0_ms}, {end_ms})"
        )
    cpu_max = summary["cpu"]["percent_max"]
    memory_max = summary["memory"]["max_used_percent"]
    rx_dropped = summary["network"]["rx_dropped_packets"]
    tx_dropped = summary["network"]["tx_dropped_packets"]
    saturation = []
    if cpu_max is not None and cpu_max > thresholds["max_cpu_utilization_pct"]:
        saturation.append(
            f"cpu max {cpu_max:.1f}% > {thresholds['max_cpu_utilization_pct']}%"
        )
    if (
        memory_max is not None
        and memory_max > thresholds["max_memory_used_pct"]
    ):
        saturation.append(
            f"memory max {memory_max:.1f}% > "
            f"{thresholds['max_memory_used_pct']}%"
        )
    dropped_total = sum(
        value for value in (rx_dropped, tx_dropped) if value is not None
    )
    if dropped_total > thresholds["max_network_dropped_packets"]:
        saturation.append(
            f"dropped packets {dropped_total:.0f} > "
            f"{thresholds['max_network_dropped_packets']}"
        )
    issues.extend(f"generator saturation: {item}" for item in saturation)
    status = {
        "run_id": run_id,
        "collected_at_utc": utc_now(),
        "source": "ssm_proc_sampler",
        "sampler_script_sha256": sampler_sha,
        "interval_seconds": interval_seconds,
        "raw_file": str(out / "generator-resources-raw.csv"),
        "samples": len(samples),
        "first_timestamp_ms": first_ms,
        "last_timestamp_ms": last_ms,
        "window": {"t0_epoch_ms": t0_ms, "end_epoch_ms": end_ms},
        "covers_window": covers,
        "thresholds": thresholds,
        "summary": summary,
        "measurement_summary": summarize_samples(measurement),
        "saturated": bool(saturation),
        "ok": bool(samples) and len(samples) >= 2 and covers,
        "issues": issues,
    }
    write_json(out / "generator-resources-status.json", status)
    return status


def metric_count(metrics, name):
    metric = (metrics or {}).get(name) or {}
    values = metric.get("values") or {}
    if "count" in values:
        return values["count"]
    if "value" in values:
        return values["value"]
    return None


def extract_t0_from_raw(path):
    """Fallback T0 from the Gauge or first request sample when summary is missing.

    Returns the value and the real source so a VU-local Date.now() fallback
    cannot be reported as a generator T0.
    """
    gauge_t0 = None
    tag_t0 = None
    first_start = None
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "Point":
                continue
            if obj.get("metric") == "scenario_start_epoch_ms":
                value = obj.get("data", {}).get("value")
                if value and gauge_t0 is None:
                    gauge_t0 = int(value)
            if obj.get("metric") == "bench_request_latency" and first_start is None:
                data = obj.get("data", {})
                tags = data.get("tags") or {}
                value = data.get("value")
                time_ms = data.get("time")
                if tags.get("t0_epoch_ms") and tag_t0 is None:
                    tag_t0 = int(tags["t0_epoch_ms"])
                if value is not None and time_ms:
                    from datetime import datetime
                    end_ms = int(
                        datetime.fromisoformat(time_ms).timestamp() * 1000
                    )
                    first_start = end_ms - round(float(value))
    if gauge_t0 is not None:
        return gauge_t0, "k6_raw_gauge"
    if tag_t0 is not None:
        return tag_t0, "k6_raw_sample_tag"
    if first_start is not None:
        return first_start, "k6_raw_request_fallback"
    return None, None


def validate_run_id(run_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id):
        raise SystemExit(
            "run_id must start with an alphanumeric character and contain only "
            f"letters, digits, dot, underscore or hyphen: {run_id!r}"
        )


def read_storage_artifacts(out):
    artifacts = {}
    for name in (
        "storage-before.txt",
        "storage-preflight.txt",
        "storage-before-cleanup.txt",
    ):
        path = Path(out) / name
        if not path.exists():
            continue
        fields = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
        artifacts[name.removesuffix(".txt")] = fields
    return artifacts


def read_key_values(path):
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def parse_chrony(path):
    if not path.exists():
        return {"available": False, "error": "missing chrony tracking output"}
    text = path.read_text(encoding="utf-8", errors="replace")
    if "chronyc not found" in text:
        return {"available": False, "error": "chronyc not installed on generator"}
    offset = None
    direction = None
    leap_status = None
    for line in text.splitlines():
        match = re.match(
            r"System time\s*:\s*([+-]?[0-9.]+)\s+seconds\s+(fast|slow)",
            line.strip(),
        )
        if match:
            offset = abs(float(match.group(1))) * 1000
            direction = match.group(2)
        leap = re.match(r"Leap status\s*:\s*(.+)", line.strip())
        if leap:
            leap_status = leap.group(1).strip()
    return {
        "available": offset is not None,
        "offset_ms": offset,
        "direction": direction,
        "leap_status": leap_status,
        "raw": text[-2000:],
    }


def read_clock_artifacts(out, require_chrony=True, max_offset_ms=50):
    before = parse_chrony(Path(out) / "chrony-before.txt")
    after = parse_chrony(Path(out) / "chrony-after.txt")
    offsets = [
        item["offset_ms"] for item in (before, after)
        if item.get("offset_ms") is not None
    ]
    checks = {
        "before_available": before.get("available") is True,
        "after_available": after.get("available") is True,
        "leap_normal": all(
            item.get("leap_status") in (None, "Normal") for item in (before, after)
        ),
        "offset_within_tolerance": bool(offsets) and max(offsets) <= float(max_offset_ms),
    }
    return {
        "required": require_chrony,
        "max_offset_ms": float(max_offset_ms),
        "before": before,
        "after": after,
        "max_observed_offset_ms": max(offsets) if offsets else None,
        "checks": checks,
        "ok": all(checks.values()) if require_chrony else True,
    }


def build_remote_command(
    *,
    remote_root,
    remote,
    s3_uri,
    k6_script,
    sampler_b64,
    sampler_sha,
    sampler_interval,
    envs,
    min_free_disk_mb,
    cleanup_remote_after_upload,
    k6_expected_version="",
    require_chrony=True,
):
    q = shlex.quote
    remote_q = q(remote)
    remote_root_q = q(remote_root)
    s3_uri = s3_uri.rstrip("/") + "/"
    s3_uri_q = q(s3_uri)
    k6_script_q = q(k6_script)
    sampler_b64_q = q(sampler_b64)
    sampler_sha_q = q(sampler_sha)
    sampler_interval_q = q(str(sampler_interval))
    k6_expected_version_q = q(str(k6_expected_version or ""))
    require_chrony_value = "1" if require_chrony else "0"
    min_free_kb = int(min_free_disk_mb) * 1024
    exports = "; ".join(f"export {key}={q(str(value))}" for key, value in envs.items())
    cleanup_block = ""
    if cleanup_remote_after_upload:
        cleanup_block = (
            "cleanup_status=0; "
            "case \"$remote\" in \"$remote_root\"/*) "
            "rm -rf -- \"$remote\" || cleanup_status=$?;; "
            "*) printf 'Refusing to remove path outside remote_output_dir: %s\\n' "
            "\"$remote\" >&2; cleanup_status=1;; esac; "
            "if [ \"$cleanup_status\" -ne 0 ] || [ -e \"$remote\" ]; then "
            "printf 'WARNING: remote staging cleanup failed for %s\\n' "
            "\"$remote\" >&2; fi; "
        )

    # Keep this compatible with /bin/sh on Amazon Linux. The k6 exit status is
    # preserved, artifacts are uploaded even after a failed k6 run, and the
    # per-run staging directory is removed only after a successful upload.
    return (
        f"set -u; "
        f"remote={remote_q}; remote_root={remote_root_q}; "
        f"mkdir -p \"$remote\"; {exports}; "
        f"date -u +%Y-%m-%dT%H:%M:%SZ > \"$remote/shell-start-utc.txt\"; "
        f"date -u +%s%3N > \"$remote/shell_start_epoch_ms.txt\"; "
        f"k6 version > \"$remote/k6-version.txt\"; "
        f"k6_status=0; clock_ok=1; "
        f"if command -v chronyc >/dev/null 2>&1; then "
        f"chronyc tracking > \"$remote/chrony-before.txt\" 2>&1 || true; "
        f"else printf 'chronyc not found\n' > \"$remote/chrony-before.txt\"; clock_ok=0; fi; "
        f"printf 'ok=%s\n' \"$clock_ok\" > \"$remote/chrony-preflight.txt\"; "
        f"sha256sum {k6_script_q} > \"$remote/k6-script-sha256.txt\"; "
        f"printf '%s' {sampler_b64_q} | base64 -d "
        f"> \"$remote/generator_sampler.sh\"; "
        f"chmod +x \"$remote/generator_sampler.sh\"; "
        f"printf '%s\\n' {sampler_sha_q} "
        f"> \"$remote/generator-sampler-sha256.txt\"; "
        f"free_kb=$(df -Pk \"$remote\" 2>/dev/null | awk 'NR==2 {{print $4}}'); "
        f"case \"$free_kb\" in ''|*[!0-9]*) free_kb=0;; esac; "
        f"run_dir_bytes=$(du -sb \"$remote\" 2>/dev/null | awk '{{print $1}}'); "
        f"case \"$run_dir_bytes\" in ''|*[!0-9]*) run_dir_bytes=0;; esac; "
        f"printf 'captured_at_utc=%s\\nfree_kb=%s\\nrun_dir_bytes=%s\\n' "
        f"\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" \"$free_kb\" \"$run_dir_bytes\" "
        f"> \"$remote/storage-before.txt\"; "
        f"min_free_kb={min_free_kb}; "
        f"if [ \"$free_kb\" -lt \"$min_free_kb\" ]; then "
        f"printf 'ok=false\\nfree_kb=%s\\nmin_free_kb=%s\\n' "
        f"\"$free_kb\" \"$min_free_kb\" > \"$remote/storage-preflight.txt\"; "
        f"k6_status=75; "
        f"else "
        f"printf 'ok=true\\nfree_kb=%s\\nmin_free_kb=%s\\n' "
        f"\"$free_kb\" \"$min_free_kb\" > \"$remote/storage-preflight.txt\"; "
        f"if [ \"$clock_ok\" -ne 1 ] && [ {require_chrony_value} -eq 1 ]; then "
        f"k6_status=77; printf 'ok=false\\nreason=chronyc_not_found\\n' "
        f"> \"$remote/load-preflight.txt\"; "
        f"elif [ -n {k6_expected_version_q} ] && "
        f"! grep -Fqx {k6_expected_version_q} \"$remote/k6-version.txt\"; then "
        f"k6_status=76; printf 'ok=false\\nreason=k6_version_mismatch\\nexpected=%s\\nactual=%s\\n' "
        f"{k6_expected_version_q} \"$(cat \"$remote/k6-version.txt\")\" "
        f"> \"$remote/load-preflight.txt\"; "
        f"else "
        f"printf 'ok=true\\n' > \"$remote/load-preflight.txt\"; "
        f"\"$remote/generator_sampler.sh\" \"$remote/generator-resources-raw.csv\" "
        f"{sampler_interval_q} & sampler_pid=$!; "
        f"k6 run --out json={q(remote + '/k6-raw.json')} "
        f"{k6_script_q} || k6_status=$?; "
        f"kill \"$sampler_pid\" 2>/dev/null || true; "
        f"wait \"$sampler_pid\" 2>/dev/null || true; "
        f"fi; "
        f"fi; "
        f"printf '%s\\n' \"$k6_status\" > \"$remote/k6-exit-code.txt\"; "
        f"if command -v chronyc >/dev/null 2>&1; then "
        f"chronyc tracking > \"$remote/chrony-after.txt\" 2>&1 || true; "
        f"else printf 'chronyc not found\\n' > \"$remote/chrony-after.txt\"; fi; "
        f"date -u +%s%3N > \"$remote/shell_end_epoch_ms.txt\"; "
        f"free_kb=$(df -Pk \"$remote\" 2>/dev/null | awk 'NR==2 {{print $4}}'); "
        f"case \"$free_kb\" in ''|*[!0-9]*) free_kb=0;; esac; "
        f"run_dir_bytes=$(du -sb \"$remote\" 2>/dev/null | awk '{{print $1}}'); "
        f"case \"$run_dir_bytes\" in ''|*[!0-9]*) run_dir_bytes=0;; esac; "
        f"printf 'captured_at_utc=%s\\nfree_kb=%s\\nrun_dir_bytes=%s\\n' "
        f"\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" \"$free_kb\" \"$run_dir_bytes\" "
        f"> \"$remote/storage-before-cleanup.txt\"; "
        f"upload_status=0; aws s3 cp \"$remote\" {s3_uri_q} "
        f"--recursive || upload_status=$?; "
        f"printf '%s\\n' \"$upload_status\" > \"$remote/upload-exit-code.txt\"; "
        f"aws s3 cp \"$remote/upload-exit-code.txt\" "
        f"{q(s3_uri + 'upload-exit-code.txt')} || true; "
        f"if [ \"$upload_status\" -ne 0 ]; then exit \"$upload_status\"; fi; "
        f"{cleanup_block}"
        f"exit \"$k6_status\""
    )


def wait_for_invocation(ssm, command_id, instance_id, timeout_seconds):
    deadline = time.time() + timeout_seconds
    time.sleep(2)  # SSM get_command_invocation is eventually consistent
    last_error = None
    while time.time() < deadline:
        try:
            invocation = ssm.get_command_invocation(
                CommandId=command_id, InstanceId=instance_id
            )
            if invocation["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
                return invocation
            last_error = None
        except ssm.exceptions.InvocationDoesNotExist as exc:
            last_error = exc
        time.sleep(3)
    suffix = f" (last error: {last_error})" if last_error else ""
    raise TimeoutError(f"SSM command timeout: {command_id}{suffix}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="experiments/harness-config.yaml")
    ap.add_argument("--plan", default="experiments/experiment-plan.yaml")
    ap.add_argument("--composition", required=True, choices=["monolithic", "decomposed"])
    ap.add_argument("--pattern", required=True,
                    choices=["constant", "burst", "sparse_miniburst"])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--document-id", required=True)
    a = ap.parse_args()
    validate_run_id(a.run_id)
    cfg = load_yaml(a.config)
    plan = load_yaml(a.plan)
    generator = cfg["generator"]
    aws = cfg["aws"]
    if not generator.get("instance_id") or not aws.get("s3_bucket"):
        raise SystemExit("Configure generator.instance_id and aws.s3_bucket")

    sampler_path = Path(
        generator.get("local_sampler_script", "k6/generator_sampler.sh")
    )
    if not sampler_path.exists():
        raise SystemExit(f"Generator sampler script not found: {sampler_path}")
    sampler_sha = sha256_file(sampler_path)
    sampler_b64 = base64.b64encode(sampler_path.read_bytes()).decode("ascii")
    sampler_interval = generator.get("telemetry_interval_seconds", 1)

    route_cfg = cfg["api"]["routes"][a.composition]
    template = route_cfg["route_path_template"]
    if "{id}" not in template:
        raise SystemExit(f"route_path_template para {a.composition} deve conter {{id}}: {template}")
    resolved_path = template.replace("{id}", a.document_id)
    target_url = route_cfg["base_url"].rstrip("/") + resolved_path

    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "target-url.json", {
        "run_id": a.run_id,
        "composition": a.composition,
        "pattern": a.pattern,
        "target_url": target_url,
        "api_id": route_cfg["api_id"],
        "route_path_template": template,
        "resolved_path": resolved_path,
        "route_method": cfg["api"]["route_method"],
        "resolved_at_utc": utc_now(),
    })

    import boto3

    session = boto3.Session(profile_name=aws.get("profile"), region_name=aws["region"])
    ssm = session.client("ssm")
    s3 = session.client("s3")
    bucket = aws["s3_bucket"]
    remote_root = generator["remote_output_dir"].rstrip("/")
    if not remote_root or remote_root == "/":
        raise SystemExit("generator.remote_output_dir must be a non-root path")
    remote = f"{remote_root}/{a.run_id}"
    prefix = f"{aws['s3_prefix'].strip('/')}/raw-generator/{a.run_id}"
    s3_uri = f"s3://{bucket}/{prefix}/"
    k6_script = generator["remote_k6_script"]
    min_free_disk_mb = int(generator.get("min_free_disk_mb", 1024))
    if min_free_disk_mb < 0:
        raise SystemExit("generator.min_free_disk_mb must be zero or positive")
    cleanup_remote_after_upload = bool(
        generator.get("cleanup_remote_after_upload", True)
    )
    envs = {
        "RUN_ID": a.run_id,
        "LOAD_PATTERN": a.pattern,
        "TARGET_URL": target_url,
        "OUT_DIR": remote,
        "PREALLOCATED_VUS": str(generator.get("pre_allocated_vus", 100)),
        "MAX_VUS": str(generator.get("max_vus", 500)),
        "WARMUP_SECONDS": str(plan["warmup_seconds"]),
        "MEASUREMENT_SECONDS": str(plan["measurement_seconds"]),
        "TOTAL_SECONDS": str(plan["total_duration_seconds"]),
    }
    cmd = build_remote_command(
        remote_root=remote_root,
        remote=remote,
        s3_uri=s3_uri,
        k6_script=k6_script,
        sampler_b64=sampler_b64,
        sampler_sha=sampler_sha,
        sampler_interval=sampler_interval,
        envs=envs,
        min_free_disk_mb=min_free_disk_mb,
        cleanup_remote_after_upload=cleanup_remote_after_upload,
        k6_expected_version=generator.get("k6_expected_version", ""),
        require_chrony=bool(generator.get("require_chrony", True)),
    )
    timeout = int(generator.get("ssm_timeout_seconds", 900))
    response = ssm.send_command(
        InstanceIds=[generator["instance_id"]],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [cmd], "executionTimeout": [str(timeout)]},
        TimeoutSeconds=timeout,
    )
    command_id = response["Command"]["CommandId"]
    invocation = wait_for_invocation(ssm, command_id, generator["instance_id"], timeout + 60)
    status = {
        "run_id": a.run_id,
        "command_id": command_id,
        "ssm_status": invocation["Status"],
        "composition": a.composition,
        "target_url": target_url,
        "stdout": invocation.get("StandardOutputContent", "")[-4000:],
        "stderr": invocation.get("StandardErrorContent", "")[-4000:],
        "s3_prefix": f"s3://{bucket}/{prefix}/",
        "storage_policy": {
            "cleanup_remote_after_upload": cleanup_remote_after_upload,
            "min_free_disk_mb": min_free_disk_mb,
        },
        "collected_at_utc": utc_now(),
    }
    write_json(out / "load-test-status.json", status)

    objects = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix + "/"):
        objects.extend(page.get("Contents", []))
    if not objects:
        raise RuntimeError(
            f"No generator artifacts in s3://{bucket}/{prefix}/; SSM status={invocation['Status']}"
        )
    for obj in objects:
        rel = obj["Key"][len(prefix) + 1:]
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, obj["Key"], str(dest))
    exit_code_path = out / "k6-exit-code.txt"
    if exit_code_path.exists():
        status["k6_exit_code"] = exit_code_path.read_text(encoding="utf-8").strip()
    storage = read_storage_artifacts(out)
    status["storage"] = storage
    clock = read_clock_artifacts(
        out,
        require_chrony=bool(generator.get("require_chrony", True)),
        max_offset_ms=float(generator.get("max_clock_offset_ms", 50)),
    )
    status["clock"] = clock
    load_preflight = read_key_values(out / "load-preflight.txt")
    status["load_preflight"] = load_preflight
    write_json(out / "clock.json", clock)
    write_json(out / "load-test-status.json", status)
    if load_preflight.get("ok") == "false":
        raise RuntimeError(
            "Generator load preflight failed; k6 was not started: "
            + json.dumps(load_preflight)
        )
    preflight = storage.get("storage-preflight") or {}
    if preflight.get("ok") == "false":
        raise RuntimeError(
            "Generator disk preflight failed; k6 was not started: "
            f"free_kb={preflight.get('free_kb')}, "
            f"min_free_kb={preflight.get('min_free_kb')}"
        )
    ssm_failure = None
    if invocation["Status"] != "Success":
        ssm_failure = (
            f"k6/SSM command failed (status={invocation['Status']}, "
            f"k6_exit_code={status.get('k6_exit_code')}); "
            f"generator artifacts were still downloaded to {out}: {json.dumps(status)}"
        )

    local_script = Path(generator.get("local_k6_script", "k6/benchmark.js"))
    local_script_sha = sha256_file(local_script) if local_script.exists() else None
    remote_hash_file = out / "k6-script-sha256.txt"
    remote_script_sha = None
    if remote_hash_file.exists():
        parts = remote_hash_file.read_text(encoding="utf-8").strip().split()
        remote_script_sha = parts[0] if parts else None
    if local_script_sha and remote_script_sha and local_script_sha != remote_script_sha:
        raise RuntimeError(
            "The k6 script on the generator does not match the repository copy: "
            f"local={local_script_sha}, remote={remote_script_sha}. Copy k6/benchmark.js "
            "to the EC2 host and rerun."
        )

    summary_path = out / "k6-summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    t0 = summary.get("t0_epoch_ms")
    t0_source = summary.get("t0_source", "k6_summary")
    if not t0:
        t0, t0_source = extract_t0_from_raw(out / "k6-raw.json")
    if not t0:
        sampler_rows = read_sampler_rows(out / "generator-resources-raw.csv")
        write_generator_resources(
            out, a.run_id, derived_samples(sampler_rows, None), None,
            int(plan["total_duration_seconds"]) * 1000,
            int(plan["warmup_seconds"]) * 1000,
            sampler_interval, sampler_sha, cfg,
        )
        raise RuntimeError("k6 did not report T0 in summary or raw output")
    t0 = int(t0)
    shell_start = None
    shell_end = None
    if (out / "shell_start_epoch_ms.txt").exists():
        shell_start = int((out / "shell_start_epoch_ms.txt").read_text().strip())
    if (out / "shell_end_epoch_ms.txt").exists():
        shell_end = int((out / "shell_end_epoch_ms.txt").read_text().strip())
    k6_version = (out / "k6-version.txt").read_text(encoding="utf-8").strip() \
        if (out / "k6-version.txt").exists() else None
    expected_k6_version = generator.get("k6_expected_version")
    if expected_k6_version and k6_version != expected_k6_version:
        raise RuntimeError(
            "The k6 version on the generator does not match the configured "
            f"version: expected={expected_k6_version!r}, actual={k6_version!r}"
        )

    sampler_rows = read_sampler_rows(out / "generator-resources-raw.csv")
    sampler_samples = derived_samples(sampler_rows, t0)
    generator_resources = write_generator_resources(
        out, a.run_id, sampler_samples, t0,
        int(plan["total_duration_seconds"]) * 1000,
        int(plan["warmup_seconds"]) * 1000,
        sampler_interval, sampler_sha, cfg,
    )

    metrics = summary.get("metrics", {})
    loadgen_summary = {
        "run_id": a.run_id,
        "pattern": a.pattern,
        "t0_epoch_ms": t0,
        "t0_source": t0_source,
        "tend_epoch_ms": shell_end,
        "k6_version": k6_version,
        "k6_script_sha256_local": local_script_sha,
        "k6_script_sha256_remote": remote_script_sha,
        "pre_allocated_vus": generator.get("pre_allocated_vus"),
        "max_vus": generator.get("max_vus"),
        "external_http_started": metric_count(metrics, "external_http_started") or 0,
        "external_http_responses": metric_count(metrics, "external_http_responses") or 0,
        "external_http_new_connection": metric_count(metrics, "external_http_new_connection") or 0,
        "external_http_new_tls": metric_count(metrics, "external_http_new_tls") or 0,
        "scheduled_iterations": metric_count(metrics, "scheduled_iterations") or 0,
        "dropped_iterations": metric_count(metrics, "dropped_iterations") or 0,
        "iterations": metric_count(metrics, "iterations") or 0,
    }
    write_json(out / "loadgen-summary.json", loadgen_summary)
    write_json(out / "generator-timing.json", {
        "run_id": a.run_id,
        "t0_epoch_ms": t0,
        "t0_source": t0_source,
        "tend_epoch_ms": shell_end,
        "shell_start_epoch_ms": shell_start,
        "shell_end_epoch_ms": shell_end,
        "k6_version": k6_version,
        "k6_script_sha256_local": local_script_sha,
        "k6_script_sha256_remote": remote_script_sha,
        "clock": clock,
    })
    print(json.dumps({
        **status,
        **loadgen_summary,
        "generator_resources_ok": generator_resources["ok"],
        "generator_saturated": generator_resources["saturated"],
        "downloaded_objects": len(objects),
    }, indent=2))
    if not clock.get("ok"):
        ssm_failure = ssm_failure or (
            "Generator clock was not verified within tolerance: "
            + json.dumps(clock, indent=2)
        )
    if ssm_failure:
        raise RuntimeError(ssm_failure)


if __name__ == "__main__":
    main()
