"""Testes do handler CRUD decomposto (comportamento identico ao monolitico)."""

import json

import pytest
from conftest import FakeDynamoDB

from app.repositories.document_repository import DocumentRepository
from app.services.document_service import DocumentService
from decomposed import crud_handler

BODY = "Este é um documento de teste."
VALID_PAYLOAD = {"title": "Meu Documento", "body": BODY, "language": "pt", "tags": ["teste"]}


@pytest.fixture
def handler(monkeypatch) -> crud_handler.handler:
    service = DocumentService(DocumentRepository(table_name="Documents", client=FakeDynamoDB()))
    monkeypatch.setattr(crud_handler, "_service", service)
    return crud_handler.handler


def _event(method: str, path: str, body=None, http_api_v2: bool = False) -> dict:
    raw_body = json.dumps(body) if body is not None else None
    if http_api_v2:
        return {
            "version": "2.0",
            "routeKey": f"{method} {path}",
            "rawPath": path,
            "body": raw_body,
            "requestContext": {"http": {"method": method, "path": path}},
        }
    return {"httpMethod": method, "path": path, "body": raw_body, "headers": {}}


def _created_id(handler, method="POST", path="/documents", body=VALID_PAYLOAD) -> str:
    response = handler(_event(method, path, body), None)
    return json.loads(response["body"])["id"]


def test_post_cria_documento(handler):
    response = handler(_event("POST", "/documents", VALID_PAYLOAD), None)
    assert response["statusCode"] == 201
    created = json.loads(response["body"])
    assert set(created) == {"id", "title", "body", "language", "tags"}
    assert created["body"] == BODY


def test_post_json_invalido_retorna_400(handler):
    event = _event("POST", "/documents", VALID_PAYLOAD)
    event["body"] = "{nao e json"
    response = handler(event, None)
    assert response["statusCode"] == 400


def test_post_sem_corpo_retorna_400(handler):
    response = handler(_event("POST", "/documents"), None)
    assert response["statusCode"] == 400


def test_post_dados_semanticamente_invalidos_retorna_422(handler):
    response = handler(_event("POST", "/documents", {"title": "T", "body": "   ", "language": "pt", "tags": []}), None)
    assert response["statusCode"] == 422


def test_get_documento_existente(handler):
    document_id = _created_id(handler)
    response = handler(_event("GET", f"/documents/{document_id}"), None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["id"] == document_id


def test_get_documento_inexistente_retorna_404(handler):
    response = handler(_event("GET", "/documents/nao-existe"), None)
    assert response["statusCode"] == 404
    assert json.loads(response["body"]) == {"error": "DOCUMENT_NOT_FOUND", "message": "document not found"}


def test_put_atualiza_documento_e_preserva_id(handler):
    document_id = _created_id(handler)
    updated = {"title": "Novo Título", "body": "Conteúdo atualizado.", "language": "pt", "tags": ["atualizado"]}
    response = handler(_event("PUT", f"/documents/{document_id}", updated), None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"id": document_id, **updated}


def test_put_documento_inexistente_retorna_404(handler):
    response = handler(_event("PUT", "/documents/nao-existe", VALID_PAYLOAD), None)
    assert response["statusCode"] == 404


def test_delete_remove_documento(handler):
    document_id = _created_id(handler)
    response = handler(_event("DELETE", f"/documents/{document_id}"), None)
    assert response["statusCode"] == 204
    deleted = handler(_event("GET", f"/documents/{document_id}"), None)
    assert deleted["statusCode"] == 404


def test_rota_desconhecida_retorna_404(handler):
    response = handler(_event("GET", "/documents"), None)
    assert response["statusCode"] == 404
    assert json.loads(response["body"]) == {"error": "NOT_FOUND", "message": "route not found"}