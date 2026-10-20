#!/usr/bin/env python3
import argparse, itertools, random, json
from pathlib import Path
from common import load_yaml, csv_write, write_json, sha256_file

def build(plan):
    f=plan["factors"]
    rows=[]
    for i,(comp,arch,mem,load) in enumerate(itertools.product(
        f["composition"],f["architecture"],f["memory_mb"],f["load_pattern"]),1):
        rows.append({"condition_id":f"C{i:03d}","composition":comp,"architecture":arch,
                     "memory_mb":mem,"load_pattern":load})
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--plan",default="experiments/experiment-plan.yaml")
    ap.add_argument("--out",default="experiments/metadata/plan")
    a=ap.parse_args()
    plan=load_yaml(a.plan); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    rows=build(plan)
    expected=len(plan["factors"]["composition"])*len(plan["factors"]["architecture"])*len(plan["factors"]["memory_mb"])*len(plan["factors"]["load_pattern"])
    if len(rows)!=expected or len({r["condition_id"] for r in rows})!=expected: raise SystemExit("Condition matrix invalid")
    csv_write(out/"conditions.csv",rows)
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
        "conditions":len(rows),"blocks":len(block_summaries),"runs_planned":len(rows)*len(block_summaries),
        "condition_matrix_sha256":sha256_file(out/"conditions.csv"),"blocks":block_summaries})
    print(f"Generated {len(rows)} conditions x {len(block_summaries)} blocks = {len(rows)*len(block_summaries)} planned runs in {out}")
if __name__=="__main__": main()
