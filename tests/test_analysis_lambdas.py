"""Testes dos handlers das Lambdas especializadas de analise."""

import pytest

from common.analysis import keyword_analysis, lexical_analysis, readability_analysis
from decomposed.analyses import keywords, lexical, readability

BODY = "Este é um documento de teste. A análise é simples."
INPUT = {"body": BODY, "language": "pt", "tags": ["teste"]}


@pytest.mark.parametrize(
    ("handler", "pure_function"),
    [
        (lexical.handler, lexical_analysis),
        (readability.handler, readability_analysis),
        (keywords.handler, keyword_analysis),
    ],
)
def test_handler_devolve_resultado_da_funcao_pura(handler, pure_function):
    assert handler(INPUT, None) == pure_function(BODY, "pt", ["teste"])