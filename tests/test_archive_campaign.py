import csv
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "experiment" / "report" / "archive_campaign.py"


def write_run(runs_dir, phase, run_id, block_id, pattern="constant", status="valid"):
    run_dir = runs_dir / phase / run_id
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": run_id,
        "phase": phase,
        "block_id": block_id,
        "condition_id": "C001",
        "factors": {"load_pattern": pattern},
        "validation_status": status,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    (run_dir / "validation.json").write_text(json.dumps({"status": status}))
    (run_dir / "requests.csv").write_text("run_id,latency_ms\nr,1\n")
    (run_dir / "cost.json").write_text(json.dumps({"measurement": {}}))
    (run_dir / "k6-raw.json").write_text('{"type":"Point"}\n')
    return run_dir


def run_archive(tmp_path, *args):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--campaign-id",
            "tcc-test",
            "--campaign-manifest",
            str(tmp_path / "missing-manifest.json"),
            *args,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def archive_names(path):
    with tarfile.open(path, "r:gz") as archive:
        return archive.getnames()


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_derived_archive_omits_k6_raw_and_writes_metadata(tmp_path):
    write_run(tmp_path / "runs", "campaign", "C001-b01-r01-1", 1)
    write_run(tmp_path / "runs", "campaign", "C002-b01-r01-2", 1, pattern="burst")

    completed = run_archive(
        tmp_path,
        "--phase",
        "campaign",
        "--scope",
        "derived",
        "--out",
        str(tmp_path / "out"),
    )
    assert completed.returncode == 0, completed.stderr

    out = tmp_path / "out"
    archive = out / "tcc-test-derived-block-01.tar.gz"
    assert archive.exists()
    names = archive_names(archive)
    assert any(name.endswith("requests.csv") for name in names)
    assert any(name.endswith("manifest.json") for name in names)
    assert not any(name.endswith("k6-raw.json") for name in names)

    manifest = json.loads((out / "archive-manifest.json").read_text())
    assert manifest["runs_total"] == 2
    assert manifest["excluded"] == ["k6-raw.json"]
    assert manifest["runs_by_status"] == {"valid": 2}
    assert manifest["archives"][0]["files"] == 8

    with (out / "per-run-index.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert {row["run_id"] for row in rows} == {"C001-b01-r01-1", "C002-b01-r01-2"}
    assert (out / "SHA256SUMS").exists()
    assert (out / "README.md").exists()


@pytest.mark.parametrize(
    ("compression", "extension"), [("gzip", ".tar.gz"), ("xz", ".tar.xz")]
)
def test_derived_archive_is_deterministic(tmp_path, compression, extension):
    write_run(tmp_path / "runs", "campaign", "C001-b01-r01-1", 1)

    for name in ("out-a", "out-b"):
        completed = run_archive(
            tmp_path,
            "--phase",
            "campaign",
            "--scope",
            "derived",
            "--compression",
            compression,
            "--out",
            str(tmp_path / name),
        )
        assert completed.returncode == 0, completed.stderr

    first = tmp_path / "out-a" / f"tcc-test-derived-block-01{extension}"
    second = tmp_path / "out-b" / f"tcc-test-derived-block-01{extension}"
    assert sha256(first) == sha256(second)


def test_full_single_archive_keeps_k6_raw(tmp_path):
    write_run(tmp_path / "runs", "campaign", "C001-b01-r01-1", 1)

    completed = run_archive(
        tmp_path,
        "--phase",
        "campaign",
        "--scope",
        "full",
        "--group",
        "single",
        "--out",
        str(tmp_path / "out"),
    )
    assert completed.returncode == 0, completed.stderr

    archive = tmp_path / "out" / "tcc-test-full-raw.tar.gz"
    assert archive.exists()
    assert any(name.endswith("k6-raw.json") for name in archive_names(archive))
    manifest = json.loads((tmp_path / "out" / "archive-manifest.json").read_text())
    assert manifest["excluded"] == []


def test_dry_run_writes_nothing(tmp_path):
    write_run(tmp_path / "runs", "campaign", "C001-b01-r01-1", 1)

    completed = run_archive(
        tmp_path,
        "--phase",
        "campaign",
        "--scope",
        "derived",
        "--out",
        str(tmp_path / "out"),
        "--dry-run",
    )
    assert completed.returncode == 0, completed.stderr
    assert "tcc-test-derived-block-01.tar.gz" in completed.stdout
    assert not (tmp_path / "out").exists()


def test_incomplete_run_is_skipped_with_flag(tmp_path):
    write_run(tmp_path / "runs", "campaign", "C001-b01-r01-1", 1)
    broken = tmp_path / "runs" / "campaign" / "C002-b01-r01-2"
    broken.mkdir(parents=True)

    failed = run_archive(
        tmp_path, "--phase", "campaign", "--out", str(tmp_path / "out-a")
    )
    assert failed.returncode != 0
    assert "missing manifest.json" in failed.stderr

    allowed = run_archive(
        tmp_path,
        "--phase",
        "campaign",
        "--out",
        str(tmp_path / "out-b"),
        "--allow-incomplete",
    )
    assert allowed.returncode == 0, allowed.stderr
    assert "skipping" in allowed.stderr
