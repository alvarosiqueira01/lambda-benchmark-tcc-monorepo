"""Acesso ao DynamoDB para a entidade Document."""

from __future__ import annotations

import os

import boto3
from botocore.exceptions import ClientError

from app.models import Document

_CONDITIONAL_CHECK_FAILED = "ConditionalCheckFailedException"

DEFAULT_TABLE_NAME = "Documents"


class DocumentNotFoundError(Exception):
    """Documento inexistente no armazenamento."""


class DocumentAlreadyExistsError(Exception):
    """Ja existe um documento com o mesmo id."""


class DocumentRepository:
    """Operacoes GetItem, PutItem, UpdateItem e DeleteItem.

    Tabela unica "Documents" com partition key id (S), sem GSI/LSI.
    """

    def __init__(self, table_name: str | None = None, client=None) -> None:
        self.table_name = table_name or os.environ.get("DOCUMENTS_TABLE", DEFAULT_TABLE_NAME)
        self._client = client or boto3.client("dynamodb")

    def create(self, document: Document) -> Document:
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=_to_item(document),
                ConditionExpression="attribute_not_exists(id)",
            )
        except ClientError as error:
            if _is_conditional_check_failed(error):
                raise DocumentAlreadyExistsError(document.id) from error
            raise
        return document

    def get(self, document_id: str) -> Document:
        response = self._client.get_item(
            TableName=self.table_name,
            Key={"id": {"S": document_id}},
        )
        item = response.get("Item")
        if item is None:
            raise DocumentNotFoundError(document_id)
        return _from_item(item)

    def update(self, document: Document) -> Document:
        try:
            self._client.update_item(
                TableName=self.table_name,
                Key={"id": {"S": document.id}},
                UpdateExpression="SET body = :body, language = :language, tags = :tags",
                ExpressionAttributeValues={
                    ":body": {"S": document.body},
                    ":language": {"S": document.language},
                    ":tags": {"L": [{"S": tag} for tag in document.tags]},
                },
                ConditionExpression="attribute_exists(id)",
            )
        except ClientError as error:
            if _is_conditional_check_failed(error):
                raise DocumentNotFoundError(document.id) from error
            raise
        return document

    def delete(self, document_id: str) -> None:
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={"id": {"S": document_id}},
                ConditionExpression="attribute_exists(id)",
            )
        except ClientError as error:
            if _is_conditional_check_failed(error):
                raise DocumentNotFoundError(document_id) from error
            raise


def _to_item(document: Document) -> dict:
    return {
        "id": {"S": document.id},
        "body": {"S": document.body},
        "language": {"S": document.language},
        "tags": {"L": [{"S": tag} for tag in document.tags]},
    }


def _from_item(item: dict) -> Document:
    return Document(
        id=item["id"]["S"],
        body=item["body"]["S"],
        language=item["language"]["S"],
        tags=[tag["S"] for tag in item["tags"]["L"]],
    )


def _is_conditional_check_failed(error: ClientError) -> bool:
    return error.response["Error"]["Code"] == _CONDITIONAL_CHECK_FAILED