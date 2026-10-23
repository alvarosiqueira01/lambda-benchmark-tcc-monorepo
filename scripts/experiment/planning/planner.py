#!/usr/bin/env python3
import argparse
import itertools
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import csv_write, load_yaml, sha256_file, write_json


def build(plan):
    f=plan["factors"]
    rows=[]
    for i,(comp,arch,mem,load) in enumerate(itertools.product(
        f["composition"],f["architecture"],f["memory_mb"],f["load_pattern"]),1):
        rows.append({"condition_id":f"C{i:03d}","composition":comp,"architecture":arch,
                     "memory_mb":mem,"load_pattern":load})
    return rows


def build_pilot(rows, plan):
    spec = plan.get("pilot") or {}
    selections = spec.get("conditions") or []
    if not selections:
        raise SystemExit("Plan has no pilot.conditions selection")
    by_factors = {
        (r["composition"], r["architecture"], int(r["memory_mb"]), r["load_pattern"]): r
        for r in rows
    }
    pilot = []
    seen = set()
    for entry in selections:
        key = (
            entry["composition"], entry["architecture"],
            int(entry["memory_mb"]), entry["load_pattern"],
        )
        row = by_factors.get(key)
        if row is None:
            raise SystemExit(f"Pilot condition is not in the factor matrix: {key}")
        if key in seen:
            raise SystemExit(f"Duplicate pilot condition: {key}")
        seen.add(key)
        item = dict(row)
        item["block_id"] = 0
        item["replication"] = 0
        pilot.append(item)
    expected_runs = int(spec.get("runs", len(selections)))
    if len(pilot) != expected_runs:
        raise SystemExit(
            f"Pilot has {len(pilot)} runs, expected {expected_runs} from plan"
        )
    factors = plan["factors"]
    for name in ("composition", "architecture", "load_pattern"):
        required = set(factors[name])
        observed = {item[name] for item in pilot}
        missing = required - observed
        if missing:
            raise SystemExit(f"Pilot does not cover {name}: {sorted(missing)}")
    memories = sorted(int(value) for value in factors["memory_mb"])
    observed_memories = {int(item["memory_mb"]) for item in pilot}
    if memories[0] not in observed_memories or memories[-1] not in observed_memories:
        raise SystemExit(
            "Pilot must include the minimum and maximum memory configurations: "
            f"{memories[0]} and {memories[-1]} MB"
        )
    return pilot


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--plan",default="experiments/experiment-plan.yaml")
    ap.add_argument("--out",default="experiments/metadata/plan")
    ap.add_argument("--pilot-out",default="experiments/pilot-conditions.csv")
    a=ap.parse_args()
    plan=load_yaml(a.plan); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    rows=build(plan)
    expected=len(plan["factors"]["composition"])*len(plan["factors"]["architecture"])*len(plan["factors"]["memory_mb"])*len(plan["factors"]["load_pattern"])
    if len(rows)!=expected or len({r["condition_id"] for r in rows})!=expected: raise SystemExit("Condition matrix invalid")
    csv_write(out/"conditions.csv",rows)
    pilot=build_pilot(rows,plan)
    pilot_path=Path(a.pilot_out)
    csv_write(pilot_path,pilot)
    rng=random.Random(int(plan["random_seed"]))
    block_summaries=[]
    for block in range(1,int(plan["blocks"])+1):
        shuffled=rows.copy(); rng.shuffle(shuffled)
        block_rows=[]
        for r in shuffled:
            x=dict(r); x["block_id"]=block; x["replication"]=block
            block_rows.append(x)
        csv_write(out/f"conditions_block_{block:02d}.csv",block_rows)
        block_summaries.append({"block_id":block,"rows":len(block_rows),"sha256":sha256_file(out/f"conditions_block_{block:02d}.csv")})
    write_json(out/"randomization-manifest.json",{"campaign_id":plan["campaign_id"],"seed":plan["random_seed"],
        "algorithm":"Python random.Random(seed), shuffle each complete condition list independently in sequence",
        "conditions":len(rows),"block_count":len(block_summaries),"runs_planned":len(rows)*len(block_summaries),
        "condition_matrix_sha256":sha256_file(out/"conditions.csv"),"blocks":block_summaries,
        "pilot":{"runs":len(pilot),"sha256":sha256_file(pilot_path),
                 "condition_ids":[item["condition_id"] for item in pilot]}})
    print(f"Generated {len(rows)} conditions x {len(block_summaries)} blocks = {len(rows)*len(block_summaries)} planned runs in {out}")
    print(f"Generated {len(pilot)} pilot runs in {pilot_path}")
if __name__=="__main__": main()
