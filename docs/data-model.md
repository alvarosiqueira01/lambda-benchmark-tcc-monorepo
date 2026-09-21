## 1. Modelo mínimo no DynamoDB

Tabela única:

```text
Tabela: Documents
Partition key: id (S)
Sort key: nenhuma
GSI: nenhum
LSI: nenhum
```

Item armazenado:

```json
{
  "id": "uuid",
  "body": "Olá, Mundo!",
  "language": "pt",
  "tags": ["AWS", "aws"]
}
```

Em tipos DynamoDB:

```json
{
  "id":       { "S": "uuid" },
  "title":    { "S": "Meu Documento" },
  "body":     { "S": "Olá, Mundo!" },
  "language": { "S": "pt" },
  "tags":     { "L": [ { "S": "AWS" }, { "S": "aws" } ] }
}
```

### Por que `tags` como `L` e não `SS`?

Porque `tags` deve preservar:

- ordem de fornecimento;
- duplicatas, conforme RN-19.

`String Set` (`SS`) não permite duplicatas e não garante ordem. Portanto, use `List` (`L`) de strings (`S`).

## 2. Operações permitidas

### Criar — `PutItem`

```text
PutItem
Item: { id, title, body, language, tags }
ConditionExpression: attribute_not_exists(id)
```

A condição garante que um `id` já existente não seja sobrescrito, atendendo à RN-01/RN-25.

### Buscar — `GetItem`

```text
GetItem
Key: { id }
```

Única forma de leitura. Sem `Scan`, sem `Query`.

### Atualizar — `UpdateItem`

```text
UpdateItem
Key: { id }
UpdateExpression: SET title = :t, body = :b, language = :l, tags = :g
ConditionExpression: attribute_exists(id)
```

O `id` nunca é alterado, pois é a partition key. A validação de `title`, `body`, `language` e `tags` deve ocorrer na aplicação antes do `UpdateItem`, conforme RN-22.

### Excluir — `DeleteItem`

```text
DeleteItem
Key: { id }
```

Opcionalmente com `ConditionExpression: attribute_exists(id)` se for necessário diferenciar “excluído” de “não existia”. Depois disso, `GetItem` não retorna mais o documento.

Não há transações: todas as operações são atômicas em um único item.

## 3. Campos que devem ser armazenados

| Campo API         | DynamoDB | Observação |
|-------------------|----------|------------|
| `documentId`      | `id` (S) | Partition key. Mapear na borda da API. |
| `title`           | `title` (S) | Preservar exatamente como fornecido. |
| `body`            | `body` (S) | Preservar exatamente como fornecido. |
| `language`        | `language` (S) | Validar contra lista suportada. |
| `tags`            | `tags` (L) | Lista de strings. Preservar ordem e duplicatas. |

Se `createdAt` ou `updatedAt` forem realmente exigidos por contrato externo, eles seriam atributos extras, sem índice e sem participação nas regras de domínio. Ainda assim, isso contraria a RN-29 e adiciona processamento desnecessário ao benchmark.

## 4. Como evitar estruturas desnecessárias

- Não criar tabela separada para tags.
- Não usar `SS` para tags, porque duplicatas seriam perdidas.
- Não armazenar resultados de análise (`wordCount`, `readabilityIndex`, `topKeywords`) no documento — RN-28.
- Não normalizar `title`, `body` nem `tags` na persistência — RN-06, RN-10 e RN-18.
- Não criar GSI/LSI para `language`, `tags`, `title` ou timestamps. O objetivo proíbe e não há padrão de acesso alternativo.
- Não usar `Scan` para listar documentos. Se listagem for necessária, o objetivo atual precisa ser revisto.
- Não usar transações. `PutItem`, `GetItem`, `UpdateItem` e `DeleteItem` resolvem o domínio.
- Manter o item plano: `id`, `title`, `body`, `language`, `tags`. Sem mapas aninhados, sem atributos derivados.

## 5. Resumo final

```text
Tabela única: Documents
PK: id (S)
Sem SK, sem GSI, sem LSI

Item:
{
  id: S,
  title: S,
  body: S,
  language: S,
  tags: L<S>
}

Operações:
- PutItem com attribute_not_exists(id)
- GetItem por id
- UpdateItem por id, preservando id
- DeleteItem por id

Validação: na aplicação, antes de persistir.
Análise: fora da entidade, sem alterar o documento.
```
