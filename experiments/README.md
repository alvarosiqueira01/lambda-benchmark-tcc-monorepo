# Harness experimental — AWS Lambda benchmark (TCC)

Este pacote implementa o instrumento experimental descrito no protocolo. Ele
**não altera o código da aplicação** e não presume nomes de funções, rotas ou
saídas que não tenham sido confirmados no repositório e na AWS.

Antes de usar AWS, preencha `experiments/harness-config.yaml` com os recursos
reais e valide a rota de análise com os smoke tests existentes.

## 1. Requisitos

- Python **3.14.x** (o projeto fixa `>=3.14,<3.15`).
- AWS CLI v2, AWS SAM CLI e k6 instalado na instância geradora.
- `git` e `sha256sum` na máquina de controle e no gerador.
- Credenciais AWS via SSO/role. Nunca coloque chaves no YAML.

Instalação reproduzível com `uv`:

```bash
uv sync --group experiment
```

Ou com `pip`:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements-experiment.txt
```

As versões de `boto3`, `PyYAML`, `pandas`, `numpy`, `scipy` e `matplotlib` estão
fixadas em `requirements-experiment.txt` e no grupo `experiment` do
`pyproject.toml`. O `uv.lock` registra a resolução completa.

## 2. Estrutura

```text
experiments/
├── experiment-plan.yaml          # protocolo congelado (60 condições, 10 blocos)
├── pilot-conditions.csv          # 12 runs do piloto gerados pelo planner
├── harness-config.yaml           # recursos AWS, gerador, preços e retries
├── validity-criteria.md          # critérios de validade
├── procedimento_campanha_experimental.md
├── pricing/
│   ├── pricing-template.json
│   └── pricing-YYYY-MM-DD.json   # snapshot real usado na campanha
├── metadata/
│   ├── plan/                     # condições e blocos gerados
│   ├── pilot-manifest.json       # manifesto congelado do piloto
│   ├── campaign-manifest.json    # manifesto congelado da campanha
│   ├── deploy-validation.json
│   ├── pilot-controller-results.json
│   └── campaign-controller-results.json
├── pilot-validation.json         # decisão estruturada do piloto
├── pilot-validation.md           # versão legível para revisão
├── runs/
│   ├── pilot/                    # um diretório por run do piloto
│   └── campaign/                 # um diretório por run da campanha
└── aggregate/                    # runs.csv, testes, seleção e figuras

k6/benchmark.js                   # gerador open-loop e T0 reportado pelo k6
scripts/experiment/
├── common.py                     # utilitários compartilhados
├── controller.py                 # orquestra um run completo
├── planning/                     # protocolo, blocos e manifesto congelado
├── prepare/                      # infraestrutura AWS, preflight e cold reset
├── run/                          # carga k6 e artefatos do gerador
├── collect/                      # Lambda, CloudWatch e custos
├── validate/                     # classificação do run
└── report/                       # evidência e relatório do piloto
scripts/analysis/                 # agregação, bootstrap, hipóteses e seleção
tests/                            # testes unitários do instrumento
```

### Responsabilidades

| Componente | Responsabilidade |
|---|---|
| `planner.py` | Gera 60 condições, 10 blocos randomizados e os 12 runs do piloto. |
| `deploy.py` | Valida SAM, aliases, rotas, integrações e variáveis do orquestrador; aplica a retenção de logs. |
| `cold_reset.py` | Ajusta memória/arquitetura, publica versão nova, espera `State=Active` e move o alias `benchmark`. |
| `verify_target.py` | Confirma, por run, API, alias/versão, `State=Active` e envs dos workers. |
| `load_test.py` | Executa o k6 via SSM, exige chrony e versão pinada do k6, amostra CPU/memória/rede e baixa artefatos do S3. |
| `export_k6_requests.py` | Gera `requests.csv` por requisição, incluindo covariáveis de timing. |
| `verify_schedule.py` | Verifica chegadas por janela, offsets, contagens e `dropped_iterations`. |
| `collect_lambda.py` | Coleta `START`/`REPORT`, pareia por `RequestId` e filtra pela versão publicada. |
| `collect_cloudwatch.py` | Métricas complementares de Lambda, API Gateway e da EC2 geradora. |
| `collect_cost.py` | Custo Lambda/API Gateway/DynamoDB por janela, auditoria de `Init Duration` e totais brutos. |
| `validate_run.py` | Classifica o run como `valid`, `valid-system-anomaly` ou `invalid-apparatus`. |
| `manifest.py` | Congela campanha e registra hashes, ferramentas, stack e gerador. |
| `controller.py` | Executa runs sequencialmente, com preflight, checagem de concorrência e fila de substituição. |
| `pilot_report.py` | Produz a evidência estruturada e o Markdown do piloto a partir dos runs arquivados. |
| `aggregate.py` | Consolida runs válidos em `runs.csv` e `function_runs.csv`. |
| `bootstrap.py` | Intervalos de confiança por condição e contrastes pareados por bloco. |
| `hypotheses.py` | Contrastes exploratórios H1–H6 com IC bootstrap. |
| `select_config.py` | Seleciona a configuração de menor custo com limite superior do IC de p95 ≤ Lmax. |
| `plots.py` | Figuras por padrão de carga, arquitetura e composição. |

## 3. Protocolo

| Parâmetro | Valor |
|---|---|
| Composição | monolítica e decomposta |
| Arquitetura | `x86_64` e `arm64` |
| Memória | 128, 256, 512, 1.024 e 2.048 MB |
| Carga constante | 20 req/s por 240 s |
| Rajada | 100 req/s por 1 s a cada 5 s |
| Carga esparsa | 5 requisições em 0/200/400/600/800 ms a cada 10 s |
| Aquecimento | 120 s |
| Medição | 120 s |
| Repetições | 10 blocos por condição |
| Total planejado | 600 runs |
| Limite primário | p95 ≤ 500 ms (sensibilidade 300/800 ms) |

O k6 usa chegada aberta (`constant-arrival-rate`). O cenário esparso implementa
a mini-rajada da Opção B, sem substituí-la por uma taxa uniforme de 0,5 req/s.
As contagens esperadas são 4.800 requisições para constante, 4.800 para rajada e
120 para esparsa (metade na janela de medição).

O T0 é o `exec.scenario.startTime` reportado pelo k6, não o horário do comando
SSM. Se esse valor não estiver disponível, o gerador marca a fonte real como
`generator_date_now_fallback`; esse fallback é VU-local e o run é reprovado pelo
`validate_run.py`. O piloto deve confirmar que todas as amostras têm o mesmo
`t0_epoch_ms` e a mesma `sample_t0_source`. O gerador tem 200 VUs pré-alocadas e
1.000 VUs máximas; o piloto deve comprovar `dropped_iterations == 0`.

A janela ativa da rajada usa a mesma folga de `sparse_timing_jitter_ms` (50 ms)
na fronteira de 1.000 ms: a última chegada agendada ocorre em 990 ms e um atraso
de rede de alguns milissegundos não deve ser classificado como chegada ociosa.

## 4. Configuração

1. Preencha `experiments/harness-config.yaml`:

- stack CloudFormation e caminho do template;
- API IDs, base URLs, stage e rota;
- nomes do monólito, orquestrador e workers;
- tabela DynamoDB e tamanho do item;
- instância EC2 geradora, bucket/prefixo S3 e caminhos remotos;
- política de retenção do staging remoto (`cleanup_remote_after_upload`) e
  espaço mínimo livre em disco (`min_free_disk_mb`, padrão 1024 MB);
- tipo/CPU credits da EC2 geradora (`t8i.small`, `standard`), versão esperada do
  k6 e tolerância de relógio (`max_clock_offset_ms`, padrão 50 ms);
- retenção de logs (`log_retention_days`, padrão 90);
- snapshot de preços.

O `load_test.py` mede o espaço livre antes do k6 e encerra o run antes da carga
se o disco estiver abaixo de `min_free_disk_mb`. Depois de enviar o diretório
do run ao S3 e confirmar o upload, ele remove apenas o diretório de staging
`<remote_output_dir>/<run_id>` na EC2; o S3 e o diretório local
`experiments/runs/<phase>/<run_id>` são preservados. Se o upload falhar, o staging
remoto é mantido para nova tentativa. Isso evita que os ~18 GB estimados para
os 600 runs se acumulem no volume de 16 GiB do gerador.

2. Aplique a retenção de 90 dias aos log groups já criados pelo Lambda:

```bash
python scripts/experiment/prepare/configure_log_retention.py \
  --config experiments/harness-config.yaml
```

O template SAM não declara `AWS::Logs::LogGroup` porque os grupos já existem e
são criados pelo serviço Lambda; declará-los no stack faria o deploy falhar por
conflito. A retenção é aplicada de forma idempotente por esse script e
verificada pelo preflight e por `validate_run.py`.

3. Crie o snapshot de preços:

```bash
cp experiments/pricing/pricing-template.json experiments/pricing/pricing-$(date +%F).json
# preencha captured_at_utc com a data real da consulta
```

O preço on-demand padrão do DynamoDB em `us-east-1` é **US$ 0,125/M RRU** e
**US$ 0,625/M WRU** desde novembro de 2024. O valor US$ 0,25/M RRU está
desatualizado. O `controller.py` recusa um snapshot incompleto ou com região
diferente do plano.

## 5. Ordem de execução

```bash
# 1. Planejar e validar os blocos
python scripts/experiment/planning/planner.py \
  --plan experiments/experiment-plan.yaml \
  --out experiments/metadata/plan
python scripts/experiment/planning/validate_block.py \
  --plan experiments/experiment-plan.yaml \
  --block-dir experiments/metadata/plan

# 2. Validar a infraestrutura sem gerar tráfego e aplicar retenção
python scripts/experiment/prepare/deploy.py \
  --config experiments/harness-config.yaml \
  --validate-only --configure-logs

# 3. Congelar o piloto antes do primeiro run (pode ser regerado com --force)
python scripts/experiment/planning/manifest.py \
  --config experiments/harness-config.yaml \
  --plan experiments/experiment-plan.yaml \
  --pricing experiments/pricing/pricing-YYYY-MM-DD.json \
  --template template.yaml \
  --out experiments/metadata/pilot-manifest.json \
  --phase pilot --force

# 4. Run controlado (piloto)
python scripts/experiment/controller.py \
  --plan experiments/experiment-plan.yaml \
  --config experiments/harness-config.yaml \
  --pricing experiments/pricing/pricing-YYYY-MM-DD.json \
  --phase pilot \
  --condition-file experiments/pilot-conditions.csv \
  --limit 1

# 5. Depois do piloto aprovado, congelar a campanha com worktree limpo
python scripts/experiment/planning/manifest.py \
  --config experiments/harness-config.yaml \
  --plan experiments/experiment-plan.yaml \
  --pricing experiments/pricing/pricing-YYYY-MM-DD.json \
  --template template.yaml \
  --out experiments/metadata/campaign-manifest.json \
  --phase campaign --require-clean

# 6. Run controlado (campanha)
python scripts/experiment/controller.py \
  --plan experiments/experiment-plan.yaml \
  --config experiments/harness-config.yaml \
  --pricing experiments/pricing/pricing-YYYY-MM-DD.json \
  --phase campaign \
  --condition-file experiments/metadata/plan/conditions_block_01.csv \
  --limit 1
```

`controller.py` exige `--pricing`, verifica a stack antes de cada run, arquiva
artefatos no S3 e enfileira substitutos quando o run é `invalid-apparatus`
(`campaign.max_retries`, padrão 2). Antes do primeiro run de cada fase, o
controller executa `preflight.py`: dataset, `GetItem` nas duas tabelas,
equivalência funcional das duas rotas, gerador e retenção de logs. O lock
`experiments/.campaign.lock` impede controllers concorrentes; além dele,
`check_concurrency.py` consulta via SSM se já existe k6/sampler ativo na EC2 e
grava `concurrency-check.json` em cada run. Nunca remova o lock sem confirmar
que não há run ativo.

Piloto e campanha usam manifestos separados: `experiments/metadata/pilot-manifest.json`
e `experiments/metadata/campaign-manifest.json`. O controlador reescreve o
manifesto do piloto (`manifest.py --force`) a cada correção do instrumento. Na
fase `campaign` ele usa `--require-clean` e recusa explicitamente um worktree
Git sujo, para que o commit registrado corresponda ao código executado.

## 6. Artefatos por run

- `manifest.json` — condição, fatores, fase, hashes, versões e status.
- `preflight.json` — cópia do preflight da fase (dados, equivalência, retenção).
- `concurrency-check.json` — processos k6/sampler detectados via SSM.
- `cold-reset.json` — memória, arquitetura, versão anterior/nova, alias e drift.
- `target-verification.json` — integração da API e versão do alias.
- `target-url.json` — URL exata usada pelo gerador.
- `k6-raw.json`, `k6-summary.json`, `loadgen-summary.json`, `generator-timing.json`.
- `k6-exit-code.txt`, `upload-exit-code.txt` — o gerador envia os artefatos ao
  S3 mesmo quando o `k6 run` falha; o código de saída é preservado.
- `storage-before.txt`, `storage-preflight.txt`, `storage-before-cleanup.txt` —
  espaço livre e tamanho do staging antes da carga, resultado do preflight e
  tamanho antes da limpeza remota.
- `generator-resources-raw.csv` — amostras brutas de `/proc` do gerador.
- `chrony-before.txt`, `chrony-after.txt`, `chrony-preflight.txt`, `clock.json`
  — offset NTP antes/depois e validação da tolerância.
- `cloudwatch-metrics.csv`, `cloudwatch-collection-status.json` — Lambda,
  API Gateway (`Count`, `Latency`, `IntegrationLatency`, `4xx`, `5xx`) e EC2.
- `generator-resources.csv`, `generator-resources-status.json` — CPU, memória,
  rede, load e pressão do gerador durante o run, com cobertura da janela.
- `requests.csv` — uma linha por requisição HTTP, com fase, timings,
  `error_code` e classificação de timeout (`1050`/`1211`).
- `schedule-verification.json` — contagens por janela, offsets e capacidade.
- `lambda-invocations.csv`, `lambda-collection-status.json`.
- `cost.json` — custo por janela, política de atribuição e totais brutos.
- `validation.json` — classificação e justificativas.
- `deploy-validation.json` — cópia da validação de integração do run.

O `validate_run.py` exige todos os artefatos acima. Runs inválidos permanecem
no dataset para auditoria; nunca são apagados para “melhorar” a amostra.

`collect_cost.py` atribui Lambda e requisições externas pelo `START` dentro de
`[T0, T0+240)` para `full_run` e `[T0+120, T0+240)` para `measurement`. A
margem de 120 s em `collect_lambda.py` serve apenas para ingestão e pareamento
`START`/`REPORT`; ela não amplia a janela contabilizada.

Se um run falhar em qualquer etapa, `controller.py` grava um `validation.json`
de falha, preserva o diretório parcial, envia todos os artefatos ao S3 e
verifica o upload. Uma falha de upload não apaga os arquivos locais nem oculta
o erro original; ela é registrada em `upload_error` no manifesto.

## 7. Piloto

O `planner.py` grava `experiments/pilot-conditions.csv` com as 12 condições
congeladas no bloco `pilot` do plano. Elas cobrem as duas composições, as duas
arquiteturas, os três padrões e os extremos de memória.

O piloto deve cobrir as 12 execuções previstas no protocolo e demonstrar:

- aliases e integrações qualificadas;
- alvo correto por run;
- ausência de invocações entre o reset e o T0;
- chegadas constantes, rajada e esparsa dentro das janelas;
- capacidade do gerador (`dropped_iterations == 0`);
- CPU, memória e rede do gerador registradas e sem saturação;
- CSV por requisição e pareamento `START`/`REPORT`;
- `Init Duration` na primeira invocação dos workers;
- janela de medição e custo corretos;
- artefatos brutos no S3.

Gere a evidência estruturada:

```bash
python scripts/experiment/report/pilot_report.py \
  --runs-dir experiments/runs/pilot \
  --pilot-conditions experiments/pilot-conditions.csv \
  --campaign-id tcc-2026-10 \
  --template experiments/metadata/pilot-validation-template.json \
  --deploy-validation experiments/metadata/deploy-validation.json \
  --out experiments/pilot-validation.json
```

O relatório filtra os runs pelo `campaign_id` e pelas 12 condições do piloto,
escolhe a repetição válida mais recente de cada condição e ignora tentativas
`invalid-apparatus` já substituídas, mas lista as tentativas falhas ou parciais
em `failed_or_partial_runs` para preservar a auditoria. A decisão continua exigindo cobertura das
duas arquiteturas (incluindo arm64), das duas composições, dos três padrões e da
memória mínima e máxima, além de preflight, concorrência, relógio, VUs,
equivalência funcional, custo independente, erros/timeouts/throttles, métricas
de API Gateway e estabilidade do warm-up. `APPROVED` só é retornado se todas as
verificações passarem e não houver issues. O Markdown é gravado ao lado do JSON
(`experiments/pilot-validation.md` por padrão). A assinatura humana
(`approved_by`/`approved_at_utc`) continua obrigatória antes da campanha
principal.

## 8. Análise

```bash
python scripts/analysis/aggregate.py --phase campaign
python scripts/analysis/bootstrap.py
python scripts/analysis/hypotheses.py
python scripts/analysis/select_config.py
python scripts/analysis/plots.py
```

Hipóteses:

- **H1** — memória versus p95/custo, com contrastes pareados entre memórias
  adjacentes por composição, arquitetura e padrão.
- **H2** — memória de menor custo que atende ao limite, **por padrão de carga**;
  inclui frequência de seleção via bootstrap e relato de conjunto vazio.
- **H3** — ARM64 versus x86_64 no custo e no p95, pareado por bloco.
- **H4** — razões `R_T = duração_faturada_x86 / duração_faturada_arm64` e
  `R_C = Creq_arm64 / Creq_x86`.
- **H5** — custo da composição decomposta menos monolítica, por arquitetura,
  memória e padrão.
- **H6** — p95 da composição decomposta menos monolítica e viabilidade.

Os contrastes usam bootstrap pareado por `block_id`. Os ICs são exploratórios;
aplique Holm/BH antes de afirmações confirmatórias em toda a família.

A seleção usa a regra congelada: **limite superior do IC 95% de p95 ≤ Lmax** e,
entre as configurações elegíveis, o menor `Creq` mediano. Se o conjunto for
vazio, o relatório registra o vazio e não relaxa o limite após ver os resultados.
As sensibilidades 300 ms e 800 ms são relatadas separadamente.

## 9. Limitações

- `cold_reset.py` altera recursos fora do CloudFormation; um `sam deploy`
  posterior sobrescreve a configuração do benchmark.
- A coleta Lambda depende de `START`/`REPORT` e da versão publicada; não há
  prova absoluta de ausência de ambiente aquecido, apenas o protocolo de versão
  nova + alias + nenhuma invocação antes do T0.
- O custo DynamoDB é estimado a partir de 9,5 RRU por requisição externa
  (item de ~75 KB, leitura eventual). Confirme o consumo real antes de tratar
  `Creq_app` como resultado definitivo.
- Nunca execute runs em paralelo.
