"""Lambda orquestradora: GET /documents/{id}/analysis.

Recupera o documento, invoca as tres Lambdas especializadas em paralelo
e agrega os resultados. Nenhuma regra de negocio alem disso.
"""

from app.handlers.documents import error, response, route
from app.repositories.document_repository import (
    DocumentNotFoundError,
    DocumentRepository,
)
from app.services.document_service import DocumentService
from decomposed.invoker import AnalysisInvoker

_service = DocumentService(DocumentRepository())
_invoker = AnalysisInvoker()


def handler(event: dict, context) -> dict:
    method, path = route(event)
    parts = [part for part in path.split("/") if part]

    if method == "GET" and parts[:1] == ["documents"] and len(parts) == 3 and parts[2] == "analysis":
        try:
            document = _service.get_document(parts[1])
        except DocumentNotFoundError:
            return error(404, "DOCUMENT_NOT_FOUND", "document not found")
        try:
            results = _invoker.invoke_all(document.body, document.language, document.tags)
        except Exception:  # noqa: BLE001 - falha numa analise vira 500 com ANALYSIS_ERROR
            return error(500, "ANALYSIS_ERROR", "document analysis failed")
        return response(200, results)

    return error(404, "NOT_FOUND", "route not found")