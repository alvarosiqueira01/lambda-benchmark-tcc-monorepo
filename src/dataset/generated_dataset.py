#!/usr/bin/env python3
"""Generate a deterministic dataset for the Document API benchmark.

The generator uses only Python's standard library. Given the same
(dataset_version, seed, document count, and body size), it produces the
same documents byte-for-byte (when serialized by this script).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

DATASET_VERSION = "v1"
DEFAULT_SEED = 42
DEFAULT_COUNT = 100
DEFAULT_BODY_SIZE = 1000

# Fixed timestamps: generation never depends on wall-clock time.
CREATED_AT = "2025-01-01T00:00:00Z"
UPDATED_AT = "2025-01-01T00:00:00Z"

# Private, fixed namespaces. They are constants so UUID generation remains stable.
DATASET_NAMESPACE = uuid.UUID("6f4dbf8d-6f57-4a27-9c7c-1dbe4b95f1a0")

# Small built-in vocabularies. No external files or services are required.
PT_SENTENCES = (
    "Sistemas distribuídos processam tarefas de forma eficiente.",
    "Uma função recebe dados, executa regras e produz um resultado.",
    "Aplicações serverless podem variar recursos conforme a carga.",
    "A análise textual mede propriedades do conteúdo de um documento.",
    "Memória e tempo de execução influenciam o custo de uma função.",
    "A decomposição separa responsabilidades em funções especializadas.",
    "O experimento registra latência, duração e quantidade de invocações.",
    "Resultados determinísticos facilitam a comparação entre execuções.",
    "Documentos maiores aumentam o trabalho real de processamento.",
    "Uma API simples reduz variáveis desnecessárias no experimento.",
    "O armazenamento mantém o conteúdo original do documento.",
    "A orquestração combina as respostas das análises independentes.",
)

EN_SENTENCES = (
    "Distributed systems process tasks in an efficient way.",
    "A function receives data, applies rules, and produces a result.",
    "Serverless applications can vary resources according to load.",
    "Text analysis measures properties of a document body.",
    "Memory and execution time influence the cost of a function.",
    "Decomposition separates responsibilities into specialized functions.",
    "The experiment records latency, duration, and invocation counts.",
    "Deterministic results make repeated executions easier to compare.",
    "Larger documents increase the amount of real processing work.",
    "A simple API reduces unnecessary variables in the experiment.",
    "Storage preserves the original document content.",
    "The orchestrator combines the results of independent analyses.",
)

PT_TOPICS = (
    "serverless",
    "lambda",
    "backend",
    "documento",
    "analise",
    "desempenho",
    "latencia",
    "custo",
    "memoria",
    "arquitetura",
)

EN_TOPICS = (
    "serverless",
    "lambda",
    "backend",
    "document",
    "analysis",
    "performance",
    "latency",
    "cost",
    "memory",
    "architecture",
)

PT_TAG_POOLS = (
    ("serverless", "backend", "aws"),
    ("lambda", "performance", "benchmark"),
    ("document", "analysis", "api"),
    ("latency", "cost", "memory"),
    ("architecture", "decomposition", "experiment"),
)

EN_TAG_POOLS = (
    ("serverless", "backend", "aws"),
    ("lambda", "performance", "benchmark"),
    ("document", "analysis", "api"),
    ("latency", "cost", "memory"),
    ("architecture", "decomposition", "experiment"),
)

_WORD_OR_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    body: str
    tags: list[str]
    language: str
    createdAt: str
    updatedAt: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic Document API benchmark dataset."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_COUNT,
        help=f"Number of documents to generate (default: {DEFAULT_COUNT}).",
    )
    parser.add_argument(
        "--body-size",
        type=int,
        default=DEFAULT_BODY_SIZE,
        help=f"Exact body size in characters (default: {DEFAULT_BODY_SIZE}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Fixed PRNG seed (default: {DEFAULT_SEED}).",
    )
    parser.add_argument(
        "--dataset-version",
        default=DATASET_VERSION,
        help=f"Dataset identifier/version (default: {DATASET_VERSION}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "generated",
        help="Directory receiving the versioned dataset (default: dataset/generated).",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.count < 1:
        raise ValueError("--count must be >= 1")
    if args.body_size < 1:
        raise ValueError("--body-size must be >= 1")
    if not args.dataset_version.strip():
        raise ValueError("--dataset-version must not be empty")


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    slug = slug.strip(".-")
    return slug or "v1"


def document_uuid(dataset_version: str, seed: int, index: int) -> str:
    # UUIDv5 is deterministic for a fixed namespace + name.
    name = f"{dataset_version}|seed={seed}|document={index}"
    return str(uuid.uuid5(DATASET_NAMESPACE, name))


def choose_language(index: int, seed: int) -> str:
    # Stable but seed-dependent. Changing document count does not reshuffle earlier docs.
    return "pt" if ((index + seed) % 2 == 0) else "en"


def make_title(language: str, index: int) -> str:
    if language == "pt":
        return f"Documento de benchmark {index:04d}"
    return f"Benchmark document {index:04d}"


def make_tags(language: str, index: int, rng: random.Random) -> list[str]:
    pools = PT_TAG_POOLS if language == "pt" else EN_TAG_POOLS
    pool = pools[(index + rng.randrange(len(pools))) % len(pools)]
    # Tags are a fixed-size deterministic subset, preserving order.
    return list(pool)


def sentence_bank(language: str) -> tuple[str, ...]:
    return PT_SENTENCES if language == "pt" else EN_SENTENCES


def topic_bank(language: str) -> tuple[str, ...]:
    return PT_TOPICS if language == "pt" else EN_TOPICS


def generate_body(*, language: str, size: int, rng: random.Random) -> str:
    """Generate an exactly `size` character body from fixed internal text.

    The text is assembled from deterministic sentence templates, then repeated
    as necessary. The final output is truncated to exactly the requested length.
    """
    if size <= 0:
        return ""

    sentences = sentence_bank(language)
    topics = topic_bank(language)

    pieces: list[str] = []
    length = 0

    # Keep generating real textual sentences until the requested size is reached.
    # Repeated themes create a meaningful and reproducible workload for analysis.
    while length < size:
        sentence = sentences[rng.randrange(len(sentences))]
        topic = topics[rng.randrange(len(topics))]
        if language == "pt":
            sentence = f"{sentence} O tema {topic} aparece no experimento."
        else:
            sentence = f"{sentence} The topic {topic} appears in the experiment."

        # Preserve sentence boundaries while building the body.
        if pieces:
            sentence = " " + sentence
        pieces.append(sentence)
        length += len(sentence)

    return "".join(pieces)[:size]


def generate_document(*, dataset_version: str, seed: int, index: int, body_size: int) -> Document:
    # Per-document RNG keeps generation stable if implementation adds unrelated
    # random choices elsewhere in the future.
    rng_seed = f"{dataset_version}|seed={seed}|document={index}"
    rng = random.Random(rng_seed)

    language = choose_language(index, seed)
    body = generate_body(language=language, size=body_size, rng=rng)
    tags = make_tags(language, index, rng)

    return Document(
        id=document_uuid(dataset_version, seed, index),
        title=make_title(language, index),
        body=body,
        tags=tags,
        language=language,
        createdAt=CREATED_AT,
        updatedAt=UPDATED_AT,
    )


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=False)
        file.write("\n")


def generate_dataset(
    *,
    dataset_version: str,
    count: int,
    body_size: int,
    seed: int,
    output_dir: Path,
) -> tuple[Path, Path]:
    version_dir = output_dir / safe_slug(dataset_version)
    version_dir.mkdir(parents=True, exist_ok=True)

    documents = [
        asdict(
            generate_document(
                dataset_version=dataset_version,
                seed=seed,
                index=index,
                body_size=body_size,
            )
        )
        for index in range(count)
    ]

    metadata = {
        "datasetVersion": dataset_version,
        "seed": seed,
        "documentCount": count,
        "bodySize": body_size,
        "timestamps": {
            "createdAt": CREATED_AT,
            "updatedAt": UPDATED_AT,
        },
        "generator": "generate_dataset.py",
        "format": "json",
        "deterministic": True,
    }

    dataset_path = version_dir / "documents.json"
    manifest_path = version_dir / "manifest.json"

    write_json(dataset_path, documents)
    write_json(manifest_path, metadata)

    return dataset_path, manifest_path


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
        dataset_path, manifest_path = generate_dataset(
            dataset_version=args.dataset_version,
            count=args.count,
            body_size=args.body_size,
            seed=args.seed,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"dataset_version={args.dataset_version}")
    print(f"documents={args.count}")
    print(f"body_size={args.body_size}")
    print(f"seed={args.seed}")
    print(f"dataset={dataset_path}")
    print(f"manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
