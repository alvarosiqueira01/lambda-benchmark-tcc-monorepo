from __future__ import annotations

import argparse
import json
from pathlib import Path

import boto3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", required=True)
    parser.add_argument(
        "--dataset",
        default="./dataset/generated/v2/documents.json",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    documents = json.loads(dataset_path.read_text(encoding="utf-8"))

    table = boto3.resource("dynamodb").Table(args.table)

    with table.batch_writer() as batch:
        for document in documents:
            batch.put_item(
                Item={
                    "id": document["id"],
                    "title": document["title"],
                    "body": document["body"],
                    "language": document["language"],
                    "tags": document["tags"],
                }
            )

    print(f"Seeded {len(documents)} documents into {args.table}")


if __name__ == "__main__":
    main()