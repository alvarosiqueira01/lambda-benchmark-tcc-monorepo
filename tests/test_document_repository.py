"""Testes unitarios do DocumentRepository com cliente DynamoDB fake."""

import pytest
from conftest import FakeDynamoDB

from app.models import Document
from app.repositories.document_repository import (
    DocumentAlreadyExistsError,
    DocumentNotFoundError,
    DocumentRepository,
)


@pytest.fixture
def repository() -> DocumentRepository:
    return DocumentRepository(table_name="Documents", client=FakeDynamoDB())


def _document(document_id: str = "doc-1") -> Document:
    return Document(id=document_id, title="Meu Documento", body="Olá, Mundo!", language="pt", tags=["AWS", "aws"])


def test_create_persiste_documento(repository):
    doc = _document()
    assert repository.create(doc) is doc
    assert repository.get("doc-1") == doc


def test_create_duplicado_levanta_already_exists(repository):
    repository.create(_document())
    with pytest.raises(DocumentAlreadyExistsError):
        repository.create(_document())


def test_get_documento_inexistente_levanta_not_found(repository):
    with pytest.raises(DocumentNotFoundError):
        repository.get("nao-existe")


def test_update_substitui_conteudo_e_preserva_id(repository):
    repository.create(_document())
    updated = Document(id="doc-1", title="Novo Título", body="Conteúdo atualizado.", language="pt", tags=["atualizado"])
    assert repository.update(updated) == updated
    assert repository.get("doc-1") == updated


def test_update_documento_inexistente_levanta_not_found(repository):
    with pytest.raises(DocumentNotFoundError):
        repository.update(_document())


def test_delete_remove_documento(repository):
    repository.create(_document())
    repository.delete("doc-1")
    with pytest.raises(DocumentNotFoundError):
        repository.get("doc-1")


def test_delete_documento_inexistente_levanta_not_found(repository):
    with pytest.raises(DocumentNotFoundError):
        repository.delete("nao-existe")


def test_tags_armazenadas_como_lista_preservando_ordem_e_duplicatas(repository):
    fake = repository._client
    doc = Document(id="doc-9", title="Texto", body="Texto", language="en", tags=["b", "a", "b"])
    repository.create(doc)
    assert fake.data["doc-9"]["tags"] == {"L": [{"S": "b"}, {"S": "a"}, {"S": "b"}]}
    assert repository.get("doc-9").tags == ["b", "a", "b"]