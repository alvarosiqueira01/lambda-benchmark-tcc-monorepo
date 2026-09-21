# Regras de Negócio — Entidade `Document`

## 1. Objetivo

Este documento define as regras de negócio mínimas da entidade `Document` da aplicação **Document API Benchmark**.

As regras têm como objetivos:

* definir o estado válido de um documento;
* garantir comportamento determinístico;
* permitir implementação simples;
* permitir testes automatizados;
* não introduzir processamento desnecessário no workload do benchmark.

As regras não devem depender de:

* autenticação;
* autorização;
* serviços externos;
* bibliotecas externas de NLP;
* processamento probabilístico;
* regras não especificadas neste documento.

---

# 2. Estrutura da entidade

A entidade `Document` possui exatamente os seguintes atributos de domínio:

```text
Document
├── id
├── title
├── body
├── language
└── tags
```

---

## RN-01 — Identificador obrigatório

**Regra:**
Todo `Document` persistido deverá possuir um identificador único.

**Comportamento:**

* `id` não pode ser nulo;
* `id` não pode ser vazio;
* dois documentos persistidos não podem possuir o mesmo `id`.

**Teste:**

```text
Dado dois documentos distintos
Quando ambos são persistidos
Então seus identificadores devem ser diferentes.
```

---

## RN-02 — Identificador imutável

**Regra:**
O `id` de um documento não poderá ser alterado durante uma atualização.

**Comportamento:**

Uma operação de atualização deverá preservar o identificador original.

**Teste:**

```text
Dado um documento com id = X
Quando o documento for atualizado
Então seu id continuará sendo X.
```

---

# 3. Regra para `title`

## RN-03 — `title` obrigatório

**Regra:**
Todo documento deverá possuir `title`.

**Comportamento:**

O valor não poderá ser:

* `null`;
* ausente;
* uma string vazia.

**Teste:**

```text
Dado um documento sem title
Quando tentar criar o documento
Então a operação deverá ser rejeitada.
```

---

## RN-04 — `title` deve ser texto

**Regra:**
`title` deverá ser uma string.

**Comportamento:**

Valores de outros tipos deverão ser rejeitados.

Exemplos inválidos:

```text
title = 123
title = true
title = []
title = {}
```

**Teste:**

```text
Dado title = 123
Quando tentar criar o documento
Então a operação deverá ser rejeitada.
```

---

## RN-05 — Espaços não tornam `title` vazio

**Regra:**
Uma string composta exclusivamente por espaços em branco deverá ser considerada vazia.

Exemplo:

```text
"     "
```

deverá ser rejeitado.

**Teste:**

```text
Dado title = "   "
Quando tentar criar o documento
Então a operação deverá ser rejeitada.
```

Uma string contendo pelo menos um caractere não branco deverá ser considerada não vazia.

---

## RN-06 — Conteúdo de `title` não deve ser alterado

**Regra:**
A entidade deverá preservar o conteúdo fornecido em `title`.

A aplicação não deverá:

* converter automaticamente para lowercase;
* remover espaços internos;
* remover acentos;
* truncar o conteúdo.

**Teste:**

```text
Dado title = "Meu Documento"
Quando o documento for persistido
Então title deverá continuar sendo "Meu Documento".
```

---

# 4. Regra para `body`

## RN-07 — `body` obrigatório

**Regra:**
Todo documento deverá possuir `body`.

**Comportamento:**

O valor não poderá ser:

* `null`;
* ausente;
* uma string vazia.

**Teste:**

```text
Dado um documento sem body
Quando tentar criar o documento
Então a operação deverá ser rejeitada.
```

---

## RN-08 — `body` deve ser texto

**Regra:**
`body` deverá ser uma string.

**Comportamento:**

Valores de outros tipos deverão ser rejeitados.

Exemplos inválidos:

```text
body = 123
body = true
body = []
body = {}
```

**Teste:**

```text
Dado body = 123
Quando tentar criar o documento
Então a operação deverá ser rejeitada.
```

---

## RN-09 — Espaços não tornam `body` vazio

**Regra:**
Uma string composta exclusivamente por espaços em branco deverá ser considerada vazia.

Exemplo:

```text
"     "
```

deverá ser rejeitado.

**Teste:**

```text
Dado body = "   "
Quando tentar criar o documento
Então a operação deverá ser rejeitada.
```

Uma string contendo pelo menos um caractere não branco deverá ser considerada não vazia.

---

## RN-10 — Conteúdo de `body` não deve ser alterado

**Regra:**
A entidade deverá preservar o conteúdo fornecido em `body`.

A aplicação não deverá:

* converter automaticamente para lowercase;
* remover pontuação;
* remover espaços internos;
* remover acentos;
* normalizar palavras;
* truncar o conteúdo.

**Teste:**

```text
Dado body = "Olá, Mundo!"
Quando o documento for persistido
Então body deverá continuar sendo "Olá, Mundo!".
```

A normalização necessária para a análise de palavras-chave deverá ocorrer **dentro da análise**, e não alterar o documento armazenado.

---

# 5. Regra para `language`

## RN-11 — `language` obrigatório

**Regra:**
Todo documento deverá possuir um idioma.

**Comportamento:**

`language` não poderá ser:

* ausente;
* `null`;
* string vazia.

**Teste:**

```text
Dado um documento sem language
Quando tentar criá-lo
Então a operação deverá ser rejeitada.
```

---

## RN-12 — `language` deve ser texto

**Regra:**
`language` deverá ser uma string.

Exemplos inválidos:

```text
language = 1
language = true
language = []
```

**Teste:**

```text
Dado language = 1
Quando tentar criar o documento
Então a operação deverá ser rejeitada.
```

---

## RN-13 — `language` deve possuir valor determinístico

**Regra:**
O domínio deverá utilizar uma representação previamente definida para o idioma.

Para o benchmark, recomenda-se utilizar códigos simples e padronizados, por exemplo:

```text
"pt"
"en"
```

A lista de idiomas efetivamente suportados deverá ser definida pela implementação antes da execução dos experimentos e permanecer constante durante eles.

**Teste:**

```text
Dado language = "pt"
Quando o documento for processado
Então as regras linguísticas associadas a "pt" deverão ser utilizadas.
```

Um código que não pertença à lista configurada deverá ser rejeitado.

---

# 6. Regras para `tags`

## RN-14 — `tags` obrigatório

**Regra:**
Todo documento deverá possuir o campo `tags`.

O campo poderá representar uma lista vazia.

Portanto:

```json
"tags": []
```

é válido.

Já:

```text
tags ausente
tags = null
```

é inválido.

**Teste:**

```text
Dado tags = []
Quando criar o documento
Então a criação deverá ser aceita.
```

```text
Dado tags ausente
Quando criar o documento
Então a criação deverá ser rejeitada.
```

---

## RN-15 — `tags` deve ser uma lista

**Regra:**
`tags` deverá ser representado por uma coleção/lista de strings.

Exemplo válido:

```json
["computação", "backend", "aws"]
```

Exemplos inválidos:

```json
"aws"
```

```json
[1, 2, 3]
```

```json
{"tag": "aws"}
```

**Teste:**

```text
Dado tags = ["aws", "lambda"]
Quando criar o documento
Então a criação deverá ser aceita.
```

---

## RN-16 — Cada tag deve ser texto

**Regra:**
Cada elemento de `tags` deverá ser uma string.

**Teste:**

```text
Dado tags = ["aws", 123]
Quando criar o documento
Então a operação deverá ser rejeitada.
```

---

## RN-17 — Tags vazias

**Regra:**
Uma tag individual não poderá ser uma string vazia ou composta exclusivamente por espaços.

Exemplo inválido:

```json
["aws", ""]
```

Exemplo inválido:

```json
["aws", "   "]
```

**Teste:**

```text
Dado tags = ["aws", ""]
Quando criar o documento
Então a operação deverá ser rejeitada.
```

---

## RN-18 — Conteúdo das tags

**Regra:**
As tags deverão ser armazenadas conforme fornecidas pelo cliente.

A entidade não deverá:

* converter tags para lowercase;
* remover acentos;
* remover caracteres;
* realizar stemming;
* aplicar normalização linguística.

**Teste:**

```text
Dado tags = ["AWS", "Computação"]
Quando o documento for persistido
Então as tags deverão permanecer ["AWS", "Computação"].
```

---

## RN-19 — Duplicidade de tags

**Regra:**
Tags duplicadas não deverão ser automaticamente removidas ou modificadas pela entidade.

Exemplo:

```json
["aws", "aws"]
```

deverá permanecer conforme fornecido.

Essa decisão evita introduzir uma transformação de domínio que não é necessária ao benchmark.

**Teste:**

```text
Dado tags = ["aws", "aws"]
Quando o documento for persistido
Então as duas ocorrências deverão permanecer.
```

---

# 7. Criação

## RN-20 — Documento válido para criação

Um documento será considerado válido para criação quando:

```text
id       → gerado pelo sistema
title    → string não vazia
body     → string não vazia
language → string válida e suportada
tags     → lista de strings válidas, podendo estar vazia
```

O cliente não deverá precisar fornecer `id`.

**Teste:**

```text
Dado:
title = "Meu Documento"
body = "Texto de teste"
language = "pt"
tags = ["teste"]

Quando criar o documento

Então:
- a operação deverá ser aceita;
- um id deverá ser atribuído;
- os quatro campos fornecidos deverão ser preservados.
```

---

# 8. Atualização

## RN-21 — Atualização preserva o `id`

Uma atualização poderá alterar:

```text
title
body
language
tags
```

mas nunca deverá alterar:

```text
id
```

**Teste:**

```text
Dado:
id = X
body = "Texto A"

Quando atualizar para:
body = "Texto B"

Então:
id = X
body = "Texto B"
```

---

## RN-22 — Atualização deve respeitar as regras da entidade

Os novos valores fornecidos em uma atualização deverão obedecer às mesmas regras de validade aplicadas à criação.

Assim, uma atualização não poderá introduzir:

* `title` nulo;
* `title` vazio;
* `body` nulo;
* `body` vazio;
* `language` inválido;
* `tags` nulo;
* tags não textuais;
* tags vazias.

**Teste:**

```text
Dado um documento válido
Quando tentar atualizá-lo com body = ""
Então a atualização deverá ser rejeitada
E o documento anterior deverá permanecer válido.
```

---

# 9. Exclusão

## RN-23 — Exclusão remove o documento

Quando um documento existente for excluído, ele não deverá mais estar disponível para consulta ou análise.

**Teste:**

```text
Dado um documento existente com id = X

Quando:
DELETE /documents/X

Então:
GET /documents/X
não deverá retornar o documento.
```

---

## RN-24 — Documento excluído não pode ser analisado

Após a exclusão, uma tentativa de análise utilizando seu `id` deverá ser rejeitada porque o documento não existe.

**Teste:**

```text
Dado um documento X
Quando X for excluído
E posteriormente for executado:
GET /documents/X/analysis

Então a operação deverá indicar que o documento não existe.
```

---

# 10. Identidade e persistência

## RN-25 — Identidade única

Dois documentos distintos não poderão compartilhar o mesmo `id`.

**Teste:**

```text
Quando dois documentos forem criados
Então:
documentoA.id != documentoB.id
```

---

## RN-26 — Persistência dos dados

Após a criação, os valores válidos de:

```text
title
body
language
tags
```

deverão poder ser recuperados sem alteração.

**Teste:**

```text
Dado:
title = "Meu Documento"
body = "Texto original"
language = "pt"
tags = ["teste"]

Quando:
1. criar o documento;
2. recuperar o documento;

Então os valores recuperados deverão ser iguais aos valores fornecidos.
```

---

# 11. Relação com o processamento de análise

## RN-27 — A análise não modifica o documento

Executar:

```text
GET /documents/{id}/analysis
```

não deverá modificar:

```text
id
title
body
language
tags
```

do documento.

**Teste:**

```text
Dado um documento X

Quando:
1. recuperar X;
2. executar X/analysis;
3. recuperar X novamente;

Então os dados do documento deverão permanecer iguais.
```

---

## RN-28 — Entrada das análises

As três análises deverão receber como entrada lógica exatamente:

```text
document.body
document.language
document.tags
```

O campo `title` é um metadado da entidade e não participa das análises.

A entidade `Document` não deverá armazenar resultados de análise.

Não deverão ser adicionados campos como:

```text
wordCount
readabilityIndex
topKeywords
```

ao documento.

**Teste:**

```text
Dado um documento existente

Quando executar /analysis

Então:
- o documento original permanece inalterado;
- os resultados são retornados pela operação de análise;
- os resultados não são adicionados ao estado persistido do Document.
```

---

# 12. Regras de simplicidade do domínio

## RN-29 — Ausência de regras implícitas

A entidade `Document` não deverá executar transformações que não estejam explicitamente especificadas neste documento.

Em particular, a entidade não deverá:

* analisar o texto durante a criação;
* calcular palavras-chave durante a criação;
* calcular legibilidade durante a criação;
* normalizar o texto;
* alterar tags;
* acessar serviços externos;
* executar processamento assíncrono;
* armazenar resultados derivados.

**Teste:**

A criação e atualização de um documento deverão limitar seu processamento às operações necessárias para validar e persistir os dados da entidade.

---

# 13. Resumo das regras

| ID    | Regra                                            |
| ----- | ------------------------------------------------ |
| RN-01 | Documento persistido possui `id` único           |
| RN-02 | `id` é imutável                                  |
| RN-03 | `title` é obrigatório                            |
| RN-04 | `title` é string                                 |
| RN-05 | `title` não pode ser somente whitespace          |
| RN-06 | `title` é preservado sem transformação           |
| RN-07 | `body` é obrigatório                             |
| RN-08 | `body` é string                                  |
| RN-09 | `body` não pode ser somente whitespace           |
| RN-10 | `body` é preservado sem transformação            |
| RN-11 | `language` é obrigatório                         |
| RN-12 | `language` é string                              |
| RN-13 | `language` deve pertencer aos idiomas suportados |
| RN-14 | `tags` é obrigatório e pode ser vazio            |
| RN-15 | `tags` é uma lista                               |
| RN-16 | Cada tag é uma string                            |
| RN-17 | Tag não pode ser vazia/whitespace                |
| RN-18 | Tags são preservadas sem normalização            |
| RN-19 | Tags duplicadas são preservadas                  |
| RN-20 | Documento válido pode ser criado                 |
| RN-21 | Atualização preserva `id`                        |
| RN-22 | Atualização respeita as regras de validade       |
| RN-23 | Exclusão remove o documento                      |
| RN-24 | Documento excluído não pode ser analisado        |
| RN-25 | IDs são únicos                                   |
| RN-26 | Dados persistidos são recuperáveis sem alteração |
| RN-27 | Análise não modifica o documento                 |
| RN-28 | Análises recebem `body`, `language` e `tags`     |
| RN-29 | Domínio não possui processamento implícito       |

## 14. Regra fundamental do domínio

A entidade `Document` deve permanecer **passiva e mínima**:

```text
Document
   │
   ├── id
   ├── title
   ├── body
   ├── language
   └── tags
```

Sua responsabilidade é representar e garantir a validade dos dados do documento.

O processamento computacional:

```text
Document
   │
   └──► Analysis
          ├── lexical
          ├── readability
          └── keywords
```

pertence ao domínio de análise e não à entidade `Document`.

Essa separação é necessária para que a execução de `/analysis` represente um workload controlado e para que a comparação entre as configurações monolítica e decomposta não seja afetada por processamento adicional na entidade.
