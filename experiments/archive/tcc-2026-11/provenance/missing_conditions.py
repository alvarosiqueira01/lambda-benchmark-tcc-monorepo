#!/usr/bin/env python3
"""Compute the conditions of a block that still lack a usable run.

A run is usable when validation.json reports status "valid" or
"valid-system-anomaly". The output CSV preserves the order of the frozen
condition file so the controller runs the retries in plan order.
"""
import argparse
import csv
import json
from pathlib import Path

USABLE = {"valid", "valid-system-anomaly"}


def run_status(run_dir: Path):
    validation = run_dir / "validation.json"
    if not validation.exists():
        return None
    try:
        return json.loads(validation.read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conditions", required=True)
    parser.add_argument("--runs-dir", required=True)
    parser.add_argument("--block", type=int, required=True)
    parser.add_argument("--out")
    args = parser.parse_args()

    with open(args.conditions, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows = [row for row in rows if int(row["block_id"]) == args.block]
    if not rows:
        raise SystemExit(f"no conditions for block {args.block} in {args.conditions}")

    usable = set()
    for run_dir in Path(args.runs_dir).glob(f"*-b{args.block:02d}-*"):
        if run_dir.is_dir() and run_status(run_dir) in USABLE:
            usable.add(run_dir.name.split("-", 1)[0])

    plan_ids = {row["condition_id"] for row in rows}
    usable &= plan_ids
    missing = [row for row in rows if row["condition_id"] not in usable]

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(missing)

    print(f"block={args.block} conditions={len(rows)} usable={len(usable)} missing={len(missing)}")
    print("missing: " + " ".join(row["condition_id"] for row in missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
