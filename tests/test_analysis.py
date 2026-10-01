r"""Testes das analises deterministicas conforme docs/analysis-algorithms.md.

Os valores esperados abaixo sao derivados da especificacao normativa
(nao da implementacao): tokenizacao [^\W_]+, normalizacao casefold(),
arredondamento Decimal + ROUND_HALF_UP, sentencas por [.!?]+(?:\s+|$),
estimativa de silabas por grupos de vogais, formulas FRE e stopwords fixas.
"""

import pytest

from common.analysis import (
    aggregate_analysis,
    keyword_analysis,
    lexical_analysis,
    readability_analysis,
)

BODY_PT = "Este é um documento de teste. A análise é simples."


# ---------------------------------------------------------------------------
# Analise lexical (secao 4 da especificacao)
# ---------------------------------------------------------------------------


def test_lexical_exemplo_44_da_especificacao():
    """Exemplo numerico da secao 4.4: 'Python is simple. Python is powerful.'"""
    assert lexical_analysis("Python is simple. Python is powerful.", "en", []) == {
        "wordCount": 6,
        "uniqueWordCount": 4,
        "characterCount": 37,
        "averageWordLength": 5.0,
        "frequencyDistribution": {
            "is": 2,
            "powerful": 1,
            "python": 2,
            "simple": 1,
        },
    }


def test_lexical_tokenizer_nao_trata_hifen_nem_apostrofo():
    """Seccao 3.3: hifen e apostrofo nao fazem parte do token."""
    result = lexical_analysis("don't state-of-the-art", "en", [])
    assert result["wordCount"] == 6
    assert result["characterCount"] == 22
    assert result["averageWordLength"] == pytest.approx(17 / 6, abs=0.01)
    assert result["frequencyDistribution"] == {
        "art": 1,
        "don": 1,
        "of": 1,
        "state": 1,
        "t": 1,
        "the": 1,
    }


def test_lexical_normalizacao_usa_casefold():
    """Seccao 3.4: normalizacao por casefold() e nao lower()."""
    assert lexical_analysis("Straße STRASSE Straße", "en", []) == {
        "wordCount": 3,
        "uniqueWordCount": 1,
        "characterCount": 21,
        "averageWordLength": pytest.approx(19 / 3, abs=0.01),
        "frequencyDistribution": {"strasse": 3},
    }


def test_lexical_arredondamento_round_half_up():
    """Seccao 3.5: media 1.335 arredonda para 1.34 (ROUND_HALF_UP)."""
    words = ["a"] * 133 + ["aa"] * 67
    result = lexical_analysis(" ".join(words), "pt", [])
    assert result["wordCount"] == 200
    assert result["averageWordLength"] == 1.34


def test_lexical_frequency_distribution_ordenada_lexicograficamente():
    """Seccao 4.5: chaves da distribuicao em ordem lexicografica."""
    result = lexical_analysis("Python is simple. Python is powerful.", "en", [])
    assert list(result["frequencyDistribution"]) == ["is", "powerful", "python", "simple"]


def test_lexical_sem_palavras():
    """Seccao 10: sem palavras, averageWordLength = 0.00."""
    assert lexical_analysis("?!", "pt", []) == {
        "wordCount": 0,
        "uniqueWordCount": 0,
        "characterCount": 2,
        "averageWordLength": 0.0,
        "frequencyDistribution": {},
    }


def test_lexical_portugues():
    """Tokenizacao, casefold e contagem em portugues."""
    assert lexical_analysis(BODY_PT, "pt", []) == {
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
            "simples": 1,
            "teste": 1,
            "um": 1,
            "é": 2,
        },
    }


# ---------------------------------------------------------------------------
# Analise de legibilidade (secao 5 da especificacao)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Hello. World.", 2),
        ("Hello! World?", 2),
        ("Hello.   World.", 2),
        ("Hello\nWorld", 1),
        ("Hello!World", 1),
        ("3.14 is a number.", 1),
    ],
)
def test_readability_segmentacao_de_sentencas(body, expected):
    """Seccao 5.2: pontuacao so encerra sentenca seguida de espaco ou fim."""
    assert readability_analysis(body, "en", [])["sentenceCount"] == expected


def test_readability_exemplo_completo_ingles():
    """Calculo completo: sentencas do preambulo com formula FRE em ingles."""
    assert readability_analysis("Hello. World.", "en", []) == {
        "sentenceCount": 2,
        "averageSentenceLength": 1.0,
        "averageWordLength": 5.0,
        "readabilityIndex": 78.92,
    }


def test_readability_silabas_en_aplica_ajuste_do_e_final():
    """Seccao 5.7.2: make -> 1 silaba (e final) e table -> 2 (termina em le)."""
    assert readability_analysis("make. table. simple.", "en", []) == {
        "sentenceCount": 3,
        "averageSentenceLength": 1.0,
        "averageWordLength": 5.0,
        "readabilityIndex": 64.82,
    }


def test_readability_silabas_pt_conta_grupos_de_vogais():
    """Seccao 5.7.1: casa possui 2 grupos de vogais -> 2 silabas."""
    assert readability_analysis("casa", "pt", []) == {
        "sentenceCount": 1,
        "averageSentenceLength": 1.0,
        "averageWordLength": 4.0,
        "readabilityIndex": 78.62,
    }


def test_readability_portugues():
    """Formula FRE adaptada para portugues (248.835) sobre o corpo de teste."""
    assert readability_analysis(BODY_PT, "pt", []) == {
        "sentenceCount": 2,
        "averageSentenceLength": 5.0,
        "averageWordLength": 3.9,
        "readabilityIndex": 83.02,
    }


def test_readability_indice_limitado_a_100():
    """Seccao 5.9: indice superior a 100 e limitado a 100."""
    assert readability_analysis("a b c d e.", "en", [])["readabilityIndex"] == 100.0


def test_readability_indice_limitado_a_0():
    """Seccao 5.9: indice inferior a 0 e limitado a 0."""
    long_body = " ".join(["entities"] * 30)
    assert readability_analysis(long_body, "en", [])["readabilityIndex"] == 0.0


def test_readability_sem_palavras():
    """Seccao 10: sem palavras, metricas zeradas."""
    assert readability_analysis("?!", "pt", []) == {
        "sentenceCount": 0,
        "averageSentenceLength": 0.0,
        "averageWordLength": 0.0,
        "readabilityIndex": 0.0,
    }


# ---------------------------------------------------------------------------
# Extracao de palavras-chave (secao 6 da especificacao)
# ---------------------------------------------------------------------------


def test_keywords_remove_tokens_numericos():
    """Seccao 6.4: tokens exclusivamente numericos nao sao palavras-chave."""
    body = "Python 2026 is better than 1998 but Python 3.12 rules"
    assert keyword_analysis(body, "en", []) == {
        "topKeywords": ["python", "better", "rules"],
        "keywordFrequencies": {"python": 2, "better": 1, "rules": 1},
    }


def test_keywords_ordenacao_por_frequencia_e_desempate_lexicografico():
    """Seccoes 6.7: frequencia decrescente e empate por ordem lexicografica."""
    body = " ".join(["python"] * 5 + ["aws"] * 5 + ["lambda"] * 3)
    assert keyword_analysis(body, "en", []) == {
        "topKeywords": ["aws", "python", "lambda"],
        "keywordFrequencies": {"aws": 5, "python": 5, "lambda": 3},
    }


def test_keywords_respeita_o_limite_top_k():
    """Seccao 6.8: no maximo TOP_K = 10 palavras-chave."""
    body = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    result = keyword_analysis(body, "en", [])
    assert len(result["topKeywords"]) == 10
    assert result["topKeywords"] == [
        "alpha", "beta", "delta", "epsilon", "eta",
        "gamma", "iota", "kappa", "lambda", "mu",
    ]


def test_keywords_frequencias_seguem_a_ordem_dos_top_keywords():
    """Seccao 6.9: chaves de keywordFrequencies na mesma ordem de topKeywords."""
    result = keyword_analysis(
        " ".join(["python"] * 5 + ["aws"] * 5 + ["lambda"] * 3), "en", []
    )
    assert list(result["keywordFrequencies"]) == result["topKeywords"]


def test_keywords_tokenizer_hifen_e_apostrofo():
    """Seccao 6.2: tokenizacao padrao divide hifen e apostrofo."""
    assert keyword_analysis("state-of-the-art don't", "en", []) == {
        "topKeywords": ["art", "don", "state", "t"],
        "keywordFrequencies": {"art": 1, "don": 1, "state": 1, "t": 1},
    }


def test_keywords_ingles():
    """Stopwords em ingles removem palavras funcionais."""
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


def test_keywords_portugues():
    """Stopwords em portugues removem palavras funcionais."""
    assert keyword_analysis(BODY_PT, "pt", []) == {
        "topKeywords": ["análise", "documento", "simples", "teste"],
        "keywordFrequencies": {
            "análise": 1,
            "documento": 1,
            "simples": 1,
            "teste": 1,
        },
    }


def test_keywords_sem_palavras():
    """Seccao 10: sem tokens, listas vazias."""
    assert keyword_analysis("?!", "pt", []) == {
        "topKeywords": [],
        "keywordFrequencies": {},
    }


# ---------------------------------------------------------------------------
# Propriedades comuns (secoes 2 e 9 da especificacao)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn",
    [
        lambda body: lexical_analysis(body, "es", []),
        lambda body: readability_analysis(body, "es", []),
        lambda body: keyword_analysis(body, "es", []),
    ],
)
def test_idioma_invalido_e_rejeitado(fn):
    """Seccao 3.1: apenas en/pt sao aceitos; nao ha fallback."""
    with pytest.raises(ValueError):
        fn("Este é um documento de teste.")


def test_tags_sao_ignoradas_no_calculo():
    """Seccao 2.1: tags fazem parte do contrato mas nao alteram o resultado."""
    body = "Python is simple. Python is powerful."
    assert lexical_analysis(body, "en", ["x", "y"]) == lexical_analysis(body, "en", [])
    assert readability_analysis(body, "en", ["x", "y"]) == readability_analysis(
        body, "en", []
    )
    assert keyword_analysis(body, "en", ["python", "aws"]) == keyword_analysis(
        body, "en", []
    )


def test_analises_sao_deterministicas():
    """Seccao 9: mesma entrada produz sempre a mesma saida."""
    assert lexical_analysis(BODY_PT, "pt", []) == lexical_analysis(BODY_PT, "pt", [])
    assert readability_analysis(BODY_PT, "pt", []) == readability_analysis(
        BODY_PT, "pt", []
    )
    assert keyword_analysis(BODY_PT, "pt", []) == keyword_analysis(BODY_PT, "pt", [])


def test_aggregate_analysis_agrupa_as_tres_analises():
    """Seccao 8: agregacao combina os tres resultados independentes."""
    assert aggregate_analysis(BODY_PT, "pt", ["teste"]) == {
        "lexical": lexical_analysis(BODY_PT, "pt", ["teste"]),
        "readability": readability_analysis(BODY_PT, "pt", ["teste"]),
        "keywords": keyword_analysis(BODY_PT, "pt", ["teste"]),
    }