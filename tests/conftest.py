"""Fixtures e helpers compartilhados dos testes."""

import pytest
from botocore.exceptions import ClientError

from app.repositories.document_repository import DocumentRepository


def client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {}},
        "operation",
    )


class FakeDynamoDB:
    """Cliente fake com o mesmo contrato do cliente DynamoDB de baixo nivel."""

    def __init__(self, initial: dict | None = None) -> None:
        self.data = initial or {}

    def get_item(self, TableName, Key, **kwargs):
        document_id = Key["id"]["S"]
        if document_id in self.data:
            return {"Item": self.data[document_id]}
        return {}

    def put_item(self, TableName, Item, ConditionExpression=None, **kwargs):
        document_id = Item["id"]["S"]
        if ConditionExpression and document_id in self.data:
            raise client_error("ConditionalCheckFailedException")
        self.data[document_id] = Item

    def update_item(self, TableName, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None, **kwargs):
        document_id = Key["id"]["S"]
        if ConditionExpression and document_id not in self.data:
            raise client_error("ConditionalCheckFailedException")
        self.data[document_id] = {
            "id": {"S": document_id},
            "body": ExpressionAttributeValues[":body"],
            "language": ExpressionAttributeValues[":language"],
            "tags": ExpressionAttributeValues[":tags"],
        }

    def delete_item(self, TableName, Key, ConditionExpression=None, **kwargs):
        document_id = Key["id"]["S"]
        if ConditionExpression and document_id not in self.data:
            raise client_error("ConditionalCheckFailedException")
        self.data.pop(document_id, None)


@pytest.fixture
def repository() -> DocumentRepository:
    return DocumentRepository(table_name="Documents", client=FakeDynamoDB())