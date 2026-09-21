# Requisitos Funcionais Testáveis — Document API Benchmark

## 1. Objetivo

Este documento especifica os requisitos funcionais da aplicação **Document API Benchmark**.

A aplicação deverá fornecer uma API CRUD mínima de documentos e o endpoint de análise responsável por executar três análises independentes sobre o mesmo documento:

1. Estatísticas lexicais;
2. Métricas de legibilidade;
3. Extração de palavras-chave.

Os requisitos abaixo são formulados de maneira verificável por testes funcionais.

---

# 2. Modelo de dados

Um documento deverá possuir os seguintes atributos:

| Campo      | Descrição                        |
| ---------- | -------------------------------- |
| `id`       | Identificador único do documento |
| `title`    | Título do documento              |
| `body`     | Conteúdo textual do documento    |
| `language` | Idioma do documento              |
| `tags`     | Lista de etiquetas do documento  |

Os campos `body`, `language` e `tags` constituem a entrada das três análises. O campo `title` é apenas armazenado e retornado.

---

# 3. Requisitos funcionais

## RF-01 — Criar documento

**Descrição:**
A API deverá permitir a criação de um documento.

**Pré-condições:**

* A API deverá estar disponível.
* Não deverá ser necessária autenticação.
* O armazenamento deverá estar disponível.

**Entrada:**

```http
POST /documents
```

Corpo da requisição:

```json
{
  "title": "<título>",
  "body": "<texto>",
  "language": "<idioma>",
  "tags": ["<tag1>", "<tag2>"]
}
```

**Processamento esperado:**

1. Receber os campos `title`, `body`, `language` e `tags`.
2. Criar um novo documento.
3. Atribuir um identificador único ao documento.
4. Persistir o documento.
5. Retornar o identificador e os dados do documento criado.

**Saída esperada:**

Resposta HTTP de sucesso contendo o documento criado e seu `id`.

**Erros possíveis:**

* Dados obrigatórios ausentes ou inválidos.
* Falha de persistência.

**Critério de aceitação:**

Dado um corpo de requisição válido, a API deverá criar um documento persistido e retornar um identificador que permita recuperá-lo posteriormente por `GET /documents/{id}`.

---

## RF-02 — Recuperar documento

**Descrição:**
A API deverá permitir a recuperação de um documento pelo identificador.

**Pré-condições:**

* O documento deverá existir.

**Entrada:**

```http
GET /documents/{id}
```

**Processamento esperado:**

1. Receber o identificador.
2. Consultar o armazenamento.
3. Localizar o documento correspondente.
4. Retornar seus dados.

**Saída esperada:**

```json
{
  "id": "<id>",
  "body": "<texto>",
  "language": "<idioma>",
  "tags": ["<tag1>", "<tag2>"]
}
```

**Erros possíveis:**

* Documento inexistente.
* Identificador inválido.
* Falha de acesso ao armazenamento.

**Critério de aceitação:**

Dado um `id` correspondente a um documento existente, a API deverá retornar exatamente os campos persistidos desse documento.

---

## RF-03 — Atualizar documento

**Descrição:**
A API deverá permitir a substituição dos dados de um documento existente.

**Pré-condições:**

* O documento deverá existir.

**Entrada:**

```http
PUT /documents/{id}
```

Corpo:

```json
{
  "title": "<novo título>",
  "body": "<novo texto>",
  "language": "<novo idioma>",
  "tags": ["<nova tag>"]
}
```

**Processamento esperado:**

1. Localizar o documento pelo `id`.
2. Substituir `title`, `body`, `language` e `tags`.
3. Persistir os novos valores.
4. Retornar o documento atualizado.

**Saída esperada:**

O documento atualizado.

**Erros possíveis:**

* Documento inexistente.
* Identificador inválido.
* Dados inválidos.
* Falha de persistência.

**Critério de aceitação:**

Após uma atualização válida, uma chamada subsequente a `GET /documents/{id}` deverá retornar os novos valores de `title`, `body`, `language` e `tags`.

---

## RF-04 — Excluir documento

**Descrição:**
A API deverá permitir a exclusão de um documento existente.

**Pré-condições:**

* O documento deverá existir.

**Entrada:**

```http
DELETE /documents/{id}
```

**Processamento esperado:**

1. Localizar o documento pelo `id`.
2. Remover o documento do armazenamento.
3. Confirmar a operação.

**Saída esperada:**

Resposta HTTP indicando que a exclusão foi realizada.

**Erros possíveis:**

* Documento inexistente.
* Identificador inválido.
* Falha de persistência.

**Critério de aceitação:**

Após uma exclusão bem-sucedida, uma chamada a `GET /documents/{id}` não deverá mais localizar o documento.

---

# 4. Análise de documentos

## RF-05 — Executar análise de documento

**Descrição:**
A API deverá permitir a execução das três análises sobre um documento existente.

**Pré-condições:**

* O documento deverá existir.
* O documento deverá possuir `title`, `body`, `language` e `tags`.

**Entrada:**

```http
GET /documents/{id}/analysis
```

**Processamento esperado:**

1. Recuperar o documento.
2. Obter:

   * `document.body`;
   * `document.language`;
   * `document.tags`.
3. Executar a análise lexical.
4. Executar a análise de legibilidade.
5. Executar a extração de palavras-chave.
6. Combinar os três resultados.
7. Retornar uma única resposta contendo os resultados.

As três análises deverão ser independentes entre si.

Nenhuma análise deverá utilizar como entrada o resultado de outra análise.

**Saída esperada:**

```json
{
  "lexical": {},
  "readability": {},
  "keywords": {}
}
```

O conteúdo de cada objeto deverá obedecer aos requisitos RF-06, RF-07 e RF-08.

**Erros possíveis:**

* Documento inexistente.
* Identificador inválido.
* Falha durante a recuperação do documento.
* Falha durante uma das análises.

**Critério de aceitação:**

Para um documento existente, `GET /documents/{id}/analysis` deverá retornar os resultados das três análises em uma única resposta, utilizando exclusivamente `body`, `language` e `tags` do documento como entrada das análises.

---

# 5. Estatísticas lexicais

## RF-06 — Calcular estatísticas lexicais

**Descrição:**
A aplicação deverá calcular estatísticas lexicais do documento.

**Pré-condições:**

* O documento deverá existir.
* `body` deverá estar disponível.
* A implementação deverá utilizar somente a biblioteca padrão do Python.

**Entrada:**

```text
document.body
document.language
document.tags
```

A análise deverá receber os três campos, embora as estatísticas lexicais sejam determinadas pelo conteúdo textual.

**Processamento esperado:**

Calcular, no mínimo:

* `wordCount`;
* `uniqueWordCount`;
* `characterCount`;
* `averageWordLength`;
* `frequencyDistribution`.

A implementação deverá ser determinística.

**Saída esperada:**

Objeto contendo os resultados das estatísticas lexicais:

```json
{
  "wordCount": 0,
  "uniqueWordCount": 0,
  "characterCount": 0,
  "averageWordLength": 0,
  "frequencyDistribution": {}
}
```

**Erros possíveis:**

* Entrada textual inválida.
* Erro interno durante o processamento.

**Critério de aceitação:**

Para uma mesma combinação de `body`, `language` e `tags`, a análise deverá produzir os mesmos valores em execuções repetidas.

Os valores de `wordCount`, `uniqueWordCount`, `characterCount`, `averageWordLength` e `frequencyDistribution` deverão corresponder às regras de processamento definidas para a implementação.

---

# 6. Métricas de legibilidade

## RF-07 — Calcular métricas de legibilidade

**Descrição:**
A aplicação deverá calcular métricas de legibilidade do documento.

**Pré-condições:**

* O documento deverá existir.
* `body` deverá estar disponível.
* A fórmula de legibilidade deverá estar previamente definida e permanecer fixa durante o experimento.

**Entrada:**

```text
document.body
document.language
document.tags
```

**Processamento esperado:**

Calcular, no mínimo:

* `sentenceCount`;
* `averageSentenceLength`;
* `averageWordLength`;
* `readabilityIndex`.

A análise deverá ser independente da análise lexical.

**Saída esperada:**

```json
{
  "sentenceCount": 0,
  "averageSentenceLength": 0,
  "averageWordLength": 0,
  "readabilityIndex": 0
}
```

**Erros possíveis:**

* Entrada textual inválida.
* Idioma não suportado pela regra definida.
* Erro interno durante o processamento.

**Critério de aceitação:**

Para uma mesma combinação de `body`, `language` e `tags`, a análise deverá produzir resultados idênticos em execuções repetidas e não deverá depender do resultado produzido por RF-06 ou RF-08.

---

# 7. Extração de palavras-chave

## RF-08 — Extrair palavras-chave

**Descrição:**
A aplicação deverá extrair as palavras-chave mais relevantes segundo um processo determinístico.

**Pré-condições:**

* O documento deverá existir.
* `body`, `language` e `tags` deverão estar disponíveis.
* As regras de tokenização, normalização e stopwords deverão estar definidas.

**Entrada:**

```text
document.body
document.language
document.tags
```

**Processamento esperado:**

Executar obrigatoriamente as seguintes etapas:

```text
tokenização
      ↓
normalização
      ↓
remoção de stopwords
      ↓
contagem
      ↓
ordenação determinística
```

A contagem deverá produzir a frequência das palavras consideradas.

A ordenação deverá ser determinística.

**Saída esperada:**

```json
{
  "topKeywords": [],
  "keywordFrequencies": {}
}
```

**Erros possíveis:**

* Entrada textual inválida.
* Idioma não suportado pelas regras definidas.
* Erro interno durante o processamento.

**Critério de aceitação:**

Para a mesma combinação de `body`, `language` e `tags`, a extração deverá produzir exatamente o mesmo conjunto e ordenação de palavras-chave em execuções repetidas.

A análise não deverá consumir os resultados de RF-06 ou RF-07.

---

# 8. Determinismo

## RF-09 — Garantir determinismo das análises

**Descrição:**
As três análises deverão produzir resultados determinísticos.

**Pré-condições:**

* O mesmo documento deverá ser utilizado nas execuções.
* As configurações da aplicação deverão permanecer inalteradas.

**Entrada:**

A mesma combinação:

```text
body
language
tags
```

**Processamento esperado:**

Executar novamente:

* estatísticas lexicais;
* métricas de legibilidade;
* extração de palavras-chave.

**Saída esperada:**

Resultados idênticos entre as execuções.

**Erros possíveis:**

* Resultado não determinístico.
* Alteração não autorizada das regras de processamento.

**Critério de aceitação:**

Duas ou mais execuções com entradas idênticas deverão produzir respostas funcionalmente idênticas nas três análises.

---

# 9. Independência das análises

## RF-10 — Manter independência funcional das análises

**Descrição:**
As três análises deverão utilizar o documento como entrada comum, sem dependência entre seus resultados.

**Pré-condições:**

* O documento deverá existir.

**Entrada:**

```text
document.body
document.language
document.tags
```

**Processamento esperado:**

Cada análise deverá poder ser executada conceitualmente como uma operação independente:

```text
Lexical(body, language, tags)

Readability(body, language, tags)

Keywords(body, language, tags)
```

Nenhuma operação deverá receber o resultado produzido por outra.

**Saída esperada:**

Três resultados independentes:

```text
lexicalResult
readabilityResult
keywordResult
```

**Erros possíveis:**

* Dependência entre resultados das análises.
* Entrada diferente entre as análises.
* Falha interna em uma análise.

**Critério de aceitação:**

A implementação deverá permitir executar cada análise sem utilizar o resultado de qualquer outra análise como entrada.

---

# 10. Equivalência do workload

## RF-11 — Preservar o workload entre arquiteturas

**Descrição:**
A arquitetura monolítica e a arquitetura decomposta deverão executar o mesmo workload lógico.

**Pré-condições:**

* O mesmo documento deverá estar disponível.
* As mesmas regras de análise deverão estar implementadas.

**Entrada:**

```text
body
language
tags
```

**Processamento esperado:**

Na configuração monolítica:

```text
1 função
 ├── análise lexical
 ├── análise de legibilidade
 └── análise de keywords
```

Na configuração decomposta:

```text
1 função especializada → análise lexical
1 função especializada → análise de legibilidade
1 função especializada → análise de keywords
```

**Saída esperada:**

As duas configurações deverão produzir resultados funcionalmente equivalentes para a mesma entrada.

**Erros possíveis:**

* Diferença nos algoritmos.
* Diferença nos dados de entrada.
* Resultado divergente entre arquiteturas.

**Critério de aceitação:**

Dado o mesmo documento, a arquitetura monolítica e a arquitetura decomposta deverão produzir resultados equivalentes para as três análises.

A diferença entre as arquiteturas deverá estar na granularidade de execução, e não no trabalho lógico realizado.

---

# 11. Consistência das entradas das análises

## RF-12 — Utilizar entrada comum nas três análises

**Descrição:**
As três análises deverão receber exatamente os mesmos dados do documento.

**Pré-condições:**

* O documento deverá existir.

**Entrada:**

```text
document.body
document.language
document.tags
```

**Processamento esperado:**

Encaminhar os mesmos valores de:

```text
body
language
tags
```

para:

* análise lexical;
* análise de legibilidade;
* extração de palavras-chave.

**Saída esperada:**

Cada análise deverá produzir seu resultado a partir da mesma entrada lógica.

**Erros possíveis:**

* Campo omitido.
* Valor diferente entre as análises.
* Transformação não especificada da entrada antes de uma das análises.

**Critério de aceitação:**

Para uma requisição de análise, os três componentes deverão receber os mesmos valores de `body`, `language` e `tags`.

---

# 12. Escopo dos requisitos

Os requisitos funcionais da aplicação ficam restritos aos seguintes fluxos:

```text
POST   /documents
   │
   ▼
criação do documento

GET    /documents/{id}
   │
   ▼
recuperação do documento

PUT    /documents/{id}
   │
   ▼
atualização do documento

DELETE /documents/{id}
   │
   ▼
remoção do documento

GET    /documents/{id}/analysis
   │
   ├──► Estatísticas lexicais
   │
   ├──► Métricas de legibilidade
   │
   └──► Extração de palavras-chave
```

Não fazem parte dos requisitos funcionais:

* autenticação;
* autorização;
* cache;
* filas;
* busca textual;
* paginação;
* múltiplos bancos;
* processamento externo de NLP;
* versionamento de documentos;
* colaboração;
* comentários;
* notificações;
* anexos;
* funcionalidades administrativas;
* qualquer outra funcionalidade não especificada neste documento.

## 13. Condição geral de aceitação

A aplicação será considerada funcionalmente conforme quando:

1. Os cinco endpoints especificados estiverem implementados;
2. O CRUD permitir criar, recuperar, atualizar e excluir documentos;
3. `/documents/{id}/analysis` executar as três análises;
4. As três análises utilizarem `body`, `language` e `tags` como entrada;
5. As análises forem independentes;
6. Os resultados forem determinísticos;
7. As arquiteturas monolítica e decomposta produzirem resultados funcionalmente equivalentes;
8. Nenhuma funcionalidade fora do escopo for necessária para executar o workload.
