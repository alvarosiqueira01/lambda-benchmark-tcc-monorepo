"""Modelo da entidade Document."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Document:
    """Documento com id, title, body, language e tags.

    Entidade passiva e minima conforme docs/domain-rules.md.
    """

    id: str
    title: str
    body: str
    language: str
    tags: list[str]