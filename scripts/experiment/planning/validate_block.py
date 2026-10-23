#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import csv_read, load_yaml


def main():
    p=argparse.ArgumentParser(); p.add_argument("--plan",default="experiments/experiment-plan.yaml")
    p.add_argument("--block-dir",default="experiments/metadata/plan"); a=p.parse_args()
    plan=load_yaml(a.plan); files=sorted(Path(a.block_dir).glob("conditions_block_*.csv"))
    expected=len(plan["factors"]["composition"])*len(plan["factors"]["architecture"])*len(plan["factors"]["memory_mb"])*len(plan["factors"]["load_pattern"])
    if len(files)!=int(plan["blocks"]): raise SystemExit(f"Expected {plan['blocks']} blocks, got {len(files)}")
    base={(r["composition"],r["architecture"],str(r["memory_mb"]),r["load_pattern"]) for r in csv_read(Path(a.block_dir)/"conditions.csv")}
    for f in files:
        rows=csv_read(f); keys=[(r["composition"],r["architecture"],str(r["memory_mb"]),r["load_pattern"]) for r in rows]
        if len(rows)!=expected or set(keys)!=base or len(set(keys))!=expected: raise SystemExit(f"Invalid block: {f}")
    print(f"PASS: {len(files)} blocks, {expected} unique conditions each.")
if __name__=="__main__": main()
