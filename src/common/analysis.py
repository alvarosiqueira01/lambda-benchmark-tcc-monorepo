"""Analises deterministicas da entidade Document.

Baseado exclusivamente no pseudocodigo definido em docs/research-scope.md
e docs/requirements.md:

    A(document) =
        Lexical(document.body, document.language, document.tags)
        Readability(document.body, document.language, document.tags)
        Keywords(document.body, document.language, document.tags)

Cada analise e uma funcao pura: recebe (body, language, tags), nao depende
do resultado das outras e usa somente a biblioteca padrao do Python.

Regras fixas do experimento (nao podem variar entre arquiteturas):
- tokenizacao: sequencias de caracteres de palavra (letras/digitos/_);
- normalizacao: lowercase;
- stopwords: conjuntos fixos por idioma em _STOPWORDS;
- ordenacao de keywords: frequencia decrescente, depois lexicografica;
- formula de legibilidade fixa (ver readability_analysis).
"""

from __future__ import annotations

import re
from collections import Counter

_TOP_KEYWORDS = 10

_WORD_RE = re.compile(r"\w+")
_SENTENCE_RE = re.compile(r"[.!?]+")

# Conjuntos de stopwords fixos para o experimento.
_STOPWORDS = {
    "pt": frozenset(
        {
            "a", "à", "ao", "aos", "as", "às", "até", "com", "como",
            "da", "das", "de", "do", "dos",
            "e", "é", "em", "na", "nas", "no", "nos", "o", "os",
            "que", "se", "sem", "ser", "são",
            "um", "uma", "umas", "uns",
            "eu", "tu", "ele", "ela", "eles", "elas", "nós", "você",
            "me", "te", "lhe", "vos",
            "este", "esta", "estes", "estas", "esse", "essa", "esses", "essas",
            "isso", "isto", "mesmo", "mesma",
            "mais", "menos", "muito", "pouco",
            "não", "já", "agora", "ainda", "também",
            "quando", "onde", "porque", "porém",
            "para", "por",
            "sobre", "entre", "contra", "desde", "depois", "antes", "durante",
            "está", "foi", "há", "tem", "ter", "têm",
        }
    ),
    "en": frozenset(
        {
            "a", "an", "about", "and", "are", "as", "at", "be", "been", "but", "by",
            "can", "could", "did", "do", "does", "for", "from", "had", "has",
            "have", "he", "her", "his", "i", "if", "in", "into", "is", "it",
            "its", "me", "my", "no", "not", "of", "on", "or", "our", "she",
            "so", "than", "that", "the", "their", "them", "then", "there",
            "these", "they", "this", "those", "to", "us", "was", "we", "were",
            "what", "when", "which", "who", "will", "with", "you", "your",
        }
    ),
}


def _tokenize(text: str) -> list[str]:
    """Tokeniza o texto e normaliza cada token para lowercase."""
    return [token.lower() for token in _WORD_RE.findall(text)]


def _average_word_length(tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    return round(sum(len(token) for token in tokens) / len(tokens), 2)


def lexical_analysis(body: str, language: str, tags: list[str]) -> dict:
    """Estatisticas lexicais: wordCount, uniqueWordCount, characterCount,
    averageWordLength e frequencyDistribution."""
    tokens = _tokenize(body)
    frequencies = Counter(tokens)
    return {
        "wordCount": len(tokens),
        "uniqueWordCount": len(frequencies),
        "characterCount": len(body),
        "averageWordLength": _average_word_length(tokens),
        "frequencyDistribution": dict(sorted(frequencies.items())),
    }


def readability_analysis(body: str, language: str, tags: list[str]) -> dict:
    """Metricas de legibilidade: sentenceCount, averageSentenceLength,
    averageWordLength e readabilityIndex.

    Formula fixa do experimento:
        score = averageSentenceLength + 6.0 * averageWordLength
        readabilityIndex = max(0, 100 - score)
    """
    sentences = [sentence.strip() for sentence in _SENTENCE_RE.split(body) if sentence.strip()]
    tokens = _tokenize(body)
    sentence_count = len(sentences)
    average_sentence_length = round(len(tokens) / max(1, sentence_count), 2)
    average_word_length = _average_word_length(tokens)
    score = average_sentence_length + 6.0 * average_word_length
    readability_index = round(max(0.0, 100.0 - score), 2)
    return {
        "sentenceCount": sentence_count,
        "averageSentenceLength": average_sentence_length,
        "averageWordLength": average_word_length,
        "readabilityIndex": readability_index,
    }


def keyword_analysis(body: str, language: str, tags: list[str]) -> dict:
    """Extracao de palavras-chave: tokenizacao, normalizacao, remocao de
    stopwords, contagem e ordenacao deterministica (frequencia desc,
    depois palavra asc)."""
    stopwords = _STOPWORDS.get(language, frozenset())
    tokens = [token for token in _tokenize(body) if token not in stopwords]
    frequencies = Counter(tokens)
    ranked = sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))
    top = ranked[:_TOP_KEYWORDS]
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