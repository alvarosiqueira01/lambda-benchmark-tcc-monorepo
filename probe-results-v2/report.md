# Dataset Size Probe Report

- **Date**: 2026-10-12T12:42:42Z
- **Dataset version**: probe-results-v2

## Probe results

| Probe | Memory | Arch | N | Warm N | p50 warm (ms) | p95 warm (ms) | max warm (ms) | MaxMemUsed (MB) | ColdN |
|-------|--------|------|---|--------|---------------|---------------|---------------|-----------------|-------|
| low | 128 | x86_64 | 20 | - | 379.72 | 399.88 | 402.17 | 98 | 0 |
| mid | 512 | x86_64 | 20 | - | 88.75 | 99.45 | 100.10 | 98 | 0 |
| high | 2048 | x86_64 | 20 | - | 30.80 | 31.44 | 31.46 | 98 | 0 |

## Acceptance criteria

| # | Criterion | Expected | Observed | Status |
|---|-----------|----------|----------|--------|
| 1 | p50_warm @ 512 MB in [50.0, 300.0] ms | [50.0, 300.0] | 88.75 | **PASS** |
| 2 | p50_warm(128)/p50_warm(2048) >= 2.0 | >= 2.0 | 12.33 | **PASS** |
| 3 | MaxMemUsed @ 128 MB <= 110.0 MB | <= 110.0 | 98 | **PASS** |
| 4 | cold_rate <= 0.1 AND init_frac <= 0.2 | cold<=0.1, init<=0.2 | cold=0.000, init_frac=0.000 | **PASS** |

## Verdict

**PASS** — dataset `probe-results-v2` is adequate.
