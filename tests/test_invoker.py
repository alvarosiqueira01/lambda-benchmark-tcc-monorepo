"""Testes do AnalysisInvoker com FakeLambdaClient."""

import json

import pytest
from conftest import FakeLambdaClient

from decomposed.invoker import AnalysisInvocationError, AnalysisInvoker

BODY = "Este é um documento de teste."


def test_invoke_chama_funcao_correta_e_retorna_resultado(function_names):
    client = FakeLambdaClient({"fn-lexical": {"wordCount": 5}})
    invoker = AnalysisInvoker(client=client, function_names=function_names)

    result = invoker.invoke("lexical", BODY, "pt", ["teste"])

    assert result == {"wordCount": 5}
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["FunctionName"] == "fn-lexical"
    assert call["InvocationType"] == "RequestResponse"
    assert json.loads(call["Payload"]) == {"body": BODY, "language": "pt", "tags": ["teste"]}


def test_invoke_all_invoca_tres_funcoes_uma_vez_cada(function_names):
    results = {
        "fn-lexical": {"wordCount": 5},
        "fn-readability": {"sentenceCount": 1},
        "fn-keywords": {"topKeywords": ["teste"]},
    }
    client = FakeLambdaClient(results)
    invoker = AnalysisInvoker(client=client, function_names=function_names)

    result = invoker.invoke_all(BODY, "pt", ["teste"])

    assert result == {
        "lexical": {"wordCount": 5},
        "readability": {"sentenceCount": 1},
        "keywords": {"topKeywords": ["teste"]},
    }
    function_names_used = [call["FunctionName"] for call in client.calls]
    assert sorted(function_names_used) == sorted(results)
    for call in client.calls:
        assert json.loads(call["Payload"]) == {"body": BODY, "language": "pt", "tags": ["teste"]}


def test_invoker_usa_nomes_de_variaveis_de_ambiente(monkeypatch, function_names):
    monkeypatch.setenv("LEXICAL_FUNCTION_NAME", "fn-lexical")
    monkeypatch.setenv("READABILITY_FUNCTION_NAME", "fn-readability")
    monkeypatch.setenv("KEYWORDS_FUNCTION_NAME", "fn-keywords")
    client = FakeLambdaClient({"fn-lexical": {}, "fn-readability": {}, "fn-keywords": {}})
    invoker = AnalysisInvoker(client=client)
    assert invoker.invoke("readability", BODY, "pt", []) == {}


def test_invoker_sem_variaveis_de_ambiente_falha(monkeypatch):
    monkeypatch.delenv("LEXICAL_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("READABILITY_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("KEYWORDS_FUNCTION_NAME", raising=False)
    with pytest.raises(KeyError):
        AnalysisInvoker(client=FakeLambdaClient({}))


def test_invoke_com_error_message_no_payload_e_erro(function_names):
    client = FakeLambdaClient({"fn-lexical": {"errorType": "RuntimeError", "errorMessage": "boom"}})
    invoker = AnalysisInvoker(client=client, function_names=function_names)

    with pytest.raises(AnalysisInvocationError) as excinfo:
        invoker.invoke("lexical", BODY, "pt", ["teste"])

    assert excinfo.value.analysis == "lexical"
    assert excinfo.value.error_message == "boom"


def test_invoke_all_propaga_erro_da_lambda(function_names):
    results = {
        "fn-lexical": {"wordCount": 5},
        "fn-readability": {"sentenceCount": 1},
        "fn-keywords": {"errorMessage": "falhou"},
    }
    invoker = AnalysisInvoker(client=FakeLambdaClient(results), function_names=function_names)

    with pytest.raises(AnalysisInvocationError):
        invoker.invoke_all(BODY, "pt", ["teste"])