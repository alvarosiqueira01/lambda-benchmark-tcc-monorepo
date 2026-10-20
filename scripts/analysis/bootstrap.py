#!/usr/bin/env python3
import argparse, numpy as np, pandas as pd
from pathlib import Path

def bootstrap_ci(values, statistic=np.median, n_boot=10000, seed=42):
    x=np.asarray(pd.Series(values).dropna(),dtype=float)
    if len(x)<2: return (float("nan"),float("nan"))
    rng=np.random.default_rng(seed)
    samples=np.empty(n_boot)
    for i in range(n_boot): samples[i]=statistic(rng.choice(x,size=len(x),replace=True))
    return tuple(np.quantile(samples,[.025,.975]))

def main():
    p=argparse.ArgumentParser(); p.add_argument("--input",default="experiments/aggregate/runs.csv"); p.add_argument("--out",default="experiments/aggregate/condition-summary.csv"); p.add_argument("--n-boot",type=int,default=10000)
    a=p.parse_args(); df=pd.read_csv(a.input)
    factors=["composition","architecture","memory_mb","load_pattern"]
    rows=[]
    for key,g in df.groupby(factors,dropna=False):
        d=dict(zip(factors,key)); d["n_runs"]=len(g)
        for metric in ["p95_ms","c_req_lambda_usd","c_req_app_usd"]:
            x=g[metric].dropna()
            d[f"{metric}_median"]=x.median() if len(x) else None
            lo,hi=bootstrap_ci(x,n_boot=a.n_boot)
            d[f"{metric}_ci95_low"]=lo; d[f"{metric}_ci95_high"]=hi
        rows.append(d)
    Path(a.out).parent.mkdir(parents=True,exist_ok=True); pd.DataFrame(rows).to_csv(a.out,index=False)
if __name__=="__main__": main()
