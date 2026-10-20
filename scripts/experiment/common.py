from __future__ import annotations
import csv, hashlib, json, os, subprocess, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import yaml

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

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
    except Exception: return None

def new_run_id(condition_id: str, block_id: int, replication: int) -> str:
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
