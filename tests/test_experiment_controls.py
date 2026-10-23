import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "scripts" / "experiment"
for directory in (EXPERIMENT, EXPERIMENT / "prepare", EXPERIMENT / "planning"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

MODULE_PATHS = {
    "controller": "controller.py",
    "preflight": "prepare/preflight.py",
    "log_retention": "prepare/log_retention.py",
    "load_test": "run/load_test.py",
    "cold_reset": "prepare/cold_reset.py",
}


def _load_experiment_common():
    spec = importlib.util.spec_from_file_location(
        "experiment_common", EXPERIMENT / "common.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_module(name):
    previous_common = sys.modules.get("common")
    sys.modules["common"] = _load_experiment_common()
    try:
        spec = importlib.util.spec_from_file_location(
            f"test_{name}", EXPERIMENT / MODULE_PATHS.get(name, f"{name}.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous_common is not None:
            sys.modules["common"] = previous_common
        else:
            sys.modules.pop("common", None)
    return module


def test_preflight_normalizes_tags_and_estimates_item_size():
    preflight = load_module("preflight")
    assert preflight.normalize_tags("['lambda', 'benchmark']") == ["benchmark", "lambda"]
    assert preflight.normalize_tags('["a", "b"]') == ["a", "b"]
    assert preflight.estimated_dynamodb_item_bytes({"id": "x", "body": "abc"}) > 3
    assert preflight.dynamodb_value({"L": [{"S": "lambda"}, {"S": "benchmark"}]}) == [
        "lambda", "benchmark"
    ]
    assert preflight.dynamodb_value({"M": {"id": {"S": "x"}}}) == {"id": "x"}


def test_log_retention_is_idempotent_and_verified():
    log_retention = load_module("log_retention")

    class FakeLogs:
        def __init__(self):
            self.retention = {}

        def put_retention_policy(self, logGroupName, retentionInDays):
            self.retention[logGroupName] = retentionInDays

        def describe_log_groups(self, logGroupNamePrefix):
            if logGroupNamePrefix not in self.retention:
                return {"logGroups": []}
            return {"logGroups": [{
                "logGroupName": logGroupNamePrefix,
                "retentionInDays": self.retention[logGroupNamePrefix],
                "storedBytes": 10,
            }]}

    class FakeSession:
        def __init__(self, logs):
            self.logs = logs

        def client(self, service):
            assert service == "logs"
            return self.logs

    cfg = {"functions": {
        "monolithic": {"name": "m"}, "orchestrator": {"name": "o"},
        "workers": [{"name": "w"}], "decomposed_crud": {"name": "c"},
    }, "aws": {"log_retention_days": 90}}
    logs = FakeLogs()
    result = log_retention.ensure(FakeSession(logs), cfg)
    assert result["ok"] is True
    assert logs.retention["/aws/lambda/m"] == 90
    assert log_retention.verify(FakeSession(logs), cfg)["ok"] is True


def test_clock_artifacts_require_both_chrony_snapshots(tmp_path):
    load_test = load_module("load_test")
    (tmp_path / "chrony-before.txt").write_text(
        "System time     : 0.002000 seconds fast of NTP time\n"
        "Leap status     : Normal\n"
    )
    (tmp_path / "chrony-after.txt").write_text(
        "System time     : 0.001000 seconds slow of NTP time\n"
        "Leap status     : Normal\n"
    )
    result = load_test.read_clock_artifacts(tmp_path, True, 50)
    assert result["ok"] is True
    assert result["max_observed_offset_ms"] == 2.0


def test_clock_artifacts_fail_without_chrony(tmp_path):
    load_test = load_module("load_test")
    (tmp_path / "chrony-before.txt").write_text("chronyc not found\n")
    (tmp_path / "chrony-after.txt").write_text("chronyc not found\n")
    result = load_test.read_clock_artifacts(tmp_path, True, 50)
    assert result["ok"] is False


def test_cold_reset_waits_for_active_state():
    cold_reset = load_module("cold_reset")

    class FakeLambda:
        def __init__(self):
            self.calls = 0

        def get_function_configuration(self, FunctionName):
            self.calls += 1
            return {"LastUpdateStatus": "Successful", "State": "Active"}

    client = FakeLambda()
    result = cold_reset.wait_update(client, "fn")
    assert result["State"] == "Active"
    assert client.calls == 1


def test_export_k6_requests_preserves_error_code(tmp_path):
    raw = tmp_path / "raw.jsonl"
    raw.write_text(json.dumps({
        "type": "Point", "metric": "bench_request_latency",
        "data": {
            "time": "2026-10-22T00:00:00.100Z", "value": 100,
            "tags": {
                "sample_id": "1", "run_id": "r1", "phase": "measurement",
                "status_code": "0", "error_code": "1050", "error": "timeout",
                "timeout": "true", "request_sent": "true",
                "t0_epoch_ms": "1000000",
            },
        },
    }) + "\n")
    out = tmp_path / "requests.csv"
    completed = subprocess.run([
        sys.executable, "scripts/experiment/run/export_k6_requests.py",
        "--raw", str(raw), "--t0-epoch-ms", "1000000",
        "--warmup-seconds", "0", "--total-seconds", "240",
        "--out", str(out),
    ], cwd=ROOT, text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
    header = out.read_text().splitlines()[0]
    assert "error_code" in header and "timeout" in header


def test_controller_uses_phase_scoped_paths():
    controller = load_module("controller")
    cfg = {"aws": {"s3_prefix": "bucket/prefix"}}
    assert controller.s3_run_prefix(cfg, "run-1", "pilot") == (
        "bucket/prefix/pilot/runs/run-1"
    )
    assert controller.s3_run_prefix(cfg, "run-1", "campaign") == (
        "bucket/prefix/campaign/runs/run-1"
    )


def _write_json(path, value):
    path.write_text(json.dumps(value))


def _build_run(run_dir):
    run_dir.mkdir()
    _write_json(run_dir / "manifest.json", {
        "run_id": run_dir.name, "phase": "pilot",
        "condition": {"composition": "monolithic", "architecture": "x86_64",
                      "memory_mb": 128, "load_pattern": "constant"},
        "log_retention": {"/aws/lambda/m": {"exists": True, "retention_in_days": 90}},
        "log_retention_policy": {"expected_days": 90},
    })
    (run_dir / "requests.csv").write_text(
        "run_id,status_code,timeout,request_sent,phase,latency_ms,error_code,"
        "sample_t0_epoch_ms,sample_t0_source,start_epoch_ms\n"
        "r1,500,false,true,measurement,100,0,1000000,k6_summary,1000000\n"
    )
    _write_json(run_dir / "k6-summary.json", {"metrics": {}})
    (run_dir / "k6-raw.json").write_text("")
    _write_json(run_dir / "loadgen-summary.json", {"dropped_iterations": 0})
    _write_json(run_dir / "generator-timing.json", {
        "t0_epoch_ms": 1_000_000, "t0_source": "k6_exec_scenario_startTime",
        "clock": {"ok": True, "max_observed_offset_ms": 1,
                  "checks": {"offset_within_tolerance": True}},
    })
    _write_json(run_dir / "schedule-verification.json", {"ok": True})
    (run_dir / "lambda-invocations.csv").write_text("function_name\n")
    _write_json(run_dir / "lambda-collection-status.json",
                {"status": "COMPLETE", "reports_missing": 0})
    _write_json(run_dir / "cold-reset.json", {"functions": []})
    _write_json(run_dir / "cost.json", {
        "init_duration_validation": {"ok": True},
        "measurement": {"external_requests_counted": 1},
    })
    _write_json(run_dir / "target-url.json", {})
    _write_json(run_dir / "load-test-status.json", {})
    (run_dir / "generator-resources.csv").write_text("run_id\n")
    _write_json(run_dir / "generator-resources-status.json",
                {"ok": True, "covers_window": True, "samples": 2, "saturated": False})
    _write_json(run_dir / "preflight.json", {
        "ok": True, "phase": "pilot", "functional_equivalence": {"ok": True},
    })
    _write_json(run_dir / "concurrency-check.json",
                {"ok": True, "concurrent_run_detected": False})
    _write_json(run_dir / "cloudwatch-collection-status.json", {
        "status": "COMPLETE", "api": {"available": True},
    })
    (run_dir / "cloudwatch-metrics.csv").write_text("metric,namespace,sum\n")
    _write_json(run_dir / "target-verification.json",
                {"ok": True, "composition": "monolithic"})


def test_validate_run_rejects_run_without_any_2xx(tmp_path):
    run_dir = tmp_path / "run-1"
    _build_run(run_dir)
    target = run_dir / "target-verification.json"
    completed = subprocess.run([
        sys.executable, "scripts/experiment/validate/validate_run.py",
        "--run-dir", str(run_dir), "--pattern", "constant",
        "--composition", "monolithic", "--target-verification", str(target),
        "--expected-requests", "1",
    ], cwd=ROOT, text=True, capture_output=True, check=False)
    assert completed.returncode != 0
    result = json.loads((run_dir / "validation.json").read_text())
    check = next(
        item for item in result["checks"]
        if item["name"] == "successful_http_response_present"
    )
    assert check["passed"] is False
    assert result["status"] == "invalid-apparatus"
