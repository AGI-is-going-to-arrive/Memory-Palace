#!/usr/bin/env python3
"""Download and normalize public benchmark datasets for benchmark tests."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import re
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import TextIOWrapper
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import requests


SCHEMA_VERSION = "v1"
DEFAULT_SAMPLE_SIZES = (100, 200, 500)
DEFAULT_MAX_RECORDS = 6000
DEFAULT_SEED = 20260219

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DATASETS_ROOT = PROJECT_ROOT / "backend" / "tests" / "datasets"
RAW_DIR = DATASETS_ROOT / "raw"
PROCESSED_DIR = DATASETS_ROOT / "processed"
MANIFESTS_DIR = DATASETS_ROOT / "manifests"


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    source_url: str
    domain: str
    language: str = "en"


DATASET_SPECS: Sequence[DatasetSpec] = (
    DatasetSpec(
        key="squad_v2_dev",
        source_url="https://rajpurkar.github.io/SQuAD-explorer/dataset/dev-v2.0.json",
        domain="qa",
    ),
    DatasetSpec(
        key="dailydialog",
        source_url="https://huggingface.co/datasets/ConvLab/dailydialog/resolve/main/data.zip",
        domain="dialogue",
    ),
    DatasetSpec(
        key="msmarco_passages",
        source_url="https://msmarco.z22.web.core.windows.net/msmarcoranking/collectionandqueries.tar.gz",
        domain="ir_general",
    ),
    DatasetSpec(
        key="beir_nfcorpus",
        source_url="https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/nfcorpus.zip",
        domain="ir_biomedical",
    ),
    DatasetSpec(
        key="beir_nq",
        source_url="https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/nq.zip",
        domain="qa_open_domain",
    ),
    DatasetSpec(
        key="beir_hotpotqa",
        source_url="https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/hotpotqa.zip",
        domain="qa_multihop",
    ),
    DatasetSpec(
        key="beir_fiqa",
        source_url="https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/fiqa.zip",
        domain="qa_finance",
    ),
)


class _HTTPRangeReader(io.RawIOBase):
    """Minimal seekable range reader for remote ZIP files."""

    def __init__(
        self,
        *,
        url: str,
        size: int,
        chunk_size: int = 8 * 1024 * 1024,
        timeout_sec: int = 300,
    ) -> None:
        if size <= 0:
            raise ValueError("size must be positive")
        self._url = url
        self._size = size
        self._chunk_size = max(1024 * 1024, chunk_size)
        self._timeout_sec = timeout_sec
        self._position = 0
        self._cache: Dict[int, bytes] = {}
        self._session = requests.Session()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            next_pos = offset
        elif whence == io.SEEK_CUR:
            next_pos = self._position + offset
        elif whence == io.SEEK_END:
            next_pos = self._size + offset
        else:
            raise ValueError(f"unsupported whence: {whence}")
        self._position = max(0, min(self._size, next_pos))
        return self._position

    def read(self, n: int = -1) -> bytes:
        if self._position >= self._size:
            return b""

        if n is None or n < 0:
            remaining = self._size - self._position
        else:
            remaining = min(n, self._size - self._position)
        if remaining <= 0:
            return b""

        chunks: List[bytes] = []
        to_read = remaining
        while to_read > 0 and self._position < self._size:
            chunk_index = self._position // self._chunk_size
            chunk = self._load_chunk(chunk_index)
            chunk_start = chunk_index * self._chunk_size
            offset = self._position - chunk_start
            available = len(chunk) - offset
            if available <= 0:
                break
            step = min(to_read, available)
            chunks.append(chunk[offset : offset + step])
            self._position += step
            to_read -= step

        return b"".join(chunks)

    def close(self) -> None:
        try:
            self._session.close()
        finally:
            super().close()

    def _load_chunk(self, chunk_index: int) -> bytes:
        if chunk_index in self._cache:
            return self._cache[chunk_index]

        start = chunk_index * self._chunk_size
        end = min(self._size - 1, start + self._chunk_size - 1)
        headers = {"Range": f"bytes={start}-{end}"}
        response = self._session.get(
            self._url, headers=headers, timeout=self._timeout_sec
        )
        response.raise_for_status()
        content = response.content
        if not content:
            raise RuntimeError(
                f"empty response while range-fetching {self._url} bytes={start}-{end}"
            )
        self._cache[chunk_index] = content
        return content


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_rel(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _dataset_seed(base_seed: int, dataset_key: str) -> int:
    key_seed = int(hashlib.sha256(dataset_key.encode("utf-8")).hexdigest()[:8], 16)
    return base_seed + key_seed


def _get_remote_content_length(url: str, timeout_sec: int) -> int | None:
    try:
        response = requests.head(url, allow_redirects=True, timeout=timeout_sec)
        response.raise_for_status()
        value = response.headers.get("Content-Length")
        if value is None:
            return None
        return int(value)
    except (requests.RequestException, ValueError):
        return None


def _download_file(url: str, target: Path, overwrite: bool, timeout_sec: int) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    expected_size = _get_remote_content_length(url, timeout_sec=timeout_sec)
    if target.exists() and not overwrite:
        if expected_size is None or target.stat().st_size >= expected_size:
            return target
        target.unlink(missing_ok=True)

    temp_target = target.with_suffix(target.suffix + ".part")
    temp_target.unlink(missing_ok=True)
    with requests.get(url, stream=True, timeout=timeout_sec) as response:
        response.raise_for_status()
        with temp_target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    if expected_size is not None and temp_target.stat().st_size < expected_size:
        raise RuntimeError(
            f"incomplete download for {url}: "
            f"{temp_target.stat().st_size} < {expected_size}"
        )
    temp_target.replace(target)
    return target


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    required = [
        "id",
        "query",
        "relevant_uris_or_doc_ids",
        "language",
        "domain",
        "source_dataset",
        "split",
    ]
    for field in required:
        if field not in row:
            raise ValueError(f"missing required field: {field}")

    query = str(row["query"]).strip()
    if not query:
        raise ValueError("query must not be empty")

    relevant = row["relevant_uris_or_doc_ids"]
    if not isinstance(relevant, list):
        raise ValueError("relevant_uris_or_doc_ids must be list")

    cleaned_relevant = sorted({str(item).strip() for item in relevant if str(item).strip()})
    if not cleaned_relevant:
        raise ValueError("relevant_uris_or_doc_ids must contain at least one item")

    return {
        "id": str(row["id"]),
        "query": query,
        "relevant_uris_or_doc_ids": cleaned_relevant,
        "language": str(row["language"] or "en"),
        "domain": str(row["domain"] or "general"),
        "source_dataset": str(row["source_dataset"]),
        "split": str(row["split"]),
    }


def _stable_pick_rows(
    rows: List[Dict[str, Any]],
    dataset_key: str,
    seed: int,
    max_records: int,
) -> List[Dict[str, Any]]:
    dedup: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        normalized = _normalize_row(row)
        dedup[normalized["id"]] = normalized

    values = list(dedup.values())
    rng = random.Random(_dataset_seed(seed, dataset_key))
    rng.shuffle(values)
    if max_records > 0 and len(values) > max_records:
        values = values[:max_records]
    values.sort(key=lambda item: item["id"])
    return values


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _sample_rows(
    rows: List[Dict[str, Any]],
    dataset_key: str,
    seed: int,
    sample_sizes: Sequence[int],
) -> Dict[int, List[Dict[str, Any]]]:
    shuffled = list(rows)
    rng = random.Random(_dataset_seed(seed + 17, dataset_key))
    rng.shuffle(shuffled)

    result: Dict[int, List[Dict[str, Any]]] = {}
    for size in sorted({int(s) for s in sample_sizes if int(s) > 0}):
        result[size] = shuffled[: min(size, len(shuffled))]
    return result


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return cleaned or "untitled"


def _prepare_squad_v2(
    spec: DatasetSpec, raw_dir: Path, max_records: int, seed: int
) -> Tuple[List[Dict[str, Any]], List[Path]]:
    raw_file = _download_file(
        spec.source_url,
        raw_dir / "squad_dev_v2.json",
        overwrite=False,
        timeout_sec=300,
    )
    payload = json.loads(raw_file.read_text(encoding="utf-8"))

    rows: List[Dict[str, Any]] = []
    for article in payload.get("data", []):
        title = str(article.get("title") or "untitled")
        title_slug = _slugify(title)
        for paragraph_idx, paragraph in enumerate(article.get("paragraphs", [])):
            doc_id = f"squad_v2:{title_slug}:{paragraph_idx}"
            for qa in paragraph.get("qas", []):
                qa_id = str(qa.get("id") or f"{title_slug}_{paragraph_idx}")
                question = str(qa.get("question") or "").strip()
                if not question:
                    continue
                rows.append(
                    {
                        "id": f"squad_v2:{qa_id}",
                        "query": question,
                        "relevant_uris_or_doc_ids": [doc_id],
                        "language": spec.language,
                        "domain": spec.domain,
                        "source_dataset": spec.key,
                        "split": "dev",
                    }
                )

    return _stable_pick_rows(rows, spec.key, seed, max_records), [raw_file]


def _prepare_dailydialog(
    spec: DatasetSpec, raw_dir: Path, max_records: int, seed: int
) -> Tuple[List[Dict[str, Any]], List[Path]]:
    raw_file = _download_file(
        spec.source_url,
        raw_dir / "dailydialog_data.zip",
        overwrite=False,
        timeout_sec=300,
    )

    rows: List[Dict[str, Any]] = []
    with zipfile.ZipFile(raw_file, "r") as archive:
        names = set(archive.namelist())
        if "data/dialogues.json" in names:
            dialogues_payload = json.loads(archive.read("data/dialogues.json").decode("utf-8"))
            for dialogue in dialogues_payload:
                split = str(dialogue.get("data_split") or "unknown").strip().lower() or "unknown"
                dialogue_id = str(dialogue.get("dialogue_id") or "dialogue")
                domains = dialogue.get("domains") or []
                domain = (
                    f"{spec.domain}:{_slugify(str(domains[0]))}"
                    if isinstance(domains, list) and domains
                    else spec.domain
                )
                turns = dialogue.get("turns") or []
                utterances = [
                    str(turn.get("utterance") or "").strip()
                    for turn in turns
                    if isinstance(turn, dict)
                ]
                utterances = [item for item in utterances if item]
                for turn_idx in range(len(utterances) - 1):
                    rows.append(
                        {
                            "id": f"dailydialog:{split}:{dialogue_id}:{turn_idx}",
                            "query": utterances[turn_idx],
                            "relevant_uris_or_doc_ids": [
                                f"dailydialog:{split}:{dialogue_id}:{turn_idx + 1}"
                            ],
                            "language": spec.language,
                            "domain": domain,
                            "source_dataset": spec.key,
                            "split": split,
                        }
                    )
        else:
            members = [
                name
                for name in archive.namelist()
                if name.lower().endswith(".txt")
                and "dialogues_" in Path(name).name.lower()
                and "dialogues_text" not in Path(name).name.lower()
            ]
            if not members:
                members = [
                    name
                    for name in archive.namelist()
                    if name.lower().endswith(".txt")
                    and "dialogues" in Path(name).name.lower()
                ]

            for member in sorted(members):
                filename = Path(member).name.lower()
                if "train" in filename:
                    split = "train"
                elif "valid" in filename or "validation" in filename:
                    split = "validation"
                elif "test" in filename:
                    split = "test"
                else:
                    split = "unknown"

                with archive.open(member, "r") as handle:
                    reader = TextIOWrapper(handle, encoding="utf-8", errors="ignore")
                    for line_idx, line in enumerate(reader):
                        utterances = [part.strip() for part in line.split("__eou__") if part.strip()]
                        for turn_idx in range(len(utterances) - 1):
                            query = utterances[turn_idx]
                            doc_id = f"dailydialog:{split}:{line_idx}:{turn_idx + 1}"
                            rows.append(
                                {
                                    "id": f"dailydialog:{split}:{line_idx}:{turn_idx}",
                                    "query": query,
                                    "relevant_uris_or_doc_ids": [doc_id],
                                    "language": spec.language,
                                    "domain": spec.domain,
                                    "source_dataset": spec.key,
                                    "split": split,
                                }
                            )

    return _stable_pick_rows(rows, spec.key, seed, max_records), [raw_file]


def _parse_qrels_tsv(path: Path) -> Dict[str, List[str]]:
    qrels: Dict[str, List[str]] = {}
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for row_idx, row in enumerate(reader):
            if not row:
                continue
            if row_idx == 0 and any("query" in item.lower() for item in row):
                continue
            if len(row) >= 4:
                query_id = row[0].strip()
                doc_id = row[2].strip()
                rel_raw = row[3].strip()
            elif len(row) >= 3:
                query_id = row[0].strip()
                doc_id = row[1].strip()
                rel_raw = row[2].strip()
            else:
                continue
            try:
                rel = float(rel_raw)
            except ValueError:
                continue
            if rel <= 0:
                continue
            if not query_id or not doc_id:
                continue
            qrels.setdefault(query_id, []).append(doc_id)
    return qrels


def _prepare_msmarco(
    spec: DatasetSpec, raw_dir: Path, max_records: int, seed: int
) -> Tuple[List[Dict[str, Any]], List[Path]]:
    queries_tar = _download_file(
        "https://msmarco.z22.web.core.windows.net/msmarcoranking/queries.tar.gz",
        raw_dir / "msmarco_queries.tar.gz",
        overwrite=False,
        timeout_sec=300,
    )
    qrels_file = _download_file(
        "https://msmarco.z22.web.core.windows.net/msmarcoranking/qrels.dev.tsv",
        raw_dir / "msmarco_qrels_dev.tsv",
        overwrite=False,
        timeout_sec=300,
    )

    qrels_map = _parse_qrels_tsv(qrels_file)
    if not qrels_map:
        raise RuntimeError("MS MARCO qrels parsing returned 0 rows")

    queries_map: Dict[str, str] = {}
    with tarfile.open(queries_tar, "r:gz") as archive:
        members = [m for m in archive.getmembers() if m.isfile()]
        target = None
        for member in members:
            name = member.name.lower()
            if "queries.dev.tsv" in name:
                target = member
                break
        if target is None:
            for member in members:
                name = member.name.lower()
                if "queries.dev" in name and name.endswith(".tsv"):
                    target = member
                    break
        if target is None:
            raise RuntimeError("MS MARCO queries.dev.tsv not found in queries.tar.gz")
        extracted = archive.extractfile(target)
        if extracted is None:
            raise RuntimeError("failed to extract MS MARCO queries file")
        reader = TextIOWrapper(extracted, encoding="utf-8", errors="ignore")
        for line in reader:
            parts = line.rstrip("\n").split("\t", maxsplit=1)
            if len(parts) != 2:
                continue
            query_id, query_text = parts[0].strip(), parts[1].strip()
            if query_id and query_text and query_id in qrels_map:
                queries_map[query_id] = query_text

    rows: List[Dict[str, Any]] = []
    for query_id, relevant_docs in qrels_map.items():
        query = queries_map.get(query_id)
        if not query:
            continue
        rows.append(
            {
                "id": f"msmarco:dev:{query_id}",
                "query": query,
                "relevant_uris_or_doc_ids": sorted(set(relevant_docs)),
                "language": spec.language,
                "domain": spec.domain,
                "source_dataset": spec.key,
                "split": "dev",
            }
        )

    return _stable_pick_rows(rows, spec.key, seed, max_records), [queries_tar, qrels_file]


def _extract_members_from_remote_zip(
    *,
    url: str,
    destination_dir: Path,
    include_predicate,
) -> List[Path]:
    content_length = _get_remote_content_length(url, timeout_sec=120)
    if content_length is None:
        raise RuntimeError(f"failed to get Content-Length for remote zip: {url}")

    reader = _HTTPRangeReader(url=url, size=content_length, timeout_sec=300)
    extracted_paths: List[Path] = []
    with zipfile.ZipFile(reader, "r") as archive:
        members = [name for name in archive.namelist() if include_predicate(name)]
        if not members:
            raise RuntimeError(f"no matching members in remote zip: {url}")
        for member in members:
            relative_name = member.split("/")[-1]
            target = destination_dir / relative_name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as source, target.open("wb") as sink:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    sink.write(chunk)
            extracted_paths.append(target)
    reader.close()
    return extracted_paths


def _prepare_beir(
    spec: DatasetSpec, raw_dir: Path, max_records: int, seed: int
) -> Tuple[List[Dict[str, Any]], List[Path]]:
    dataset_name = spec.key.replace("beir_", "")

    def _is_target_member(name: str) -> bool:
        normalized = name.replace("\\", "/")
        return normalized.endswith("queries.jsonl") or (
            "/qrels/" in normalized and normalized.lower().endswith(".tsv")
        )

    extracted_files = _extract_members_from_remote_zip(
        url=spec.source_url,
        destination_dir=raw_dir,
        include_predicate=_is_target_member,
    )

    rows: List[Dict[str, Any]] = []
    queries_file = next((p for p in extracted_files if p.name == "queries.jsonl"), None)
    if queries_file is None:
        raise RuntimeError(f"{spec.key}: extracted queries.jsonl not found")

    queries: Dict[str, str] = {}
    with queries_file.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            query_id = str(item.get("_id") or item.get("id") or "").strip()
            query_text = str(item.get("text") or item.get("query") or "").strip()
            if query_id and query_text:
                queries[query_id] = query_text

    qrel_files = sorted([p for p in extracted_files if p.name.endswith(".tsv")])
    if not qrel_files:
        raise RuntimeError(f"{spec.key}: extracted qrels files not found")

    for qrel_file in qrel_files:
        split = qrel_file.stem.lower()
        qrel_map = _parse_qrels_tsv(qrel_file)
        for query_id, doc_ids in qrel_map.items():
            query = queries.get(query_id)
            if not query:
                continue
            rows.append(
                {
                    "id": f"{spec.key}:{split}:{query_id}",
                    "query": query,
                    "relevant_uris_or_doc_ids": sorted(set(doc_ids)),
                    "language": spec.language,
                    "domain": spec.domain,
                    "source_dataset": spec.key,
                    "split": split,
                }
            )

    if not rows:
        raise RuntimeError(f"{spec.key}: normalized rows is empty")
    return _stable_pick_rows(rows, spec.key, seed, max_records), extracted_files


def _prepare_dataset_rows(
    spec: DatasetSpec,
    max_records: int,
    seed: int,
) -> Tuple[List[Dict[str, Any]], List[Path]]:
    dataset_raw_dir = RAW_DIR / spec.key
    dataset_raw_dir.mkdir(parents=True, exist_ok=True)

    if spec.key == "squad_v2_dev":
        return _prepare_squad_v2(spec, dataset_raw_dir, max_records, seed)
    if spec.key == "dailydialog":
        return _prepare_dailydialog(spec, dataset_raw_dir, max_records, seed)
    if spec.key == "msmarco_passages":
        return _prepare_msmarco(spec, dataset_raw_dir, max_records, seed)
    if spec.key.startswith("beir_"):
        return _prepare_beir(spec, dataset_raw_dir, max_records, seed)
    raise ValueError(f"unsupported dataset key: {spec.key}")


def _write_manifest(
    *,
    spec: DatasetSpec,
    rows: List[Dict[str, Any]],
    raw_files: List[Path],
    full_file: Path,
    sample_files: Dict[int, Path],
    sample_counts: Dict[int, int],
) -> Path:
    manifest_path = MANIFESTS_DIR / f"{spec.key}.json"
    raw_file_meta = []
    for raw_file in raw_files:
        raw_file_meta.append(
            {
                "path": _project_rel(raw_file),
                "sha256": _sha256_file(raw_file),
                "size_bytes": raw_file.stat().st_size,
            }
        )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "dataset": spec.key,
        "source_url": spec.source_url,
        "downloaded_at_utc": _utc_now(),
        "record_count_full": len(rows),
        "sample_counts": {str(size): int(count) for size, count in sorted(sample_counts.items())},
        "raw_files": raw_file_meta,
        "processed_files": {"full": _project_rel(full_file)},
        "sample_files": {
            str(size): _project_rel(path) for size, path in sorted(sample_files.items())
        },
        "status": "ready",
    }

    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def prepare_all_datasets(
    *,
    max_records: int,
    sample_sizes: Sequence[int],
    seed: int,
    datasets: Sequence[str] | None = None,
) -> List[Path]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    selected = (
        {item.strip() for item in datasets if item.strip()}
        if datasets is not None
        else {spec.key for spec in DATASET_SPECS}
    )

    manifest_paths: List[Path] = []
    for spec in DATASET_SPECS:
        if spec.key not in selected:
            continue
        print(f"[dataset] preparing {spec.key} ...", flush=True)
        rows, raw_files = _prepare_dataset_rows(spec, max_records=max_records, seed=seed)
        if not rows:
            raise RuntimeError(f"{spec.key}: normalized rows is empty")

        full_file = PROCESSED_DIR / f"{spec.key}.jsonl"
        full_count = _write_jsonl(full_file, rows)

        sampled = _sample_rows(rows, spec.key, seed=seed, sample_sizes=sample_sizes)
        sample_files: Dict[int, Path] = {}
        sample_counts: Dict[int, int] = {}
        for sample_size, sample_rows in sampled.items():
            sample_file = PROCESSED_DIR / f"{spec.key}_sample_{sample_size}.jsonl"
            count = _write_jsonl(sample_file, sample_rows)
            sample_files[sample_size] = sample_file
            sample_counts[sample_size] = count

        manifest_path = _write_manifest(
            spec=spec,
            rows=rows[:full_count],
            raw_files=raw_files,
            full_file=full_file,
            sample_files=sample_files,
            sample_counts=sample_counts,
        )
        manifest_paths.append(manifest_path)
        print(
            f"[dataset] {spec.key}: full={full_count}, "
            + ", ".join(
                f"sample_{size}={sample_counts[size]}" for size in sorted(sample_counts)
            ),
            flush=True,
        )
    return manifest_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and normalize benchmark public datasets."
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=DEFAULT_MAX_RECORDS,
        help="Maximum normalized rows per dataset.",
    )
    parser.add_argument(
        "--sample-sizes",
        type=int,
        nargs="+",
        default=list(DEFAULT_SAMPLE_SIZES),
        help="Sample sizes for PR/Nightly/Weekly gates.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Deterministic sampling seed.",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=[],
        help="Optional subset of dataset keys to process.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifests = prepare_all_datasets(
        max_records=max(1, int(args.max_records)),
        sample_sizes=args.sample_sizes,
        seed=int(args.seed),
        datasets=args.datasets if args.datasets else None,
    )
    print(f"[done] manifests generated: {len(manifests)}", flush=True)
    for manifest in manifests:
        print(f" - {manifest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
