#!/usr/bin/env python3
import argparse
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

def main():
    p=argparse.ArgumentParser(); p.add_argument("--input",default="experiments/aggregate/condition-summary.csv"); p.add_argument("--out",default="experiments/aggregate/figures"); a=p.parse_args()
    d=pd.read_csv(a.input); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    for load,g in d.groupby("load_pattern"):
        for arch,h in g.groupby("architecture"):
            for comp,k in h.groupby("composition"):
                k=k.sort_values("memory_mb")
                plt.figure(); plt.plot(k["memory_mb"],k["p95_ms_median"],marker="o")
                plt.xlabel("Memória Lambda (MB)"); plt.ylabel("p95 fim a fim (ms)")
                plt.title(f"p95 por memória — {load}, {arch}, {comp}"); plt.tight_layout()
                plt.savefig(out/f"p95-{load}-{arch}-{comp}.png",dpi=160); plt.close()
                plt.figure(); plt.plot(k["memory_mb"],k["c_req_lambda_usd_median"],marker="o")
                plt.xlabel("Memória Lambda (MB)"); plt.ylabel("Custo Lambda por requisição (USD)")
                plt.title(f"Custo por memória — {load}, {arch}, {comp}"); plt.tight_layout()
                plt.savefig(out/f"cost-{load}-{arch}-{comp}.png",dpi=160); plt.close()
    plt.figure(); plt.scatter(d["c_req_lambda_usd_median"],d["p95_ms_median"])
    plt.xlabel("Custo Lambda por requisição (USD)"); plt.ylabel("p95 fim a fim (ms)")
    plt.title("Fronteira custo-latência"); plt.tight_layout(); plt.savefig(out/"cost-latency-frontier.png",dpi=160); plt.close()
if __name__=="__main__": main()
