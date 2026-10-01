r"""Analises deterministicas da entidade Document.

Especificacao normativa: docs/analysis-algorithms.md.

Contrato comum das tres analises:
    (body: str, language: str, tags: list[str]) -> dict

Cada analise e uma funcao pura: recebe (body, language, tags), nao depende
do resultado das outras e usa somente a biblioteca padrao do Python.

Regras fixas do experimento (nao podem variar entre arquiteturas):
- tokenizacao: re.findall(r"[^\W_]+", body, re.UNICODE);
- normalizacao: token.casefold();
- valores decimais: arredondados como Decimal + ROUND_HALF_UP;
- sentencas: split em [.!?]+(?:\s+|$) com remocao de partes vazias;
- silabas: grupos consecutivos de vogais por idioma;
- formulas FRE: 206.835 (en) ou 248.835 (pt) - 1.015*ASL - 84.6*ASW,
  com clamp obrigatorio em [0, 100];
- stopwords: listas oficiais do benchmark por idioma;
- keywords: remocao de tokens numericos e TOP_K = 10.
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

TOKEN_PATTERN = r"[^\W_]+"
SUPPORTED_LANGUAGES = frozenset({"en", "pt"})
TOP_K = 10
DECIMAL_PLACES = 2
ROUNDING_MODE = ROUND_HALF_UP

# Vogais para a estimativa de silabas (5.7 do documento normativo).
ENGLISH_VOWELS = frozenset("aeiouy")
PORTUGUESE_VOWELS = frozenset("aeiouáéíóúâêôãõàü")

# Formula FRE: (constante, coeficiente ASL, coeficiente ASW).
ENGLISH_READABILITY_FORMULA = (
    Decimal("206.835"),
    Decimal("1.015"),
    Decimal("84.6"),
)
PORTUGUESE_READABILITY_FORMULA = (
    Decimal("248.835"),
    Decimal("1.015"),
    Decimal("84.6"),
)

# Listas oficiais de stopwords do benchmark (6.5 do documento normativo).
ENGLISH_STOPWORDS = frozenset(
    {
        "a", "about", "above", "after", "again", "against", "all", "am",
        "an", "and", "any", "are", "as", "at",
        "be", "because", "been", "before", "being", "below", "between",
        "both", "but", "by",
        "can", "could",
        "did", "do", "does", "doing", "down", "during",
        "each",
        "few", "for", "from", "further",
        "had", "has", "have", "having", "he", "her", "here", "hers",
        "herself", "him", "himself", "his", "how",
        "i", "if", "in", "into", "is", "it", "its", "itself",
        "just",
        "me", "more", "most", "my", "myself",
        "no", "nor", "not", "now",
        "of", "off", "on", "once", "only", "or", "other", "our", "ours",
        "ourselves", "out", "over", "own",
        "same", "she", "should", "so", "some", "such",
        "than", "that", "the", "their", "theirs", "them", "themselves",
        "then", "there", "these", "they", "this", "those", "through",
        "to", "too",
        "under", "until", "up",
        "very",
        "was", "we", "were", "what", "when", "where", "which", "while",
        "who", "whom", "why", "will", "with",
        "you", "your", "yours", "yourself", "yourselves",
    }
)

PORTUGUESE_STOPWORDS = frozenset(
    {
        "a", "à", "às", "ao", "aos",
        "aquela", "aquelas", "aquele", "aqueles", "aquilo",
        "as", "até",
        "com", "como",
        "da", "das", "de", "dela", "delas", "dele", "deles", "depois",
        "do", "dos",
        "e", "é", "ela", "elas", "ele", "eles", "em", "entre",
        "era", "eram", "essa", "essas", "esse", "esses", "esta", "estas",
        "este", "estes", "eu",
        "foi", "foram",
        "há",
        "isso", "isto",
        "já",
        "lhe", "lhes",
        "mais", "mas", "me", "mesmo", "meu", "meus", "minha", "minhas",
        "na", "nas", "não", "nem", "no", "nos", "nós", "num", "numa",
        "o", "os", "ou", "outra", "outras", "outro", "outros",
        "para", "pela", "pelas", "pelo", "pelos", "por",
        "qual", "quando", "que", "quem",
        "se", "sem", "ser", "seu", "seus", "só", "sua", "suas",
        "também", "te", "tem", "têm", "tendo", "ter", "teu", "teus",
        "toda", "todas", "todo", "todos", "tu",
        "um", "uma", "umas", "uns",
        "vai", "vão", "você", "vocês",
    }
)

_QUANTUM = Decimal("0.01")
_TOKEN_RE = re.compile(TOKEN_PATTERN)
_SENTENCE_RE = re.compile(r"[.!?]+(?:\s+|$)")


def _validate_language(language: str) -> None:
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported language: {language!r}")


def _tokenize(text: str) -> list[str]:
    """Tokeniza com o padrao oficial (3.3), sem normalizar."""
    return _TOKEN_RE.findall(text)


def _round_half_up(value: Decimal) -> Decimal:
    return value.quantize(_QUANTUM, rounding=ROUNDING_MODE)


def _divide(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal(0)
    return Decimal(numerator) / Decimal(denominator)


def _estimate_syllables(token: str, language: str) -> int:
    """Estimativa deterministica de silabas por grupos de vogais (5.7)."""
    normalized = token.casefold()
    vowels = ENGLISH_VOWELS if language == "en" else PORTUGUESE_VOWELS
    syllables = 0
    in_group = False
    for char in normalized:
        is_vowel = char in vowels
        if is_vowel and not in_group:
            syllables += 1
        in_group = is_vowel
    syllables = max(syllables, 1)
    if (
        language == "en"
        and syllables > 1
        and normalized.endswith("e")
        and not normalized.endswith("le")
    ):
        syllables -= 1
    return syllables


def lexical_analysis(body: str, language: str, tags: list[str]) -> dict:
    """Estatisticas lexicais: wordCount, uniqueWordCount, characterCount,
    averageWordLength e frequencyDistribution (4.4)."""
    _validate_language(language)
    tokens = _tokenize(body)
    frequencies = Counter(token.casefold() for token in tokens)
    total_characters = sum(len(token) for token in tokens)
    return {
        "wordCount": len(tokens),
        "uniqueWordCount": len(frequencies),
        "characterCount": len(body),
        "averageWordLength": float(
            _round_half_up(_divide(total_characters, len(tokens)))
        ),
        "frequencyDistribution": {
            word: frequencies[word] for word in sorted(frequencies)
        },
    }


def readability_analysis(body: str, language: str, tags: list[str]) -> dict:
    """Metricas de legibilidade: sentenceCount, averageSentenceLength,
    averageWordLength e readabilityIndex (5.4 a 5.10)."""
    _validate_language(language)
    text = body.strip()
    sentences = [
        part for part in _SENTENCE_RE.split(text) if part.strip()
    ]
    sentence_count = len(sentences)

    tokens = _tokenize(body)
    word_count = len(tokens)
    total_characters = sum(len(token) for token in tokens)

    average_word_length = float(
        _round_half_up(_divide(total_characters, word_count))
    )
    average_sentence_length = float(
        _round_half_up(_divide(word_count, sentence_count))
    )

    total_syllables = sum(_estimate_syllables(token, language) for token in tokens)

    if word_count > 0 and sentence_count > 0:
        asl = _divide(word_count, sentence_count)
        asw = _divide(total_syllables, word_count)
        base, asl_coef, asw_coef = (
            ENGLISH_READABILITY_FORMULA
            if language == "en"
            else PORTUGUESE_READABILITY_FORMULA
        )
        index = base - asl_coef * asl - asw_coef * asw
        index = max(Decimal(0), min(index, Decimal(100)))
    else:
        index = Decimal(0)

    return {
        "sentenceCount": sentence_count,
        "averageSentenceLength": average_sentence_length,
        "averageWordLength": average_word_length,
        "readabilityIndex": float(_round_half_up(index)),
    }


def keyword_analysis(body: str, language: str, tags: list[str]) -> dict:
    """Extracao de palavras-chave: tokenizacao, normalizacao, remocao de
    numeros e stopwords, contagem e selecao dos TOP_K (6.2 a 6.8)."""
    _validate_language(language)
    stopwords = (
        ENGLISH_STOPWORDS if language == "en" else PORTUGUESE_STOPWORDS
    )
    frequencies: Counter[str] = Counter()
    for token in _tokenize(body):
        normalized = token.casefold()
        if normalized.isnumeric():
            continue
        if normalized in stopwords:
            continue
        frequencies[normalized] += 1

    ranked = sorted(
        frequencies.items(),
        key=lambda item: (-item[1], item[0]),
    )
    top = ranked[:TOP_K]
    return {
        "topKeywords": [word for word, _count in top],
        "keywordFrequencies": dict(top),
    }


def aggregate_analysis(body: str, language: str, tags: list[str]) -> dict:
    """Executa as tres analises independentes e agrupa o resultado."""
    return {
        "lexical": lexical_analysis(body, language, tags),
        "readability": readability_analysis(body, language, tags),
        "keywords": keyword_analysis(body, language, tags),
    }