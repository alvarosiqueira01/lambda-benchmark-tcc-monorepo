#!/usr/bin/env python3
import argparse, json
from pathlib import Path
import pandas as pd
from scipy import stats

def main():
    p=argparse.ArgumentParser(); p.add_argument("--runs-dir",default="experiments/runs"); p.add_argument("--out",default="experiments/aggregate")
    a=p.parse_args(); rows=[]; function_rows=[]
    for d in sorted(Path(a.runs_dir).iterdir()):
        if not d.is_dir() or not (d/"validation.json").exists() or not (d/"manifest.json").exists(): continue
        val=json.loads((d/"validation.json").read_text()); man=json.loads((d/"manifest.json").read_text())
        if val.get("status") not in ("valid","valid-system-anomaly"): continue
        cond=man.get("condition",{})
        if not (d/"requests.csv").exists() or not (d/"cost.json").exists(): continue
        req=pd.read_csv(d/"requests.csv")
        if "phase" in req: req=req[req["phase"]=="measurement"]
        lat=pd.to_numeric(req.get("latency_ms",pd.Series(dtype=float)),errors="coerce").dropna()
        cost=json.loads((d/"cost.json").read_text()); cost_measurement=cost.get("measurement",cost)
        rows.append({"run_id":d.name,**cond,"block_id":cond.get("block_id",man.get("block_id")),"n_requests":len(lat),"p50_ms":lat.quantile(.5) if len(lat) else None,
          "p90_ms":lat.quantile(.9) if len(lat) else None,"p95_ms":lat.quantile(.95) if len(lat) else None,
          "p99_ms":lat.quantile(.99) if len(lat) else None,"mean_ms":lat.mean() if len(lat) else None,
          "std_ms":lat.std(ddof=1) if len(lat)>1 else None,"c_req_lambda_usd":cost_measurement.get("c_req_lambda_usd"),
          "c_req_app_usd":cost_measurement.get("c_req_app_usd"),"status":val["status"]})
        if (d/"lambda-invocations.csv").exists():
            f=pd.read_csv(d/"lambda-invocations.csv")
            for fn,g in f.groupby("function_name"):
                function_rows.append({"run_id":d.name,**cond,"function_name":fn,"invocations":len(g),
                  "billed_duration_ms":pd.to_numeric(g["billed_duration_ms"],errors="coerce").sum(),
                  "cold_starts":g["cold_start"].astype(str).str.lower().isin(["true","1"]).sum()})
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(rows).to_csv(out/"runs.csv",index=False); pd.DataFrame(function_rows).to_csv(out/"function_runs.csv",index=False)
    print(f"Aggregated {len(rows)} valid runs to {out}")
if __name__=="__main__": main()
