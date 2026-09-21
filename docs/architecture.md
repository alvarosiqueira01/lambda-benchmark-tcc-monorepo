# Arquitetura — Document API Benchmark

## 1. Visão geral

```text
HTTP
 │
 ▼
Handler
 │
 ▼
Service ───────────────► Analysis
 │                         ├── lexical
 │                         ├── readability
 │                         └── keywords
 ▼
Repository
 │
 ▼
DynamoDB
```

A arquitetura é deliberadamente simples:

- `Handler`: adapta HTTP para chamadas de serviço.
- `Service`: concentra validação, orquestração e regras do domínio.
- `Repository`: isola o acesso ao DynamoDB.
- `Analysis`: executa as três análises independentes.
- `DynamoDB`: persistência mínima do documento.

O contrato HTTP permanece o mesmo nas arquiteturas monolítica e decomposta. A diferença fica apenas em como `Analysis` é executado.

---

## 2. Camadas e responsabilidades

| Camada | Responsabilidade | Não faz |
|---|---|---|
| `Handler` | Ler path/body, chamar `Service`, mapear exceções para status HTTP. | Regra de negócio, DynamoDB, análise. |
| `Service` | Validar dados, gerar `id`, orquestrar CRUD e `/analysis`. | HTTP, DynamoDB, algoritmo de análise. |
| `Repository` | Executar `GetItem`, `PutItem`, `UpdateItem`, `DeleteItem`. | Validar domínio, analisar texto. |
| `DynamoDB` | Armazenar o item do documento. | Lógica de aplicação. |
| `Analysis` | Executar as três análises de forma pura e determinística. | Persistir, alterar documento, depender de outra análise. |

---

## 3. Modelo `Document`

O documento possui exatamente:

```text
Document
├── id
├── body
├── language
└── tags
```

Não fazem parte do escopo:

```text
title
createdAt
updatedAt
wordCount
readabilityIndex
topKeywords
```

Resultados de análise não são persistidos no documento.

### Item no DynamoDB

Tabela única:

```text
Tabela: Documents
Partition key: id (S)
Sort key: nenhuma
GSI: nenhum
LSI: nenhum
```

Item:

```json
{
  "id":       { "S": "uuid" },
  "body":     { "S": "Texto original" },
  "language": { "S": "pt" },
  "tags":     { "L": [ { "S": "AWS" }, { "S": "aws" } ] }
}
```

`tags` deve ser `List` de strings, não `String Set`, porque duplicatas e ordem precisam ser preservadas.

---

## 4. Interfaces internas

### Handler

Recebe HTTP e chama o `Service`.

```text
POST   /documents
GET    /documents/{id}
PUT    /documents/{id}
DELETE /documents/{id}
GET    /documents/{id}/analysis
```

O handler não conhece DynamoDB nem algoritmos de análise.

### Service

Métodos principais:

```text
create_document(body, language, tags) -> Document
get_document(id) -> Document
update_document(id, body, language, tags) -> Document
delete_document(id) -> void
analyze_document(id) -> AnalysisResponse
```

Responsabilidades:

- validar `body`, `language` e `tags`;
- gerar `id` único na criação;
- preservar `id` na atualização;
- chamar `Repository` para persistência;
- chamar `Analysis` no endpoint de análise;
- agregar a resposta final.

### Repository

Métodos principais:

```text
put(document)    -> PutItem
get(id)          -> GetItem
update(document) -> UpdateItem
delete(id)       -> DeleteItem
```

Operações DynamoDB:

| Operação | Uso |
|---|---|
| `PutItem` | Criar documento, com `attribute_not_exists(id)`. |
| `GetItem` | Recuperar documento por `id`. |
| `UpdateItem` | Atualizar `body`, `language`, `tags`, com `attribute_exists(id)`. |
| `DeleteItem` | Excluir documento por `id`. |

Sem `Scan`, sem `Query`, sem transações, sem índices.

### Analysis

Interface conceitual independente:

```text
lexical_analysis(body, language, tags) -> resultado lexical
readability_analysis(body, language, tags) -> resultado de legibilidade
keyword_analysis(body, language, tags) -> resultado de keywords
```

Cada análise:

- recebe exatamente `body`, `language`, `tags`;
- não depende do resultado das outras;
- é determinística;
- usa apenas biblioteca padrão;
- não acessa serviços externos;
- não altera o documento.

Resposta composta:

```json
{
  "lexical": {},
  "readability": {},
  "keywords": {}
}
```

---

## 5. Fluxos principais

### POST `/documents`

```text
Handler
  → Service valida body, language, tags
  → Service gera id
  → Repository.put
  → DynamoDB PutItem
  → Handler retorna 201
```

### GET `/documents/{id}`

```text
Handler
  → Service.get_document
  → Repository.get
  → DynamoDB GetItem
  → Handler retorna 200 ou 404
```

### PUT `/documents/{id}`

```text
Handler
  → Service valida novos dados
  → Repository.update
  → DynamoDB UpdateItem preservando id
  → Handler retorna 200 ou 404
```

### DELETE `/documents/{id}`

```text
Handler
  → Service.delete_document
  → Repository.delete
  → DynamoDB DeleteItem
  → Handler retorna 204 ou 404
```

### GET `/documents/{id}/analysis`

```text
Handler
  → Service.analyze_document
  → Repository.get
  → DynamoDB GetItem
  → Service chama Analysis:
       lexical_analysis(body, language, tags)
       readability_analysis(body, language, tags)
       keyword_analysis(body, language, tags)
  → Service agrega
  → Handler retorna 200
```

O documento não é modificado. Os resultados não são persistidos.

---

## 6. Monolítica vs decomposta

### Monolítica

```text
Handler
  → Service
      → Repository → DynamoDB
      → Analysis local
          ├── lexical
          ├── readability
          └── keywords
```

As três análises rodam no mesmo processo/Lambda.

### Decomposta

```text
Handler
  → Service / Orquestrador
      → Repository → DynamoDB
      → Lexical Lambda
      → Readability Lambda
      → Keyword Lambda
      → Agrega resultados
```

Cada Lambda especializada recebe os mesmos dados:

```text
body
language
tags
```

O contrato HTTP, o modelo `Document`, o `Repository` e o workload lógico não mudam.

---

## 7. Estrutura sugerida

```text
src/
├── handlers/
│   └── documents.py
├── services/
│   ├── document_service.py
│   └── analysis_service.py
├── repositories/
│   └── document_repository.py
├── analyses/
│   ├── lexical.py
│   ├── readability.py
│   └── keywords.py
└── models/
    └── document.py
```

---

## 8. Restrições de simplicidade

- Sem autenticação.
- Sem autorização.
- Sem paginação.
- Sem filtros.
- Sem ordenação.
- Sem busca textual.
- Sem `PATCH`.
- Sem listagem `GET /documents`.
- Sem cache.
- Sem filas.
- Sem múltiplos bancos.
- Sem NLP externo.
- Sem armazenamento de resultados de análise.
- Sem campos extras no `Document`.
- Sem processamento implícito na criação ou atualização.

A entidade `Document` permanece passiva. O processamento pertence a `Analysis`.