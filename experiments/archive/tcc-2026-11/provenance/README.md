# Proveniência da retomada do bloco 4 — campanha `tcc-2026-11`

Estes arquivos documentam a interrupção e a retomada do bloco 4. Eles **não**
fazem parte dos pacotes tar (o `SHA256SUMS` do diretório pai cobre apenas os
`.tar.xz`), mas ficam versionados para auditoria.

## Causa da interrupção

Em 2026-11-11, o host do controlador perdeu sincronização NTP e o relógio passou
a adiantar cerca de 1,5–2,2 s por hora. A janela de coleta começa em
`reset_completed` (relógio do controlador) e o `T0` vem do k6 (relógio da EC2,
correto). A partir de ~13:50Z, isso produziu:

- `t0_after_cold_reset` inválido (`reset_completed` depois do `T0`);
- `lambda_collection_complete=INCOMPLETE` (janela iniciando no meio da carga).

O gerador não teve problema: o chrony da EC2 permaneceu correto.

## Estado e retomada

- Bloco 4 preservado: **60 runs `valid`** e **35 runs `invalid-apparatus`**.
- No momento da retomada, **32 condições** estavam sem run utilizável.
- O host foi ressincronizado; o offset ficou em **42,8 ms** (limite: 50 ms).
- A retomada de 2026-11-12 fechou as 32 condições restantes: **60/60** com run
  utilizável (`missing_after = 0`, `controller_rc = 0`).
- As mudanças de código em andamento foram isoladas via `git stash` durante a
  retomada; os runs continuaram em `git_commit=6d3c9f1`, `git_dirty=false`.
- Runs inválidos foram mantidos no dataset e nos pacotes, conforme o protocolo.

## Arquivos

- `RESUME-BLOCK-04.md` — procedimento e diagnóstico da retomada.
- `resume-report-20261112T105232Z.json` — relatório estruturado da execução.
- `conditions_block_04_missing.csv` — snapshot das 32 condições faltantes.
- `conditions_block_04_missing_after.csv` — estado após a retomada (vazio).
- `controller-results-backup-20261112T105232Z.json` — backup dos resultados do
  controller antes da retomada.
- `resume_block_04.sh` — script de retomada usado.
- `missing_conditions.py` — recálculo das condições sem run utilizável.
