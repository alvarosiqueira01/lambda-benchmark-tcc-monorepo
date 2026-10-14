#!/usr/bin/env python3
"""Aggregate probe stats, evaluate acceptance criteria, emit report + verdict."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

LABEL_RE = re.compile(r"^(?P<label>[a-z]+)_(?P<mem>\d+)_(?P<arch>[a-z0-9_]+)_stats$")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--results-dir", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--verdict", required=True)
    p.add_argument("--criterion-p50-512-min", type=float, default=20.0)
    p.add_argument("--criterion-p50-512-max", type=float, default=300.0)
    p.add_argument("--criterion-ratio-128-2048-min", type=float, default=2.0)
    p.add_argument("--criterion-mem-128-max-mb", type=float, default=110.0)
    p.add_argument("--criterion-cold-rate-max", type=float, default=0.10)
    p.add_argument("--criterion-init-fraction-max", type=float, default=0.20)
    return p.parse_args()


def load_probes(results_dir: Path) -> dict[str, dict]:
    probes: dict[str, dict] = {}
    for label in ("low", "mid", "high"):
        matches = list(results_dir.glob(f"{label}_*_stats.json"))
        if len(matches) != 1:
            raise ValueError(
                f"Esperado exatamente um arquivo para '{label}'; encontrados {len(matches)}"
            )
        path = matches[0]
        m = LABEL_RE.match(path.stem)
        mem = m.group("mem") if m else "?"
        arch = m.group("arch") if m else "?"

        data = json.loads(path.read_text())
        if data.get("error"):
            raise ValueError(f"Erro em {path}: {data['error']}")
        if data.get("count", 0) <= 0:
            raise ValueError(f"Sem medições válidas em {path}")

        data["_meta"] = {"memory_mb": mem, "architecture": arch, "path": str(path)}
        probes[label] = data
    return probes


def _warm(probe: dict) -> dict:
    return probe.get("duration_ms_warm_only", probe.get("duration_ms", {}))


def evaluate(probes: dict[str, dict], args: argparse.Namespace) -> list[dict]:
    results: list[dict] = []

    def add(num, desc, expected, observed, passed):
        results.append({"num": num, "description": desc,
                        "expected": expected, "observed": observed, "pass": passed})

    low = probes.get("low", {})
    mid = probes.get("mid", {})
    high = probes.get("high", {})

    # 1) p50 warm @ 512 MB
    p50_mid = _warm(mid).get("p50", 0.0)
    add(1,
        f"p50_warm @ 512 MB in [{args.criterion_p50_512_min}, {args.criterion_p50_512_max}] ms",
        f"[{args.criterion_p50_512_min}, {args.criterion_p50_512_max}]",
        f"{p50_mid:.2f}",
        args.criterion_p50_512_min <= p50_mid <= args.criterion_p50_512_max)

    # 2) razão memória (128 vs 2048, ambos x86_64)
    p50_low = _warm(low).get("p50", 0.0)
    p50_high = _warm(high).get("p50", 0.0)
    ratio = (p50_low / p50_high) if p50_high > 0 else 0.0
    add(2,
        f"p50_warm(128)/p50_warm(2048) >= {args.criterion_ratio_128_2048_min}",
        f">= {args.criterion_ratio_128_2048_min}",
        f"{ratio:.2f}",
        ratio >= args.criterion_ratio_128_2048_min)

    # 3) MaxMemUsed @ 128 MB
    max_mem = low.get("max_memory_used_mb", {}).get("max", 0)
    add(3,
        f"MaxMemUsed @ 128 MB <= {args.criterion_mem_128_max_mb} MB",
        f"<= {args.criterion_mem_128_max_mb}",
        f"{max_mem}",
        max_mem <= args.criterion_mem_128_max_mb)

    # 4) cold start não domina
    cold_rate = low.get("cold_start_rate", 0.0)
    init_frac = low.get("cold_start_fraction_of_total", 0.0)
    add(4,
        f"cold_rate <= {args.criterion_cold_rate_max} AND init_frac <= {args.criterion_init_fraction_max}",
        f"cold<={args.criterion_cold_rate_max}, init<={args.criterion_init_fraction_max}",
        f"cold={cold_rate:.3f}, init_frac={init_frac:.3f}",
        cold_rate <= args.criterion_cold_rate_max and init_frac <= args.criterion_init_fraction_max)

    return results


def render_report(probes: dict[str, dict], criteria: list[dict], dataset_version: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = []
    lines.append("# Dataset Size Probe Report")
    lines.append("")
    lines.append(f"- **Date**: {now}")
    lines.append(f"- **Dataset version**: {dataset_version}")
    lines.append("")

    lines.append("## Probe results")
    lines.append("")
    lines.append("| Probe | Memory | Arch | N | Warm N | p50 warm (ms) | p95 warm (ms) | max warm (ms) | MaxMemUsed (MB) | ColdN |")
    lines.append("|-------|--------|------|---|--------|---------------|---------------|---------------|-----------------|-------|")

    for label in ("low", "mid", "high"):
        p = probes.get(label, {})
        meta = p.get("_meta", {})
        w = _warm(p)
        lines.append(
            f"| {label} | {meta.get('memory_mb','-')} | {meta.get('architecture','-')} | "
            f"{p.get('count',0)} | {w.get('count','-')} | "
            f"{w.get('p50',0.0):.2f} | {w.get('p95',0.0):.2f} | {w.get('max',0.0):.2f} | "
            f"{p.get('max_memory_used_mb',{}).get('max',0)} | {p.get('cold_count',0)} |"
        )

    lines.append("")
    lines.append("## Acceptance criteria")
    lines.append("")
    lines.append("| # | Criterion | Expected | Observed | Status |")
    lines.append("|---|-----------|----------|----------|--------|")
    for c in criteria:
        status = "**PASS**" if c["pass"] else "**FAIL**"
        lines.append(f"| {c['num']} | {c['description']} | {c['expected']} | {c['observed']} | {status} |")

    lines.append("")
    verdict = "PASS" if all(c["pass"] for c in criteria) else "FAIL"
    lines.append("## Verdict")
    lines.append("")
    if verdict == "PASS":
        lines.append(f"**PASS** — dataset `{dataset_version}` is adequate.")
    else:
        failed = [c for c in criteria if not c["pass"]]
        lines.append(f"**FAIL** — {len(failed)} criterion/criteria not met:")
        for c in failed:
            lines.append(f"- #{c['num']}: observed `{c['observed']}`, expected `{c['expected']}`")
        lines.append("")
        lines.append("Recommendation: adjust `body_size`, promote to a new `dataset_version`, and re-run the probe.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    results_dir = Path(args.results_dir)
    probes = load_probes(results_dir)

    criteria = evaluate(probes, args)
    report = render_report(probes, criteria, dataset_version=args.results_dir)
    Path(args.report).write_text(report)

    verdict = {
        "verdict": "PASS" if all(c["pass"] for c in criteria) else "FAIL",
        "criteria": criteria,
    }
    Path(args.verdict).write_text(json.dumps(verdict, indent=2))

    return 0 if verdict["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())