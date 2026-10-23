import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "scripts" / "experiment"
if str(EXPERIMENT) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT))

MODULE_PATHS = {
    "collect_lambda": "collect/collect_lambda.py",
    "collect_cloudwatch": "collect/collect_cloudwatch.py",
    "controller": "controller.py",
    "load_test": "run/load_test.py",
}


def _load_experiment_common():
    spec = importlib.util.spec_from_file_location("experiment_common", EXPERIMENT / "common.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_module(name, filename=None):
    filename = filename or MODULE_PATHS.get(name, f"{name}.py")
    previous_common = sys.modules.get("common")
    sys.modules["common"] = _load_experiment_common()
    try:
        spec = importlib.util.spec_from_file_location(
            f"test_{name}", EXPERIMENT / filename
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous_common is not None:
            sys.modules["common"] = previous_common
        else:
            sys.modules.pop("common", None)
    return module


def report_message(request_id="req-1", duration=10.0, billed=12, memory=128,
                   max_memory=64, init=None):
    message = (
        f"REPORT RequestId: {request_id}\tDuration: {duration} ms\t"
        f"Billed Duration: {billed} ms\tMemory Size: {memory} MB\t"
        f"Max Memory Used: {max_memory} MB"
    )
    if init is not None:
        message += f"\tInit Duration: {init} ms"
    return message


def test_parse_events_keeps_paired_post_run_invocations():
    collect_lambda = load_module("collect_lambda")
    t0 = 1_000_000
    events = []
    for request_id, timestamp, phase in (
        ("aaaa", t0 - 10_000, "pre_run"),
        ("bbbb", t0 + 10_000, "warmup"),
        ("cccc", t0 + 130_000, "measurement"),
        ("dddd", t0 + 250_000, "post_run"),
    ):
        events.append({
            "timestamp": timestamp,
            "message": f"START RequestId: {request_id} Version: 7",
        })
        events.append({
            "timestamp": timestamp + 100,
            "message": report_message(request_id),
        })

    rows = collect_lambda.parse_events(
        events, "fn", {"fn": {"7"}}, {"fn": "x86_64"}, {"fn": 128},
        t0, 120_000, 240_000,
    )
    by_id = {row["request_id"]: row for row in rows}
    assert by_id["dddd"]["phase"] == "post_run"
    assert by_id["dddd"]["report_found"] is True
    assert {row["phase"] for row in rows} == {
        "pre_run", "warmup", "measurement", "post_run",
    }


def _write_cost_inputs(tmp_path):
    lambda_rows = [
        {
            "function_name": "fn", "phase": phase,
            "start_timestamp_ms": start, "report_found": "true",
            "billed_duration_ms": 100, "memory_mb": 128,
            "duration_ms": 90, "init_duration_ms": "",
            "architecture": "x86_64", "request_id": phase,
        }
        for phase, start in (
            ("pre_run", 990_000),
            ("warmup", 1_010_000),
            ("measurement", 1_130_000),
            ("post_run", 1_250_000),
        )
    ]
    request_rows = [
        {"phase": phase, "start_epoch_ms": start, "request_sent": "true"}
        for phase, start in (
            ("warmup", 1_010_000),
            ("measurement", 1_130_000),
            ("outside_run", 1_250_000),
        )
    ]
    lambdas = tmp_path / "lambda.csv"
    requests = tmp_path / "requests.csv"
    pd.DataFrame(lambda_rows).to_csv(lambdas, index=False)
    pd.DataFrame(request_rows).to_csv(requests, index=False)
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({
        "lambda_request_usd_per_million": 0.2,
        "lambda_gb_second_usd_arm64": 0.0000133334,
        "lambda_gb_second_usd_x86_64": 0.0000166667,
        "http_api_usd_per_million_requests_first_tier": 1.0,
        "dynamodb_standard_usd_per_million_rru": 0.125,
        "dynamodb_standard_usd_per_million_wru": 0.625,
    }))
    return lambdas, requests, pricing


def test_collect_cost_uses_only_start_times_inside_accounting_windows(tmp_path):
    lambdas, requests, pricing = _write_cost_inputs(tmp_path)
    out = tmp_path / "cost.json"
    completed = subprocess.run([
        sys.executable, "scripts/experiment/collect/collect_cost.py",
        "--pricing", str(pricing),
        "--requests-csv", str(requests),
        "--lambda-csv", str(lambdas),
        "--composition", "monolithic",
        "--architecture", "x86_64",
        "--t0-epoch-ms", "1000000",
        "--warmup-seconds", "120",
        "--total-seconds", "240",
        "--output", str(out),
    ], cwd=ROOT, text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(out.read_text())
    assert result["full_run"]["lambda_invocations_counted"] == 2
    assert result["full_run"]["external_requests_counted"] == 2
    assert result["measurement"]["lambda_invocations_counted"] == 1
    assert result["measurement"]["external_requests_counted"] == 1
    assert result["raw_totals"]["lambda_rows"] == 4
    assert result["raw_totals"]["lambda_rows_full_run"] == 2
    assert result["raw_totals"]["request_phase_counts"]["outside_run"] == 1


class FakePaginator:
    def __init__(self, s3):
        self.s3 = s3

    def paginate(self, Bucket, Prefix):
        keys = sorted(key for key in self.s3.objects if key.startswith(Prefix))
        return [{"Contents": [{"Key": key} for key in keys]}]


class FakeS3:
    def __init__(self, fail=False):
        self.objects = {}
        self.fail = fail

    def upload_file(self, filename, bucket, key):
        if self.fail:
            raise RuntimeError("s3 unavailable")
        self.objects[key] = Path(filename).read_bytes()

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return FakePaginator(self)


class FakeSession:
    def __init__(self, s3):
        self.s3 = s3

    def client(self, name):
        assert name == "s3"
        return self.s3


def _failure_fixture(tmp_path):
    run_id = "run-1"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "partial.txt").write_text("partial")
    manifest = {"run_id": run_id, "attempt": 0, "max_retries": 2}
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    cfg = {"aws": {"s3_bucket": "bucket", "s3_prefix": "campaign"}}
    row = {"condition_id": "c1", "composition": "monolithic",
           "load_pattern": "constant"}
    return run_id, run_dir, manifest, cfg, row


def test_finalize_failure_writes_validation_and_uploads_partial_run(tmp_path):
    controller = load_module("controller")
    s3 = FakeS3()
    run_id, run_dir, manifest, cfg, row = _failure_fixture(tmp_path)

    result = controller.finalize_failure(
        run_dir, manifest, FakeSession(s3), cfg, run_id, row,
        "cold_reset", RuntimeError("reset failed"),
    )

    validation = json.loads((run_dir / "validation.json").read_text())
    archived_manifest = json.loads((run_dir / "manifest.json").read_text())
    assert result["status"] == "invalid-apparatus"
    assert result["failure_stage"] == "cold_reset"
    assert validation["status"] == "invalid-apparatus"
    assert validation["issues"][0]["check"] == "controller_failure"
    assert archived_manifest["status"] == "FAILED_OR_PARTIAL"
    assert archived_manifest["artifacts_uploaded"] is True
    assert f"campaign/runs/{run_id}/validation.json" in s3.objects
    assert f"campaign/runs/{run_id}/partial.txt" in s3.objects
    assert f"campaign/runs/{run_id}/manifest.json" in s3.objects


def test_finalize_failure_records_upload_error_without_losing_artifacts(tmp_path):
    controller = load_module("controller")
    run_id, run_dir, manifest, cfg, row = _failure_fixture(tmp_path)

    result = controller.finalize_failure(
        run_dir, manifest, FakeSession(FakeS3(fail=True)), cfg, run_id, row,
        "load", RuntimeError("k6 failed"),
    )

    archived_manifest = json.loads((run_dir / "manifest.json").read_text())
    assert result["status"] == "invalid-apparatus"
    assert archived_manifest["status"] == "FAILED_OR_PARTIAL"
    assert archived_manifest["artifacts_uploaded"] is False
    assert "s3 unavailable" in archived_manifest["upload_error"]
    assert (run_dir / "validation.json").exists()
    assert (run_dir / "partial.txt").exists()


def _write_raw_sampler_csv(path):
    header = (
        "timestamp_ms,cpu_user,cpu_nice,cpu_system,cpu_idle,cpu_iowait,"
        "cpu_irq,cpu_softirq,cpu_steal,mem_total_kb,mem_available_kb,"
        "mem_free_kb,buffers_kb,cached_kb,swap_total_kb,swap_free_kb,"
        "load1,load5,load15,procs_running,procs_blocked,net_rx_bytes,"
        "net_tx_bytes,net_rx_packets,net_tx_packets,net_rx_dropped,"
        "net_tx_dropped"
    )
    rows = [
        (
            "999000,100,0,50,100,0,0,0,0,1000,500,400,50,50,0,0,"
            "0.5,0.5,0.5,1,0,1000,500,10,5,0,0"
        ),
        (
            "1240500,200,0,100,200,0,0,0,0,1000,400,300,50,50,0,0,"
            "1.5,1.0,0.8,2,1,2000,1000,20,10,0,0"
        ),
    ]
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


def test_generator_resources_summary_and_coverage(tmp_path):
    load_test = load_module("load_test")
    raw = tmp_path / "generator-resources-raw.csv"
    _write_raw_sampler_csv(raw)
    rows = load_test.read_sampler_rows(raw)
    samples = load_test.derived_samples(rows, 1_000_000)
    status = load_test.write_generator_resources(
        tmp_path, "run-1", samples, 1_000_000, 240_000, 120_000, 1,
        "sha", {"generator": {}},
    )

    assert status["ok"] is True
    assert status["covers_window"] is True
    assert status["samples"] == 2
    assert status["saturated"] is False
    assert status["summary"]["cpu"]["percent_max"] == 60.0
    assert status["summary"]["memory"]["max_used_percent"] == 60.0
    assert status["summary"]["network"]["rx_bytes_per_s_max"] > 0
    assert (tmp_path / "generator-resources.csv").exists()


def test_generator_resources_missing_sampler_is_explicit(tmp_path):
    load_test = load_module("load_test")
    status = load_test.write_generator_resources(
        tmp_path, "run-1", [], 1_000_000, 240_000, 120_000, 1,
        "sha", {"generator": {}},
    )
    assert status["ok"] is False
    assert "no samples" in status["issues"][0]
    header = (tmp_path / "generator-resources.csv").read_text().splitlines()[0]
    assert header.startswith("run_id,timestamp_ms")


def test_collect_cloudwatch_marks_ec2_generator_scope():
    cloudwatch = load_module("collect_cloudwatch")

    class FakeCloudWatch:
        def get_metric_statistics(self, **kwargs):
            assert kwargs["Namespace"] == "AWS/EC2"
            assert kwargs["Dimensions"][0]["Value"] == "i-123"
            return {"Datapoints": [{
                "Timestamp": datetime.now(UTC),
                "Average": 12.5,
                "Unit": "Percent",
            }]}

    rows = []
    raw = []
    datapoints = cloudwatch.collect(
        FakeCloudWatch(), "run-1", "AWS/EC2", "i-123", "", "CPUUtilization",
        [{"Name": "InstanceId", "Value": "i-123"}], ["Average"],
        datetime.now(UTC), datetime.now(UTC), 60, rows, raw,
    )
    assert datapoints == 1
    assert rows[0]["scope"] == "generator"
    assert rows[0]["average"] == 12.5
    assert raw[0]["period"] == 60


def test_pilot_report_lists_manifest_only_failed_run(tmp_path):
    runs = tmp_path / "runs"
    failed = runs / "failed-run"
    failed.mkdir(parents=True)
    (failed / "manifest.json").write_text(json.dumps({
        "run_id": "failed-run",
        "condition_id": "c1",
        "condition": {"condition_id": "c1", "composition": "monolithic"},
        "status": "FAILED_OR_PARTIAL",
        "failure_stage": "cold_reset",
        "error": "reset failed",
        "artifacts_uploaded": True,
        "campaign_id": "campaign-1",
    }))
    out = tmp_path / "pilot-validation.json"
    completed = subprocess.run([
        sys.executable, "scripts/experiment/report/pilot_report.py",
        "--runs-dir", str(runs),
        "--pilot-conditions", str(tmp_path / "missing.csv"),
        "--campaign-id", "campaign-1",
        "--out", str(out),
    ], cwd=ROOT, text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(out.read_text())
    assert result["failed_or_partial_runs"][0]["run_id"] == "failed-run"
    assert result["failed_or_partial_runs"][0]["validation_present"] is False


def _remote_command(**overrides):
    load_test = load_module("load_test")
    values = {
        "remote_root": "/opt/lambda-benchmark/results",
        "remote": "/opt/lambda-benchmark/results/run-1",
        "s3_uri": "s3://bucket/prefix/run-1/",
        "k6_script": "/opt/lambda-benchmark/k6/benchmark.js",
        "sampler_b64": "YWJj",
        "sampler_sha": "deadbeef",
        "sampler_interval": 1,
        "envs": {"RUN_ID": "run-1", "OUT_DIR": "/opt/lambda-benchmark/results/run-1"},
        "min_free_disk_mb": 1024,
        "cleanup_remote_after_upload": True,
    }
    values.update(overrides)
    return load_test.build_remote_command(**values)


def test_remote_command_cleans_staging_only_after_upload():
    cmd = _remote_command()
    assert "df -Pk" in cmd
    assert "min_free_kb=1048576" in cmd
    assert "k6_status=75" in cmd
    assert "rm -rf -- \"$remote\"" in cmd
    assert "\"$remote_root\"/*" in cmd
    assert cmd.index('if [ "$upload_status" -ne 0 ]') < cmd.index('rm -rf -- "$remote"')


def test_remote_command_can_disable_cleanup():
    cmd = _remote_command(cleanup_remote_after_upload=False)
    assert "rm -rf -- \"$remote\"" not in cmd
    assert "cleanup_status" not in cmd


def test_load_test_rejects_unsafe_run_id():
    load_test = load_module("load_test")
    for run_id in ("../run-1", "run/1", ".hidden", ""):
        with pytest.raises(SystemExit):
            load_test.validate_run_id(run_id)
    load_test.validate_run_id("2026-10-22_run.1")


def test_read_storage_artifacts(tmp_path):
    load_test = load_module("load_test")
    (tmp_path / "storage-before.txt").write_text(
        "captured_at_utc=2026-10-22T00:00:00Z\nfree_kb=2048\nrun_dir_bytes=10\n"
    )
    (tmp_path / "storage-preflight.txt").write_text(
        "ok=true\nfree_kb=2048\nmin_free_kb=1024\n"
    )
    artifacts = load_test.read_storage_artifacts(tmp_path)
    assert artifacts["storage-before"]["free_kb"] == "2048"
    assert artifacts["storage-preflight"]["ok"] == "true"


def test_read_storage_artifacts_ignores_missing_files(tmp_path):
    load_test = load_module("load_test")
    assert load_test.read_storage_artifacts(tmp_path) == {}
