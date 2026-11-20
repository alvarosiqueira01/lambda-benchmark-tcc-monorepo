#!/usr/bin/env python3
"""Package experimental runs for Git and for an external release asset.

The default ``derived`` scope keeps the per-run analysis artifacts and omits
``k6-raw.json`` so the repository stays small. The ``full`` scope keeps every
file, including the k6 raw output, for publication as a release asset.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import lzma
import sys
import tarfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import git_value, sha256_file, utc_now, write_json

try:
    from compression import zstd
except ImportError:  # pragma: no cover - Python < 3.14
    zstd = None


PHASES = ("pilot", "campaign")
DERIVED_EXCLUDES = {"k6-raw.json"}
IGNORED_NAMES = {"__pycache__", ".DS_Store"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
RUNTIME_PREFIXES = (
    "experiments/runs/",
    "experiments/aggregate/",
    "experiments/archive/",
    "experiments/metadata/plan/",
)
COMPRESSION_EXTENSIONS = {"gzip": ".tar.gz", "xz": ".tar.xz", "zstd": ".tar.zst"}


@dataclass(frozen=True)
class RunRecord:
    phase: str
    run_dir: Path
    manifest: dict

    @property
    def run_id(self) -> str:
        return str(self.manifest.get("run_id") or self.run_dir.name)

    @property
    def block_id(self) -> int | None:
        value = self.manifest.get("block_id")
        return int(value) if value is not None else None

    @property
    def condition_id(self) -> str | None:
        return self.manifest.get("condition_id")


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    sha256: str
    size_bytes: int
    uncompressed_bytes: int
    files: int
    run_ids: list[str]


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def git_dirty() -> bool:
    output = (
        git_value(["status", "--porcelain", "--untracked-files=all"], strip=False) or ""
    )
    for line in output.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith(RUNTIME_PREFIXES):
            continue
        if path.startswith("experiments/metadata/") and path.endswith(".json"):
            continue
        if path.startswith("experiments/pilot-"):
            continue
        if path == "experiments/.campaign.lock":
            continue
        return True
    return False


def campaign_metadata(path: Path) -> dict:
    manifest = load_json(path) or {}
    pricing = manifest.get("pricing") or {}
    return {
        "manifest_path": str(path),
        "manifest_available": bool(manifest),
        "campaign_id": manifest.get("campaign_id"),
        "git_commit": manifest.get("git_commit") or git_value(["rev-parse", "HEAD"]),
        "git_dirty": git_dirty(),
        "dataset": manifest.get("dataset"),
        "plan": manifest.get("plan"),
        "config": manifest.get("config"),
        "template": manifest.get("template"),
        "pricing": pricing,
        "protocol": manifest.get("protocol"),
        "tools": manifest.get("tools"),
    }


def discover_runs(
    runs_dir: Path,
    phases: list[str],
    blocks: set[int] | None,
    allow_incomplete: bool,
) -> list[RunRecord]:
    records: list[RunRecord] = []
    for phase in phases:
        root = runs_dir / phase
        if not root.exists():
            continue
        for run_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            manifest = load_json(run_dir / "manifest.json")
            if manifest is None:
                message = f"missing manifest.json in {run_dir}"
                if allow_incomplete:
                    print(f"warning: {message}; skipping", file=sys.stderr)
                    continue
                raise SystemExit(message)
            record = RunRecord(phase=phase, run_dir=run_dir, manifest=manifest)
            if (
                phase == "campaign"
                and blocks is not None
                and record.block_id not in blocks
            ):
                continue
            records.append(record)
    return records


def iter_run_files(run_dir: Path, scope: str) -> list[Path]:
    files: list[Path] = []
    for path in sorted(run_dir.rglob("*")):
        if path.is_dir() or path.is_symlink() or not path.is_file():
            continue
        if any(part in IGNORED_NAMES for part in path.parts):
            continue
        if path.suffix in IGNORED_SUFFIXES:
            continue
        if scope == "derived" and path.name in DERIVED_EXCLUDES:
            continue
        files.append(path)
    return files


def _compression_stream(handle, compression: str, level: int):
    if compression == "gzip":
        return gzip.GzipFile(
            filename="", mode="wb", fileobj=handle, mtime=0, compresslevel=level
        )
    if compression == "xz":
        return lzma.LZMAFile(handle, mode="wb", preset=level, format=lzma.FORMAT_XZ)
    if compression == "zstd":
        if zstd is None:
            raise SystemExit("compression.zstd is unavailable on this interpreter")
        return zstd.open(handle, mode="wb", level=level)
    raise SystemExit(f"unsupported compression: {compression}")


def build_archive(
    archive_path: Path,
    entries: list[tuple[str, Path]],
    compression: str,
    level: int,
) -> int:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    uncompressed_bytes = sum(path.stat().st_size for _, path in entries)
    with archive_path.open("wb") as handle:
        stream = _compression_stream(handle, compression, level)
        try:
            with tarfile.open(
                fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT
            ) as archive:
                for arcname, path in entries:
                    info = tarfile.TarInfo(arcname)
                    info.size = path.stat().st_size
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mode = 0o644
                    with path.open("rb") as source:
                        archive.addfile(info, source)
        finally:
            stream.close()
    return uncompressed_bytes


def archive_names(archive_path: Path, compression: str) -> list[str]:
    if compression == "gzip":
        with tarfile.open(archive_path, mode="r:gz") as archive:
            return archive.getnames()
    if compression == "xz":
        with tarfile.open(archive_path, mode="r:xz") as archive:
            return archive.getnames()
    if compression == "zstd":
        if zstd is None:
            raise SystemExit("compression.zstd is unavailable on this interpreter")
        with (
            zstd.open(archive_path, mode="rb") as stream,
            tarfile.open(fileobj=stream, mode="r|") as archive,
        ):
            return [member.name for member in archive]
    raise SystemExit(f"unsupported compression: {compression}")


def verify_archive(
    archive_path: Path,
    compression: str,
    expected: list[str],
) -> None:
    observed = archive_names(archive_path, compression)
    if sorted(observed) != sorted(expected):
        missing = sorted(set(expected) - set(observed))
        extra = sorted(set(observed) - set(expected))
        raise SystemExit(
            f"archive verification failed for {archive_path}: "
            f"missing={missing[:5]} extra={extra[:5]}"
        )


def group_key(record: RunRecord) -> str:
    if record.phase == "pilot":
        return "pilot"
    if record.block_id is None:
        return "block-unknown"
    return f"block-{record.block_id:02d}"


def group_runs(records: list[RunRecord], mode: str) -> dict[str, list[RunRecord]]:
    if mode == "single":
        return {"all": records}
    grouped: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        grouped[group_key(record)].append(record)
    return dict(sorted(grouped.items()))


def archive_basename(
    campaign_id: str,
    scope: str,
    group: str,
    group_mode: str,
    compression: str,
    explicit_name: str | None,
) -> str:
    extension = COMPRESSION_EXTENSIONS[compression]
    if explicit_name:
        return explicit_name
    if group_mode == "single":
        suffix = "full-raw" if scope == "full" else "derived"
        return f"{campaign_id}-{suffix}{extension}"
    return f"{campaign_id}-{scope}-{group}{extension}"


def count_by(values: list[str | None]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def write_sha256sums(out_dir: Path, archives: list[ArchiveEntry]) -> None:
    lines = [f"{entry.sha256}  {entry.name}" for entry in archives]
    (out_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_index(out_dir: Path, rows: list[dict]) -> None:
    path = out_dir / "per-run-index.csv"
    fieldnames = [
        "run_id",
        "phase",
        "block_id",
        "condition_id",
        "load_pattern",
        "validation_status",
        "archive",
        "archive_sha256",
        "files",
        "uncompressed_bytes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_archive_readme(
    out_dir: Path,
    campaign_id: str,
    scope: str,
    group_mode: str,
    compression: str,
    records: list[RunRecord],
    archives: list[ArchiveEntry],
    release_url: str | None,
) -> None:
    extension = COMPRESSION_EXTENSIONS[compression]
    scope_text = (
        "artefatos derivados por run, sem `k6-raw.json`"
        if scope == "derived"
        else "artefatos completos, incluindo `k6-raw.json`"
    )
    lines = [
        f"# Dados experimentais — {campaign_id}",
        "",
        f"Gerado em `{utc_now()}`.",
        "",
        f"Escopo: **{scope}** ({scope_text}).",
        f"Agrupamento: **{group_mode}**. Compressão: **{compression}**.",
        "",
        "## Conteúdo",
        "",
        f"- {len(records)} runs em {len(archives)} arquivo(s) `{extension}`.",
        "- `SHA256SUMS` — hashes dos arquivos deste diretório.",
        "- `archive-manifest.json` — commit, hashes de entrada, contagens e arquivos.",
        "- `per-run-index.csv` — índice run a run, com bloco, condição e status.",
        "",
        "## Extração",
        "",
        "```bash",
        "sha256sum -c SHA256SUMS",
        f"tar -xf {archives[0].name}" if archives else "",
        "```",
        "",
        "## Reprodutibilidade",
        "",
    ]
    if scope == "derived":
        lines += [
            "O escopo derivado permite regenerar `runs.csv` e `function_runs.csv` com",
            "`scripts/analysis/aggregate.py`. A revalidação completa com",
            "`scripts/experiment/validate/validate_run.py` exige o pacote `full-raw`",
            "publicado no GitHub Release.",
            "",
        ]
    else:
        lines += [
            "Este pacote contém todos os artefatos por run, incluindo `k6-raw.json`,",
            "e permite reexecutar `scripts/experiment/validate/validate_run.py`.",
            "",
        ]
    if release_url:
        lines += ["## Release", "", release_url, ""]
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def parse_blocks(value: str | None) -> set[int] | None:
    if not value:
        return None
    blocks: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            blocks.add(int(item))
        except ValueError as exc:
            raise SystemExit(f"invalid block id: {item}") from exc
    return blocks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", default="experiments/runs")
    parser.add_argument("--phase", choices=("pilot", "campaign", "all"), default="all")
    parser.add_argument("--blocks", default=None)
    parser.add_argument("--out", default="experiments/archive/tcc-2026-11")
    parser.add_argument("--campaign-id", default="tcc-2026-11")
    parser.add_argument(
        "--campaign-manifest", default="experiments/metadata/campaign-manifest.json"
    )
    parser.add_argument("--scope", choices=("derived", "full"), default="derived")
    parser.add_argument("--group", choices=("block", "single"), default="block")
    parser.add_argument("--compression", choices=("gzip", "xz", "zstd"), default="gzip")
    parser.add_argument("--level", type=int, default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument("--release-url", default=None)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    phases = list(PHASES) if args.phase == "all" else [args.phase]
    blocks = parse_blocks(args.blocks)
    records = discover_runs(Path(args.runs_dir), phases, blocks, args.allow_incomplete)
    if not records:
        raise SystemExit("no runs found for the selected filters")

    levels = {"gzip": 6, "xz": 6, "zstd": 9}
    level = args.level if args.level is not None else levels[args.compression]
    out_dir = Path(args.out)
    groups = group_runs(records, args.group)

    planned = []
    for group, group_records in groups.items():
        entries: list[tuple[str, Path]] = []
        for record in group_records:
            for path in iter_run_files(record.run_dir, args.scope):
                arcname = (
                    f"{record.phase}/{record.run_id}/"
                    f"{path.relative_to(record.run_dir).as_posix()}"
                )
                entries.append((arcname, path))
        name = archive_basename(
            args.campaign_id,
            args.scope,
            group,
            args.group,
            args.compression,
            args.name if args.group == "single" else None,
        )
        planned.append((group, group_records, entries, name))

    if args.dry_run:
        for group, group_records, entries, name in planned:
            size = sum(path.stat().st_size for _, path in entries)
            print(
                f"{name}: {len(group_records)} runs, {len(entries)} files, "
                f"{size} bytes uncompressed"
            )
        print(f"dry-run: would write {len(planned)} archive(s) to {out_dir}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    for _, _, _, name in planned:
        target = out_dir / name
        if target.exists() and not args.force:
            raise SystemExit(f"archive already exists: {target} (use --force)")

    archives: list[ArchiveEntry] = []
    index_rows: list[dict] = []
    for _, group_records, entries, name in planned:
        target = out_dir / name
        uncompressed = build_archive(target, entries, args.compression, level)
        expected = [arcname for arcname, _ in entries]
        verify_archive(target, args.compression, expected)
        digest = sha256_file(target)
        run_ids = [record.run_id for record in group_records]
        archives.append(
            ArchiveEntry(
                name=name,
                sha256=digest,
                size_bytes=target.stat().st_size,
                uncompressed_bytes=uncompressed,
                files=len(entries),
                run_ids=run_ids,
            )
        )
        for record in group_records:
            files = [path for path in iter_run_files(record.run_dir, args.scope)]
            index_rows.append(
                {
                    "run_id": record.run_id,
                    "phase": record.phase,
                    "block_id": record.block_id,
                    "condition_id": record.condition_id,
                    "load_pattern": (record.manifest.get("factors") or {}).get(
                        "load_pattern"
                    ),
                    "validation_status": record.manifest.get("validation_status"),
                    "archive": name,
                    "archive_sha256": digest,
                    "files": len(files),
                    "uncompressed_bytes": sum(path.stat().st_size for path in files),
                }
            )

    metadata = campaign_metadata(Path(args.campaign_manifest))
    payload = {
        "archive_version": 1,
        "campaign_id": args.campaign_id,
        "generated_at_utc": utc_now(),
        "scope": args.scope,
        "group": args.group,
        "compression": args.compression,
        "compression_level": level,
        "excluded": sorted(DERIVED_EXCLUDES) if args.scope == "derived" else [],
        "release_url": args.release_url,
        "inputs": metadata,
        "runs_total": len(records),
        "runs_by_phase": count_by([record.phase for record in records]),
        "runs_by_status": count_by(
            [record.manifest.get("validation_status") for record in records]
        ),
        "runs_by_pattern": count_by(
            [
                (record.manifest.get("factors") or {}).get("load_pattern")
                for record in records
            ]
        ),
        "archives": [
            {
                "name": entry.name,
                "sha256": entry.sha256,
                "size_bytes": entry.size_bytes,
                "uncompressed_bytes": entry.uncompressed_bytes,
                "files": entry.files,
                "run_ids": entry.run_ids,
            }
            for entry in archives
        ],
    }
    write_json(out_dir / "archive-manifest.json", payload)
    write_sha256sums(out_dir, archives)
    write_index(out_dir, index_rows)
    write_archive_readme(
        out_dir,
        args.campaign_id,
        args.scope,
        args.group,
        args.compression,
        records,
        archives,
        args.release_url,
    )

    total_size = sum(entry.size_bytes for entry in archives)
    print(
        f"wrote {len(archives)} archive(s) with {len(records)} runs "
        f"to {out_dir} ({total_size} bytes compressed)"
    )


if __name__ == "__main__":
    main()
