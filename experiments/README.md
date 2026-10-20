## 1. Estrutura

```text
lambda-benchmark-experiment-harness/
├── README.md
├── requirements-experiment.txt
│
├── experiments/
│   ├── experiment-plan.yaml
│   ├── harness-config.yaml
│   ├── validity-criteria.md
│   ├── pricing/
│   │   └── pricing-template.json
│   ├── metadata/
│   │   └── pilot-validation-template.json
│   ├── runs/
│   └── aggregate/
│
├── k6/
│   └── benchmark.js
│
├── scripts/
│   ├── experiment/
│   │   ├── common.py
│   │   ├── planner.py
│   │   ├── controller.py
│   │   ├── deploy.py
│   │   ├── cold_reset.py
│   │   ├── load_test.py
│   │   ├── export_k6_requests.py
│   │   ├── collect_lambda.py
│   │   ├── collect_cloudwatch.py
│   │   ├── collect_cost.py
│   │   ├── validate_run.py
│   │   ├── validate_block.py
│   │   └── manifest.py
│   │
│   └── analysis/
│       ├── aggregate.py
│       ├── bootstrap.py
│       ├── hypotheses.py
│       └── plots.py
│
└── tests/
    └── test_planner.py
```

### Responsabilidade dos componentes

| Componente | Responsabilidade |
|---|---|
| `planner.py` | Gera as 60 condições experimentais e os dez blocos randomizados. |
| `deploy.py` | Valida a infraestrutura AWS e as integrações configuradas. Também oferece suporte à validação e ao deploy inicial via SAM. |
| `cold_reset.py` | Atualiza a configuração das funções, publica versões e atualiza os aliases `benchmark`. |
| `load_test.py` | Aciona o k6 remotamente por AWS Systems Manager e recupera os artefatos enviados ao S3. |
| `benchmark.js` | Define os cenários de geração de carga. |
| `export_k6_requests.py` | Transforma amostras brutas do k6 em registros individuais por requisição. |
| `collect_lambda.py` | Coleta registros `START` e `REPORT`, associando-os por `RequestId` e verificando as versões. |
| `collect_cloudwatch.py` | Obtém métricas complementares do CloudWatch. |
| `collect_cost.py` | Calcula custos estimados por invocação, janela de medição e execução completa. |
| `validate_run.py` | Classifica cada execução conforme os critérios de validade. |
| `controller.py` | Coordena sequencialmente as etapas de cada execução. |
| `manifest.py` | Registra metadados da campanha e hashes dos artefatos. |
| `aggregate.py` | Consolida resultados dos runs válidos. |
| `bootstrap.py` | Calcula estatísticas por condição e intervalos de confiança. |
| `hypotheses.py` | Produz análises exploratórias relacionadas às hipóteses H1–H6. |
| `plots.py` | Gera gráficos de custo e latência. |

## 2. Protocolo experimental incorporado

As configurações iniciais seguem o protocolo definido para o TCC.

| Parâmetro | Valor |
|---|---|
| Composição | Monolítica e decomposta |
| Arquitetura de execução | `x86_64` e `arm64` |
| Memória | 128, 256, 512, 1.024 e 2.048 MB |
| Carga constante | 20 req/s durante 240 segundos |
| Carga em rajada | 100 req/s durante 1 segundo, seguidos de 4 segundos sem novas chegadas |
| Carga esparsa | Cinco requisições nos primeiros 800 ms de cada ciclo de 10 segundos, seguidas de 9 segundos sem novas chegadas |
| Aquecimento | 120 segundos |
| Medição | 120 segundos |
| Repetições | 10 por condição |
| Total planejado | 600 runs |
| Limite primário de latência | p95 ≤ 500 ms |

O arquivo `experiment-plan.yaml` centraliza os parâmetros do protocolo, enquanto `harness-config.yaml` contém os parâmetros específicos da infraestrutura.

A geração de carga utiliza o modelo de chegada aberta do k6. O cenário esparso implementa a **mini-rajada da Opção B**, sem substituí-la por uma distribuição uniforme de 0,5 req/s.

## 3. Como começar a utilizar

### Etapa 1 — Extrair o pacote para o repositório

Extraia o ZIP na raiz do repositório:

[Repositório GitHub do projeto](https://github.com/alvarosiqueira01/lambda-benchmark-tcc-monorepo)

Copie os arquivos e diretórios do pacote para a estrutura correspondente do projeto, preservando os arquivos existentes da aplicação.

O pacote foi preparado separadamente para evitar sobrescrever inadvertidamente arquivos do benchmark já implementado.

### Etapa 2 — Instalar as dependências

Na raiz do repositório:

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements-experiment.txt
```

Também serão necessárias as ferramentas externas descritas no README, incluindo AWS CLI, AWS SAM CLI e k6.

### Etapa 3 — Conferir o dataset

O caminho configurado é:

```text
dataset/generated/v2/documents.json
```

O harness calcula o SHA-256 do arquivo e extrai o ID do primeiro documento, em vez de presumir um identificador fixo.

Verifique:

```bash
test -f dataset/generated/v2/documents.json
sha256sum dataset/generated/v2/documents.json
```

O documento escolhido deverá corresponder a um registro existente na tabela utilizada pela aplicação.

### Etapa 4 — Preencher a configuração da infraestrutura

Antes de executar qualquer operação AWS, edite:

```text
experiments/harness-config.yaml
```

Os campos que precisam ser preenchidos incluem:

- Nome real da stack CloudFormation.
- ID e stage da API Gateway.
- ID da integração utilizada pela rota de análise.
- URL efetiva da API.
- Nome da função monolítica.
- Nome do orquestrador e das funções especializadas.
- Nome da tabela DynamoDB.
- ID da instância EC2 geradora.
- Bucket e prefixo S3 para os resultados.
- Contrato real do corpo da requisição HTTP.

**Não substitua esses valores por nomes presumidos.** Eles precisam corresponder à infraestrutura já implantada e validada pelos smoke tests.

### Etapa 5 — Gerar o planejamento

Execute:

```bash
python scripts/experiment/planner.py \
  --plan experiments/experiment-plan.yaml \
  --out experiments/metadata/plan
```

O resultado esperado é:

- Uma matriz com 60 condições únicas.
- Dez arquivos de blocos randomizados.
- Um manifesto contendo a seed e os hashes dos arquivos gerados.

Para verificar os blocos:

```bash
python scripts/experiment/validate_block.py \
  --plan experiments/experiment-plan.yaml \
  --block-dir experiments/metadata/plan
```

Também incluí testes automatizados para a matriz experimental.

### Etapa 6 — Preparar o piloto

Antes da campanha principal, preencha o snapshot de preços e configure a máquina geradora.

O gerador precisa ter acesso ao script:

```text
k6/benchmark.js
```

no caminho especificado por `generator.remote_k6_script`.

A instância deve estar registrada no Systems Manager, possuir as permissões necessárias e conseguir enviar os resultados ao S3.

O primeiro teste deve ser um run controlado. Depois, execute o piloto instrumental e preencha:

```text
experiments/metadata/pilot-validation-template.json
```

Esse arquivo registra as verificações realizadas e impede que a aprovação do piloto seja confundida com uma simples execução bem-sucedida do script.

## 4. O que ainda precisa ser configurado e validado

O pacote fornece a estrutura dos scripts e suas implementações iniciais, mas **não significa que a campanha esteja pronta para executar 600 runs sem ajustes no ambiente real**.

Há três pontos especialmente importantes.

**1. Integração com a aplicação existente.** O contrato HTTP, os nomes das funções e as integrações precisam ser confirmados no repositório e na AWS. A configuração inicial contém campos `REPLACE_WITH_...` justamente para evitar inventar esses valores.

**2. Validação dos artefatos reais.** O piloto precisa demonstrar que o formato dos registros Lambda, a saída JSON do k6, a associação das invocações às versões e a atribuição às janelas de medição funcionam na configuração efetivamente utilizada.

**3. Precisão do custo da aplicação.** O cálculo do custo Lambda utiliza `Billed Duration` e o número de invocações recuperadas. O custo do DynamoDB permanece uma estimativa parametrizada até que o consumo de unidades de leitura e escrita seja confirmado para as operações reais.

Os scripts foram preparados para integração e verificação, mas não executei uma campanha na sua conta AWS nem validei os nomes dos recursos reais do projeto.

---

## 5. Próximo passo recomendado

Antes de executar os 600 runs, sugiro seguir esta sequência:

1. Incorporar os arquivos ao repositório.
2. Preencher `harness-config.yaml`.
3. Gerar e validar os blocos experimentais.
4. Validar a infraestrutura sem gerar tráfego.
5. Confirmar que o gerador remoto e o armazenamento de resultados funcionam.
6. Executar um run controlado e verificar todos os artefatos.
7. Executar os 12 testes-piloto e aprová-los formalmente.
8. Iniciar a campanha principal somente após a aprovação.

A implementação deve ser tratada como um instrumento experimental: antes de utilizá-la para produzir resultados científicos, é necessário demonstrar que ela reproduz as condições planejadas e mede corretamente os fenômenos investigados.
/////////////////////////////////////////////////////////
# Harness experimental — AWS Lambda benchmark (TCC)

Este pacote implementa a estrutura do harness descrita no tutorial revisado. Ele **não altera o código da aplicação** e não presume nomes de funções, rotas ou outputs que não foram confirmados no repositório. Antes de usar AWS, complete `experiments/harness-config.yaml` com os nomes reais dos recursos e a rota de análise já validada pelos smoke tests.

## Dataset

O caminho padrão foi configurado conforme informado: `dataset/generated/v2/documents.json` (fora de `src/`). O harness calcula hash e extrai o primeiro ID do JSON; não presume `doc-001`.

## Instalação

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-experiment.txt
cp experiments/pricing/pricing-template.json experiments/pricing/pricing-YYYY-MM-DD.json
# edite experiments/harness-config.yaml e o snapshot de preços
```

Dependências locais: Python 3.12+, AWS CLI v2, SAM CLI, k6 instalado na EC2 geradora, Git e `sha256sum`. O pacote Python `boto3` opera AWS; `PyYAML`, `pandas`, `numpy`, `scipy` e `matplotlib` são usados no plano/análise.

## Primeiro: inspecionar e congelar

```bash
python scripts/experiment/planner.py --plan experiments/experiment-plan.yaml --out experiments/metadata/plan
python scripts/experiment/deploy.py --config experiments/harness-config.yaml --validate-only
python scripts/experiment/manifest.py --config experiments/harness-config.yaml --out experiments/metadata/campaign-manifest.json
```

O `planner.py` gera 60 condições e 10 blocos randomizados de 60 condições cada. O `deploy.py --validate-only` valida os nomes/ARNs configurados e a integração, sem publicar versões nem enviar tráfego.

## Piloto e campanha

Um run é executado com:

```bash
python scripts/experiment/controller.py \
  --plan experiments/experiment-plan.yaml \
  --config experiments/harness-config.yaml \
  --condition-file experiments/metadata/plan/conditions_block_01.csv \
  --limit 1
```

`--limit 1` serve apenas para um teste de integração controlado. O piloto completo deve ser planejado explicitamente; não inicie 600 execuções até que a geração de carga, a atribuição dos logs e a coleta tenham sido verificadas. A execução de AWS real requer permissões IAM e recursos configurados corretamente.

O gerador é controlado por AWS Systems Manager Run Command. Configure `generator.instance_id`, o caminho do k6 script no host remoto e o diretório de saída. O host precisa ter k6 e AWS CLI (ou mecanismo alternativo autorizado) instalados e acesso ao bucket/prefixo de resultados.

## Limitações explícitas / pontos de integração

- `harness-config.yaml` deve ser preenchido com os nomes reais de funções, aliases, API, stage, tabela, IDs de métricas e instância geradora.
- `cold_reset.py` altera memória/arquitetura, publica versões e move aliases. Isso altera recursos AWS e tem custos; rode primeiro em ambiente de teste.
- O código verifica roteamento API Gateway HTTP API para ARN qualificado. Se o projeto usar REST API, adapte a validação da integração antes do piloto.
- O script de k6 grava saída JSON bruta e um `requests.csv` individual; cada iteração é uma requisição HTTP. Não há smoke test após cold reset.
- Para decomposição, `worker_aliases` precisa conter todas as funções especializadas invocadas pelo orquestrador. O harness não inventa os nomes reais.
- A coleta de DynamoDB e API Gateway é estimada a partir do plano e de métricas/configuração. Confirme as operações reais e dimensões antes de usar `Creq_app` como resultado definitivo.
- Não é possível provar ausência absoluta de ambiente aquecido apenas por publicar versões. O protocolo operacional cria versões novas, atualiza aliases e evita invocações antes de T0; reporte essa limitação.

## Ordem de execução recomendada

1. Preencher config e confirmar `dataset/generated/v2/documents.json`.
2. Executar testes unitários: `python -m unittest discover -s tests`.
3. Validar SAM/template e aliases com `deploy.py --validate-only`.
4. Executar um run controlado e verificar todos os artefatos.
5. Executar piloto de 12 condições e aprová-lo manualmente.
6. Congelar commit, plano, hashes e preços; executar dez blocos sequencialmente.
7. Agregar somente runs válidos e manter todos os inválidos no dataset/auditoria.

**Nunca** coloque chaves AWS no YAML. Use SSO/roles. Nunca rode runs em paralelo.
