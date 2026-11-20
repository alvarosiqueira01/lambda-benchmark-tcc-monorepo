# Dados experimentais — tcc-2026-11

Gerado em `2026-11-20T14:12:48.461330+00:00`.

Escopo: **derived** (artefatos derivados por run, sem `k6-raw.json`).
Agrupamento: **block**. Compressão: **xz**.

## Conteúdo

- 651 runs em 11 arquivo(s) `.tar.xz`.
- `SHA256SUMS` — hashes dos arquivos deste diretório.
- `archive-manifest.json` — commit, hashes de entrada, contagens e arquivos.
- `per-run-index.csv` — índice run a run, com bloco, condição e status.

## Extração

```bash
sha256sum -c SHA256SUMS
tar -xf tcc-2026-11-derived-block-01.tar.xz
```

## Reprodutibilidade

O escopo derivado permite regenerar `runs.csv` e `function_runs.csv` com
`scripts/analysis/aggregate.py`. A revalidação completa com
`scripts/experiment/validate/validate_run.py` exige o pacote `full-raw`
publicado no GitHub Release.
