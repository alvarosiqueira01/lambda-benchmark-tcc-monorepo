#!/usr/bin/env python3
"""Generate per-stratum cost/latency figures without mixing load patterns."""
import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="experiments/aggregate/condition-summary.csv")
    parser.add_argument("--plan", default="experiments/experiment-plan.yaml")
    parser.add_argument("--out", default="experiments/aggregate/figures")
    args = parser.parse_args()
    plan = yaml.safe_load(Path(args.plan).read_text(encoding="utf-8"))
    primary = float(plan["latency_constraint"]["primary_lmax_ms"])
    sensitivity = [float(x) for x in plan["latency_constraint"].get(
        "sensitivity_lmax_ms", []
    )]
    data = pd.read_csv(args.input)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for load_pattern, group in data.groupby("load_pattern"):
        for architecture, architecture_group in group.groupby("architecture"):
            for composition, frame in architecture_group.groupby("composition"):
                frame = frame.sort_values("memory_mb")
                plt.figure()
                plt.plot(frame["memory_mb"], frame["p95_ms_median"], marker="o")
                plt.xlabel("Memória Lambda (MB)")
                plt.ylabel("p95 fim a fim (ms)")
                plt.title(f"p95 por memória — {load_pattern}, {architecture}, {composition}")
                plt.tight_layout()
                plt.savefig(out / f"p95-{load_pattern}-{architecture}-{composition}.png", dpi=160)
                plt.close()

                plt.figure()
                plt.plot(frame["memory_mb"], frame["c_req_lambda_usd_median"], marker="o")
                plt.xlabel("Memória Lambda (MB)")
                plt.ylabel("Custo Lambda por requisição (USD)")
                plt.title(f"Custo por memória — {load_pattern}, {architecture}, {composition}")
                plt.tight_layout()
                plt.savefig(out / f"cost-{load_pattern}-{architecture}-{composition}.png", dpi=160)
                plt.close()

    # Cost-latency scatter per load pattern and architecture. Compositions are
    # separate series, never pooled into a single "frontier".
    for load_pattern, group in data.groupby("load_pattern"):
        for architecture, architecture_group in group.groupby("architecture"):
            plt.figure(figsize=(7, 5))
            for composition, frame in architecture_group.groupby("composition"):
                eligible = frame[frame["p95_ms_ci95_high"] <= primary]
                plt.scatter(
                    frame["c_req_lambda_usd_median"], frame["p95_ms_median"],
                    label=f"{composition} (todas)", alpha=0.55,
                )
                if not eligible.empty:
                    plt.scatter(
                        eligible["c_req_lambda_usd_median"], eligible["p95_ms_median"],
                        marker="*", s=160, label=f"{composition} (elegível)",
                    )
                for _, row in frame.iterrows():
                    plt.annotate(
                        f"{int(row['memory_mb'])}", (
                            row["c_req_lambda_usd_median"], row["p95_ms_median"]
                        ),
                        fontsize=7, xytext=(3, 3), textcoords="offset points",
                    )
            plt.axhline(primary, color="black", linestyle="--", linewidth=1,
                        label=f"Lmax={primary:g} ms")
            plt.xlabel("Custo Lambda por requisição (USD)")
            plt.ylabel("p95 fim a fim (ms)")
            plt.title(f"Custo x latência — {load_pattern}, {architecture}")
            plt.legend(fontsize=7)
            plt.tight_layout()
            plt.savefig(out / f"frontier-{load_pattern}-{architecture}.png", dpi=160)
            plt.close()

    # Explicit sensitivity frontiers for the three frozen limits.
    for lmax in [primary, *sensitivity]:
        for load_pattern, group in data.groupby("load_pattern"):
            plt.figure(figsize=(7, 5))
            for (architecture, composition), frame in group.groupby(
                ["architecture", "composition"]
            ):
                eligible = frame[frame["p95_ms_ci95_high"] <= lmax]
                label = f"{composition}/{architecture}"
                plt.scatter(
                    frame["c_req_lambda_usd_median"], frame["p95_ms_median"],
                    label=f"{label} (todas)", alpha=0.45,
                )
                if not eligible.empty:
                    plt.scatter(
                        eligible["c_req_lambda_usd_median"], eligible["p95_ms_median"],
                        marker="*", s=180, label=f"{label} (elegível)",
                    )
            plt.axhline(lmax, color="black", linestyle="--", linewidth=1,
                        label=f"Lmax={lmax:g} ms")
            plt.xlabel("Custo Lambda por requisição (USD)")
            plt.ylabel("p95 fim a fim (ms)")
            plt.title(f"Fronteira custo-latência — {load_pattern}, Lmax={lmax:g} ms")
            plt.legend(fontsize=7)
            plt.tight_layout()
            plt.savefig(out / f"frontier-{load_pattern}-lmax{lmax:g}.png", dpi=160)
            plt.close()


if __name__ == "__main__":
    main()
