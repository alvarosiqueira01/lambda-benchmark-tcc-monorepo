# Tutorial prático revisado: execução da campanha experimental de otimização de custo e latência em AWS Lambda

**Projeto:** [lambda-benchmark-tcc-monorepo](https://github.com/alvarosiqueira01/lambda-benchmark-tcc-monorepo)  
**Data de referência deste protocolo:** 14/10/2026  
**Status:** especificação operacional da campanha; os scripts descritos ainda precisam ser implementados e validados no piloto.

Este documento descreve como preparar, automatizar, executar e analisar a campanha de benchmark. Ele corrige inconsistências de parâmetros, especifica a mini-rajada esparsa, explicita os contratos dos executáveis e define como atribuir requisições, invocações, custos e métricas às janelas de aquecimento e medição.

O documento **não afirma que os scripts apresentados já existem ou foram executados**. Os comandos de infraestrutura são operacionais; os módulos do harness são requisitos de implementação e seus contratos devem ser cumpridos antes da campanha principal.

---

## 1. Protocolo experimental congelado

### 1.1. Fatores e quantidade de execuções

| Fator | Níveis |
|---|---|
| Composição funcional | `monolithic`, `decomposed` |
| Arquitetura de execução | `x86_64`, `arm64` |
| Memória por função | 128, 256, 512, 1.024, 2.048 MB |
| Padrão de carga | `constant`, `burst`, `sparse_miniburst` |

O desenho é fatorial completo:

\[
2\times2\times5\times3=60\text{ condições}
\]

Cada condição será executada uma vez em cada um de dez blocos randomizados, totalizando:

\[
60\times10=600\text{ runs válidos planejados}
\]

A unidade experimental é **um run de quatro minutos**, não cada requisição individual. As requisições dentro de um run são amostras usadas para estimar a distribuição de latência daquele run.

A taxa de chegada é definida pelo padrão de carga; não é um quinto fator independente neste desenho.

### 1.2. Duração, aquecimento e medição

Cada run terá duração de tráfego planejado de 240 segundos:

- **Aquecimento:** intervalo `[T0, T0 + 120 s)`.
- **Medição primária:** intervalo `[T0 + 120 s, T0 + 240 s)`.
- **Coleta posterior:** aguarda a conclusão de requisições em andamento e a ingestão dos logs, sem gerar novas requisições.

`T0` é o instante de início do escalonamento de chegadas no gerador de carga, registrado pelo próprio gerador. Não usar como `T0` o momento em que o controlador envia o comando remoto, pois esse instante antecede o início real do cenário.

A decisão de descartar os primeiros dois minutos como aquecimento baseia-se em Eismann et al. (2022). O intervalo é uma decisão fixa do protocolo, não uma afirmação de que todos os workloads estabilizam sempre em exatamente dois minutos. A análise de piloto verificará a adequação operacional do intervalo; ele não será alterado depois de observar os resultados da campanha principal.

### 1.3. Padrões de carga e contagens esperadas

Todos os tempos e chegadas abaixo são relativos a `T0`. A janela de aquecimento é `[0,120)` e a janela de medição é `[120,240)`.

| Padrão | Agenda de chegadas | Total previsto em 240 s | Total previsto em 120 s de medição |
|---|---|---:|---:|
| Constante | 20 chegadas por segundo durante 240 s | 4.800 | 2.400 |
| Rajada | 100 chegadas em cada janela de 1 s que começa em t = 0, 5, 10, …, 235 s; nenhuma nova chegada planejada nos 4 s seguintes | 4.800 | 2.400 |
| Mini-rajada esparsa (Opção B) | Em cada ciclo de 10 s que começa em t = 0, 10, 20, …, 230 s, emitir 5 chegadas nos instantes relativos 0, 0,2, 0,4, 0,6 e 0,8 s; nenhuma nova chegada planejada durante os 9 s restantes | 120 | 60 |

**Definição obrigatória da carga esparsa:** a condição escolhida é a mini-rajada, e não uma chegada uniforme de 0,5 req/s. Assim, o tráfego ocorre em cinco chegadas distribuídas pelo primeiro segundo de cada ciclo de dez segundos, seguido de nove segundos sem chegadas planejadas. Essa diferença é importante porque a frequência dos períodos ociosos pode afetar a reutilização de ambientes Lambda.

As contagens esperadas consideram chegadas programadas, não apenas respostas HTTP concluídas. Respostas 4xx/5xx e timeouts de requisições iniciadas continuam sendo observações do sistema; não devem desaparecer da distribuição de latência.

### 1.4. Restrição primária de latência

O limite primário congelado é:

\[
L_{max}=500\text{ ms},\qquad p95_{e2e}\leq500\text{ ms}
\]

A análise de sensibilidade reutilizará os mesmos dados para `Lmax = 300 ms` e `Lmax = 800 ms`. Não alterar o limite primário depois de observar os resultados.

Para escolher a configuração de menor custo, a regra principal de viabilidade será conservadora: o limite superior do intervalo de confiança de 95% do p95 agregado entre runs também deverá ser menor ou igual a 500 ms. O p95 observado de cada run será mantido para transparência.

### 1.5. Definição das métricas de custo

O denominador de `Creq` é o número de **requisições externas efetivamente iniciadas** na janela correspondente, incluindo as que terminam em erro ou timeout. Runs com requisições que deveriam ter sido iniciadas mas foram perdidas por falta de capacidade do gerador não são aceitos como runs instrumentais válidos.

Serão produzidas duas métricas principais de custo:

\[
C_{req,\lambda} = \frac{C_{Lambda}}{N_{req,externas}}
\]

\[
C_{req,app} = \frac{C_{Lambda}+C_{HTTPAPI}+C_{DynamoDB,leituras}}{N_{req,externas}}
\]

`Creq,lambda` é a métrica principal de comparação das configurações Lambda. `Creq,app` acrescenta os custos marginais dos serviços diretamente usados no caminho de requisição. O seed, as escritas preparatórias e o armazenamento estático da tabela são registrados separadamente, não misturados ao custo marginal por requisição. O custo da EC2 geradora, do controlador, do S3 e da observabilidade também é reportado à parte como custo do aparato experimental.

Serão calculados tanto `Creq,measurement`, com invocações iniciadas na janela `[T0+120, T0+240)`, quanto `Creq,run`, para o run completo. A primeira é a métrica primária comparável ao p95 da janela de medição; a segunda é uma análise suplementar que inclui o período de aquecimento. O relatório sempre identificará qual definição está sendo usada.

---

## 2. Pré-requisitos e preparação do ambiente

### 2.1. Topologia operacional

Usar dois ambientes lógicos:

1. **Controlador:** máquina do pesquisador ou ambiente de automação que planeja os runs, opera o plano de controle AWS, coleta dados e grava manifestos.
2. **Gerador de carga:** instância EC2 dedicada, na mesma região AWS do API Gateway e das Lambdas, executando k6 e enviando os resultados para um local conhecido.

O tutorial deve usar AWS Systems Manager (SSM) Run Command para iniciar o k6 remotamente na EC2 geradora, sem depender de SSH manual. A EC2 deve estar registrada no Systems Manager, ter um instance profile com as permissões mínimas necessárias e acesso de escrita apenas ao prefixo S3 utilizado para os resultados. O controlador precisa poder enviar e consultar comandos SSM e ler os artefatos resultantes.

Não executar o gerador no mesmo processo do controlador e depois alegar que ele está isolado. Se for necessário simplificar a topologia e executar ambos na mesma máquina, isso deve ser uma decisão prévia do protocolo; nesse caso, registrar que controlador e gerador compartilham recursos. A configuração preferida para a campanha é EC2 geradora dedicada.

### 2.2. Ferramentas

| Ferramenta | Requisito | Uso |
|---|---|---|
| AWS CLI v2 | versão registrada no manifesto | Autenticação, inspeção de recursos e operações auxiliares |
| AWS SAM CLI | versão registrada no manifesto | Deploy inicial e validação de infraestrutura |
| Python 3.14.x no controlador | Versão exata registrada | Harness e análise |
| Python 3.14 no Lambda | runtime fixado no stack | Aplicação avaliada |
| k6 | versão exata registrada e congelada | Geração open-loop |
| Git | versão registrada | Identificação do código |
| `jq`, `sha256sum` | disponíveis | Inspeção e hashes |
| `uv` | recomendado | Ambiente Python reprodutível |
| PyYAML | dependência do planejador YAML | Ler `experiment-plan.yaml` |

Se o planejador importa `yaml`, declarar `pyyaml` nas dependências de desenvolvimento do projeto e atualizar o lockfile antes da campanha. Não depender de um pacote instalado acidentalmente no computador do pesquisador.

### 2.3. Autenticação AWS

Preferir AWS IAM Identity Center/SSO para o pesquisador ou credenciais de role para ambientes de automação. Evitar armazenar access keys em scripts, arquivos do repositório ou manifestos.

Verificar:

```bash
aws --version
sam --version
python --version
k6 version
git --version
aws sts get-caller-identity
```

Registrar conta, região, identidade usada e versões das ferramentas. Fixar a região antes da campanha; os exemplos deste documento usam `us-east-1`.

### 2.4. Preparar o repositório

```bash
git clone https://github.com/alvarosiqueira01/lambda-benchmark-tcc-monorepo.git
cd lambda-benchmark-tcc-monorepo
mkdir -p experiments/{pricing,runs,aggregate,metadata}
mkdir -p scripts/experiment scripts/analysis
```

Antes de começar, verificar o caminho real do dataset no checkout. Na estrutura atualmente documentada, o caminho esperado é:

```text
src/dataset/generated/v2/documents.json
```

Não usar `dataset/documents.json` sem confirmar que esse arquivo existe.

---

## 3. Fase A — congelamento do protocolo

### 3.1. Criar `experiments/experiment-plan.yaml`

O plano deve ser a fonte de verdade para parâmetros de campanha. Uma especificação de referência é:

```yaml
campaign_id: "tcc-2026-10"
random_seed: 42
region: "us-east-1"
runtime: "python3.14"

warmup_seconds: 120
measurement_seconds: 120
total_duration_seconds: 240
repetitions_per_condition: 10
blocks: 10

latency_constraint:
  primary_lmax_ms: 500
  sensitivity_lmax_ms: [300, 800]
  primary_metric: "p95_e2e"
  conservative_feasibility_rule: "upper_95ci_p95 <= primary_lmax_ms"

factors:
  composition: ["monolithic", "decomposed"]
  architecture: ["x86_64", "arm64"]
  memory_mb: [128, 256, 512, 1024, 2048]
  load_pattern: ["constant", "burst", "sparse_miniburst"]

load_patterns:
  constant:
    model: "open_loop"
    rate_per_second: 20
    period_seconds: 240
    expected_requests_total: 4800
    expected_requests_measurement: 2400

  burst:
    model: "open_loop_scheduled_windows"
    cycle_period_seconds: 5
    active_duration_seconds: 1
    active_rate_per_second: 100
    idle_duration_seconds: 4
    first_burst_start_seconds: 0
    total_cycles: 48
    expected_requests_per_cycle: 100
    expected_requests_total: 4800
    expected_requests_measurement: 2400

  sparse_miniburst:
    model: "open_loop_scheduled_windows"
    cycle_period_seconds: 10
    active_duration_seconds: 1
    requests_per_active_window: 5
    offsets_within_window_ms: [0, 200, 400, 600, 800]
    idle_duration_seconds: 9
    first_window_start_seconds: 0
    total_cycles: 24
    expected_requests_total: 120
    expected_requests_measurement: 60

arrival_schedule:
  constant_tolerance_fraction: 0.05
  burst_per_window_tolerance_fraction: 0.05
  sparse_expected_count_per_window: 5
  sparse_timing_jitter_ms: 50
  maximum_dropped_iterations: 0

validity:
  no_concurrent_runs: true
  require_alias_target_verified: true
  require_all_raw_data: true
  require_configuration_match: true
  require_generator_capacity: true
```

O campo `random_seed` deve ser usado para gerar os dez blocos randomizados. A lista de condições, as agendas produzidas e o hash dos arquivos resultantes devem ser salvos antes da campanha.

### 3.2. Gerar manifesto da campanha

Antes do primeiro run, criar `experiments/metadata/campaign-manifest.json` contendo pelo menos:

- ID da campanha, data/hora UTC e conta/região AWS;
- commit Git (`git rev-parse HEAD`);
- hash SHA-256 do dataset efetivo;
- hash do template SAM e de `experiment-plan.yaml`;
- runtime Lambda e, quando possível, runtime version ARN observado;
- versões de AWS CLI, SAM, Python, k6 e dependências do controlador;
- IDs e tipo da instância EC2 geradora;
- fonte, data de captura e região dos preços;
- seed de randomização;
- limites de latência e critérios de validade;
- configuração de logs e retenção.

Comandos auxiliares:

```bash
git rev-parse HEAD
sha256sum src/dataset/generated/v1/documents.json
sha256sum template.yaml experiments/experiment-plan.yaml
```

Não substituir os valores reais por placeholders no manifesto final.

### 3.3. Preços: registrar a fonte, região e data

Criar um snapshot versionado, por exemplo:

```text
experiments/pricing/pricing-2026-10-14.json
```

Os seguintes preços são valores de referência publicados para `us-east-1`; devem ser conferidos novamente e capturados na data real de execução da campanha. Se a região escolhida for outra, não reutilizar os valores sem verificação.

```json
{
  "region": "us-east-1",
  "captured_at_utc": "PREENCHER_COM_TIMESTAMP_REAL",
  "source_urls": {
    "lambda": "https://aws.amazon.com/lambda/pricing/",
    "api_gateway": "https://aws.amazon.com/api-gateway/pricing/",
    "dynamodb": "https://aws.amazon.com/dynamodb/pricing/"
  },
  "currency": "USD",
  "lambda_request_usd_per_million": 0.20,
  "lambda_gb_second_usd_x86_64": 0.0000166667,
  "lambda_gb_second_usd_arm64": 0.0000133334,
  "http_api_usd_per_million_requests_first_tier": 1.00,
  "dynamodb_standard_usd_per_million_rru": 0.125,
  "dynamodb_standard_usd_per_million_wru": 0.625,
  "pricing_model": "public_list_price_before_free_tier_credits_and_account_discounts"
}
```

Os valores de DynamoDB referem-se a unidades de requisição, não necessariamente a uma unidade por chamada: RRUs dependem do tamanho do item, consistência e tipo de leitura; WRUs dependem do tamanho do item e tipo de escrita. Para este benchmark, as escritas do seed ficam fora da janela medida. A leitura do documento realizada por monolito ou orquestrador deve ser custeada de acordo com as unidades consumidas/estimadas e com o modelo de consistência real. Registrar a classe de tabela (`Standard`), o modo (`PAY_PER_REQUEST`) e o tamanho do item utilizado.

Não utilizar custo líquido da fatura como métrica principal: Free Tier, créditos e descontos variam entre contas. Registrar a fatura/Cost Explorer como validação suplementar, não como substituto do cálculo analítico. Também registrar separadamente os custos do gerador EC2 e do aparato experimental.

### 3.4. Identificar o documento de benchmark sem presumir seu ID

Extrair o ID a partir do dataset, não codificar `doc-001` por conveniência:

```bash
DATASET_FILE="src/dataset/generated/v1/documents.json"
test -f "$DATASET_FILE"
DOC_ID=$(jq -r '.[0].id' "$DATASET_FILE")
DATASET_SHA256=$(sha256sum "$DATASET_FILE" | awk '{print $1}')
printf 'DOC_ID=%s\nDATASET_SHA256=%s\n' "$DOC_ID" "$DATASET_SHA256"
```

Registrar também a versão do dataset e o tamanho, em bytes e caracteres, do documento escolhido. Garantir que o mesmo `id`, corpo, idioma e tags sejam usados em todas as condições. Fazer o seed antes da campanha e não alterar a tabela durante os runs.

### 3.5. Definir critérios de validade

Criar `experiments/validity-criteria.md` antes de iniciar o piloto.

Um run pode ser `valid`, `invalid-apparatus` ou `valid-system-anomaly`.

**`valid`:** configuração, aliases, agenda de chegada e coleta correspondem ao protocolo.

**`invalid-apparatus`:** houve falha do instrumento, por exemplo versão errada, gerador incapaz de cumprir a agenda, artefatos ausentes, perda de dados ou execução concorrente não prevista.

**`valid-system-anomaly`:** o aparato funcionou, mas a aplicação apresentou erros, timeouts, throttling ou latência acima da meta. Esses resultados são parte da evidência experimental e não devem ser descartados automaticamente.

Critérios mínimos:

1. Memória, arquitetura, runtime, timeout, código e variáveis de ambiente iguais ao planejado.
2. Alias do API Gateway e aliases internos apontam para as versões registradas no manifesto.
3. Nenhuma invocação de aplicação ocorreu entre cold reset e `T0`.
4. As chegadas reais cumprem a agenda do padrão de carga dentro das tolerâncias definidas.
5. Não há iterações descartadas por incapacidade do gerador; se houver, investigar a causa e classificar o run, sem descarte automático.
6. Registros de início e `REPORT` necessários foram recuperados e podem ser associados por `RequestId`.
7. Dados brutos do gerador, métricas AWS, logs, custos, validação e manifesto estão preservados.
8. Não ocorreu outro run simultaneamente.

Os critérios de validade não devem excluir erros, cold starts tardios ou throttling causados pelo SUT.

---

## 4. Fase B — especificação do harness experimental

### 4.1. Estrutura de arquivos e contratos dos módulos

A estrutura atual do harness é organizada pelas fases do procedimento:

```text
scripts/
├── experiment/
│   ├── common.py
│   ├── controller.py
│   ├── planning/
│   │   ├── planner.py
│   │   ├── validate_block.py
│   │   └── manifest.py
│   ├── prepare/
│   │   ├── deploy.py
│   │   ├── preflight.py
│   │   ├── cold_reset.py
│   │   ├── verify_target.py
│   │   ├── check_concurrency.py
│   │   ├── log_retention.py
│   │   └── configure_log_retention.py
│   ├── run/
│   │   ├── load_test.py
│   │   ├── export_k6_requests.py
│   │   └── verify_schedule.py
│   ├── collect/
│   │   ├── collect_lambda.py
│   │   ├── collect_cloudwatch.py
│   │   └── collect_cost.py
│   ├── validate/
│   │   └── validate_run.py
│   └── report/
│       └── pilot_report.py
└── analysis/
    ├── aggregate.py
    ├── bootstrap.py
    ├── hypotheses.py
    └── plots.py
```

Cada módulo deve ter uma interface determinística e retornar um resultado explícito. O controlador não deve inferir sucesso apenas porque um comando terminou sem lançar exceção.

### 4.2. `planner.py`: gerar condições e blocos

**Entrada:** `experiment-plan.yaml`.  
**Saídas:** `conditions.csv`, dez arquivos `conditions_block_XX.csv`, e um manifesto da randomização.

`conditions.csv` deve conter as 60 combinações únicas dos quatro fatores. Cada bloco deve conter exatamente uma ocorrência de cada condição, em ordem aleatória. A seed e o algoritmo de randomização devem ser fixos e registrados, de forma que os arquivos possam ser reproduzidos.

Campos mínimos de cada linha:

```text
condition_id,composition,architecture,memory_mb,load_pattern
```

Cada run adicionará `block_id`, `replication`, `run_id`, horário planejado e horário real.

O planejador deve falhar se o número de condições não for 60, se houver duplicatas ou se algum bloco não contiver as 60 condições.

### 4.3. `deploy.py`: deploy inicial e verificação de infraestrutura

O deploy SAM deve ser feito antes do piloto, não dentro do ciclo de cada run. O harness usa as APIs do Lambda para alterar configurações controladas e publicar versões; ele não deve executar `sam deploy` a cada run.

O módulo de deploy deve:

1. validar o template (`sam validate --lint`);
2. construir (`sam build`);
3. implantar a infraestrutura inicial;
4. obter e registrar outputs do CloudFormation;
5. verificar existência de APIs, tabelas, funções, aliases, grupos de logs e permissões;
6. verificar que a integração do API Gateway usa um ARN qualificado do alias `benchmark`, e não a função sem qualificador ou `$LATEST`;
7. verificar que o orquestrador invoca os aliases `benchmark` das funções filhas.

O harness deve falhar antes de iniciar a campanha se o tráfego HTTP não puder ser direcionado para os aliases versionados.

### 4.4. `cold_reset.py`: publicar versões e atualizar aliases

A intenção do reset é assegurar que cada run use versões Lambda que não foram invocadas anteriormente pela campanha. Publicar uma versão e atualizar um alias só é útil se o caminho de chamada realmente utilizar esse alias.

Para cada função que participa da condição:

1. atualizar a configuração de memória e arquitetura, além de uma descrição única contendo `run_id`;
2. esperar `LastUpdateStatus=Successful` e o estado ativo;
3. publicar uma nova versão;
4. confirmar que a versão publicada é nova e corresponde ao código/configuração esperados;
5. atualizar o alias `benchmark` para a versão publicada;
6. reler o alias e confirmar `FunctionVersion`;
7. registrar ARN qualificado, número da versão, arquitetura, memória, timeout, `CodeSha256`, runtime version ARN disponível e `LastModified`.

A alteração da descrição única serve para garantir que exista uma alteração de configuração a publicar quando memória/arquitetura forem idênticas às do run anterior. Se `PublishVersion` não produzir uma versão nova, o procedimento deve falhar; não deve reutilizar silenciosamente a versão anterior.

Na arquitetura monolítica, publicar a versão da Lambda monolítica. Na arquitetura decomposta, publicar versões novas do orquestrador e das três Lambdas de análise e atualizar os aliases respectivos. A Lambda de CRUD da arquitetura decomposta não participa do workload medido de `/analysis`; sua configuração e rota devem permanecer consistentes, mas suas invocações não devem ser contadas no custo do workload se ela não receber chamadas durante o run.

**Verificação crítica:** o API Gateway deve integrar com o ARN do alias do monolito ou do orquestrador. As invocações internas também devem usar os ARNs qualificados dos aliases filhos. Se a integração ou o `AnalysisInvoker` utilizar um ARN sem qualificador, a versão testada não estará sob controle do protocolo.

Após o reset, são permitidas somente verificações de plano de controle (`GetFunctionConfiguration`, `GetAlias`, `GetIntegration`, etc.). **Não fazer `curl`, smoke test ou qualquer invocação de aplicação entre o reset e `T0`.**

A publicação de versões permite isolar pools de execução por versão, mas o piloto deve verificar a primeira invocação e os registros de inicialização. Não se deve afirmar que toda chamada será fria: o protocolo controla a condição inicial e observa a reutilização/expansão dos ambientes durante a carga.

### 4.5. `load_test.py`: executar a agenda open-loop

O contrato de `load_test.py` é:

**Entrada:** padrão de carga, URL do alias correto, ID de documento, duração de 240 s, offsets/agenda, `run_id`, destino de saída.  
**Saída:** amostras brutas por requisição, resumo do gerador, `T0`, `Tend`, contagem de requisições iniciadas/concluídas/erro/timeout, iterações descartadas, utilização do gerador e evidência de aderência à agenda.

O controlador deve iniciar o k6 na EC2 por SSM Run Command. A EC2 deve retornar os artefatos e o resultado do comando; uma falha no SSM deve ser registrada como falha do aparato, não ser tratada como um teste com zero requisições.

#### Implementação da carga constante

Usar um executor `constant-arrival-rate`, com 20 iterações/requisições por segundo durante 240 s. O piloto deverá dimensionar `preAllocatedVUs` e `maxVUs` para a pior latência esperada e congelar esses valores antes da campanha. Registrar CPU, memória, rede e `dropped_iterations` do gerador.

#### Implementação da rajada

Não usar `ramping-arrival-rate` como se fosse automaticamente equivalente a uma taxa constante de 100 req/s por 1 s seguida de quatro segundos sem chegadas: uma rampa pode alterar gradualmente a taxa dentro da janela.

A especificação funcional obrigatória é uma agenda aberta com 48 janelas de um segundo, iniciadas a cada cinco segundos. Uma implementação possível em k6 é um cenário open-loop de iterações a 100/s durante 240 s, no qual o código envia HTTP somente se o instante agendado cair no primeiro segundo do ciclo de cinco segundos; nas iterações das janelas ociosas, não envia uma requisição HTTP. Assim, o executor mantém seu relógio de chegadas sem gerar tráfego durante os quatro segundos ociosos. Se essa implementação for usada, contar separadamente iterações do k6 e requisições HTTP efetivamente enviadas.

O piloto deverá demonstrar: 100 chegadas programadas em cada janela ativa, nenhuma chegada planejada nas janelas ociosas, 4.800 requisições em 240 s e 2.400 na janela de medição. Também deverá demonstrar que o k6 não descarta iterações devido à alocação insuficiente de VUs.

#### Implementação da mini-rajada esparsa (Opção B)

Usar a mesma técnica de agenda aberta: escalonar cinco iterações por segundo durante todo o teste, mas enviar requisições HTTP somente quando o instante agendado estiver nos primeiros 1.000 ms de cada ciclo de dez segundos. As cinco chegadas devem ter offsets aproximados de 0, 200, 400, 600 e 800 ms a partir do início do ciclo. Não enviar requisições nos nove segundos seguintes.

O executor interno do k6 terá iterações sem chamada HTTP nos períodos ociosos; essas iterações não são requisições externas e não podem ser incluídas em `Nreq`. Deve haver um contador específico de HTTP efetivamente iniciadas. O piloto deve verificar 24 mini-rajadas, 5 requisições por mini-rajada, 120 requisições por run e 60 requisições durante a janela de medição.

Essa especificação preserva um único gerador e mantém a agenda aberta. Se o mecanismo de agenda for implementado de outra maneira, precisa demonstrar a mesma sequência temporal e as mesmas contagens no piloto.

#### Classificação warm-up/measurement das requisições

Cada amostra deve registrar o instante de início da requisição e sua duração fim a fim. A fase (`warmup` ou `measurement`) é atribuída pelo **instante em que a requisição começou**, não pelo momento em que sua resposta terminou. Assim, uma resposta que cruza a fronteira de 120 s continua pertencendo à fase em que foi iniciada.

Registrar pelo menos:

```text
run_id,request_sequence,scheduled_at_utc,started_at_utc,phase,
latency_ms,status_code,timeout,request_sent
```

O JSON de saída do k6 deve ser interpretado de acordo com seu formato real de séries/métricas. O harness deve produzir uma tabela por requisição ou artefato equivalente com tempo de início, duração e fase; não presumir que a saída JSON seja um único objeto resumido.

### 4.6. `collect_lambda.py`: logs `START` e `REPORT`

**Entrada:** grupo de logs, intervalo de coleta, lista de funções/versões esperadas, `run_id`.  
**Saída:** um registro por invocação Lambda, com associação entre `START` e `REPORT` pelo `RequestId`.

Campos mínimos:

```text
run_id,function_name,version,request_id,start_timestamp_utc,
report_timestamp_utc,duration_ms,billed_duration_ms,memory_mb,
max_memory_used_mb,init_duration_ms,cold_start,report_found
```

O parser deve extrair o horário de `START`, pois a linha `REPORT` é emitida ao final da execução. Para atribuir uma invocação à janela de medição, usar o timestamp de início da invocação, e não somente o timestamp do `REPORT`. O grupo de logs pode receber eventos de forma assíncrona; por isso, coletar com margem temporal e repetir a consulta até que os dados se estabilizem ou o limite de espera seja atingido.

O parser deve suportar o formato de logs configurado na campanha e ser testado contra exemplos reais do runtime fixado. Se o formato do `REPORT` mudar, não interpretar silenciosamente campos ausentes como zero. Uma coleta vazia ou incompleta deve produzir `collection_status=INCOMPLETE` com diagnóstico explícito.

Definição operacional de cold start: invocação cujo registro `REPORT` contém `Init Duration` (ou a representação equivalente no formato de log utilizado). A ausência de `Init Duration` em invocações individuais não implica erro; ausência em todas as funções recém-publicadas deve levar a investigação durante o piloto.

Para a arquitetura decomposta, coletar os registros de orquestrador e das três funções especializadas. Uma requisição externa válida pode gerar uma invocação do orquestrador e uma invocação de cada worker; o custo deve somar todas as invocações realizadas no caminho.

### 4.7. `collect_cloudwatch.py`

Coletar métricas pelo `GetMetricData` ou mecanismo equivalente, documentando período, estatística e dimensões utilizadas. Mínimo:

- Lambda: `Invocations`, `Errors`, `Throttles`, `Duration`, `ConcurrentExecutions`;
- API Gateway HTTP API: contagem, latência e erros disponíveis para a API/stage;
- estado do gerador: CPU, memória e rede, se disponível.

Usar as métricas agregadas como verificações complementares. A fonte primária de p95 fim a fim é a amostra individual do gerador de carga; a fonte primária da duração faturada é o `REPORT` Lambda.

O coletor deve registrar as dimensões reais consultadas (`FunctionName`, `Resource`, `ApiId`, `Stage`, etc.), unidade, estatística, período, timestamps e respostas brutas da API. Se uma métrica não estiver habilitada ou não estiver disponível, registrar isso explicitamente.

### 4.8. `collect_cost.py`: custo analítico

Para cada invocação Lambda:

\[
GBs_i=\frac{BilledDuration_{ms,i}}{1000}\times\frac{MemorySize_{MB,i}}{1024}
\]

\[
C_{duration}=\sum_i GBs_i\times Price_{GBs,architecture_i}
\]

\[
C_{requests}=N_{invocations}\times\frac{Price_{request/million}}{10^6}
\]

\[
C_{Lambda}=C_{duration}+C_{requests}
\]

Para a arquitetura decomposta, incluir orquestrador e três workers, cada um com sua própria memória, arquitetura, invocações e `BilledDuration`. Não usar apenas a quantidade de requisições externas como número de invocações Lambda.

Para o custo marginal da aplicação, acrescentar API Gateway por requisição externa e DynamoDB pelas unidades de leitura consumidas ou estimadas. Registrar expressamente a consistência do `GetItem`. Em DynamoDB Standard sob demanda, leituras são tarifadas em incrementos de 4 KB: leitura eventualmente consistente consome metade de uma RRU por incremento de 4 KB; leitura fortemente consistente consome uma RRU por incremento. Escritas são medidas por incrementos de 1 KB e não fazem parte do tráfego de análise durante a campanha. Use o tamanho real do item e a consistência configurada; não presumir uma RRU por chamada sem verificar.

A função deve produzir tanto custo de medição quanto custo do run completo. Para custo da janela de medição, atribuir as invocações Lambda pela hora de início (`START`) dentro de `[T0+120, T0+240)`. Para custo do run completo, incluir as invocações iniciadas durante todo o run. Registrar também os totais brutos para que essa política possa ser auditada.

Fórmulas:

\[
C_{req,\lambda,measurement}=
\frac{C_{Lambda,measurement}}{N_{HTTP,measurement}}
\]

\[
C_{req,app,measurement}=
\frac{C_{Lambda,measurement}+C_{API,measurement}+C_{DDB,measurement}}
{N_{HTTP,measurement}}
\]

`N_HTTP,measurement` inclui todas as requisições externas realmente iniciadas na janela, mesmo que terminem em erro/timeout. Se a agenda de chegadas não tiver sido cumprida, o run precisa ser classificado pela regra de validade antes de ser usado.

`Init Duration` já está incorporado em `Billed Duration` para as invocações relevantes após a mudança de cobrança de 1º de agosto de 2025; não somar o `Init Duration` novamente.

O custo estimado deve ser comparado, como validação secundária, às métricas de faturamento disponíveis depois da ingestão. Cost Explorer ou CUR não têm granularidade adequada para identificar diretamente cada execução de quatro minutos e, portanto, não substituem o cálculo por run.

### 4.9. `validate_run.py`

**Entrada:** plano, condição, manifesto, dados k6, logs Lambda, métricas CloudWatch, resultado de custos.  
**Saída:** `validation.json` com estado, checks individuais, valores observados, motivo e decisão.

Campos mínimos:

```json
{
  "run_id": "...",
  "status": "valid | invalid-apparatus | valid-system-anomaly",
  "checks": [],
  "issues": [],
  "decision_timestamp_utc": "..."
}
```

A validação deve confirmar configuração, alias, agenda de chegada, completude de dados, ausência de runs simultâneos e integridade dos manifestos. Erros, timeouts, throttles, latência acima de `Lmax` e cold starts tardios não tornam o run instrumentalmente inválido por si só.

Para `dropped_iterations`, distinguir falta de VUs/recursos do gerador de degradação da aplicação: o k6 documenta que ambos podem causar iterações descartadas. O critério deve avaliar a causa no piloto e preservar o diagnóstico, não converter qualquer descarte automaticamente em falha do aparato.

### 4.10. `manifest.py`

Cada execução deve produzir `experiments/runs/<phase>/<run_id>/manifest.json` antes de iniciar a carga e atualizá-lo ao final com os resultados de controle.

Campos obrigatórios:

- `run_id`, `condition_id`, `block_id`, `replication`;
- fatores (composição, arquitetura, memória, carga);
- horários UTC de preparação, `T0`, começo da medição, fim do tráfego e fim da coleta;
- IDs/ARNs de funções e versões publicadas, aliases e alvos verificados;
- configuração observada, `CodeSha256`, runtime e runtime version ARN se disponível;
- região, conta, dataset e hash, commit Git, hash do template e plano;
- agenda de chegada, contagens esperadas/observadas, versão do k6 e ambiente gerador;
- versão do snapshot de preço;
- caminhos dos dados brutos, custos, logs, métricas e validação;
- estado final e justificativa em caso de run inválido.

### 4.11. `controller.py`: contrato do controlador

O controlador deve executar um run inteiro, na seguinte ordem:

1. Ler e validar plano e condição.
2. Criar `run_id` único e manifesto inicial.
3. Confirmar que nenhum outro run está ativo.
4. Aplicar configuração, publicar versões e atualizar aliases.
5. Verificar configuração/roteamento exclusivamente pelo plano de controle.
6. Solicitar ao gerador, via SSM, a execução da agenda k6 de 240 s.
7. Receber de volta `T0`, `Tend`, amostras brutas e resumo do gerador.
8. Aguardar o término das requisições em andamento e a ingestão dos logs.
9. Coletar `START`/`REPORT` e métricas CloudWatch para todas as funções participantes.
10. Calcular custos de janela e do run completo.
11. Executar validação e salvar todos os artefatos.
12. Enviar o diretório completo do run ao S3 e verificar o sucesso do upload.
13. Atualizar o manifesto final; apenas então liberar o próximo run.

Se ocorrer falha de controle ou coleta, preservar o diretório parcial e classificar o run. Não apagar nem sobrescrever um run inválido quando uma nova tentativa for realizada.

---

## 5. Armazenamento dos artefatos e controle de custo

### 5.1. Estrutura de resultados

```text
experiments/
├── experiment-plan.yaml
├── conditions.csv
├── conditions_block_01.csv ... conditions_block_10.csv
├── pricing/pricing-2026-10-14.json
├── metadata/campaign-manifest.json
├── runs/<run_id>/
│   ├── manifest.json
│   ├── validation.json
│   ├── loadgen-summary.json
│   ├── request-samples.jsonl
│   ├── lambda-invocations.csv
│   ├── cloudwatch-raw.json
│   ├── cost.json
│   └── logs/
└── aggregate/
    ├── runs.csv
    ├── function_runs.csv
    ├── conditions.csv
    ├── hypothesis-tests.csv
    └── figures/
```

Manter os dados brutos imutáveis. Os CSVs agregados podem ser reconstruídos a partir dos arquivos de cada run.

### 5.2. S3

Criar um bucket dedicado na mesma região, com bloqueio de acesso público e criptografia habilitada. Ativar versionamento se o custo adicional for aceitável e aplicar uma política de ciclo de vida explícita após preservar os artefatos necessários ao TCC. O controlador deve enviar o diretório de cada run para um prefixo exclusivo, por exemplo `s3://<bucket>/campaigns/tcc-2026-10/runs/<run_id>/`.

Um run só pode ser considerado arquivado quando todos os arquivos esperados foram enviados e verificados. Registrar os URIs S3 no manifesto. Retenção de logs CloudWatch deve ser limitada e documentada para evitar acumulação de custos; a configuração de retenção deve ser aplicada antes do piloto e permanecer constante durante a campanha.

O diretório de staging no gerador (`/opt/lambda-benchmark/results/<run_id>`) pode ser removido depois que o upload ao S3 for concluído e verificado, pois o diretório local do controlador e a cópia S3 preservam o run. Se o upload falhar, o staging remoto deve ser mantido para nova tentativa; runs inválidos não podem ser apagados do dataset local ou do S3.

### 5.3. Orçamento e limites de segurança operacional

Antes do piloto:

- configurar alertas de orçamento AWS para a conta/campanha;
- registrar um teto operacional de custo total;
- inspecionar cotas de concorrência e limites de invocação;
- confirmar a capacidade da instância EC2 geradora;
- manter Provisioned Concurrency, SnapStart, warmers, X-Ray e extensões fora do experimento principal;
- não configurar reserved concurrency para limitar silenciosamente o SUT, salvo se isso fizer parte explícita do protocolo;
- registrar se Free Tier/créditos existem, mas usar preços de lista no cálculo científico.

Não executar runs em paralelo. Todas as condições compartilham recursos de conta/região e a execução simultânea introduziria uma variável de interferência.

---

## 6. Fase C — piloto instrumental

Executar 12 runs escolhidos para cobrir extremos e caminhos relevantes. A seleção exata deve constar em `experiments/pilot-conditions.csv`; não deixar combinações essenciais representadas por reticências. O piloto deve incluir:

- memória mínima e máxima;
- ambas as arquiteturas;
- ambas as composições;
- os três padrões de carga;
- condições monolítica e decomposta, incluindo chamada dos três workers.

O piloto não será usado para decidir antecipadamente qual configuração é melhor. Sua finalidade é verificar o aparato, calibrar a capacidade do gerador e validar coleta/cálculo.

### 6.1. Checklist do piloto

Para cada run, confirmar:

1. A condição correta foi aplicada a todas as funções participantes.
2. API Gateway encaminhou para o alias correto e o orquestrador chamou os aliases filhos.
3. Nenhuma invocação de aplicação foi feita depois do reset e antes de `T0`.
4. A primeira utilização das versões recém-publicadas produziu o comportamento de inicialização esperado; investigar a ausência de `Init Duration` em todas as funções, mas não exigir que todas as invocações posteriores sejam frias.
5. O workload constante gerou 4.800 requisições e manteve 20 req/s.
6. A rajada gerou 48 janelas ativas de 100 chegadas cada e quatro segundos sem chegadas por ciclo.
7. A mini-rajada esparsa gerou 24 ciclos com cinco chegadas nos offsets estabelecidos e nove segundos sem chegadas em cada ciclo.
8. A taxa observada e os offsets de chegada ficaram dentro das tolerâncias congeladas.
9. O gerador não ficou saturado; sua CPU, memória e rede foram registradas.
10. O parser associou `START` e `REPORT` usando `RequestId` e encontrou as invocações esperadas.
11. A fase warm-up/measurement foi classificada pela hora de início da requisição/invocação.
12. Os custos foram calculados sem dupla contabilização de `Init Duration`.
13. Monolítico e decomposto produziram saída funcionalmente equivalente.
14. Os manifestos e os arquivos brutos foram arquivados no S3.

### 6.2. Estabilidade do aquecimento

Verificar, como diagnóstico, se os p95 em subintervalos da segunda metade apresentam tendência anormal ou se ainda há mudanças marcantes na taxa de cold starts. Isso não autoriza redefinir o warm-up depois de observar a campanha principal. Se o piloto mostrar que o aparato está mal configurado ou que a regra de aquecimento não é aplicável ao comportamento planejado, corrigir o protocolo, registrar uma nova versão do plano e repetir o piloto antes de congelar a campanha.

### 6.3. Relatório de aprovação

Criar `experiments/pilot-validation.json` e um relatório legível com:

- condições executadas;
- aderência de chegada por padrão;
- VUs e `dropped_iterations`;
- erros, timeouts e throttling;
- completude de logs e métricas;
- verificação de alias/versão;
- comparação do custo calculado com estimativa independente;
- evidência de equivalência funcional;
- decisão `APPROVED` ou `NOT_APPROVED` e lista de ações pendentes.

A campanha principal só começa se o piloto for aprovado e o plano final estiver versionado no Git.

---

## 7. Fase D — campanha principal

### 7.1. Gerar os dez blocos

Executar o planejador para produzir `conditions.csv` e `conditions_block_01.csv` até `conditions_block_10.csv`. Confirmar automaticamente:

- 60 condições únicas;
- 60 linhas por bloco, excluindo cabeçalho;
- todas as condições presentes exatamente uma vez por bloco;
- ordem distinta/randomizada entre blocos;
- seed e hashes registrados.

### 7.2. Executar sequencialmente

Exemplo da interface pretendida do controlador:

```bash
python scripts/experiment/controller.py \
  --condition-file experiments/metadata/plan/conditions_block_01.csv \
  --plan experiments/experiment-plan.yaml \
  --config experiments/harness-config.yaml \
  --pricing experiments/pricing/pricing-2026-10-14.json
```

Repetir para cada bloco, sempre aguardando a coleta e o arquivamento de um run antes de iniciar o próximo. O controlador deve falhar de forma explícita se uma condição estiver incompleta, e nunca iniciar um segundo run em paralelo.

### 7.3. Tratamento de runs inválidos

Se o run for `invalid-apparatus`, preservar todos os artefatos, registrar a causa e colocar uma repetição substituta com novo `run_id` em uma fila de retry. O run substituto deve repetir exatamente a mesma condição.

Se for `valid-system-anomaly`, manter o run na análise. Erros, timeouts, throttling e p95 acima de `Lmax` são resultados. Não repetir uma condição só porque o resultado foi desfavorável.

Após um retry, devem existir tanto o registro do run original inválido quanto o do run substituto. O arquivo agregado deve identificar qual repetição válida foi utilizada no estimador principal e quantos runs instrumentais foram rejeitados.

### 7.4. Gestão das versões Lambda

Registrar a relação `run_id → function → version → alias ARN`. Manter as versões até que os artefatos estejam arquivados e a campanha esteja concluída, salvo se a quota de armazenamento exigir limpeza anterior. Monitorar o armazenamento de pacotes e não remover uma versão apontada por alias ativo. A limpeza final deve ser auditável e não pode apagar os manifestos nem os resultados.

---

## 8. Fase E — agregação e análise estatística

### 8.1. Tabelas agregadas

Produzir pelo menos:

- `runs.csv`: uma linha por run, com fator, janela, p50/p90/p95/p99, média, desvio-padrão, número de requisições iniciadas, erros/timeouts, cold-start rate, custo Lambda, custo de aplicação, estado de validade e metadados.
- `function_runs.csv`: uma linha por função e run, com versão, invocações, duração, duração faturada, `Init Duration`, cold starts, erros, throttles, memória máxima e concorrência.
- `conditions.csv`: uma linha por condição e os resumos de dez runs válidos.
- `hypothesis-tests.csv`: contrastes, efeito estimado, intervalo de confiança, método, correção de comparações múltiplas quando aplicável e conclusão cautelosa.

Não substituir os arquivos brutos por dados agregados.

### 8.2. Estatísticas por condição

Para cada célula experimental, calcular pelo menos:

- mediana do p95 por run;
- IQR do p95;
- média e desvio-padrão do p95 entre runs;
- IC de 95% por bootstrap sobre runs;
- mediana e IC de 95% do `Creq`;
- taxa de erro/timeouts;
- cold-start rate por função e da aplicação.

Fazer bootstrap sobre as replicações/run-level, não sobre todas as requisições como se fossem réplicas independentes. As requisições dentro do mesmo run compartilham condições ambientais e não devem inflar artificialmente o tamanho amostral da comparação entre configurações.

### 8.3. Análise das hipóteses

**H1 — Memória e retornos decrescentes.** Para cada combinação de composição, arquitetura e carga, comparar níveis adjacentes de memória: 128→256, 256→512, 512→1.024 e 1.024→2.048 MB. Mostrar memória × p95, memória × duração faturada e memória × Creq. Examinar se os ganhos marginais de latência diminuem nos níveis superiores.

**H2 — Memória ótima por padrão.** Para cada padrão, identificar as configurações que atendem ao limite primário de latência. Entre elas, localizar o menor Creq. Usar bootstrap entre runs para expressar a incerteza da configuração ótima e não apenas apontar um único vencedor pontual.

**H3 — arm64 versus x86_64.** Comparar as arquiteturas em pares com memória, composição e carga iguais. Apresentar razão de custos e razão de p95, efeitos percentuais e ICs de 95%. A hipótese é específica ao workload e não implica superioridade universal de arm64.

**H4 — Diferença relativa de custo e duração faturada.** Definir antecipadamente a razão `R_T = BilledDuration_x86 / BilledDuration_arm64` e a razão de custo `R_C = Creq_arm64 / Creq_x86`, sempre em condições pareadas. Analisar a relação prevista com um modelo/contraste que mantenha controlados memória, composição e padrão de carga. Não interpretar uma correlação mecânica isolada como demonstração suficiente da hipótese.

**H5 — Custo do monolito versus decomposição.** Comparar arquiteturas funcionalmente equivalentes na mesma memória, ISA e carga. Decompor o custo da versão decomposta em orquestrador e três workers, e calcular `ΔCreq = Creq_decomposto − Creq_monolítico`.

**H6 — Overhead e ganho de latência.** Comparar simultaneamente `ΔCreq` e `Δp95`. Construir um gráfico de custo por requisição versus p95, marcando configurações factíveis. Mostrar invocações e duração faturada de cada função para explicar o trade-off.

Definir contrastes primários antes da análise final e evitar dezenas de testes exploratórios sem correção para comparações múltiplas. Reportar magnitudes de efeito e intervalos de confiança, não apenas p-values.

### 8.4. Restrição de latência e análise de sensibilidade

Uma configuração é elegível para a seleção principal se o limite superior do IC de 95% para o p95 entre runs não exceder 500 ms. Entre as elegíveis, selecionar a de menor custo estimado por requisição, reportando a incerteza do custo.

Repetir a seleção usando 300 e 800 ms como análise de sensibilidade, sem modificar a conclusão primária. Se nenhuma configuração satisfizer o limite, registrar que o conjunto testado não encontrou uma configuração viável; não relaxar o limite depois de observar os resultados.

### 8.5. Figuras mínimas

1. Memória × p95 por padrão de carga.
2. Memória × Creq por padrão de carga.
3. Razão de custo arm64/x86_64 por memória e carga.
4. Creq versus p95 para monolítico e decomposto, destacando as configurações elegíveis.
5. Decomposição do custo Lambda por função.
6. Taxa de cold start por função e padrão de carga.
7. Fronteira custo-latência para `Lmax = 300, 500, 800 ms`.
8. Rajada: p95 por janela de um segundo e concorrência observada.
9. Mini-rajada esparsa: latência por ciclo e ocorrência de cold starts.

---

## 9. Conjunto de dados e atribuição de janelas

A classificação de fase deve utilizar o instante de início de cada evento relevante:

- Para uma requisição externa, usar o instante em que o gerador iniciou a chamada HTTP.
- Para uma invocação Lambda, associar `START` e `REPORT` por `RequestId` e usar o timestamp de `START` para atribuir a invocação à janela.

Isso impede que uma resposta que termina logo após `T0+120` seja incorretamente colocada na fase de medição quando começou durante o aquecimento. Também evita classificar uma invocação somente pelo timestamp de seu `REPORT`, registrado ao final da execução.

Como o caminho decomposto pode causar uma pequena defasagem entre o início da requisição externa e o início de cada função filha, a atribuição do custo por janela é operacionalmente definida pelo `START` de cada invocação. Registrar ainda o custo do run completo e declarar a regra no relatório. Se algum componente atravessar o limite temporal, não alterar manualmente sua atribuição.

A análise primária de p95 inclui todas as requisições HTTP iniciadas no intervalo de medição, incluindo status não 2xx e timeouts. A taxa de erro deve ser apresentada ao lado do p95, de modo que uma configuração não seja considerada satisfatória apenas por responder rapidamente a uma fração selecionada das chamadas.

---

## 10. Segurança, observabilidade e ameaças à validade

### 10.1. Não adicionar instrumentação desnecessária

Manter logs `REPORT` padrão do Lambda e as métricas necessárias do CloudWatch. Não habilitar X-Ray, extensões, métricas detalhadas por rota, logging de payload ou logs customizados por requisição sem justificativa e análise de impacto. Qualquer instrumentação que faça parte do caminho medido deve ser fixada e aplicada igualmente às duas arquiteturas.

### 10.2. Condições congeladas

Durante a campanha principal, não alterar código, dataset, algoritmo, payload, modo de consistência do DynamoDB, timeout, região, tamanho de memória previsto, arquitetura, versão do gerador ou configuração dos logs. Se uma correção for necessária, encerrar a campanha, versionar a alteração, executar novo piloto e iniciar uma nova campanha identificada separadamente.

### 10.3. Limites de generalização

Os resultados serão válidos para o workload de análise textual, runtime, região, configuração de API Gateway/DynamoDB e padrões de carga utilizados. Não generalizar diretamente para todos os workloads Lambda, linguagens ou provedores serverless.

### 10.4. Custos do experimento

A campanha contém aproximadamente 1,944 milhão de requisições externas se todos os runs planejados forem executados uma vez por condição e repetição; retries instrumentais podem aumentar esse total. O número de invocações Lambda será maior para a composição decomposta. Estimar o orçamento antes do piloto, incluir chamadas internas e configurar alertas de custo. O custo real dependerá de duração faturada, memória, arquitetura, tamanho do item DynamoDB, API Gateway e custos do gerador/observabilidade.

---

## 11. Checklist de conclusão por fase

### Antes do piloto

- [ ] O plano contém `Lmax=500 ms` e análise de sensibilidade em 300/800 ms.
- [ ] A carga esparsa está definida como mini-rajada de cinco chegadas nos offsets 0–800 ms a cada dez segundos.
- [ ] O dataset real, o documento fixo e seu SHA-256 estão registrados.
- [ ] Os preços têm região, fonte e timestamp de captura reais.
- [ ] A integração HTTP e as invocações internas usam aliases versionados.
- [ ] O SSM controla a EC2 geradora e o mecanismo de saída recupera todos os artefatos.
- [ ] A agenda open-loop e a separação warm-up/measurement foram testadas.
- [ ] O parser foi validado com registros reais do runtime.
- [ ] O cálculo de custo inclui todas as invocações e não soma `Init Duration` duas vezes.
- [ ] Os critérios de validade estão congelados.

### Depois do piloto

- [ ] As 12 condições do piloto cobrem todos os fatores críticos.
- [ ] Os três padrões atendem às suas agendas especificadas.
- [ ] Não há saturação indevida do gerador.
- [ ] Não há dados ou logs essenciais ausentes.
- [ ] A configuração de alias/versão é verificada sem invocar o SUT antes de `T0`.
- [ ] O cálculo de custo foi revisado independentemente.
- [ ] O relatório de aprovação do piloto foi arquivado.

### Depois da campanha principal

- [ ] Há dez runs válidos por cada uma das 60 condições, ou substituições documentadas para falhas instrumentais.
- [ ] Todos os runs, inclusive inválidos, têm registros e manifestos preservados.
- [ ] `runs.csv` e `function_runs.csv` podem ser regenerados dos dados brutos.
- [ ] Os intervalos de confiança são calculados entre runs.
- [ ] H1–H6 têm contrastes e resultados documentados.
- [ ] A seleção final respeita o critério primário de p95.
- [ ] A análise de sensibilidade foi gerada sem alterar o Lmax primário.
- [ ] Os resultados, scripts, configuração e limitações foram versionados e arquivados.

---

## 12. Referências técnicas e metodológicas

### Metodologia e ferramentas

- EISMANN, S. et al. *A case study on the stability of performance tests for serverless applications*. Journal of Systems and Software, 2022. [https://doi.org/10.1016/j.jss.2022.111294](https://doi.org/10.1016/j.jss.2022.111294).
- COPIK, M. et al. *SeBS: A Serverless Benchmark Suite for Function-as-a-Service Computing*. Middleware, 2021. [https://doi.org/10.1145/3464298.3476133](https://doi.org/10.1145/3464298.3476133).
- k6. *Open and closed models*. [https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/).
- k6. *Dropped iterations*. [https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/).

### Documentação AWS

- AWS Lambda. *PublishVersion API*. [https://docs.aws.amazon.com/lambda/latest/api/API_PublishVersion.html](https://docs.aws.amazon.com/lambda/latest/api/API_PublishVersion.html).
- AWS Lambda. *Lambda function versions*. [https://docs.aws.amazon.com/lambda/latest/dg/configuration-versions.html](https://docs.aws.amazon.com/lambda/latest/dg/configuration-versions.html).
- AWS Lambda. *View Lambda function metrics and logs*. [https://docs.aws.amazon.com/lambda/latest/dg/monitoring-cloudwatchlogs-view.html](https://docs.aws.amazon.com/lambda/latest/dg/monitoring-cloudwatchlogs-view.html).
- AWS Lambda. *Pricing*. [https://aws.amazon.com/lambda/pricing/](https://aws.amazon.com/lambda/pricing/).
- Amazon API Gateway. *Pricing*. [https://aws.amazon.com/api-gateway/pricing/](https://aws.amazon.com/api-gateway/pricing/).
- Amazon DynamoDB. *Pricing*. [https://aws.amazon.com/dynamodb/pricing/](https://aws.amazon.com/dynamodb/pricing/).

---

## 13. Ordem recomendada para executar o trabalho

1. Atualizar o plano, critérios de validade e snapshot de preços.
2. Verificar dataset e caminho de integração por alias no stack já implantado.
3. Implementar e testar os contratos dos módulos do harness.
4. Validar separadamente o gerador constante, rajada e mini-rajada esparsa.
5. Executar os 12 runs do piloto, sem concorrência.
6. Corrigir qualquer problema instrumental e repetir o piloto, mantendo os artefatos anteriores.
7. Congelar código, plano e harness aprovados no Git.
8. Gerar os dez blocos randomizados e executar sequencialmente os 600 runs planejados.
9. Agregar resultados, calcular intervalos de confiança e testar H1–H6.
10. Selecionar as configurações de menor custo que satisfaçam o limite primário de latência e reportar a análise de sensibilidade.

A decisão de avançar à campanha principal deve depender da evidência produzida pelo piloto e não apenas da existência dos scripts.
