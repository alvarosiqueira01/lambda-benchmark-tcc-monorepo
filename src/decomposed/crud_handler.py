"""Lambda CRUD da implantacao decomposta.

Apenas POST/GET/PUT/DELETE. /analysis e responsabilidade do orquestrador.
"""

from app.handlers.documents import handle_crud
from app.repositories.document_repository import DocumentRepository
from app.services.document_service import DocumentService

_service = DocumentService(DocumentRepository())


def handler(event: dict, context) -> dict:
    return handle_crud(event, _service)