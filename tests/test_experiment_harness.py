import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "scripts" / "analysis"


def load_analysis_module(name: str):
    spec = importlib.util.spec_from_file_location(f"test_{name}", ANALYSIS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_interval_brackets_center():
    bootstrap = load_analysis_module("bootstrap")
    low, high = bootstrap.bootstrap_ci([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], n_boot=500)
    assert low <= 5.5 <= high


def test_paired_contrast_uses_block_pairs():
    bootstrap = load_analysis_module("bootstrap")
    low, high, n = bootstrap.paired_bootstrap_contrast(
        pd.Series([10, 11, 12], index=[1, 2, 3]),
        pd.Series([8, 9, 10], index=[1, 2, 3]),
        n_boot=200,
    )
    assert n == 3
    assert low > 0
    assert high > 0


def test_select_config_reports_empty_set():
    select_config = load_analysis_module("select_config")
    data = pd.DataFrame([
        {
            "composition": "monolithic", "architecture": "x86_64",
            "memory_mb": 128, "load_pattern": "constant",
            "p95_ms_ci95_high": 600.0,
            "c_req_lambda_usd_median": 1e-7,
        },
    ])
    best, eligible = select_config.select_for_limit(data, 500.0)
    assert best is None
    assert eligible.empty


def _write_schedule_run(tmp_path, pattern, rows, t0=1_000_000):
    requests = tmp_path / f"requests-{pattern}.csv"
    pd.DataFrame(rows).to_csv(requests, index=False)
    summary = tmp_path / f"summary-{pattern}.json"
    summary.write_text(json.dumps({
        "t0_epoch_ms": t0,
        "t0_source": "k6_exec_scenario_startTime",
        "metrics": {
            "external_http_started": {"values": {"count": len(rows)}},
            "dropped_iterations": {"values": {"count": 0}},
        },
    }))
    out = tmp_path / f"schedule-{pattern}.json"
    return requests, summary, out


def _burst_rows(jitter_last_five_ms):
    rows = []
    for cycle in range(48):
        for index in range(100):
            delay = jitter_last_five_ms if index >= 95 else 0
            offset = cycle * 5000 + index * 10 + delay
            rows.append({
                "offset_ms": offset,
                "start_epoch_ms": 1_000_000 + offset,
                "latency_ms": 100,
                "status_code": 200,
                "request_sent": "true",
            })
    return rows


def _run_verify_schedule(pattern, requests, summary, out):
    return subprocess.run([
        sys.executable, "scripts/experiment/run/verify_schedule.py",
        "--plan", "experiments/experiment-plan.yaml",
        "--requests-csv", str(requests),
        "--summary-json", str(summary),
        "--pattern", pattern,
        "--t0-epoch-ms", "1000000",
        "--out", str(out),
    ], cwd=ROOT, text=True, capture_output=True, check=False)


def test_verify_schedule_allows_burst_jitter_at_window_boundary(tmp_path):
    requests, summary, out = _write_schedule_run(
        tmp_path, "burst", _burst_rows(12)
    )
    completed = _run_verify_schedule("burst", requests, summary, out)
    result = json.loads(out.read_text())
    idle = next(
        check for check in result["checks"]
        if check["name"] == "burst_idle_windows_empty"
    )
    assert idle["passed"] is True
    assert completed.returncode == 0


def test_verify_schedule_rejects_burst_after_jitter_allowance(tmp_path):
    requests, summary, out = _write_schedule_run(
        tmp_path, "burst", _burst_rows(60)
    )
    completed = _run_verify_schedule("burst", requests, summary, out)
    result = json.loads(out.read_text())
    idle = next(
        check for check in result["checks"]
        if check["name"] == "burst_idle_windows_empty"
    )
    assert idle["passed"] is False
    assert completed.returncode != 0


def test_verify_schedule_rejects_extra_sparse_arrival(tmp_path):
    rows = []
    for cycle in range(24):
        for offset in (0, 200, 400, 600, 800):
            rows.append({
                "offset_ms": cycle * 10000 + offset,
                "start_epoch_ms": 1_000_000 + cycle * 10000 + offset,
                "latency_ms": 100,
                "status_code": 200,
                "phase": "measurement",
                "request_sent": "true",
            })
    rows.append({
        "offset_ms": 200,
        "start_epoch_ms": 1_000_200,
        "latency_ms": 100,
        "status_code": 200,
        "phase": "warmup",
        "request_sent": "true",
    })
    requests = tmp_path / "requests.csv"
    pd.DataFrame(rows).to_csv(requests, index=False)
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({
        "t0_epoch_ms": 1_000_000,
        "t0_source": "k6_exec_scenario_startTime",
        "metrics": {
            "external_http_started": {"values": {"count": len(rows)}},
            "dropped_iterations": {"values": {"count": 0}},
        },
    }))
    out = tmp_path / "schedule.json"
    completed = subprocess.run([
        sys.executable, "scripts/experiment/run/verify_schedule.py",
        "--plan", "experiments/experiment-plan.yaml",
        "--requests-csv", str(requests),
        "--summary-json", str(summary),
        "--pattern", "sparse_miniburst",
        "--t0-epoch-ms", "1000000",
        "--out", str(out),
    ], cwd=ROOT, text=True, capture_output=True, check=False)
    assert completed.returncode != 0
    assert json.loads(out.read_text())["ok"] is False
