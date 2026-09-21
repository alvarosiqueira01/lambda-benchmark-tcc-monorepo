"""Servico de dominio de documentos."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from app.models import Document
from app.repositories.document_repository import DocumentRepository
from app.validation import validate_payload
from common.analysis import aggregate_analysis


class DocumentService:
    """Valida dados, gera id e orquestra o CRUD e as tres analises.

    Nao conhece API Gateway, formato de evento Lambda nem status HTTP.
    """

    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    def create_document(self, payload: Mapping) -> Document:
        title, body, language, tags = validate_payload(payload)
        document = Document(id=str(uuid4()), title=title, body=body, language=language, tags=tags)
        return self._repository.create(document)

    def get_document(self, document_id: str) -> Document:
        return self._repository.get(document_id)

    def update_document(self, document_id: str, payload: Mapping) -> Document:
        title, body, language, tags = validate_payload(payload)
        document = Document(id=document_id, title=title, body=body, language=language, tags=tags)
        return self._repository.update(document)

    def delete_document(self, document_id: str) -> None:
        self._repository.delete(document_id)

    def analyze_document(self, document_id: str) -> dict:
        document = self._repository.get(document_id)
        return aggregate_analysis(document.body, document.language, document.tags)