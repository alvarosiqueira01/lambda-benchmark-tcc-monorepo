# Document API Benchmark

Aplicação benchmark de API de documentos para AWS Lambda. O foco experimental é
o endpoint `GET /documents/{id}/analysis`, que executa três análises
independentes e determinísticas: lexical, legibilidade e extração de keywords.

- Contrato HTTP: [openapi/openapi.yaml](openapi/openapi.yaml)
- Escopo, regras de domínio, dados e arquitetura: [docs/](docs/)

## Estrutura

```text
src/
├── app/                      # CRUD, validação, repositório e service
├── common/analysis.py        # As três análises (puras, determinísticas)
├── monolith/handler.py       # Handler da configuração monolítica
└── decomposed/               # Configuração decomposta
    ├── crud_handler.py       # Lambda CRUD
    ├── orchestrator.py       # Lambda orquestradora de /analysis
    ├── invoker.py            # Cliente de invocação das Lambdas de análise
    └── analyses/             # Lambdas especializadas (lexical/readability/keywords)
```

A decomposição muda onde o processamento acontece, não o que é processado.
As três análises continuam sendo `lexical_analysis`, `readability_analysis` e
`keyword_analysis` de `common/analysis.py`, chamadas com os mesmos `body`,
`language` e `tags`.

## Entry points de implantação

| Configuração | Lambda | Handler |
|---|---|---|
| Monolítica | `document-api` | `monolith.handler.handler` |
| Decomposta | `document-api-crud` | `decomposed.crud_handler.handler` |
| Decomposta | `document-api-orchestrator` | `decomposed.orchestrator.handler` |
| Decomposta | `document-api-lexical` | `decomposed.analyses.lexical.handler` |
| Decomposta | `document-api-readability` | `decomposed.analyses.readability.handler` |
| Decomposta | `document-api-keywords` | `decomposed.analyses.keywords.handler` |

## Variáveis de ambiente

| Lambda | Variáveis |
|---|---|
| Monolítica | `DOCUMENTS_TABLE` |
| CRUD (decomposta) | `DOCUMENTS_TABLE` |
| Orquestradora | `DOCUMENTS_TABLE`, `LEXICAL_FUNCTION_NAME`, `READABILITY_FUNCTION_NAME`, `KEYWORDS_FUNCTION_NAME` |
| Lexical | — |
| Readability | — |
| Keywords | — |

As Lambdas de análise não precisam de `DOCUMENTS_TABLE` nem de `boto3`,
podendo ser empacotadas sem dependências (RNF-04).

Observação: nos handlers `monolith` e `decomposed.orchestrator`, `DocumentService`
e `AnalysisInvoker` são criados no import do módulo para reaproveitar o
contêiner Lambda (evitar custo de inicialização por invocação). Como efeito,
erros de configuração (ex.: variável de ambiente ausente) só aparecem no cold
start, não no momento do deploy.

## Testes

```text
python -m pytest tests/
```

Os testes usam clientes fake para DynamoDB e Lambda; não tocam em AWS.

## Equivalência entre versões monolítica e decomposta

Duas configurações são ditas equivalentes se, para todo documento do dataset, a resposta HTTP de GET /documents/{id}/analysis é byte-a-byte idêntica (status + body), exceto pelo ANALYSIS_ERROR documentado no contrato.

## Dados experimentais

Os runs brutos ficam fora do Git. O repositório versiona os artefatos derivados
por run (sem `k6-raw.json`), os agregados e os metadados congelados; o pacote
completo, com o k6 bruto, é publicado como asset de um GitHub Release.

- `experiments/archive/tcc-2026-11/` — um arquivo por bloco (e um do piloto),
  `SHA256SUMS`, `archive-manifest.json`, `per-run-index.csv` e `README.md`.
- `experiments/aggregate/` — `runs.csv`, `function_runs.csv`, intervalos de
  confiança, testes de hipóteses, seleção e figuras.
- `experiments/metadata/` — manifestos congelados e plano dos blocos.
- `experiments/runs/` e `experiments/archive/**/full/` — ignorados pelo Git; o
  segundo é o insumo do release.

O procedimento completo de geração, validação, commit e publicação está em
[experiments/README.md](experiments/README.md#9-arquivamento-e-publicação).
