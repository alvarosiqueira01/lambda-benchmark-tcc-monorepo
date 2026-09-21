"""Fixtures e helpers compartilhados dos testes."""

import json
import os

import pytest
from botocore.exceptions import ClientError

from app.repositories.document_repository import DocumentRepository
from decomposed.invoker import AnalysisInvoker

os.environ.setdefault("LEXICAL_FUNCTION_NAME", "fn-lexical")
os.environ.setdefault("READABILITY_FUNCTION_NAME", "fn-readability")
os.environ.setdefault("KEYWORDS_FUNCTION_NAME", "fn-keywords")


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
            "title": ExpressionAttributeValues[":title"],
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


class FakeLambdaClient:
    """Cliente fake com o mesmo contrato de boto3.client("lambda").invoke."""

    def __init__(self, results: dict[str, dict]) -> None:
        self.results = results
        self.calls: list[dict] = []

    def invoke(self, FunctionName, InvocationType, Payload) -> dict:
        self.calls.append({"FunctionName": FunctionName, "InvocationType": InvocationType, "Payload": Payload})
        body = json.dumps(self.results[FunctionName]).encode()

        class _Payload:
            def read(self_inner):
                return body

        return {"Payload": _Payload()}


@pytest.fixture
def function_names() -> dict[str, str]:
    return {
        "lexical": "fn-lexical",
        "readability": "fn-readability",
        "keywords": "fn-keywords",
    }


@pytest.fixture
def invoker_with(function_names):
    """Fabrica AnalysisInvoker dado o dict de resultados por FunctionName."""

    def _build(results: dict[str, dict]) -> AnalysisInvoker:
        return AnalysisInvoker(client=FakeLambdaClient(results), function_names=function_names)

    return _build