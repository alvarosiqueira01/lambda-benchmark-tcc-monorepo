#!/usr/bin/env python3
"""Exploratory tests; report estimates and uncertainty, not only p-values."""
import argparse
import pandas as pd
from scipy import stats
from pathlib import Path

def main():
    p=argparse.ArgumentParser(); p.add_argument("--input",default="experiments/aggregate/runs.csv"); p.add_argument("--out",default="experiments/aggregate/hypothesis-tests.csv"); a=p.parse_args()
    d=pd.read_csv(a.input); out=[]
    # H1: memory trend in p95 per composition/architecture/load.
    for keys,g in d.groupby(["composition","architecture","load_pattern"]):
        g=g.dropna(subset=["memory_mb","p95_ms"]).sort_values("memory_mb")
        if len(g)>=3:
            rho,pv=stats.spearmanr(g["memory_mb"],g["p95_ms"])
            out.append({"hypothesis":"H1","stratum":str(keys),"method":"Spearman(memory,p95)","estimate":rho,"p_value":pv,"n_runs":len(g)})
    # H2: identify lowest median cost level per load; descriptive evidence.
    for load,g in d.groupby("load_pattern"):
        med=g.groupby("memory_mb")["c_req_lambda_usd"].median().dropna()
        if len(med): out.append({"hypothesis":"H2","stratum":load,"method":"minimum median Creq by memory","estimate":int(med.idxmin()),"p_value":None,"n_runs":len(g)})
    # H3: paired ARM/x86 comparisons by other factors + block when available.
    factors=["composition","memory_mb","load_pattern"]
    if "block_id" in d.columns: factors.append("block_id")
    for keys,g in d.groupby(factors):
        arm=g[g["architecture"]=="arm64"]["c_req_lambda_usd"].dropna()
        x86=g[g["architecture"]=="x86_64"]["c_req_lambda_usd"].dropna()
        if len(arm) and len(x86):
            out.append({"hypothesis":"H3","stratum":str(keys),"method":"median ARM/x86 Creq ratio","estimate":arm.median()/x86.median(),"p_value":None,"n_runs":min(len(arm),len(x86))})
    # H4 requires per-pair relative cost difference vs billed duration. Emit correlation if columns exist.
    if "billed_duration_ms_per_external_request" in d.columns and "architecture" in d.columns:
        # Deliberately avoid a causal claim; report association for future model specification.
        out.append({"hypothesis":"H4","stratum":"all","method":"requires paired architecture contrasts and explicit duration model",
                    "estimate":None,"p_value":None,"n_runs":len(d)})
    # H5/H6 descriptive decomposition contrasts by matching condition.
    pivot=d.pivot_table(index=["architecture","memory_mb","load_pattern"],columns="composition",
      values=["c_req_lambda_usd","p95_ms"],aggfunc="median")
    for metric,hyp in [("c_req_lambda_usd","H5"),("p95_ms","H6")]:
        try:
            for idx,row in pivot.iterrows():
                mono=row[(metric,"monolithic")]; dec=row[(metric,"decomposed")]
                if pd.notna(mono) and pd.notna(dec):
                    out.append({"hypothesis":hyp,"stratum":str(idx),"method":f"decomposed-minus-monolithic {metric}",
                      "estimate":float(dec-mono),"p_value":None,"n_runs":None})
        except Exception: pass
    Path(a.out).parent.mkdir(parents=True,exist_ok=True); pd.DataFrame(out).to_csv(a.out,index=False)
if __name__=="__main__": main()
