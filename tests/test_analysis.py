"""Testes unitarios das analises deterministicas em src/common/analysis.py."""

from common.analysis import (
    aggregate_analysis,
    keyword_analysis,
    lexical_analysis,
    readability_analysis,
)

BODY = "Este é um documento de teste. A análise é simples."


def test_lexical_analysis_input_conhecido():
    assert lexical_analysis(BODY, "pt", ["teste"]) == {
        "wordCount": 10,
        "uniqueWordCount": 9,
        "characterCount": 50,
        "averageWordLength": 3.9,
        "frequencyDistribution": {
            "a": 1,
            "análise": 1,
            "de": 1,
            "documento": 1,
            "este": 1,
            "é": 2,
            "simples": 1,
            "teste": 1,
            "um": 1,
        },
    }


def test_readability_analysis_input_conhecido():
    assert readability_analysis(BODY, "pt", ["teste"]) == {
        "sentenceCount": 2,
        "averageSentenceLength": 5.0,
        "averageWordLength": 3.9,
        "readabilityIndex": 71.6,
    }


def test_keyword_analysis_input_conhecido():
    assert keyword_analysis(BODY, "pt", ["teste"]) == {
        "topKeywords": ["análise", "documento", "simples", "teste"],
        "keywordFrequencies": {
            "análise": 1,
            "documento": 1,
            "simples": 1,
            "teste": 1,
        },
    }


def test_keyword_analysis_ingles():
    body = "This is a research paper about distributed systems and research methods."
    assert keyword_analysis(body, "en", []) == {
        "topKeywords": ["research", "distributed", "methods", "paper", "systems"],
        "keywordFrequencies": {
            "research": 2,
            "distributed": 1,
            "methods": 1,
            "paper": 1,
            "systems": 1,
        },
    }


def test_aggregate_analysis():
    result = aggregate_analysis(BODY, "pt", ["teste"])
    assert result == {
        "lexical": lexical_analysis(BODY, "pt", ["teste"]),
        "readability": readability_analysis(BODY, "pt", ["teste"]),
        "keywords": keyword_analysis(BODY, "pt", ["teste"]),
    }


def test_analises_sao_deterministicas():
    assert lexical_analysis(BODY, "pt", []) == lexical_analysis(BODY, "pt", [])
    assert readability_analysis(BODY, "pt", []) == readability_analysis(BODY, "pt", [])
    assert keyword_analysis(BODY, "pt", []) == keyword_analysis(BODY, "pt", [])


def test_entrada_sem_palavras():
    assert lexical_analysis("?!", "pt", []) == {
        "wordCount": 0,
        "uniqueWordCount": 0,
        "characterCount": 2,
        "averageWordLength": 0.0,
        "frequencyDistribution": {},
    }
    assert readability_analysis("?!", "pt", []) == {
        "sentenceCount": 0,
        "averageSentenceLength": 0.0,
        "averageWordLength": 0.0,
        "readabilityIndex": 100.0,
    }
    assert keyword_analysis("?!", "pt", []) == {
        "topKeywords": [],
        "keywordFrequencies": {},
    }