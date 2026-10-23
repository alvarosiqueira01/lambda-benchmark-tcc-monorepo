"""Testes de equivalencia semantica entre monolitica e decomposta.

Compara as respostas HTTP (statusCode e body byte-a-byte) da mesma entrada,
fazendo a versao decomposta atravessar o round-trip JSON real do runtime
Lambda: os handlers decomposed.analyses.* sao executados e o resultado e
serializado em bytes antes de voltar ao orquestrador.

A comparacao usa a string JSON completa (nao o dict): json.loads colapsaria
5 e 5.0 na comparacao (5 == 5.0), mas a string atravessa o fio e divergencias
de tipo aparecem. E o contrato observavel da API.
"""

import json
from pathlib import Path

import pytest
from conftest import FakeDynamoDB, FakeLambdaClient

import monolith.handler as monolith
from app.repositories.document_repository import DocumentRepository
from app.services.document_service import DocumentService
from common.analysis import keyword_analysis, lexical_analysis, readability_analysis
from decomposed import orchestrator
from decomposed.analyses import keywords, lexical, readability
from decomposed.invoker import AnalysisInvoker

DATASET_DIR = Path(__file__).resolve().parent.parent / "dataset" / "generated"
if not DATASET_DIR.exists():
    DATASET_DIR = Path(__file__).resolve().parent.parent / "src" / "dataset" / "generated"

# Casos curados de borda, independentes do dataset gerado.
# tags sempre validas segundo o dominio (RN-17: tag nao pode ser vazia/whitespace).
EDGE_CASES = [
    {
        "title": "Somente pontuacao",
        "body": "?!",
        "language": "pt",
        "tags": [],
    },
    {
        "title": "Unicode e duplicatas",
        "body": "Açúcar, café e pão — três palavras com acento!",
        "language": "pt",
        "tags": ["AWS", "aws", "café"],
    },
    {
        "title": "Corpo longo",
        "body": "x " * 5000,
        "language": "en",
        "tags": ["long"],
    },
]


def _generated_entries() -> list[dict]:
    """Carrega o dataset versionado do experimento (src/dataset/generated)."""
    dataset_files = sorted(DATASET_DIR.glob("*/documents.json"))
    if not dataset_files:
        raise FileNotFoundError(
            "dataset gerado nao encontrado; rode src/dataset/generated_dataset.py"
        )
    chosen = next((path for path in dataset_files if path.parent.name == "v1"), dataset_files[0])
    return [
        {
            "title": doc["title"],
            "body": doc["body"],
            "language": doc["language"],
            "tags": doc["tags"],
        }
        for doc in json.loads(chosen.read_text(encoding="utf-8"))
    ]


_EDGE_ITEMS = [(f"edge-{i}", entry) for i, entry in enumerate(EDGE_CASES)]
_GENERATED_ITEMS = [
    (f"dataset-{index:03d}", entry) for index, entry in enumerate(_generated_entries())
]


def _event(method: str, path: str) -> dict:
    return {"httpMethod": method, "path": path, "body": None}


class TransportLambda:
    """Contrato de boto3 lambda.invoke executando os handlers decompostos.

    O Payload e desserializado (como o runtime faria), o handler real roda e
    o resultado volta serializado em bytes (como o runtime Lambda faz),
    produzindo o round-trip JSON exigido pelo AnalysisInvoker.
    """

    def __init__(self, function_names: dict[str, str]) -> None:
        self._handlers = {
            function_names["lexical"]: lexical.handler,
            function_names["readability"]: readability.handler,
            function_names["keywords"]: keywords.handler,
        }

    def invoke(self, FunctionName, InvocationType, Payload) -> dict:
        event = json.loads(Payload)
        result = self._handlers[FunctionName](event, None)
        payload = json.dumps(result, ensure_ascii=True).encode()

        class _Payload:
            def read(self_inner):
                return payload

        return {"Payload": _Payload()}


class RecordingLambda:
    """TransportLambda que tambem registra as invocacoes."""

    def __init__(self, function_names: dict[str, str]) -> None:
        self._inner = TransportLambda(function_names)
        self.calls: list[str] = []

    def invoke(self, FunctionName, InvocationType, Payload) -> dict:
        self.calls.append(FunctionName)
        return self._inner.invoke(FunctionName, InvocationType, Payload)


@pytest.mark.parametrize(
    "entry",
    [entry for _, entry in _EDGE_ITEMS + _GENERATED_ITEMS],
    ids=[item_id for item_id, _ in _EDGE_ITEMS + _GENERATED_ITEMS],
)
def test_monolith_e_decomposto_produzem_o_mesmo_body(entry, monkeypatch, function_names):
    mono_service = DocumentService(DocumentRepository("Documents", FakeDynamoDB()))
    monkeypatch.setattr(monolith, "_service", mono_service)
    doc = mono_service.create_document(entry)
    mono_resp = monolith.handler(_event("GET", f"/documents/{doc.id}/analysis"), None)

    dec_service = DocumentService(DocumentRepository("Documents", FakeDynamoDB()))
    monkeypatch.setattr(orchestrator, "_service", dec_service)
    dec_doc = dec_service.create_document(entry)
    monkeypatch.setattr(
        orchestrator,
        "_invoker",
        AnalysisInvoker(client=TransportLambda(function_names), function_names=function_names),
    )
    dec_resp = orchestrator.handler(_event("GET", f"/documents/{dec_doc.id}/analysis"), None)

    assert mono_resp["statusCode"] == dec_resp["statusCode"] == 200
    assert mono_resp["body"] == dec_resp["body"]


@pytest.mark.parametrize("scenario", ["missing_document", "lambda_failure"])
def test_caminhos_de_erro_documentam_equivalencia_e_assimetria(
    scenario, monkeypatch, function_names
):
    mono_service = DocumentService(DocumentRepository("Documents", FakeDynamoDB()))
    dec_service = DocumentService(DocumentRepository("Documents", FakeDynamoDB()))
    monkeypatch.setattr(monolith, "_service", mono_service)
    monkeypatch.setattr(orchestrator, "_service", dec_service)

    if scenario == "missing_document":
        monkeypatch.setattr(
            orchestrator,
            "_invoker",
            AnalysisInvoker(client=TransportLambda(function_names), function_names=function_names),
        )
        path = "/documents/nao-existe/analysis"
        mono_resp = monolith.handler(_event("GET", path), None)
        dec_resp = orchestrator.handler(_event("GET", path), None)
        assert mono_resp["statusCode"] == dec_resp["statusCode"] == 404
        assert mono_resp["body"] == dec_resp["body"]
        return

    entry = EDGE_CASES[0]
    mono_doc = mono_service.create_document(entry)
    dec_doc = dec_service.create_document(entry)

    def _boom(_document_id):
        raise RuntimeError("falha interna simulada")

    monkeypatch.setattr(mono_service, "analyze_document", _boom)
    monkeypatch.setattr(
        orchestrator,
        "_invoker",
        AnalysisInvoker(
            client=FakeLambdaClient({"fn-lexical": {"errorMessage": "falhou"}}),
            function_names=function_names,
        ),
    )

    mono_resp = monolith.handler(_event("GET", f"/documents/{mono_doc.id}/analysis"), None)
    dec_resp = orchestrator.handler(_event("GET", f"/documents/{dec_doc.id}/analysis"), None)

    # Assimetria proposital do contrato: falha interna do service vira
    # INTERNAL_ERROR na monolitica e ANALYSIS_ERROR na decomposta.
    assert mono_resp["statusCode"] == dec_resp["statusCode"] == 500
    assert json.loads(mono_resp["body"])["error"] == "INTERNAL_ERROR"
    assert json.loads(dec_resp["body"])["error"] == "ANALYSIS_ERROR"
    assert mono_resp["body"] != dec_resp["body"]


def test_invoke_contra_handler_real_preserva_unicode_e_floats(function_names):
    invoker = AnalysisInvoker(client=TransportLambda(function_names), function_names=function_names)
    body = "Açúcar, análise, café. Teste!"

    result = invoker.invoke("lexical", body, "pt", [])

    expected = lexical_analysis(body, "pt", [])
    assert result == expected
    # O round-trip JSON nao pode converter 5 em 5.0 (ou vice-versa):
    # so a serializacao completa captura a equivalencia de tipos.
    assert json.dumps(result) == json.dumps(expected)


def test_invoke_all_contra_handlers_reais_equivale_as_funcoes_puras(function_names):
    invoker = AnalysisInvoker(client=TransportLambda(function_names), function_names=function_names)
    body = "Açúcar, café e pão. Outra sentença aqui!"
    tags = ["café"]

    result = invoker.invoke_all(body, "pt", tags)

    expected = {
        "lexical": lexical_analysis(body, "pt", tags),
        "readability": readability_analysis(body, "pt", tags),
        "keywords": keyword_analysis(body, "pt", tags),
    }
    assert result == expected
    assert json.dumps(result) == json.dumps(expected)


def test_documento_inexistente_na_decomposta_nao_usa_o_invoker(function_names, monkeypatch):
    mono_service = DocumentService(DocumentRepository("Documents", FakeDynamoDB()))
    dec_service = DocumentService(DocumentRepository("Documents", FakeDynamoDB()))
    monkeypatch.setattr(monolith, "_service", mono_service)
    monkeypatch.setattr(orchestrator, "_service", dec_service)

    recording = RecordingLambda(function_names)
    monkeypatch.setattr(
        orchestrator,
        "_invoker",
        AnalysisInvoker(client=recording, function_names=function_names),
    )

    path = "/documents/nao-existe/analysis"
    mono_resp = monolith.handler(_event("GET", path), None)
    dec_resp = orchestrator.handler(_event("GET", path), None)

    assert mono_resp["statusCode"] == dec_resp["statusCode"] == 404
    assert mono_resp["body"] == dec_resp["body"]
    assert recording.calls == []