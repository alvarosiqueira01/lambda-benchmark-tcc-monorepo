# Documento de Escopo — Aplicação Benchmark de Document API

## 1. Objetivo da aplicação

A aplicação benchmark consiste em uma **API CRUD de documentos**, cujo objetivo experimental é fornecer um workload determinístico de processamento de texto executado em AWS Lambda.

A aplicação deverá permitir a criação, consulta, atualização e remoção de documentos e, principalmente, disponibilizar um endpoint de análise que processe o conteúdo de um documento.

O endpoint:

```text
GET /documents/{id}/analysis
```

deverá executar **três análises independentes sobre o mesmo documento**:

1. Estatísticas lexicais;
2. Métricas de legibilidade;
3. Extração de palavras-chave.

As três análises deverão receber exatamente os mesmos dados de entrada:

```text
document.body
document.language
document.tags
```

e não poderão depender do resultado umas das outras.

### Objetivo experimental

A aplicação não tem como objetivo avaliar funcionalidades de uma API de documentos em si. O CRUD constitui apenas o **contexto funcional mínimo necessário para fornecer o documento ao workload de benchmark**.

O foco experimental é medir o comportamento da operação `/analysis` sob diferentes:

* quantidades de memória;
* arquiteturas de execução (`x86_64` e `arm64`);
* granularidades funcionais (monolítica e decomposta);
* padrões de carga.

A aplicação deverá permitir a comparação controlada de:

```text
                  ┌─────────────────────┐
                  │   Document API      │
                  └──────────┬──────────┘
                             │
                             ▼
                    GET /documents/{id}
                             │
                             ▼
                    GET /documents/{id}/analysis
                             │
               ┌─────────────┼─────────────┐
               │             │             │
               ▼             ▼             ▼
          Lexical         Readability    Keywords
          Analysis         Analysis      Extraction
```

Na arquitetura monolítica, os três componentes serão executados dentro da mesma função.

Na arquitetura decomposta, serão implementados como funções especializadas independentes.

---

# 2. Funcionalidades obrigatórias

## 2.1 CRUD de documentos

A API deverá implementar exclusivamente as seguintes operações:

| Método   | Endpoint                   | Função              |
| -------- | -------------------------- | ------------------- |
| `POST`   | `/documents`               | Criar documento     |
| `GET`    | `/documents/{id}`          | Recuperar documento |
| `PUT`    | `/documents/{id}`          | Atualizar documento |
| `DELETE` | `/documents/{id}`          | Remover documento   |
| `GET`    | `/documents/{id}/analysis` | Executar análise    |

O CRUD deverá ser suficientemente simples para fornecer e recuperar o documento utilizado pelo benchmark.

---

## 2.2 Modelo de documento

Cada documento deverá possuir, no mínimo:

```text
Document
├── id
├── body
├── language
└── tags
```

### `id`

Identificador único do documento.

### `body`

Texto que será efetivamente processado pelas três análises.

### `language`

Idioma do documento.

A informação deverá ser utilizada pelas análises quando aplicável, especialmente pela extração de palavras-chave e pelas regras de processamento linguístico.

### `tags`

Lista de etiquetas associadas ao documento.

As tags fazem parte da entrada formal das análises e devem ser disponibilizadas às três funções, mesmo quando uma determinada análise não necessitar semanticamente de todas as informações.

---

# 3. Workload de análise

O endpoint:

```text
GET /documents/{id}/analysis
```

constitui o **workload principal do benchmark**.

Ele deverá obter um documento e executar três operações independentes.

Formalmente:

```text
A(document) =
    Lexical(document.body,
            document.language,
            document.tags)

    Readability(document.body,
                document.language,
                document.tags)

    Keywords(document.body,
             document.language,
             document.tags)
```

Não deverá existir dependência:

```text
Lexical → Readability
Lexical → Keywords
Readability → Keywords
```

Cada análise deverá poder ser executada isoladamente.

---

## 3.1 Estatísticas lexicais

A primeira análise deverá calcular estatísticas diretamente sobre o texto.

Exemplos obrigatórios:

```text
wordCount
uniqueWordCount
characterCount
averageWordLength
frequencyDistribution
```

A implementação deverá utilizar **exclusivamente a biblioteca padrão do Python**.

Não deverão ser utilizados:

* NLTK;
* spaCy;
* pandas;
* NumPy;
* bibliotecas externas de NLP;
* serviços externos de processamento de texto.

A intenção é manter o workload controlável e evitar que diferenças de dependências externas introduzam variáveis experimentais adicionais.

---

## 3.2 Métricas de legibilidade

A segunda análise deverá calcular métricas relacionadas à estrutura e legibilidade do documento.

Exemplos:

```text
sentenceCount
averageSentenceLength
averageWordLength
readabilityIndex
```

O algoritmo deverá ser:

* determinístico;
* implementado localmente;
* independente da análise lexical;
* independente da extração de palavras-chave.

Quando forem necessárias regras linguísticas, elas deverão ser explicitamente definidas para cada idioma suportado.

A fórmula do `readabilityIndex` deverá ser fixa durante todo o experimento.

---

## 3.3 Extração de palavras-chave

A terceira análise deverá implementar um pipeline determinístico:

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

O resultado deverá incluir, no mínimo:

```text
topKeywords
keywordFrequencies
```

O algoritmo deverá ser implementado localmente, sem utilização de APIs ou serviços externos de NLP.

A ordenação deverá ser determinística. Por exemplo:

```text
frequência decrescente
        ↓
palavra em ordem lexicográfica crescente
```

Assim, entradas idênticas deverão produzir exatamente a mesma saída.

---

# 4. Requisitos funcionais

### RF-01 — Criar documento

A API deverá permitir a criação de um documento contendo:

```text
body
language
tags
```

e retornar seu identificador.

### RF-02 — Recuperar documento

A API deverá permitir recuperar um documento através de seu `id`.

### RF-03 — Atualizar documento

A API deverá permitir substituir o conteúdo de um documento existente.

### RF-04 — Excluir documento

A API deverá permitir remover um documento.

### RF-05 — Analisar documento

A API deverá disponibilizar:

```text
GET /documents/{id}/analysis
```

e retornar os resultados das três análises.

### RF-06 — Independência das análises

As três análises deverão operar independentemente.

Nenhuma análise poderá consumir como entrada o resultado de outra.

### RF-07 — Determinismo

Para uma mesma entrada:

```text
body
language
tags
```

as análises deverão produzir a mesma saída.

### RF-08 — Resultado composto

A resposta do endpoint deverá agrupar os três resultados:

```json
{
  "lexical": {},
  "readability": {},
  "keywords": {}
}
```

A estrutura exata poderá ser definida na especificação OpenAPI, mas deverá permanecer idêntica em todas as configurações experimentais.

---

# 5. Requisitos não funcionais

## RNF-01 — Determinismo

A aplicação deverá produzir resultados reproduzíveis para uma mesma entrada.

Não deverão existir componentes aleatórios no workload.

---

## RNF-02 — Reprodutibilidade

O mesmo conjunto de documentos e a mesma configuração experimental deverão produzir resultados comparáveis entre execuções.

Deverão permanecer fixos:

* versão do código;
* dataset;
* formato dos documentos;
* algoritmo de análise;
* regras linguísticas;
* formato das respostas;
* configuração da carga.

---

## RNF-03 — Independência das análises

As três análises deverão possuir interfaces conceituais independentes:

```python
lexical_analysis(body, language, tags)

readability_analysis(body, language, tags)

keyword_analysis(body, language, tags)
```

Isso permite implementar as duas arquiteturas experimentais sem modificar o workload lógico.

---

## RNF-04 — Baixa complexidade externa

A aplicação deverá possuir o mínimo possível de componentes não relacionados ao experimento.

O caminho crítico deverá ser aproximadamente:

```text
Request
   ↓
Lambda
   ↓
document retrieval
   ↓
analysis workload
   ↓
response
```

Na arquitetura decomposta:

```text
Request
   ↓
Orchestrator
   ├──→ Lexical Lambda
   ├──→ Readability Lambda
   └──→ Keyword Lambda
             ↓
          Results
             ↓
          Response
```

---

## RNF-05 — Compatibilidade arquitetural

O mesmo workload deverá ser executável em:

```text
x86_64
arm64
```

sem alterações no algoritmo.

---

## RNF-06 — Configuração de memória

A aplicação deverá permitir executar o workload nas seguintes configurações:

```text
128 MB
256 MB
512 MB
1024 MB
2048 MB
```

A alteração de memória não deverá exigir alterações no código da aplicação.

---

## RNF-07 — Observabilidade

A aplicação deverá produzir informações suficientes para determinar, posteriormente:

* duração;
* `Init Duration`;
* invocações;
* erros;
* timeouts;
* concorrência;
* cold starts, quando identificáveis.

A instrumentação deverá ser mínima e não deverá executar processamento significativo no caminho crítico.

---

## RNF-08 — Ausência de dependências externas de processamento

As análises deverão utilizar somente mecanismos locais, com especial atenção para que a comparação não seja dominada por chamadas externas.

---

# 6. Padrões de carga

O benchmark deverá utilizar três padrões de chegada.

## 6.1 Carga constante

```text
20 req/s
durante 5 minutos
```

Total esperado:

```text
20 × 300 = 6.000 requisições
```

---

## 6.2 Carga em rajada

Ciclo:

```text
100 req/s por 1 segundo
        ↓
0 req/s por 4 segundos
```

repetido durante 5 minutos.

O ciclo possui 5 segundos.

São aproximadamente:

```text
300 / 5 = 60 ciclos
```

com aproximadamente:

```text
100 × 60 = 6.000 requisições
```

Esse padrão deverá produzir períodos de alta concorrência seguidos de períodos sem requisições.

---

## 6.3 Carga esparsa

```text
5 requisições a cada 10 segundos
durante 5 minutos
```

Total:

```text
30 ciclos × 5 = 150 requisições
```

Esse padrão é particularmente relevante para observar comportamento de inicialização e reutilização de ambientes.

---

# 7. Fatores que devem permanecer constantes

Para que as diferenças observadas possam ser atribuídas às variáveis experimentais, os seguintes fatores deverão permanecer constantes.

## 7.1 Dataset

O conjunto de documentos deverá ser fixo.

Não deverão ser utilizados documentos diferentes entre:

* x86 e ARM;
* diferentes níveis de memória;
* monolítico e decomposto;
* diferentes padrões de carga.

---

## 7.2 Tamanho dos documentos

A distribuição do tamanho dos documentos deverá permanecer constante.

Idealmente, o dataset deverá conter documentos com tamanho controlado, permitindo caracterizar o workload.

Por exemplo:

```text
pequenos
médios
grandes
```

mas a distribuição utilizada deverá ser exatamente a mesma em todas as configurações.

---

## 7.3 Conteúdo

O conteúdo textual utilizado deverá ser idêntico.

Não deverá haver geração dinâmica ou aleatória de texto durante o benchmark.

---

## 7.4 Algoritmos

As implementações das três análises deverão ser idênticas entre as arquiteturas experimentais.

A única diferença deverá ser a forma de execução:

```text
Monolítico:
    A + B + C

Decomposto:
    A || B || C
```

---

## 7.5 Regras linguísticas

Deverão permanecer constantes:

* tokenização;
* normalização;
* stopwords;
* cálculo de frases;
* fórmula de legibilidade;
* regras de pontuação;
* ordenação dos resultados.

---

## 7.6 Formato das respostas

A estrutura JSON deverá ser a mesma entre todas as configurações.

---

## 7.7 API e protocolo

Deverão permanecer constantes:

* endpoints;
* método HTTP;
* formato das requisições;
* formato das respostas;
* tamanho aproximado das mensagens;
* headers relevantes.

---

## 7.8 Ambiente de execução

Sempre que possível, deverão ser mantidos constantes:

* versão do Python;
* versão do código;
* dependências;
* configuração da região AWS;
* configuração de timeout;
* configuração de arquitetura;
* configuração de memória;
* configuração de observabilidade.

A memória e a arquitetura de execução constituem exceções, pois são fatores experimentais.

---

# 8. Decisões que não podem variar entre arquiteturas

A comparação entre **monolítica e decomposta** exige uma separação particularmente rigorosa entre o que é variável e o que é constante.

A arquitetura poderá alterar:

```text
granularidade das funções
```

mas não poderá alterar o workload lógico.

### Deve permanecer igual

| Elemento                  | Monolítico                | Decomposto                |
| ------------------------- | ------------------------- | ------------------------- |
| Documento de entrada      | igual                     | igual                     |
| `body`                    | igual                     | igual                     |
| `language`                | igual                     | igual                     |
| `tags`                    | igual                     | igual                     |
| Algoritmo lexical         | igual                     | igual                     |
| Algoritmo de legibilidade | igual                     | igual                     |
| Algoritmo de keywords     | igual                     | igual                     |
| Stopwords                 | igual                     | igual                     |
| Fórmula de legibilidade   | igual                     | igual                     |
| Ordenação                 | igual                     | igual                     |
| Dataset                   | igual                     | igual                     |
| Região AWS                | igual                     | igual                     |
| Memória por função        | configuração experimental | configuração experimental |
| Arquitetura CPU           | configuração experimental | configuração experimental |
| Padrão de carga           | igual                     | igual                     |
| Timeout                   | equivalente               | equivalente               |
| Código das análises       | igual                     | igual                     |

A decomposição deverá modificar **onde o processamento acontece**, e não **o que é processado**.

---

# 9. Funcionalidades explicitamente proibidas

Para preservar a validade do benchmark, a aplicação **não deverá implementar**:

### Autenticação e autorização

Não haverá:

* login;
* cadastro de usuários;
* JWT;
* OAuth;
* RBAC;
* permissões.

---

### Cache

Não haverá:

* Redis;
* ElastiCache;
* cache HTTP;
* cache de resultados;
* cache local intencional.

O objetivo é evitar que requisições posteriores deixem de executar o workload.

---

### Filas

Não deverão ser utilizadas:

* SQS;
* RabbitMQ;
* Kafka;
* filas internas;
* mecanismos de retry assíncrono como parte do workload.

---

### Busca textual

Não haverá:

* full-text search;
* Elasticsearch/OpenSearch;
* índices de texto;
* busca por conteúdo.

---

### Paginação

Não haverá:

```text
GET /documents
```

nem listagem paginada de documentos.

---

### Múltiplos bancos

A aplicação não deverá utilizar diferentes bancos para diferentes análises.

O armazenamento deverá ser o mais simples possível.

---

### Serviços externos de NLP

As análises não deverão depender de:

* APIs externas;
* serviços de IA;
* serviços de tradução;
* serviços externos de classificação;
* APIs de análise linguística.

---

### Funcionalidades não relacionadas ao benchmark

Também deverão ser evitados:

* versionamento de documentos;
* colaboração;
* histórico;
* comentários;
* compartilhamento;
* notificações;
* auditoria complexa;
* processamento de anexos;
* upload de arquivos;
* geração de relatórios;
* dashboards;
* processamento em background;
* funcionalidades administrativas.

---

# 10. Estrutura lógica da aplicação

## 10.1 Arquitetura monolítica

Na configuração monolítica, uma única função deverá executar o processamento:

```text
                    API Gateway
                         │
                         ▼
                Analysis Lambda
                         │
             ┌───────────┼───────────┐
             ▼           ▼           ▼
          Lexical    Readability   Keywords
          Analysis    Analysis     Analysis
             │           │           │
             └───────────┼───────────┘
                         ▼
                      Response
```

O processamento poderá ser sequencial ou utilizar concorrência interna **desde que essa decisão seja fixada para todo o experimento monolítico**.

---

# 11. Arquitetura decomposta

Na configuração decomposta, cada análise deverá ser representada por uma função especializada:

```text
                         API Gateway
                              │
                              ▼
                       Orchestrator
                     /      |       \
                    /       |        \
                   ▼        ▼         ▼
              Lexical   Readability  Keywords
              Lambda      Lambda      Lambda
                   \        |        /
                    \       |       /
                     ▼      ▼      ▼
                       Aggregator
                           │
                           ▼
                        Response
```

As três funções devem receber os mesmos dados:

```text
body
language
tags
```

e produzir seus respectivos resultados.

Como as análises são independentes, a arquitetura decomposta deverá permitir execução concorrente.

Isso é particularmente importante para a hipótese H6, pois permite estudar a relação entre:

```text
overhead de invocação
        +
overhead de inicialização
        +
custo de orquestração
        versus
execução concorrente
```

---

# 12. Variáveis experimentais

O experimento deverá considerar explicitamente os seguintes fatores:

| Variável        | Tipo              | Níveis                           |
| --------------- | ----------------- | -------------------------------- |
| Granularidade   | Categórica        | Monolítica / Decomposta          |
| Padrão de carga | Categórica        | Constante / Rajada / Esparsa     |
| Memória         | Ordinal           | 128 / 256 / 512 / 1024 / 2048 MB |
| Arquitetura     | Categórica        | x86_64 / arm64                   |
| Taxa de chegada | Contínua/derivada | determinada pelo padrão de carga |

A combinação desses fatores deverá formar a matriz experimental.

---

# 13. Métricas primárias

As métricas utilizadas diretamente para responder às RQs deverão ser:

### Custo por requisição

$$
C_{req} = \frac{C_{total}}{N_{req}}
$$

onde:

* \(C_{total}\) = custo total observado;
* \(N_{req}\) = número de requisições consideradas válidas.

### p95 de latência

O percentil 95 deverá ser a principal métrica de latência.

### Redução relativa de custo

Para uma configuração \(i\) em relação ao baseline \(B\):

$$
CR =
\frac{C_B-C_i}{C_B}\times100
$$

### Redução relativa de latência

$$
LR =
\frac{L_B-L_i}{L_B}\times100
$$

onde \(L\) representa o p95 de latência.

O **baseline deverá ser definido previamente e permanecer fixo durante toda a análise**.

---

# 14. Métricas auxiliares

Deverão ser coletadas:

* p50;
* p90;
* p95;
* p99;
* média;
* desvio-padrão;
* duração da execução;
* `Init Duration`;
* número de cold starts;
* taxa de cold starts;
* número de invocações;
* taxa de invocações;
* erros;
* timeouts;
* concorrência observada.

Essas métricas não substituem as métricas primárias, mas deverão ser utilizadas para explicar diferenças observadas nelas.

---

# 15. Requisito de equivalência do workload

Uma condição fundamental do benchmark é:

> **Uma requisição lógica deve representar exatamente o mesmo trabalho lógico em todas as configurações experimentais.**

Assim, uma requisição:

```text
GET /documents/123/analysis
```

deverá sempre resultar em:

```text
1 análise lexical
+
1 análise de legibilidade
+
1 extração de keywords
```

Independentemente de a implementação ser:

```text
1 Lambda
```

ou:

```text
3 Lambdas especializadas
```

A decomposição não poderá remover, simplificar ou adicionar etapas de processamento para melhorar artificialmente determinado cenário.

---

# 16. Escopo experimental resultante

A aplicação resultante deverá ser deliberadamente pequena:

```text
                    Document API
                         │
        ┌────────────────┼────────────────┐
        │                │                │
       CRUD          Document Store    Analysis
                                         │
                         ┌───────────────┼───────────────┐
                         │               │               │
                      Lexical       Readability       Keywords
```

O **CRUD e armazenamento** existem para fornecer o contexto funcional e os dados de entrada.

O **endpoint `/analysis` constitui o workload experimental**.

As principais dimensões do experimento serão:

```text
                         Workload
                            │
             ┌──────────────┼──────────────┐
             │              │              │
        Granularidade    Memória       Arquitetura
        Mono/Decomp.   128–2048 MB     x86/ARM
             │              │              │
             └──────────────┼──────────────┘
                            │
                     Padrão de carga
                            │
              ┌─────────────┼─────────────┐
              │             │             │
           Constante      Rajada        Esparsa
```
