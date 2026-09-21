"""Testes unitarios do DocumentService."""

import pytest

from app.repositories.document_repository import DocumentNotFoundError
from app.services.document_service import DocumentService
from app.validation import ValidationError
from common.analysis import aggregate_analysis

BODY = "Este é um documento de teste. A análise é simples."
VALID_PAYLOAD = {"title": "Meu Documento", "body": BODY, "language": "pt", "tags": ["teste"]}


def test_create_documento_valida_e_atribui_id(repository):
    service = DocumentService(repository)
    document = service.create_document(VALID_PAYLOAD)
    assert document.id
    assert service.get_document(document.id) == document


def test_create_documento_invalido_levanta_validation_error(repository):
    service = DocumentService(repository)
    with pytest.raises(ValidationError):
        service.create_document({"title": "T", "body": "   ", "language": "pt", "tags": []})


def test_get_documento_inexistente_levanta_not_found(repository):
    service = DocumentService(repository)
    with pytest.raises(DocumentNotFoundError):
        service.get_document("nao-existe")


def test_update_documento_preserva_id_e_valida(repository):
    service = DocumentService(repository)
    created = service.create_document(VALID_PAYLOAD)
    updated = service.update_document(created.id, {"title": "Novo", "body": "Novo.", "language": "en", "tags": ["x"]})
    assert updated.id == created.id
    assert service.get_document(created.id) == updated


def test_update_documento_invalido_nao_altera_registro(repository):
    service = DocumentService(repository)
    created = service.create_document(VALID_PAYLOAD)
    with pytest.raises(ValidationError):
        service.update_document(created.id, {"title": "T", "body": "", "language": "pt", "tags": []})
    assert service.get_document(created.id) == created


def test_update_documento_inexistente_levanta_not_found(repository):
    service = DocumentService(repository)
    with pytest.raises(DocumentNotFoundError):
        service.update_document("nao-existe", VALID_PAYLOAD)


def test_delete_documento(repository):
    service = DocumentService(repository)
    created = service.create_document(VALID_PAYLOAD)
    service.delete_document(created.id)
    with pytest.raises(DocumentNotFoundError):
        service.get_document(created.id)


def test_analyze_documento_executa_tres_analises(repository):
    service = DocumentService(repository)
    created = service.create_document(VALID_PAYLOAD)
    result = service.analyze_document(created.id)
    assert result == aggregate_analysis(BODY, "pt", ["teste"])
    assert set(result) == {"lexical", "readability", "keywords"}


def test_analyze_documento_nao_modifica_documento(repository):
    service = DocumentService(repository)
    created = service.create_document(VALID_PAYLOAD)
    service.analyze_document(created.id)
    assert service.get_document(created.id) == created


def test_analyze_documento_inexistente_levanta_not_found(repository):
    service = DocumentService(repository)
    with pytest.raises(DocumentNotFoundError):
        service.analyze_document("nao-existe")