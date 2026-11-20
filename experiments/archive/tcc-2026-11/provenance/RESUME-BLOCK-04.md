# Retomada do bloco 4 — campanha `tcc-2026-11`

Status em **2026-11-11T18:45Z**. Este diretório (`experiments/runs/_resume/`)
é ignorado pelo Git e não é varrido por `aggregate.py` nem por
`archive_campaign.py`; não polui o plano congelado nem o conjunto de runs.

## Por que o bloco 4 parou

O host do controlador ficou sem NTP e o relógio passou a adiantar (~1,5–2,2 s/h).
Como a janela de coleta começa em `reset_completed` (relógio do controlador) e o
`T0` vem do k6 (relógio da EC2, correto), a partir de ~13:50Z:

- `t0_after_cold_reset` falhou (`reset_completed` depois do `T0`);
- `lambda_collection_complete` virou `INCOMPLETE` (a janela começou no meio da carga).

O gerador não teve problema: o chrony da EC2 estava correto. O host foi
sincronizado via NTP e o offset ficou < 50 ms.

## Estado atual do bloco 4

- 28 condições com run `valid` — preservadas.
- 34 runs `invalid-apparatus` — preservados para auditoria (não apagar).
- Run parcial `C021-b04-r04-20261111T175214Z-530a75` finalizado como
  `invalid-apparatus` (`controller_crash`, sem tráfego) e enviado ao S3.
- Lock órfão removido; gerador ocioso; nenhum comando SSM em andamento.
- **32 condições sem run utilizável** (recalculadas a cada execução):
  `C015 C012 C021 C022 C026 C013 C014 C028 C034 C017 C011 C036 C031 C043 C009
  C035 C005 C060 C047 C049 C006 C037 C053 C004 C016 C020 C052 C054 C024 C008
  C002 C032`

O CSV `conditions_block_04_missing.csv` é um snapshot; a lista oficial é
recalculada a partir dos `validation.json` no momento da retomada.

## Pré-requisitos

1. **NTP sincronizado** (já configurado; sobrevive a reboot):
   ```bash
   timedatectl show -p NTPSynchronized --value   # deve ser yes
   ```
   Se precisar refazer:
   ```bash
   sudo mkdir -p /etc/systemd/timesyncd.conf.d
   sudo tee /etc/systemd/timesyncd.conf.d/99-tcc.conf >/dev/null <<'EOF'
   [Time]
   NTP=pool.ntp.org a.st1.ntp.br
   FallbackNTP=time.windows.com
   EOF
   sudo timedatectl set-ntp true
   sudo systemctl restart systemd-timesyncd
   sleep 15
   sudo hwclock --systohc
   ```
2. Credenciais AWS válidas (`aws sso login` se necessário).
3. **HEAD em `6d3c9f162c6cc4772f3fd499ae28ad3e2106e474`**. Não commite nem
   altere `experiment-plan.yaml`, `harness-config.yaml`, `template.yaml`,
   `pricing-2026-10-22.json` ou o dataset antes de retomar; o controller recusa
   se os insumos congelados mudarem.
4. Nenhum outro controller/run ativo (o script verifica).

## Como retomar

Primeiro, valide tudo sem executar carga:

```bash
bash experiments/runs/_resume/resume_block_04.sh --check-only
```

Depois, execute:

```bash
bash experiments/runs/_resume/resume_block_04.sh
```

Duração nominal: ~3 h (32 runs × ~5,5 min), podendo chegar a ~9 h se todas as
condições esgotarem os 2 retries. Pode ser interrompido com Ctrl-C: o script
restaura o WIP e os runs já concluídos ficam válidos; na próxima execução ele
recalcula e retenta só o que falta.

## O que o script faz

1. Exige `NTPSynchronized=yes` e offset NTP ≤ 500 ms.
2. Valida credenciais AWS.
3. Confirma via SSM que não há k6/sampler ativo no gerador.
4. Remove lock apenas se o PID estiver morto.
5. Recalcula as condições sem run `valid`/`valid-system-anomaly`, na ordem do
   plano, e grava `conditions_block_04_missing.csv`.
6. Faz `git stash push -u` somente dos arquivos não-runtime (WIP de docs e
   ferramental) e restaura no `trap` ao sair, inclusive em erro/Ctrl-C.
7. Faz backup de `campaign-controller-results.json`.
8. Finaliza eventuais runs parciais do bloco 4 ainda sem `validation.json`.
9. Executa o `controller.py` com `--condition-file` do bloco.
10. Confere a cobertura final e grava `resume-report-<timestamp>.json` e
    `resume-block-04-<timestamp>.log`.

## Como interpretar

- **rc=0**: 60/60 condições do bloco 4 com run utilizável.
- **rc=2**: ainda faltam condições; rode o script de novo (ele retenta só o que
  falta).
- **outro rc**: erro de pré-requisito (NTP, credenciais, lock, stash anterior,
  árvore suja). A mensagem indica o que corrigir.

Se algum run novo sair `invalid-apparatus`, inspecione
`experiments/runs/campaign/<run>/validation.json`:

- `lambda_collection_complete=INCOMPLETE` → suspeite do relógio do host antes de
  culpar o aparato;
- `t0_after_cold_reset` → mesma causa;
- `concurrency-check`/`dropped_iterations` → verifique o gerador.

## Depois de concluir

- O WIP é restaurado automaticamente; confira com `git status` e
  `git stash list`.
- Os runs inválidos continuam no dataset (auditoria). Não apague.
- Siga para o bloco 5 e para o arquivamento conforme
  `experiments/README.md`.
- Este arquivo e o script podem ser movidos para `docs/` e commitados depois,
  quando a campanha não estiver mais congelada.
