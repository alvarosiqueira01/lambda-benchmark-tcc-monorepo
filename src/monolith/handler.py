"""Handler AWS Lambda + API Gateway HTTP API (configuracao monolitica).

Adapta o evento HTTP aos metodos do DocumentService e mapeia o resultado
para statusHTTP. Nenhuma regra de negocio aqui.

Sem framework HTTP e sem headers CORS (nao estritamente necessarios para
o benchmark de carga).
"""

from __future__ import annotations

import json
from dataclasses import asdict

from app.models import Document
from app.repositories.document_repository import (
    DocumentAlreadyExistsError,
    DocumentNotFoundError,
    DocumentRepository,
)
from app.services.document_service import DocumentService
from app.validation import ValidationError

_service = DocumentService(DocumentRepository())


class _BadRequest(Exception):
    """Requisicao malformada (corpo ausente, JSON invalido ou nao-objeto)."""


def handler(event: dict, context) -> dict:
    method, path = _route(event)
    parts = [part for part in path.split("/") if part]
    try:
        if parts == ["documents"] and method == "POST":
            return _response(201, asdict(_service.create_document(_object_body(event))))
        if parts[:1] == ["documents"] and len(parts) == 2:
            document_id = parts[1]
            if method == "GET":
                return _response(200, _to_jsonable(_service.get_document(document_id)))
            if method == "PUT":
                return _response(200, asdict(_service.update_document(document_id, _object_body(event))))
            if method == "DELETE":
                _service.delete_document(document_id)
                return _response(204)
        if parts[:1] == ["documents"] and parts[2:] == ["analysis"] and method == "GET":
            return _response(200, _service.analyze_document(parts[1]))
    except _BadRequest as error:
        return _error(400, "VALIDATION_ERROR", str(error))
    except ValidationError as error:
        return _error(422, "VALIDATION_ERROR", str(error))
    except DocumentNotFoundError:
        return _error(404, "DOCUMENT_NOT_FOUND", "document not found")
    except DocumentAlreadyExistsError:
        return _error(500, "INTERNAL_ERROR", "internal server error")
    except Exception:  # noqa: BLE001 - qualquer falha interna vira 500 no contrato
        return _error(500, "INTERNAL_ERROR", "internal server error")
    return _error(404, "NOT_FOUND", "route not found")


def _route(event: dict) -> tuple[str, str]:
    http = (event.get("requestContext") or {}).get("http") or {}
    method = (event.get("httpMethod") or http.get("method") or "").upper()
    path = event.get("path") or event.get("rawPath") or http.get("path") or ""
    return method, path


def _object_body(event: dict) -> dict:
    raw = event.get("body")
    if not raw:
        raise _BadRequest("Invalid request")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise _BadRequest("Invalid request") from None
    if not isinstance(payload, dict):
        raise _BadRequest("Invalid request")
    return payload


def _to_jsonable(result) -> dict:
    if isinstance(result, Document):
        return asdict(result)
    return result


def _response(status_code: int, payload=None) -> dict:
    response = {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "isBase64Encoded": False,
    }
    if payload is not None:
        response["body"] = json.dumps(payload, ensure_ascii=False)
    return response


def _error(status_code: int, code: str, message: str) -> dict:
    return _response(status_code, {"error": code, "message": message})