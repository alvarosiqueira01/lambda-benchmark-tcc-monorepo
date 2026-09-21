"""Testes da Lambda orquestradora com FakeLambdaClient e FakeDynamoDB."""

import json

import pytest
from conftest import FakeDynamoDB

from app.repositories.document_repository import DocumentRepository
from app.services.document_service import DocumentService
from common.analysis import aggregate_analysis
from decomposed import orchestrator

BODY = "Este é um documento de teste. A análise é simples."
VALID_PAYLOAD = {"title": "Meu Documento", "body": BODY, "language": "pt", "tags": ["teste"]}


@pytest.fixture
def env(monkeypatch, function_names):
    svc = DocumentService(DocumentRepository(table_name="Documents", client=FakeDynamoDB()))
    document = svc.create_document(VALID_PAYLOAD)
    monkeypatch.setattr(orchestrator, "_service", svc)
    monkeypatch.setattr(orchestrator, "_invoker", None)
    return document


@pytest.fixture
def set_invoker(monkeypatch, invoker_with, function_names):
    def _apply(results):
        invoker = invoker_with(results)
        monkeypatch.setattr(orchestrator, "_invoker", invoker)
        return invoker

    return _apply


def _event(method="GET", path="/documents/x/analysis") -> dict:
    return {"httpMethod": method, "path": path, "body": None}


def test_orquestrador_agrega_tres_resultados(env, set_invoker, function_names):
    results = aggregate_analysis(BODY, "pt", ["teste"])
    expected = {
        "fn-lexical": results["lexical"],
        "fn-readability": results["readability"],
        "fn-keywords": results["keywords"],
    }
    invoker = set_invoker(expected)

    response = orchestrator.handler(_event(path=f"/documents/{env.id}/analysis"), None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {
        "lexical": results["lexical"],
        "readability": results["readability"],
        "keywords": results["keywords"],
    }
    assert sorted(call["FunctionName"] for call in invoker._client.calls) == sorted(expected)


def test_orquestrador_payload_contem_apenas_body_language_tags(env, set_invoker, function_names):
    results = aggregate_analysis(BODY, "pt", ["teste"])
    invoker = set_invoker(
        {name: results[key] for name, key in zip(function_names.values(), results)}
    )

    orchestrator.handler(_event(path=f"/documents/{env.id}/analysis"), None)

    for call in invoker._client.calls:
        assert json.loads(call["Payload"]) == {"body": BODY, "language": "pt", "tags": ["teste"]}


def test_orquestrador_documento_inexistente_retorna_404(env, set_invoker, function_names):
    set_invoker({})
    response = orchestrator.handler(_event(path="/documents/nao-existe/analysis"), None)
    assert response["statusCode"] == 404
    assert json.loads(response["body"]) == {"error": "DOCUMENT_NOT_FOUND", "message": "document not found"}


def test_orquestrador_falha_na_invocacao_retorna_500_analysis_error(env, monkeypatch, invoker_with, function_names):
    invoker = invoker_with({})
    monkeypatch.setattr(orchestrator, "_invoker", invoker)

    def failing_invoke_all(*args, **kwargs):
        raise RuntimeError("falha na lambda")

    invoker.invoke_all = failing_invoke_all

    response = orchestrator.handler(_event(path=f"/documents/{env.id}/analysis"), None)

    assert response["statusCode"] == 500
    assert json.loads(response["body"]) == {"error": "ANALYSIS_ERROR", "message": "document analysis failed"}


def test_orquestrador_rota_diferente_retorna_404(env, set_invoker, function_names):
    set_invoker({})
    response = orchestrator.handler(_event(method="POST", path="/documents"), None)
    assert response["statusCode"] == 404
    assert json.loads(response["body"]) == {"error": "NOT_FOUND", "message": "route not found"}


def test_orquestrador_erro_da_lambda_retorna_500_analysis_error(env, set_invoker, function_names):
    results = aggregate_analysis(BODY, "pt", ["teste"])
    results["keywords"] = {"errorMessage": "falhou"}
    expected = {
        "fn-lexical": results["lexical"],
        "fn-readability": results["readability"],
        "fn-keywords": results["keywords"],
    }
    set_invoker(expected)

    response = orchestrator.handler(_event(path=f"/documents/{env.id}/analysis"), None)

    assert response["statusCode"] == 500
    assert json.loads(response["body"]) == {"error": "ANALYSIS_ERROR", "message": "document analysis failed"}