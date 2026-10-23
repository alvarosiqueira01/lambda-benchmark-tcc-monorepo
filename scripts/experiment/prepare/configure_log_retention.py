#!/usr/bin/env python3
"""Apply the 90-day retention policy to the benchmark Lambda log groups."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from log_retention import ensure

from common import load_yaml, write_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="experiments/harness-config.yaml")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--out", default="experiments/metadata/log-retention.json")
    a = ap.parse_args()
    cfg = load_yaml(a.config)
    import boto3

    session = boto3.Session(
        profile_name=cfg["aws"].get("profile"), region_name=cfg["aws"]["region"]
    )
    result = ensure(session, cfg, a.days)
    write_json(a.out, result)
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit("log retention was not applied to every group")


if __name__ == "__main__":
    main()
