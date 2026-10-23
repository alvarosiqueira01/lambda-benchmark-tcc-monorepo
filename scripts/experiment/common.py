from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


def utc_now() -> str:
    return datetime.now(UTC).isoformat()

def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value)

def load_yaml(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

def write_json(path: str | Path, data: Any) -> None:
    p=Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str)+"\n", encoding="utf-8")

def sha256_file(path: str | Path) -> str:
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda:f.read(1024*1024), b""): h.update(chunk)
    return h.hexdigest()

def git_value(args: list[str], cwd: str | Path = ".") -> str | None:
    try: return subprocess.check_output(["git",*args],cwd=cwd,text=True,stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001 - git metadata is optional
        return None

PHASES = ("pilot", "campaign")

def runs_root(phase: str) -> Path:
    if phase not in PHASES:
        raise ValueError(f"unknown phase: {phase}")
    return Path("experiments") / "runs" / phase

def s3_phase_prefix(cfg: dict, phase: str) -> str:
    if phase not in PHASES:
        raise ValueError(f"unknown phase: {phase}")
    return f'{cfg["aws"]["s3_prefix"].rstrip("/")}/{phase}/runs'

def new_run_id(condition_id: str, block_id: int, replication: int) -> str:
    stamp=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{condition_id}-b{block_id:02d}-r{replication:02d}-{stamp}-{uuid.uuid4().hex[:6]}"

def csv_read(path):
    with open(path,newline="",encoding="utf-8") as f: return list(csv.DictReader(f))

def csv_write(path, rows, fieldnames=None):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    if fieldnames is None:
        fieldnames=list(rows[0].keys()) if rows else []
    with open(p,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fieldnames,extrasaction="ignore"); w.writeheader(); w.writerows(rows)

def is_placeholder(v):
    return v is None or (isinstance(v,str) and (not v.strip() or "REPLACE_WITH" in v or v.startswith("REPLACE_")))
