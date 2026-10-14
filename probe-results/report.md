# Dataset Size Probe Report

- **Date**: 2026-10-10T21:03:08Z
- **Dataset version**: probe-results

## Probe results

| Probe | Memory | Arch | N | Warm N | p50 warm (ms) | p95 warm (ms) | max warm (ms) | MaxMemUsed (MB) | ColdN |
|-------|--------|------|---|--------|---------------|---------------|---------------|-----------------|-------|
| low | 128 | x86_64 | 20 | - | 31.34 | 39.86 | 39.96 | 97 | 0 |
| mid | 512 | x86_64 | 20 | - | 6.52 | 9.76 | 16.98 | 97 | 0 |
| high | 2048 | x86_64 | 20 | - | 7.12 | 9.12 | 14.72 | 97 | 0 |

## Acceptance criteria

| # | Criterion | Expected | Observed | Status |
|---|-----------|----------|----------|--------|
| 1 | p50_warm @ 512 MB in [50.0, 300.0] ms | [50.0, 300.0] | 6.52 | **FAIL** |
| 2 | p50_warm(128)/p50_warm(2048) >= 2.0 | >= 2.0 | 4.40 | **PASS** |
| 3 | MaxMemUsed @ 128 MB <= 110.0 MB | <= 110.0 | 97 | **PASS** |
| 4 | cold_rate <= 0.1 AND init_frac <= 0.2 | cold<=0.1, init<=0.2 | cold=0.000, init_frac=0.000 | **PASS** |

## Verdict

**FAIL** — 1 criterion/criteria not met:
- #1: observed `6.52`, expected `[50.0, 300.0]`

Recommendation: adjust `body_size`, promote to a new `dataset_version`, and re-run the probe.
