"""Validacao de Document conforme docs/domain-rules.md e openapi.yaml."""

from __future__ import annotations

from collections.abc import Mapping

SUPPORTED_LANGUAGES = frozenset({"pt", "en"})

_ALLOWED_FIELDS = frozenset({"body", "language", "tags"})


class ValidationError(ValueError):
    """Dados de entrada invalidos. A mensagem e deterministica."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def validate_payload(payload: Mapping) -> tuple[str, str, list[str]]:
    """Valida body, language e tags de criacao/atualizacao.

    Retorna a tupla (body, language, tags) ou levanta ValidationError.
    """
    unknown = sorted(set(payload) - _ALLOWED_FIELDS)
    if unknown:
        raise ValidationError(f"unknown field: {unknown[0]}")
    body = _validate_body(payload)
    language = _validate_language(payload)
    tags = _validate_tags(payload)
    return body, language, tags


def _validate_body(payload: Mapping) -> str:
    if "body" not in payload:
        raise ValidationError("body is required")
    body = payload["body"]
    if not isinstance(body, str):
        raise ValidationError("body must be a string")
    if not body.strip():
        raise ValidationError("body must not be empty")
    return body


def _validate_language(payload: Mapping) -> str:
    if "language" not in payload:
        raise ValidationError("language is required")
    language = payload["language"]
    if not isinstance(language, str):
        raise ValidationError("language must be a string")
    if not language:
        raise ValidationError("language must not be empty")
    if language not in SUPPORTED_LANGUAGES:
        raise ValidationError("unsupported language")
    return language


def _validate_tags(payload: Mapping) -> list[str]:
    if "tags" not in payload:
        raise ValidationError("tags is required")
    tags = payload["tags"]
    if not isinstance(tags, list):
        raise ValidationError("tags must be a list")
    for tag in tags:
        if not isinstance(tag, str):
            raise ValidationError("tags must contain only strings")
        if not tag.strip():
            raise ValidationError("tag must not be empty")
    return tags