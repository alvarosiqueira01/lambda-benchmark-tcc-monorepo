"""Wrapper fino sobre boto3 lambda.invoke, com cliente injetavel para testes."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import boto3

_ANALYSES = ("lexical", "readability", "keywords")


class AnalysisInvocationError(Exception):
    """A Lambda de analise retornou erro no payload (errorMessage)."""

    def __init__(self, analysis: str, error_message: str) -> None:
        super().__init__(f"{analysis}: {error_message}")
        self.analysis = analysis
        self.error_message = error_message


class AnalysisInvoker:
    """Invoca as tres Lambdas especializadas de analise em paralelo."""

    def __init__(self, client=None, function_names: dict[str, str] | None = None) -> None:
        self._client = client or boto3.client("lambda")
        self._function_names = function_names or {
            "lexical": os.environ["LEXICAL_FUNCTION_NAME"],
            "readability": os.environ["READABILITY_FUNCTION_NAME"],
            "keywords": os.environ["KEYWORDS_FUNCTION_NAME"],
        }

    def invoke(self, analysis: str, body: str, language: str, tags: list[str]) -> dict:
        response = self._client.invoke(
            FunctionName=self._function_names[analysis],
            InvocationType="RequestResponse",
            Payload=json.dumps({"body": body, "language": language, "tags": tags}).encode(),
        )
        payload = json.loads(response["Payload"].read())
        if "errorMessage" in payload:
            raise AnalysisInvocationError(analysis, payload["errorMessage"])
        return payload

    def invoke_all(self, body: str, language: str, tags: list[str]) -> dict:
        with ThreadPoolExecutor(max_workers=len(_ANALYSES)) as pool:
            futures = {
                name: pool.submit(self.invoke, name, body, language, tags)
                for name in _ANALYSES
            }
            return {name: future.result() for name, future in futures.items()}