"""Handler AWS Lambda + API Gateway HTTP API (configuracao monolitica).

Wrapper fino: roteia /analysis e delega o CRUD para handle_crud.
Sem framework HTTP e sem headers CORS.
"""

from app.handlers.documents import error, handle_crud, response, route
from app.repositories.document_repository import (
    DocumentNotFoundError,
    DocumentRepository,
)
from app.services.document_service import DocumentService

_service = DocumentService(DocumentRepository())


def handler(event: dict, context) -> dict:
    method, path = route(event)
    parts = [part for part in path.split("/") if part]
    if method == "GET" and parts[:1] == ["documents"] and len(parts) == 3 and parts[2] == "analysis":
        try:
            return response(200, _service.analyze_document(parts[1]))
        except DocumentNotFoundError:
            return error(404, "DOCUMENT_NOT_FOUND", "document not found")
        except Exception:  # noqa: BLE001 - falha interna vira 500 no contrato
            return error(500, "INTERNAL_ERROR", "internal server error")
    return handle_crud(event, _service)