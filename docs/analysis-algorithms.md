# Algoritmos Determinísticos de Análise Textual

## 1. Objetivo

Este documento especifica os três algoritmos de análise textual utilizados pelo endpoint:

```http
GET /documents/{id}/analysis
```

Os algoritmos são:

1. **Análise lexical**
2. **Análise de legibilidade**
3. **Extração de palavras-chave**

A especificação foi definida para que duas implementações conformes produzam os mesmos resultados para uma mesma entrada.

Os algoritmos devem ser:

* determinísticos;
* independentes entre si;
* implementados exclusivamente com a biblioteca padrão do Python;
* livres de acesso a APIs ou serviços externos;
* livres de aleatoriedade;
* semanticamente significativos;
* suficientemente simples para permitir reprodução do experimento;
* idênticos em conteúdo e regras entre as arquiteturas monolítica e decomposta.

O objetivo do benchmark não é adicionar processamento artificial para aumentar o tempo de execução. O tempo observado deve resultar exclusivamente do processamento definido neste documento e do tamanho real dos documentos utilizados.

---

# 2. Contrato comum

Cada algoritmo recebe exatamente os mesmos três argumentos:

```python
body: str
language: str
tags: list[str]
```

Assinatura conceitual:

```python
lexical_analysis(
    body: str,
    language: str,
    tags: list[str]
) -> dict
```

```python
readability_analysis(
    body: str,
    language: str,
    tags: list[str]
) -> dict
```

```python
keyword_analysis(
    body: str,
    language: str,
    tags: list[str]
) -> dict
```

## 2.1 Utilização dos parâmetros

| Parâmetro  |                Lexical | Readability | Keywords |
| ---------- | ---------------------: | ----------: | -------: |
| `body`     |                    Sim |         Sim |      Sim |
| `language` | Não altera tokenização |         Sim |      Sim |
| `tags`     |                    Não |         Não |      Não |

`tags` faz parte obrigatoriamente do contrato comum, mas é **ignorada para efeitos de cálculo** pelos três algoritmos.

Isso não significa que o parâmetro possa ser omitido. Ele deve ser recebido para garantir que as três funções possuam exatamente o mesmo contrato de entrada.

Nenhum algoritmo pode modificar `body`, `language` ou `tags`.

---

# 3. Regras comuns de processamento

## 3.1 Idiomas suportados

O benchmark suporta exclusivamente:

```text
en
pt
```

onde:

* `en` = inglês;
* `pt` = português.

Não existe fallback automático para outros idiomas.

Qualquer valor diferente de `en` ou `pt` constitui entrada inválida para a análise.

Isso elimina comportamentos ambíguos como:

* interpretar `es` como inglês;
* utilizar stopwords vazias;
* selecionar uma fórmula padrão;
* tentar detectar automaticamente o idioma.

A validação do idioma deve ocorrer antes do processamento específico da análise.

---

## 3.2 Preservação do documento

Os algoritmos nunca alteram o conteúdo persistido.

Transformações como:

* `strip()`;
* `casefold()`;
* remoção de pontuação;
* tokenização;
* remoção de stopwords;

são realizadas somente sobre representações locais derivadas de `body`.

O valor original de `body` permanece inalterado.

---

## 3.3 Tokenização padrão

Todos os algoritmos utilizam a mesma definição de token textual:

```python
re.findall(r"[^\W_]+", body, re.UNICODE)
```

Esse padrão identifica sequências de caracteres Unicode de palavra, excluindo `_`.

Portanto, exemplos como:

```text
"Python is simple."
```

produzem:

```text
["Python", "is", "simple"]
```

### Caracteres internos

A especificação **não considera hífen ou apóstrofo como parte de um token**.

Consequentemente:

```text
"don't"
```

produz:

```text
["don", "t"]
```

e:

```text
"state-of-the-art"
```

produz:

```text
["state", "of", "the", "art"]
```

Essa decisão é intencional e deve ser mantida nas três análises.

Não deve ser utilizado um tokenizer diferente para cada algoritmo.

---

## 3.4 Normalização para análise

Quando uma análise precisar comparar tokens, a forma normalizada será:

```python
token.casefold()
```

`casefold()` deve ser utilizado em vez de `lower()` para estabelecer uma regra Unicode mais abrangente.

A normalização é utilizada apenas para comparação e contagem.

O texto original continua preservado.

---

## 3.5 Valores decimais

Todas as métricas decimais produzidas pela especificação são arredondadas obrigatoriamente para **duas casas decimais**.

O arredondamento deve utilizar:

```text
ROUND_HALF_UP
```

e não o arredondamento binário padrão de `round()`.

A implementação Python deve utilizar `decimal.Decimal` e `decimal.ROUND_HALF_UP`, ambos pertencentes à biblioteca padrão.

Exemplo:

```text
2.345 → 2.35
2.344 → 2.34
```

Essa regra aplica-se a:

* `averageWordLength`;
* `averageSentenceLength`;
* `readabilityIndex`.

---

# 4. Análise lexical

## 4.1 Objetivo

A análise lexical mede propriedades básicas do vocabulário e da extensão textual do documento.

A análise produz:

```text
wordCount
uniqueWordCount
characterCount
averageWordLength
frequencyDistribution
```

---

## 4.2 Entrada

```python
body: str
language: str
tags: list[str]
```

`language` e `tags` são recebidos para manter o contrato comum, mas não alteram o cálculo lexical.

---

## 4.3 Processamento

### Passo 1 — Tokenização

Aplicar:

```python
tokens = re.findall(r"[^\W_]+", body, re.UNICODE)
```

### Passo 2 — Normalização

Para cada token:

```python
normalized = token.casefold()
```

### Passo 3 — Contagem total

```text
wordCount = quantidade de tokens
```

### Passo 4 — Vocabulário único

```text
uniqueWordCount = quantidade de tokens normalizados distintos
```

### Passo 5 — Caracteres

```text
characterCount = len(body)
```

A contagem inclui:

* letras;
* números;
* espaços;
* pontuação;
* quebras de linha;
* demais caracteres Unicode presentes no `body`.

Nenhum caractere é removido para essa métrica.

### Passo 6 — Comprimento médio

Para cada token, utiliza-se:

```python
len(token)
```

Então:

```text
averageWordLength =
    soma dos comprimentos dos tokens / wordCount
```

Caso `wordCount == 0`:

```text
averageWordLength = 0.00
```

### Passo 7 — Distribuição de frequência

A frequência é calculada utilizando tokens normalizados:

```text
frequencyDistribution[normalized_token] += 1
```

As chaves devem ser apresentadas em ordem lexicográfica crescente.

---

## 4.4 Saída

Formato obrigatório:

```json
{
  "wordCount": 6,
  "uniqueWordCount": 4,
  "characterCount": 37,
  "averageWordLength": 5.00,
  "frequencyDistribution": {
    "is": 2,
    "powerful": 1,
    "python": 2,
    "simple": 1
  }
}
```

Para:

```text
Python is simple. Python is powerful.
```

A contagem é:

* `wordCount = 6`
* `uniqueWordCount = 4`
* `characterCount = 37`
* `averageWordLength = 5.00`

A distribuição é:

```text
python → 2
is → 2
simple → 1
powerful → 1
```

e deve ser serializada lexicograficamente:

```text
is
powerful
python
simple
```

---

## 4.5 Ordenação

A ordenação de `frequencyDistribution` é obrigatória.

A implementação deve construir o resultado na ordem:

```python
for token in sorted(frequency):
    ...
```

ou utilizar um serializador configurado com ordenação de chaves.

Apenas depender da ordem de inserção do dicionário não é suficiente como especificação de serialização.

---

## 4.6 Pseudocódigo

```text
FUNCTION lexical_analysis(body, language, tags):

    tokens ← tokenize(body)

    wordCount ← length(tokens)
    characterCount ← length(body)

    frequency ← empty map
    totalCharacters ← 0

    FOR token IN tokens:

        normalized ← casefold(token)

        frequency[normalized] ←
            frequency.get(normalized, 0) + 1

        totalCharacters ←
            totalCharacters + length(token)

    uniqueWordCount ← size(frequency)

    IF wordCount > 0:
        averageWordLength ←
            totalCharacters / wordCount
    ELSE:
        averageWordLength ← 0

    orderedFrequency ← keys(frequency) sorted lexicographically

    RETURN {
        wordCount,
        uniqueWordCount,
        characterCount,
        averageWordLength rounded to 2 decimals,
        frequencyDistribution ordered lexicographically
    }
```

---

# 5. Análise de legibilidade

## 5.1 Objetivo

A análise de legibilidade produz métricas relacionadas à estrutura sintática e à complexidade aproximada do vocabulário:

```text
sentenceCount
averageSentenceLength
averageWordLength
readabilityIndex
```

A análise utiliza uma estimativa determinística de sílabas baseada em grupos de vogais.

Não é utilizado NLP externo.

---

# 5.2 Identificação de sentenças

Uma sentença é uma sequência de texto separada da próxima por:

* um ou mais caracteres `.`, `!` ou `?`;
* seguidos por pelo menos um caractere de espaço em branco;

ou pela ocorrência desses sinais no final do texto.

Exemplos:

```text
"Hello. World."
```

→ 2 sentenças.

```text
"Hello! World?"
```

→ 2 sentenças.

```text
"Hello.   World."
```

→ 2 sentenças.

```text
"Hello\nWorld"
```

→ 1 sentença.

```text
"Hello!World"
```

→ 1 sentença.

```text
"3.14 is a number."
```

→ 1 sentença.

A última regra é importante: uma pontuação `.`, `!` ou `?` somente encerra uma sentença quando estiver seguida por espaço em branco ou pelo fim do texto.

---

## 5.3 Algoritmo de segmentação

Antes da segmentação, é utilizada uma cópia local:

```python
text = body.strip()
```

Isso não modifica o documento persistido.

A segmentação utiliza:

```python
sentences = re.split(r"[.!?]+\s+", text)
```

Para evitar que pontuação final seguida por espaços produza uma sentença vazia, a implementação deve remover elementos vazios após a divisão.

Além disso, se o texto terminar com pontuação, essa pontuação não precisa ser seguida por espaço para encerrar a última sentença.

Uma implementação equivalente e recomendada é:

```python
parts = re.split(r"[.!?]+(?:\s+|$)", text)
sentences = [part for part in parts if part.strip()]
```

A implementação deve produzir exatamente o mesmo comportamento descrito nesta seção.

---

# 5.4 Contagem de palavras

A contagem de palavras da análise de legibilidade utiliza exatamente o mesmo tokenizer:

```python
re.findall(r"[^\W_]+", body, re.UNICODE)
```

A análise de legibilidade **não chama a função da análise lexical**.

Ela realiza sua própria tokenização para preservar a independência computacional entre os algoritmos.

---

# 5.5 Comprimento médio de sentença

Para cada sentença, são identificadas suas palavras utilizando o tokenizer padrão.

Então:

```text
averageSentenceLength =
    wordCount / sentenceCount
```

Caso não existam palavras ou sentenças:

```text
averageSentenceLength = 0.00
```

---

# 5.6 Comprimento médio das palavras

É calculado diretamente a partir dos tokens:

```text
averageWordLength =
    soma dos comprimentos dos tokens / quantidade de tokens
```

Caso não existam tokens:

```text
averageWordLength = 0.00
```

---

# 5.7 Estimativa de sílabas

A estimativa de sílabas é deliberadamente simples e determinística.

Ela não pretende substituir um analisador fonético. Seu objetivo é fornecer uma métrica consistente para o benchmark.

## 5.7.1 Português

Para `language = "pt"`:

```text
Vogais:
a e i o u á é í ó ú â ê ô ã õ à ü
```

Após `casefold()`, o algoritmo percorre os caracteres do token e conta grupos consecutivos de vogais.

Exemplo conceitual:

```text
casa
```

grupos:

```text
a
a
```

→ 2 sílabas estimadas.

O número mínimo de sílabas estimadas é sempre:

```text
1
```

quando o token possui pelo menos um caractere.

Não são aplicadas regras fonéticas adicionais.

Portanto, o algoritmo não tenta distinguir automaticamente:

* ditongos;
* hiatos;
* encontros vocálicos;
* vogais mudas;
* exceções fonéticas.

Essa simplificação é intencional para garantir reprodutibilidade.

---

## 5.7.2 Inglês

Para `language = "en"`:

```text
Vogais:
a e i o u y
```

Após `casefold()`, o algoritmo conta grupos consecutivos de vogais.

É aplicado adicionalmente o seguinte ajuste determinístico:

Se:

1. o token possui mais de uma sílaba estimada;
2. termina em `e`;
3. não termina em `le`;

então:

```text
syllables = syllables - 1
```

O resultado mínimo continua sendo:

```text
1
```

Exemplos conceituais:

```text
make
```

contagem inicial:

```text
a + e = 2
```

ajuste do `e` final:

```text
1
```

Já:

```text
table
```

não recebe esse ajuste devido à terminação `le`.

Nenhuma outra exceção fonética é aplicada.

---

# 5.8 Fórmulas de legibilidade

O índice utilizado depende explicitamente do idioma.

## 5.8.1 Inglês

Para `en`, utiliza-se:

```text
FRE_en =
206.835
- 1.015 × ASL
- 84.6 × ASW
```

onde:

```text
ASL = palavras / sentenças
```

e:

```text
ASW = sílabas estimadas / palavras
```

---

## 5.8.2 Português

Para `pt`, utiliza-se a versão adaptada definida especificamente para este benchmark:

```text
FRE_pt =
248.835
- 1.015 × ASL
- 84.6 × ASW
```

A diferença entre as duas fórmulas é intencional e faz parte da especificação experimental.

A fórmula não deve ser substituída durante os experimentos.

---

# 5.9 Limitação do índice

O resultado final é obrigatoriamente limitado ao intervalo:

```text
0 ≤ readabilityIndex ≤ 100
```

Portanto:

```text
IF readabilityIndex < 0:
    readabilityIndex = 0

IF readabilityIndex > 100:
    readabilityIndex = 100
```

A aplicação do limite é obrigatória.

---

# 5.10 Arredondamento

Depois do cálculo e do limite:

```text
readabilityIndex
```

é arredondado para duas casas decimais utilizando:

```text
ROUND_HALF_UP
```

O mesmo procedimento é utilizado para:

```text
averageSentenceLength
averageWordLength
```

---

# 5.11 Saída

Formato:

```json
{
  "sentenceCount": 2,
  "averageSentenceLength": 3.00,
  "averageWordLength": 5.00,
  "readabilityIndex": 72.35
}
```

Os valores acima são apenas ilustrativos; os resultados efetivos devem ser produzidos pelo algoritmo especificado.

---

# 5.12 Pseudocódigo

```text
FUNCTION readability_analysis(body, language, tags):

    VALIDATE language ∈ {"en", "pt"}

    text ← body.strip()

    sentences ← split text according to sentence rules

    wordTokens ← tokenize(body)

    sentenceCount ← count(sentences)
    wordCount ← count(wordTokens)

    totalCharacters ← 0

    FOR token IN wordTokens:
        totalCharacters ←
            totalCharacters + length(token)

    IF wordCount > 0:
        averageWordLength ←
            totalCharacters / wordCount
    ELSE:
        averageWordLength ← 0

    IF sentenceCount > 0:
        averageSentenceLength ←
            wordCount / sentenceCount
    ELSE:
        averageSentenceLength ← 0

    totalSyllables ← 0

    FOR token IN wordTokens:
        totalSyllables ←
            totalSyllables + estimate_syllables(token, language)

    IF wordCount > 0 AND sentenceCount > 0:

        ASL ← wordCount / sentenceCount
        ASW ← totalSyllables / wordCount

        IF language == "en":
            readabilityIndex ←
                206.835 - 1.015 * ASL - 84.6 * ASW

        ELSE IF language == "pt":
            readabilityIndex ←
                248.835 - 1.015 * ASL - 84.6 * ASW

    ELSE:
        readabilityIndex ← 0

    readabilityIndex ←
        clamp(readabilityIndex, 0, 100)

    RETURN {
        sentenceCount,
        averageSentenceLength rounded to 2 decimals,
        averageWordLength rounded to 2 decimals,
        readabilityIndex rounded to 2 decimals
    }
```

---

# 6. Extração de palavras-chave

## 6.1 Objetivo

A extração de palavras-chave identifica os termos mais frequentes e semanticamente mais relevantes dentro do vocabulário textual, removendo palavras funcionais por meio de uma lista fixa de stopwords.

O processamento é:

```text
tokenização
→ normalização
→ remoção de números isolados
→ remoção de stopwords
→ contagem
→ ordenação determinística
→ seleção dos 10 primeiros
```

---

# 6.2 Tokenização

Utiliza exatamente:

```python
re.findall(r"[^\W_]+", body, re.UNICODE)
```

---

# 6.3 Normalização

Cada token é convertido utilizando:

```python
token.casefold()
```

Nenhuma outra normalização é realizada.

Não são removidos:

* acentos;
* caracteres Unicode;
* letras específicas do idioma.

Por exemplo:

```text
"ação"
```

permanece:

```text
"ação"
```

---

# 6.4 Remoção de números

Tokens constituídos exclusivamente por caracteres numéricos não são considerados palavras-chave.

Exemplo:

```text
"2026"
```

é descartado.

Tokens alfanuméricos que contenham letras permanecem.

---

# 6.5 Stopwords

As listas de stopwords são parte da especificação do benchmark.

Não é permitido utilizar:

* NLTK;
* spaCy;
* bibliotecas externas;
* listas baixadas da Internet;
* stopwords dependentes do sistema operacional.

A lista abaixo é a lista oficial utilizada pelo benchmark.

## 6.5.1 Inglês

```text
a
about
above
after
again
against
all
am
an
and
any
are
as
at
be
because
been
before
being
below
between
both
but
by
can
could
did
do
does
doing
down
during
each
few
for
from
further
had
has
have
having
he
her
here
hers
herself
him
himself
his
how
i
if
in
into
is
it
its
itself
just
me
more
most
my
myself
no
nor
not
now
of
off
on
once
only
or
other
our
ours
ourselves
out
over
own
same
she
should
so
some
such
than
that
the
their
theirs
them
themselves
then
there
these
they
this
those
through
to
too
under
until
up
very
was
we
were
what
when
where
which
while
who
whom
why
will
with
you
your
yours
yourself
yourselves
```

## 6.5.2 Português

```text
a
à
às
ao
aos
aquela
aquelas
aquele
aqueles
aquilo
as
até
com
como
da
das
de
dela
delas
dele
deles
depois
do
dos
e
é
ela
elas
ele
eles
em
entre
era
eram
essa
essas
esse
esses
esta
estas
este
estes
eu
foi
foram
há
isso
isto
já
lhe
lhes
mais
mas
me
mesmo
meu
meus
minha
minhas
na
nas
não
nem
no
nos
nós
num
numa
o
os
ou
outra
outras
outro
outros
para
pela
pelas
pelo
pelos
por
qual
quando
que
quem
se
sem
ser
seu
seus
só
sua
suas
também
te
tem
têm
tendo
ter
teu
teus
toda
todas
todo
todos
tu
um
uma
umas
uns
vai
vão
você
vocês
```

Essas listas são consideradas constantes do experimento.

Qualquer alteração exige uma nova versão do algoritmo e, consequentemente, não deve ser misturada aos resultados de uma versão anterior.

---

# 6.6 Contagem

Depois da tokenização, normalização e filtragem:

```text
frequency[keyword] += 1
```

A contagem é realizada somente sobre os tokens restantes.

---

# 6.7 Ordenação

As palavras são ordenadas por dois critérios:

1. frequência decrescente;
2. palavra em ordem lexicográfica crescente para desempate.

Formalmente:

```python
sorted(
    frequency.items(),
    key=lambda item: (-item[1], item[0])
)
```

Exemplo:

```text
python → 5
aws → 5
lambda → 3
```

A ordem será:

```text
aws
python
lambda
```

porque `aws` e `python` possuem a mesma frequência e `aws < python`.

---

# 6.8 Quantidade de palavras-chave

O número máximo de palavras-chave é fixado em:

```text
TOP_K = 10
```

Se houver menos de 10 termos válidos, todos os termos disponíveis são retornados.

---

# 6.9 Saída

Formato:

```json
{
  "topKeywords": [
    "aws",
    "lambda",
    "serverless"
  ],
  "keywordFrequencies": {
    "aws": 8,
    "lambda": 6,
    "serverless": 4
  }
}
```

`keywordFrequencies` contém somente os termos presentes em `topKeywords`.

As chaves devem seguir a mesma ordem dos elementos de `topKeywords`.

---

# 6.10 Pseudocódigo

```text
FUNCTION keyword_analysis(body, language, tags):

    VALIDATE language ∈ {"en", "pt"}

    tokens ← tokenize(body)

    IF language == "en":
        stopwords ← ENGLISH_STOPWORDS
    ELSE:
        stopwords ← PORTUGUESE_STOPWORDS

    frequency ← empty map

    FOR token IN tokens:

        normalized ← casefold(token)

        IF normalized is numeric:
            CONTINUE

        IF normalized IN stopwords:
            CONTINUE

        frequency[normalized] ←
            frequency.get(normalized, 0) + 1

    ordered ← sort frequency by:
        1. frequency descending
        2. keyword ascending

    selected ← first 10 elements of ordered

    topKeywords ← keywords from selected

    keywordFrequencies ← frequencies from selected
        preserving selected order

    RETURN {
        topKeywords,
        keywordFrequencies
    }
```

---

# 7. Independência dos algoritmos

Os três algoritmos são independentes.

Isso significa que:

```text
Lexical
   │
   ├── não chama Readability
   └── não chama Keywords

Readability
   │
   ├── não chama Lexical
   └── não chama Keywords

Keywords
   │
   ├── não chama Lexical
   └── não chama Readability
```

Cada função recebe diretamente:

```text
body
language
tags
```

e realiza seu próprio processamento.

Uma implementação não pode reutilizar o resultado de outra análise como entrada.

Essa regra é particularmente importante para a comparação entre:

```text
Monolithic
```

e:

```text
Decomposed
```

porque garante que a decomposição funcional não altere a quantidade ou a natureza do processamento lógico realizado.

---

# 8. Equivalência entre arquiteturas

## 8.1 Monolítica

A função monolítica recebe:

```text
body
language
tags
```

e executa:

```text
lexical_analysis
readability_analysis
keyword_analysis
```

em uma única função Lambda.

---

## 8.2 Decomposta

A arquitetura decomposta possui:

```text
Orchestrator
    │
    ├── LexicalAnalysisFunction
    ├── ReadabilityAnalysisFunction
    └── KeywordAnalysisFunction
```

Cada função especializada recebe:

```text
body
language
tags
```

de forma independente.

Os três resultados são posteriormente combinados pelo orquestrador.

---

## 8.3 Regra de equivalência

Para uma mesma entrada:

```text
(body, language, tags)
```

as duas arquiteturas devem produzir exatamente:

```json
{
  "lexical": {...},
  "readability": {...},
  "keywords": {...}
}
```

com os mesmos valores.

A decomposição não pode:

* alterar os algoritmos;
* alterar os dados de entrada;
* adicionar processamento;
* remover processamento;
* modificar as listas de stopwords;
* utilizar outro tokenizer;
* utilizar outra fórmula;
* alterar arredondamento;
* alterar regras de ordenação.

A única diferença experimental entre as arquiteturas deve ser a **granularidade funcional e a forma de execução/orquestração**.

---

# 9. Determinismo

Para uma mesma entrada:

```text
body
language
tags
```

a saída deve ser sempre idêntica.

Os algoritmos não podem utilizar:

* números aleatórios;
* timestamps;
* UUIDs;
* estado global mutável;
* variáveis dependentes da execução anterior;
* APIs externas;
* serviços externos;
* banco de dados durante a análise;
* informações do ambiente para modificar o resultado;
* paralelismo que altere a ordem do resultado.

Toda ordenação deve ser explicitamente definida.

---

# 10. Tratamento de entradas sem palavras

Embora a regra de domínio exija `body` não vazio ou composto exclusivamente por espaços, os algoritmos devem possuir comportamento definido para evitar divisão por zero.

Para:

```text
wordCount = 0
```

devem ser utilizados:

```text
averageWordLength = 0.00
```

e, na análise de legibilidade:

```text
averageSentenceLength = 0.00
readabilityIndex = 0.00
```

Essa regra é defensiva e não substitui a validação do domínio.

---

# 11. Complexidade

Se:

```text
N = quantidade de caracteres
U = quantidade de tokens únicos
```

a tokenização, normalização e contagem possuem custo aproximadamente:

```text
O(N)
```

A ordenação dos vocabulários possui custo:

```text
O(U log U)
```

Portanto, o custo total é aproximadamente:

```text
O(N + U log U)
```

No pior caso, quando quase todos os tokens são diferentes:

```text
O(N log N)
```

Essa característica é aceitável para o benchmark.

Não deve ser introduzido processamento artificial para produzir um determinado tempo de execução.

---

# 12. Controle experimental do tempo de execução

A especificação anterior mencionava uma faixa aproximada de:

```text
80–330 ms
```

Essa faixa **não é uma regra funcional do algoritmo**.

Não é permitido utilizar:

```python
time.sleep(...)
```

ou loops artificiais destinados exclusivamente a aumentar a duração da execução.

A duração observada deve resultar de:

* tamanho do documento;
* quantidade de palavras;
* diversidade lexical;
* quantidade de sentenças;
* custo real dos algoritmos;
* recursos de CPU associados à configuração da Lambda.

Caso os documentos utilizados sejam pequenos demais para produzir duração mensurável na faixa desejada, deve-se ajustar o **dataset experimental**, e não artificialmente o algoritmo.

---

# 13. Constantes que devem permanecer fixas

Para garantir comparabilidade entre experimentos, permanecem constantes:

```text
TOKEN_PATTERN
SUPPORTED_LANGUAGES = {"en", "pt"}
TOP_K = 10
ENGLISH_STOPWORDS
PORTUGUESE_STOPWORDS
ENGLISH_VOWELS
PORTUGUESE_VOWELS
ENGLISH_READABILITY_FORMULA
PORTUGUESE_READABILITY_FORMULA
ROUNDING_MODE = ROUND_HALF_UP
DECIMAL_PLACES = 2
```

Esses elementos não podem ser modificados entre execuções que serão comparadas diretamente.

---

# 14. Regras de implementação

Uma implementação conforme deve:

1. utilizar somente módulos da biblioteca padrão do Python;
2. receber `body`, `language` e `tags`;
3. validar `language` contra `{"en", "pt"}`;
4. utilizar o tokenizer especificado;
5. utilizar `casefold()` na normalização;
6. preservar o `body` original;
7. utilizar as listas de stopwords exatamente como especificadas;
8. utilizar as fórmulas de legibilidade exatamente como especificadas;
9. utilizar a estimativa de sílabas exatamente como especificada;
10. aplicar o clamp de legibilidade obrigatoriamente;
11. arredondar métricas decimais para duas casas com `ROUND_HALF_UP`;
12. ordenar resultados de maneira determinística;
13. retornar exatamente os campos definidos;
14. não utilizar os resultados de outro algoritmo;
15. não acessar serviços externos;
16. não adicionar processamento artificial.

---

# 15. Contrato final dos três algoritmos

## Lexical

```text
Entrada:
    body
    language
    tags

Saída:
    wordCount
    uniqueWordCount
    characterCount
    averageWordLength
    frequencyDistribution
```

## Readability

```text
Entrada:
    body
    language
    tags

Saída:
    sentenceCount
    averageSentenceLength
    averageWordLength
    readabilityIndex
```

## Keywords

```text
Entrada:
    body
    language
    tags

Saída:
    topKeywords
    keywordFrequencies
```

---

# 16. Garantia de reprodutibilidade

A especificação é considerada fechada somente quando uma implementação não precisa tomar decisões adicionais sobre:

* tokenizer;
* normalização;
* definição de palavra;
* definição de sentença;
* idiomas suportados;
* stopwords;
* estimativa de sílabas;
* fórmula de legibilidade;
* limite do índice;
* arredondamento;
* desempate;
* quantidade de keywords;
* ordenação;
* tratamento de ausência de palavras.

Dessa forma, duas implementações conformes devem executar a mesma transformação determinística:

```text
(body, language, tags)
        │
        ├──────────────► Lexical
        │
        ├──────────────► Readability
        │
        └──────────────► Keywords
```

produzindo resultados funcionalmente equivalentes independentemente de a execução ocorrer como:

```text
uma única Lambda
```

ou como:

```text
Orchestrator
    ├── Lambda Lexical
    ├── Lambda Readability
    └── Lambda Keywords
```

A diferença observada entre essas arquiteturas poderá, portanto, ser atribuída à granularidade funcional, invocações adicionais, orquestração, inicialização e características de execução da AWS Lambda, e não a diferenças semânticas entre as implementações dos algoritmos.
