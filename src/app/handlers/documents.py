"""Helpers HTTP e roteamento CRUD compartilhados pelos handlers Lambda."""

from __future__ import annotations

import json
from dataclasses import asdict

from app.models import Document
from app.repositories.document_repository import (
    DocumentAlreadyExistsError,
    DocumentNotFoundError,
)
from app.validation import ValidationError


class BadRequest(Exception):
    """Requisicao malformada (corpo ausente, JSON invalido ou nao-objeto)."""


def route(event: dict) -> tuple[str, str]:
    """Extrai (method, path) do evento, suportando payload v1 e v2 da HTTP API."""
    http = (event.get("requestContext") or {}).get("http") or {}
    method = (event.get("httpMethod") or http.get("method") or "").upper()
    path = event.get("path") or event.get("rawPath") or http.get("path") or ""
    return method, path


def response(status_code: int, payload=None) -> dict:
    """Resposta de proxy: statusCode e body JSON quando ha payload."""
    result = {"statusCode": status_code, "isBase64Encoded": False}
    if payload is not None:
        result["headers"] = {"content-type": "application/json"}
        result["body"] = json.dumps(payload, ensure_ascii=False)
    return result


def error(status_code: int, code: str, message: str) -> dict:
    """Resposta de erro no formato do esquema Error do openapi.yaml."""
    return response(status_code, {"error": code, "message": message})


def object_body(event: dict) -> dict:
    """Le e converte o corpo JSON; levanta BadRequest se nao for um objeto."""
    raw = event.get("body")
    if not raw:
        raise BadRequest("Invalid request")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise BadRequest("Invalid request") from None
    if not isinstance(payload, dict):
        raise BadRequest("Invalid request")
    return payload


def handle_crud(event: dict, service) -> dict:
    """Roteia POST/GET/PUT/DELETE e mapeia excecoes para status HTTP.

    Nao trata /analysis (responsabilidade dos wrappers por configuracao).
    """
    method, path = route(event)
    parts = [part for part in path.split("/") if part]
    try:
        if parts == ["documents"] and method == "POST":
            return response(201, _to_jsonable(service.create_document(object_body(event))))
        if parts[:1] == ["documents"] and len(parts) == 2:
            document_id = parts[1]
            if method == "GET":
                return response(200, _to_jsonable(service.get_document(document_id)))
            if method == "PUT":
                return response(200, _to_jsonable(service.update_document(document_id, object_body(event))))
            if method == "DELETE":
                service.delete_document(document_id)
                return response(204)
    except BadRequest as exc:
        return error(400, "VALIDATION_ERROR", str(exc))
    except ValidationError as exc:
        return error(422, "VALIDATION_ERROR", str(exc))
    except DocumentNotFoundError:
        return error(404, "DOCUMENT_NOT_FOUND", "document not found")
    except DocumentAlreadyExistsError:
        return error(500, "INTERNAL_ERROR", "internal server error")
    except Exception:  # noqa: BLE001 - falha interna vira 500 no contrato
        return error(500, "INTERNAL_ERROR", "internal server error")
    return error(404, "NOT_FOUND", "route not found")


def _to_jsonable(result) -> dict:
    if isinstance(result, Document):
        return asdict(result)
    return result