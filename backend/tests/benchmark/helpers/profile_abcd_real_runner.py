"""Real A/B/C/D benchmark runner for retrieval profiles.

This module complements the deterministic Phase4/6 baseline artifacts by
executing real retrieval over locally materialized benchmark corpora.

Current real-run scope:
- Profile A: keyword only
- Profile B: hybrid + hash embedding
- Profile C: hybrid + API embedding (no reranker)
- Profile D: hybrid + API embedding + reranker

Datasets in this runner focus on corpora that can be materialized in-repo:
- squad_v2_dev
- beir_nfcorpus
"""

from __future__ import annotations

import io
import json
import math
import os
import random
import shutil
import sys
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set

import requests

BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from db.sqlite_client import SQLiteClient

from .common import BENCHMARK_DIR, DATASETS_DIR

REAL_PROFILE_JSON_ARTIFACT = BENCHMARK_DIR / "profile_abcd_real_metrics.json"
REAL_PROFILE_MARKDOWN_ARTIFACT = BENCHMARK_DIR / "benchmark_results_profile_abcd_real.md"
REAL_PROFILE_CD_MARKDOWN_ARTIFACT = BENCHMARK_DIR / "benchmark_results_profile_cd_real.md"
REAL_PROFILE_WORKDIR = BENCHMARK_DIR / ".real_profile_cache"

PROFILE_D_INVALID_GATE_REASONS = {
    "embedding_fallback_hash",
    "embedding_request_failed",
    "reranker_request_failed",
}

DATASET_LABELS = {
    "squad_v2_dev": "SQuAD v2 Dev",
    "beir_nfcorpus": "BEIR NFCorpus",
}

REAL_DATASET_DEFAULTS = ("squad_v2_dev", "beir_nfcorpus")
REAL_RANDOM_SEED = 20260219


@dataclass(frozen=True)
class QueryCase:
    query_id: str
    query: str
    relevant_doc_ids: Set[str]


@dataclass(frozen=True)
class DatasetBundle:
    key: str
    label: str
    domain: str
    queries: List[QueryCase]
    docs: List[tuple[str, str]]
    sample_bucket_size: int
    query_count_raw: int


@dataclass(frozen=True)
class ProfileConfig:
    key: str
    mode: str
    env_overrides: Mapping[str, str]
    reuse_data_from: Optional[str] = None


PROFILE_CONFIGS: Sequence[ProfileConfig] = (
    ProfileConfig(
        key="profile_a",
        mode="keyword",
        env_overrides={
            "RETRIEVAL_EMBEDDING_BACKEND": "none",
            "RETRIEVAL_RERANKER_ENABLED": "false",
        },
    ),
    ProfileConfig(
        key="profile_b",
        mode="hybrid",
        env_overrides={
            "RETRIEVAL_EMBEDDING_BACKEND": "hash",
            "RETRIEVAL_EMBEDDING_DIM": "64",
            "RETRIEVAL_RERANKER_ENABLED": "false",
        },
    ),
    ProfileConfig(
        key="profile_c",
        mode="hybrid",
        env_overrides={
            "RETRIEVAL_EMBEDDING_BACKEND": "api",
            "RETRIEVAL_RERANKER_ENABLED": "false",
        },
    ),
    ProfileConfig(
        key="profile_d",
        mode="hybrid",
        env_overrides={
            "RETRIEVAL_EMBEDDING_BACKEND": "api",
            "RETRIEVAL_RERANKER_ENABLED": "true",
        },
        reuse_data_from="profile_c",
    ),
)

SOTA_BENCHMARK_REFERENCES_2026_02 = {
    "bm25_avg_ndcg10": 0.43,
    "dense_avg_ndcg10_min": 0.48,
    "dense_avg_ndcg10_max": 0.52,
    "hybrid_avg_ndcg10_min": 0.54,
    "hybrid_avg_ndcg10_max": 0.56,
    "hybrid_rerank_avg_ndcg10_min": 0.58,
    "hybrid_rerank_avg_ndcg10_max": 0.62,
    "sota_avg_ndcg10_floor": 0.62,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slugify(value: str) -> str:
    lowered = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    compact = "_".join(chunk for chunk in lowered.split("_") if chunk)
    return compact or "untitled"


def _sample_bucket_size(sample_size: int) -> int:
    if sample_size <= 100:
        return 100
    if sample_size <= 200:
        return 200
    return 500


def _load_jsonl_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


def _squad_raw_path() -> Path:
    return DATASETS_DIR / "raw" / "squad_v2_dev" / "squad_dev_v2.json"


def _beir_raw_dir(dataset_key: str) -> Path:
    return DATASETS_DIR / "raw" / dataset_key


def _dataset_source_url(dataset_key: str) -> str:
    manifest_path = DATASETS_DIR / "manifests" / f"{dataset_key}.json"
    if not manifest_path.exists():
        raise RuntimeError(f"missing dataset manifest: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_url = str(payload.get("source_url") or "").strip()
    if not source_url:
        raise RuntimeError(f"manifest missing source_url: {manifest_path}")
    return source_url


def _ensure_beir_corpus_jsonl(dataset_key: str) -> Path:
    raw_dir = _beir_raw_dir(dataset_key)
    raw_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = raw_dir / "corpus.jsonl"
    if corpus_path.exists():
        return corpus_path

    source_url = _dataset_source_url(dataset_key)
    response = requests.get(source_url, timeout=300)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content), "r") as archive:
        member_name = next(
            (
                name
                for name in archive.namelist()
                if name.endswith("/corpus.jsonl") or name == "corpus.jsonl"
            ),
            None,
        )
        if member_name is None:
            raise RuntimeError(f"{dataset_key}: corpus.jsonl not found in source zip")
        with archive.open(member_name, "r") as source, corpus_path.open("wb") as target:
            shutil.copyfileobj(source, target)

    return corpus_path


def _load_squad_corpus() -> Dict[str, str]:
    path = _squad_raw_path()
    if not path.exists():
        raise RuntimeError(f"missing SQuAD raw dataset: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))

    corpus: Dict[str, str] = {}
    for article in payload.get("data", []):
        title = str(article.get("title") or "untitled")
        title_slug = _slugify(title)
        for paragraph_idx, paragraph in enumerate(article.get("paragraphs", [])):
            context = str(paragraph.get("context") or "").strip()
            if not context:
                continue
            doc_id = f"squad_v2:{title_slug}:{paragraph_idx}"
            corpus[doc_id] = context
    return corpus


def _load_beir_corpus(dataset_key: str) -> Dict[str, str]:
    corpus_path = _ensure_beir_corpus_jsonl(dataset_key)
    corpus: Dict[str, str] = {}
    for row in _load_jsonl_rows(corpus_path):
        doc_id = str(row.get("_id") or row.get("id") or "").strip()
        if not doc_id:
            continue
        title = str(row.get("title") or "").strip()
        text = str(row.get("text") or "").strip()
        combined = text if not title else f"{title}\n{text}".strip()
        if combined:
            corpus[doc_id] = combined
    if not corpus:
        raise RuntimeError(f"{dataset_key}: loaded empty corpus from {corpus_path}")
    return corpus


def _load_dataset_corpus(dataset_key: str) -> Dict[str, str]:
    if dataset_key == "squad_v2_dev":
        return _load_squad_corpus()
    if dataset_key == "beir_nfcorpus":
        return _load_beir_corpus(dataset_key)
    raise RuntimeError(f"unsupported real benchmark dataset: {dataset_key}")


def _load_sample_queries(dataset_key: str, sample_size: int) -> tuple[List[Dict[str, Any]], int]:
    bucket_size = _sample_bucket_size(sample_size)
    sample_path = DATASETS_DIR / "processed" / f"{dataset_key}_sample_{bucket_size}.jsonl"
    if not sample_path.exists():
        raise RuntimeError(f"missing sample file: {sample_path}")
    rows = _load_jsonl_rows(sample_path)
    if not rows:
        raise RuntimeError(f"sample file has no rows: {sample_path}")
    return rows[:sample_size], bucket_size


def _build_query_cases(
    *,
    rows: Sequence[Mapping[str, Any]],
    corpus: Mapping[str, str],
    first_relevant_only: bool,
) -> List[QueryCase]:
    cases: List[QueryCase] = []
    for row in rows:
        query_id = str(row.get("id") or "").strip()
        query = str(row.get("query") or "").strip()
        if not query_id or not query:
            continue
        relevant_raw = row.get("relevant_uris_or_doc_ids")
        if not isinstance(relevant_raw, list):
            continue
        relevant_available = [str(item) for item in relevant_raw if str(item) in corpus]
        if first_relevant_only and relevant_available:
            relevant_available = relevant_available[:1]
        relevant_set = {item for item in relevant_available if item}
        if not relevant_set:
            continue
        cases.append(QueryCase(query_id=query_id, query=query, relevant_doc_ids=relevant_set))
    return cases


def _select_doc_ids(
    *,
    corpus: Mapping[str, str],
    queries: Sequence[QueryCase],
    extra_distractors: int,
    seed: int,
) -> List[str]:
    required: Set[str] = set()
    for case in queries:
        required.update(case.relevant_doc_ids)

    pool = [doc_id for doc_id in corpus if doc_id not in required]
    rng = random.Random(seed)
    rng.shuffle(pool)
    distractors = pool[: max(0, int(extra_distractors))]
    selected = sorted(required.union(distractors))
    return selected


def build_dataset_bundle(
    *,
    dataset_key: str,
    sample_size: int,
    first_relevant_only: bool,
    extra_distractors: int,
    seed: int,
) -> DatasetBundle:
    corpus = _load_dataset_corpus(dataset_key)
    rows, bucket_size = _load_sample_queries(dataset_key, sample_size)
    queries = _build_query_cases(
        rows=rows,
        corpus=corpus,
        first_relevant_only=first_relevant_only,
    )
    if not queries:
        raise RuntimeError(
            f"{dataset_key}: no valid query cases after intersecting with corpus"
        )

    selected_doc_ids = _select_doc_ids(
        corpus=corpus,
        queries=queries,
        extra_distractors=extra_distractors,
        seed=seed,
    )
    docs = [(doc_id, corpus[doc_id]) for doc_id in selected_doc_ids]
    if not docs:
        raise RuntimeError(f"{dataset_key}: selected corpus docs is empty")

    return DatasetBundle(
        key=dataset_key,
        label=DATASET_LABELS.get(dataset_key, dataset_key),
        domain=f"bench_{dataset_key}",
        queries=queries,
        docs=docs,
        sample_bucket_size=bucket_size,
        query_count_raw=len(rows),
    )


def compute_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])

    p = max(0.0, min(1.0, float(percentile)))
    sorted_values = sorted(float(v) for v in values)
    pos = p * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    fraction = pos - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * fraction


def compute_retrieval_metrics(
    *,
    retrieved_doc_ids: Sequence[str],
    relevant_doc_ids: Set[str],
    k: int = 10,
) -> Dict[str, float]:
    if not relevant_doc_ids:
        return {
            "hr_at_5": 0.0,
            "hr_at_10": 0.0,
            "mrr": 0.0,
            "ndcg_at_10": 0.0,
            "recall_at_10": 0.0,
        }

    unique_retrieved: List[str] = []
    seen: Set[str] = set()
    for doc_id in retrieved_doc_ids:
        if doc_id in seen:
            continue
        seen.add(doc_id)
        unique_retrieved.append(doc_id)

    topk = list(unique_retrieved[: max(1, int(k))])
    top5 = topk[:5]
    top10 = topk[:10]

    hr_at_5 = 1.0 if any(doc_id in relevant_doc_ids for doc_id in top5) else 0.0
    hr_at_10 = 1.0 if any(doc_id in relevant_doc_ids for doc_id in top10) else 0.0

    first_rank: Optional[int] = None
    for idx, doc_id in enumerate(top10, start=1):
        if doc_id in relevant_doc_ids:
            first_rank = idx
            break
    mrr = 0.0 if first_rank is None else (1.0 / float(first_rank))

    hit_count = sum(1 for doc_id in top10 if doc_id in relevant_doc_ids)
    recall_at_10 = hit_count / float(len(relevant_doc_ids))

    gains = [1.0 if doc_id in relevant_doc_ids else 0.0 for doc_id in top10]
    dcg = sum(gain / math.log2(idx + 2.0) for idx, gain in enumerate(gains))
    ideal_hits = min(len(relevant_doc_ids), len(top10))
    idcg = sum(1.0 / math.log2(idx + 2.0) for idx in range(ideal_hits))
    ndcg_at_10 = 0.0 if idcg == 0 else (dcg / idcg)

    return {
        "hr_at_5": hr_at_5,
        "hr_at_10": hr_at_10,
        "mrr": mrr,
        "ndcg_at_10": ndcg_at_10,
        "recall_at_10": recall_at_10,
    }


@contextmanager
def patched_env(overrides: Mapping[str, str]):
    previous: Dict[str, Optional[str]] = {}
    for key, value in overrides.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = str(value)
    try:
        yield
    finally:
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def _reset_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()


async def _populate_bundle_docs(
    client: SQLiteClient,
    bundle: DatasetBundle,
) -> Dict[int, str]:
    memory_to_doc: Dict[int, str] = {}
    for idx, (doc_id, content) in enumerate(bundle.docs):
        if not content.strip():
            continue
        result = await client.create_memory(
            parent_path="",
            content=content,
            priority=10,
            title=f"{bundle.key}_{idx:05d}",
            domain=bundle.domain,
            index_now=True,
        )
        memory_id = int(result["id"])
        memory_to_doc[memory_id] = doc_id
    if not memory_to_doc:
        raise RuntimeError(f"{bundle.key}: no documents indexed for benchmark")
    return memory_to_doc


def _round_metric(value: float) -> float:
    return round(float(value), 6)


def build_phase6_gate(profile_d_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    gate_rows: List[Dict[str, Any]] = []
    invalid_union: Set[str] = set()
    for row in profile_d_rows:
        degradation = row.get("degradation", {})
        invalid_reasons = [
            reason
            for reason in degradation.get("invalid_reasons", [])
            if isinstance(reason, str)
        ]
        invalid_union.update(invalid_reasons)
        gate_rows.append(
            {
                "dataset": row.get("dataset"),
                "dataset_label": row.get("dataset_label"),
                "valid": len(invalid_reasons) == 0,
                "invalid_reasons": invalid_reasons,
            }
        )
    return {
        "valid": len(invalid_union) == 0,
        "invalid_reasons": sorted(invalid_union),
        "rows": gate_rows,
    }


def _build_comparison_rows(
    profiles: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    rows_a = {row["dataset"]: row for row in profiles["profile_a"]["rows"]}
    rows_b = {row["dataset"]: row for row in profiles["profile_b"]["rows"]}
    rows_c = {row["dataset"]: row for row in profiles["profile_c"]["rows"]}
    rows_d = {row["dataset"]: row for row in profiles["profile_d"]["rows"]}

    comparison_rows: List[Dict[str, Any]] = []
    for dataset_key in rows_d:
        row_a = rows_a[dataset_key]
        row_b = rows_b[dataset_key]
        row_c = rows_c[dataset_key]
        row_d = rows_d[dataset_key]
        invalid_reasons = list(row_d.get("degradation", {}).get("invalid_reasons", []))
        comparison_rows.append(
            {
                "dataset": dataset_key,
                "dataset_label": row_d["dataset_label"],
                "a_hr10": row_a["quality"]["hr_at_10"],
                "b_hr10": row_b["quality"]["hr_at_10"],
                "c_hr10": row_c["quality"]["hr_at_10"],
                "d_hr10": row_d["quality"]["hr_at_10"],
                "a_ndcg10": row_a["quality"]["ndcg_at_10"],
                "b_ndcg10": row_b["quality"]["ndcg_at_10"],
                "c_ndcg10": row_c["quality"]["ndcg_at_10"],
                "d_ndcg10": row_d["quality"]["ndcg_at_10"],
                "a_p95": row_a["latency_ms"]["p95"],
                "b_p95": row_b["latency_ms"]["p95"],
                "c_p95": row_c["latency_ms"]["p95"],
                "d_p95": row_d["latency_ms"]["p95"],
                "valid": len(invalid_reasons) == 0,
                "invalid_reasons": invalid_reasons,
            }
        )
    return comparison_rows


async def _evaluate_dataset(
    *,
    client: SQLiteClient,
    bundle: DatasetBundle,
    profile_mode: str,
    memory_to_doc: Mapping[int, str],
) -> Dict[str, Any]:
    metric_rows: List[Dict[str, float]] = []
    latencies_ms: List[float] = []
    degrade_count = 0
    degrade_reasons_union: Set[str] = set()
    invalid_reasons_union: Set[str] = set()

    for case in bundle.queries:
        start = perf_counter()
        payload = await client.search_advanced(
            query=case.query,
            mode=profile_mode,
            max_results=10,
            candidate_multiplier=4,
            filters={"domain": bundle.domain},
        )
        elapsed_ms = (perf_counter() - start) * 1000.0
        latencies_ms.append(elapsed_ms)

        degraded = bool(payload.get("degraded"))
        if degraded:
            degrade_count += 1

        raw_reasons = payload.get("degrade_reasons")
        reasons = (
            [reason for reason in raw_reasons if isinstance(reason, str)]
            if isinstance(raw_reasons, list)
            else []
        )
        degrade_reasons_union.update(reasons)
        invalid_reasons_union.update(
            reason for reason in reasons if reason in PROFILE_D_INVALID_GATE_REASONS
        )

        results = payload.get("results")
        result_rows = results if isinstance(results, list) else []
        retrieved_doc_ids: List[str] = []
        for item in result_rows[:10]:
            if not isinstance(item, Mapping):
                continue
            memory_id = item.get("memory_id")
            if memory_id is None:
                continue
            try:
                parsed_memory_id = int(memory_id)
            except (TypeError, ValueError):
                continue
            doc_id = memory_to_doc.get(parsed_memory_id)
            if doc_id:
                retrieved_doc_ids.append(doc_id)

        metric_rows.append(
            compute_retrieval_metrics(
                retrieved_doc_ids=retrieved_doc_ids,
                relevant_doc_ids=case.relevant_doc_ids,
                k=10,
            )
        )

    query_count = len(metric_rows)
    if query_count <= 0:
        raise RuntimeError(f"{bundle.key}: query_count is zero after evaluation")

    quality = {
        "hr_at_5": _round_metric(
            sum(item["hr_at_5"] for item in metric_rows) / float(query_count)
        ),
        "hr_at_10": _round_metric(
            sum(item["hr_at_10"] for item in metric_rows) / float(query_count)
        ),
        "mrr": _round_metric(sum(item["mrr"] for item in metric_rows) / float(query_count)),
        "ndcg_at_10": _round_metric(
            sum(item["ndcg_at_10"] for item in metric_rows) / float(query_count)
        ),
        "recall_at_10": _round_metric(
            sum(item["recall_at_10"] for item in metric_rows) / float(query_count)
        ),
    }

    row = {
        "dataset": bundle.key,
        "dataset_label": bundle.label,
        "mode": profile_mode,
        "sample_size": query_count,
        "query_count": query_count,
        "query_count_raw": bundle.query_count_raw,
        "sample_bucket_size": bundle.sample_bucket_size,
        "corpus_doc_count": len(bundle.docs),
        "quality": quality,
        "latency_ms": {
            "p50": _round_metric(compute_percentile(latencies_ms, 0.50)),
            "p95": _round_metric(compute_percentile(latencies_ms, 0.95)),
            "p99": _round_metric(compute_percentile(latencies_ms, 0.99)),
        },
        "degradation": {
            "queries": query_count,
            "degraded": int(degrade_count),
            "degrade_rate": _round_metric(float(degrade_count) / float(query_count)),
            "degrade_reasons": sorted(degrade_reasons_union),
            "invalid_reasons": sorted(invalid_reasons_union),
            "valid": len(invalid_reasons_union) == 0,
        },
    }
    return row


async def _run_profile(
    *,
    config: ProfileConfig,
    bundles: Sequence[DatasetBundle],
    db_path: Path,
    existing_mapping: Optional[Mapping[str, Mapping[int, str]]] = None,
    populate: bool,
) -> tuple[Dict[str, Any], Dict[str, Dict[int, str]]]:
    profile_mapping: Dict[str, Dict[int, str]] = (
        {key: dict(value) for key, value in existing_mapping.items()}
        if existing_mapping is not None
        else {}
    )

    with patched_env(config.env_overrides):
        client = SQLiteClient(_sqlite_url(db_path))
        try:
            await client.init_db()

            if populate:
                for bundle in bundles:
                    profile_mapping[bundle.key] = await _populate_bundle_docs(client, bundle)
            else:
                for bundle in bundles:
                    if bundle.key not in profile_mapping:
                        raise RuntimeError(
                            f"{config.key}: missing pre-populated mapping for {bundle.key}"
                        )

            rows: List[Dict[str, Any]] = []
            for bundle in bundles:
                rows.append(
                    await _evaluate_dataset(
                        client=client,
                        bundle=bundle,
                        profile_mode=config.mode,
                        memory_to_doc=profile_mapping[bundle.key],
                    )
                )
            return (
                {
                    "profile": config.key,
                    "mode": config.mode,
                    "rows": rows,
                },
                profile_mapping,
            )
        finally:
            await client.close()


async def build_profile_abcd_real_metrics(
    *,
    sample_size: int = 30,
    dataset_keys: Sequence[str] = REAL_DATASET_DEFAULTS,
    first_relevant_only: bool = True,
    extra_distractors: int = 200,
    seed: int = REAL_RANDOM_SEED,
) -> Dict[str, Any]:
    if int(sample_size) <= 0:
        raise ValueError("sample_size must be > 0")

    selected_keys = [key for key in dataset_keys if key in DATASET_LABELS]
    if not selected_keys:
        raise RuntimeError("no supported dataset_keys selected")

    bundles: List[DatasetBundle] = []
    for index, dataset_key in enumerate(selected_keys):
        bundles.append(
            build_dataset_bundle(
                dataset_key=dataset_key,
                sample_size=int(sample_size),
                first_relevant_only=bool(first_relevant_only),
                extra_distractors=int(extra_distractors),
                seed=seed + index * 17,
            )
        )

    profile_results: Dict[str, Dict[str, Any]] = {}
    profile_doc_mappings: Dict[str, Dict[str, Dict[int, str]]] = {}

    REAL_PROFILE_WORKDIR.mkdir(parents=True, exist_ok=True)
    db_paths = {
        config.key: REAL_PROFILE_WORKDIR / f"{config.key}.db"
        for config in PROFILE_CONFIGS
    }

    for config in PROFILE_CONFIGS:
        if config.reuse_data_from is None:
            _reset_db(db_paths[config.key])
            existing_mapping = None
            populate = True
        else:
            source_key = config.reuse_data_from
            source_db_path = db_paths[source_key]
            if not source_db_path.exists():
                raise RuntimeError(
                    f"{config.key}: source profile db missing: {source_db_path}"
                )
            db_paths[config.key] = source_db_path
            existing_mapping = profile_doc_mappings.get(source_key)
            populate = False

        profile_payload, mapping = await _run_profile(
            config=config,
            bundles=bundles,
            db_path=db_paths[config.key],
            existing_mapping=existing_mapping,
            populate=populate,
        )
        profile_results[config.key] = profile_payload
        profile_doc_mappings[config.key] = mapping

    phase6_gate = build_phase6_gate(profile_results["profile_d"]["rows"])
    phase6_comparison = _build_comparison_rows(profile_results)

    return {
        "generated_at_utc": _utc_now_iso(),
        "source": "backend/tests/benchmark/helpers/profile_abcd_real_runner.py",
        "sample_size_requested": int(sample_size),
        "dataset_scope": selected_keys,
        "dataset_labels": {bundle.key: bundle.label for bundle in bundles},
        "real_run_strategy": {
            "first_relevant_only": bool(first_relevant_only),
            "extra_distractors": int(extra_distractors),
            "seed": int(seed),
        },
        "profiles": profile_results,
        "phase6": {
            "gate": phase6_gate,
            "comparison_rows": phase6_comparison,
            "invalid_gate_reasons": sorted(PROFILE_D_INVALID_GATE_REASONS),
        },
    }


def _render_profile_rows_table(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    lines = [
        "| Dataset | Queries | Corpus Docs | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) | Degrade Rate | Invalid Reasons |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        degradation = row["degradation"]
        invalid_reasons = ",".join(degradation["invalid_reasons"]) or "-"
        lines.append(
            "| {dataset} | {queries} | {corpus_docs} | {hr10:.3f} | {mrr:.3f} | {ndcg:.3f} | {recall:.3f} | {p95:.1f} | {degrade_rate:.1%} | {invalid} |".format(
                dataset=row["dataset_label"],
                queries=int(row["query_count"]),
                corpus_docs=int(row["corpus_doc_count"]),
                hr10=float(row["quality"]["hr_at_10"]),
                mrr=float(row["quality"]["mrr"]),
                ndcg=float(row["quality"]["ndcg_at_10"]),
                recall=float(row["quality"]["recall_at_10"]),
                p95=float(row["latency_ms"]["p95"]),
                degrade_rate=float(degradation["degrade_rate"]),
                invalid=invalid_reasons,
            )
        )
    return lines


def render_profile_abcd_real_markdown(payload: Mapping[str, Any]) -> str:
    lines: List[str] = [
        "# Benchmark Results - profile_abcd_real",
        "",
        f"> generated_at_utc: {payload['generated_at_utc']}",
        "> mode: real execution (SQLiteClient.search_advanced + runtime profile env)",
        "",
        "## Run Strategy",
        "",
        f"- dataset_scope: {', '.join(payload.get('dataset_scope', []))}",
        f"- sample_size_requested: {payload.get('sample_size_requested')}",
        f"- first_relevant_only: {payload['real_run_strategy']['first_relevant_only']}",
        f"- extra_distractors: {payload['real_run_strategy']['extra_distractors']}",
        "",
    ]

    profiles = payload["profiles"]
    for profile_key in ("profile_a", "profile_b", "profile_c", "profile_d"):
        profile_payload = profiles[profile_key]
        lines.extend(
            [
                f"## {profile_key}",
                "",
                f"- mode: `{profile_payload['mode']}`",
                "",
            ]
        )
        lines.extend(_render_profile_rows_table(profile_payload["rows"]))
        lines.append("")

    lines.extend(
        [
            "## Phase 6 Gate (Profile D)",
            "",
            (
                f"- overall_valid: {'true' if payload['phase6']['gate']['valid'] else 'false'}"
            ),
            (
                "- invalid_reasons: "
                + ", ".join(payload["phase6"]["gate"]["invalid_reasons"])
                if payload["phase6"]["gate"]["invalid_reasons"]
                else "- invalid_reasons: (none)"
            ),
            "",
            "| Dataset | Valid | Invalid Reasons |",
            "|---|---|---|",
        ]
    )
    for row in payload["phase6"]["gate"]["rows"]:
        reasons = ",".join(row["invalid_reasons"]) if row["invalid_reasons"] else "-"
        lines.append(
            f"| {row['dataset_label']} | {'PASS' if row['valid'] else 'INVALID'} | {reasons} |"
        )

    lines.extend(
        [
            "",
            "## A/B/C/D Comparison",
            "",
            "| Dataset | A HR@10 | B HR@10 | C HR@10 | D HR@10 | A NDCG@10 | B NDCG@10 | C NDCG@10 | D NDCG@10 | A p95 | B p95 | C p95 | D p95 | D Gate |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in payload["phase6"]["comparison_rows"]:
        lines.append(
            "| {dataset} | {a_hr10:.3f} | {b_hr10:.3f} | {c_hr10:.3f} | {d_hr10:.3f} | "
            "{a_ndcg10:.3f} | {b_ndcg10:.3f} | {c_ndcg10:.3f} | {d_ndcg10:.3f} | "
            "{a_p95:.1f} | {b_p95:.1f} | {c_p95:.1f} | {d_p95:.1f} | {gate} |".format(
                dataset=row["dataset_label"],
                a_hr10=float(row["a_hr10"]),
                b_hr10=float(row["b_hr10"]),
                c_hr10=float(row["c_hr10"]),
                d_hr10=float(row["d_hr10"]),
                a_ndcg10=float(row["a_ndcg10"]),
                b_ndcg10=float(row["b_ndcg10"]),
                c_ndcg10=float(row["c_ndcg10"]),
                d_ndcg10=float(row["d_ndcg10"]),
                a_p95=float(row["a_p95"]),
                b_p95=float(row["b_p95"]),
                c_p95=float(row["c_p95"]),
                d_p95=float(row["d_p95"]),
                gate="PASS" if row["valid"] else "INVALID",
            )
        )
    lines.append("")
    return "\n".join(lines)


def render_profile_cd_real_markdown(payload: Mapping[str, Any]) -> str:
    profile_c = payload["profiles"]["profile_c"]
    profile_d = payload["profiles"]["profile_d"]
    lines: List[str] = [
        "# Benchmark Results - profile_cd_real",
        "",
        f"> generated_at_utc: {payload['generated_at_utc']}",
        "> mode: real API embedding/reranker execution",
        "",
        "## profile_c",
        "",
    ]
    lines.extend(_render_profile_rows_table(profile_c["rows"]))
    lines.extend(
        [
            "",
            "## profile_d",
            "",
        ]
    )
    lines.extend(_render_profile_rows_table(profile_d["rows"]))
    lines.append("")
    lines.extend(
        [
            "## Phase 6 Gate",
            "",
            f"- overall_valid: {'true' if payload['phase6']['gate']['valid'] else 'false'}",
            (
                "- invalid_reasons: "
                + ", ".join(payload["phase6"]["gate"]["invalid_reasons"])
                if payload["phase6"]["gate"]["invalid_reasons"]
                else "- invalid_reasons: (none)"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def write_profile_abcd_real_artifacts(
    payload: Mapping[str, Any],
    *,
    json_path: Path = REAL_PROFILE_JSON_ARTIFACT,
    markdown_path: Path = REAL_PROFILE_MARKDOWN_ARTIFACT,
    cd_markdown_path: Path = REAL_PROFILE_CD_MARKDOWN_ARTIFACT,
) -> Dict[str, Path]:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_profile_abcd_real_markdown(payload),
        encoding="utf-8",
    )
    cd_markdown_path.write_text(
        render_profile_cd_real_markdown(payload),
        encoding="utf-8",
    )
    return {
        "json": json_path,
        "markdown": markdown_path,
        "cd_markdown": cd_markdown_path,
    }


def _profile_dataset_mean(rows: Sequence[Mapping[str, Any]], field: str) -> float:
    if not rows:
        return 0.0
    if field == "p95":
        values = [float(row["latency_ms"]["p95"]) for row in rows]
    else:
        values = [float(row["quality"][field]) for row in rows]
    return sum(values) / float(len(values))


def render_abcd_sota_analysis_markdown(payload: Mapping[str, Any]) -> str:
    profiles = payload["profiles"]
    lines = [
        "# A/B/C/D Benchmark Analysis vs 2026-02 Reference",
        "",
        f"- generated_at_utc: {payload['generated_at_utc']}",
        f"- dataset_scope: {', '.join(payload.get('dataset_scope', []))}",
        f"- sample_size_requested: {payload.get('sample_size_requested')}",
        "",
        "## Profile Means (dataset-average)",
        "",
        "| Profile | HR@10 | MRR | NDCG@10 | Recall@10 | p95(ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for profile_key in ("profile_a", "profile_b", "profile_c", "profile_d"):
        rows = profiles[profile_key]["rows"]
        lines.append(
            "| {profile} | {hr10:.3f} | {mrr:.3f} | {ndcg:.3f} | {recall:.3f} | {p95:.1f} |".format(
                profile=profile_key,
                hr10=_profile_dataset_mean(rows, "hr_at_10"),
                mrr=_profile_dataset_mean(rows, "mrr"),
                ndcg=_profile_dataset_mean(rows, "ndcg_at_10"),
                recall=_profile_dataset_mean(rows, "recall_at_10"),
                p95=_profile_dataset_mean(rows, "p95"),
            )
        )

    refs = SOTA_BENCHMARK_REFERENCES_2026_02
    d_ndcg = _profile_dataset_mean(profiles["profile_d"]["rows"], "ndcg_at_10")
    lines.extend(
        [
            "",
            "## 2026-02 Reference Ranges (from project docs)",
            "",
            "| Reference | Avg NDCG@10 |",
            "|---|---:|",
            f"| BM25 baseline | ~{refs['bm25_avg_ndcg10']:.2f} |",
            f"| Dense retrieval | ~{refs['dense_avg_ndcg10_min']:.2f}–{refs['dense_avg_ndcg10_max']:.2f} |",
            f"| Hybrid (embedding+BM25) | ~{refs['hybrid_avg_ndcg10_min']:.2f}–{refs['hybrid_avg_ndcg10_max']:.2f} |",
            f"| Hybrid + reranker | ~{refs['hybrid_rerank_avg_ndcg10_min']:.2f}–{refs['hybrid_rerank_avg_ndcg10_max']:.2f} |",
            f"| SOTA floor | ~{refs['sota_avg_ndcg10_floor']:.2f}+ |",
            "",
            "## Positioning",
            "",
            f"- Profile D dataset-mean NDCG@10: `{d_ndcg:.3f}`",
            f"- Relative to hybrid+reranker reference (`~{refs['hybrid_rerank_avg_ndcg10_min']:.2f}–{refs['hybrid_rerank_avg_ndcg10_max']:.2f}`): "
            + ("above-range" if d_ndcg > refs["hybrid_rerank_avg_ndcg10_max"] else "within-or-below-range"),
            "",
            "## Comparability Notes",
            "",
            "- 本报告使用项目内可落盘语料（SQuAD + BEIR NFCorpus）的小样本运行，不等价于完整 BEIR 全量评测。",
            "- query relevance 采用 `first_relevant_only=true` 策略以控制真实 API 成本，结果更适合回归比较，不适合外部 SOTA 声明。",
            "- Profile D 的有效性仍由 phase6 gate 判定（`embedding_fallback_hash` / `embedding_request_failed` / `reranker_request_failed`）。",
        ]
    )
    return "\n".join(lines) + "\n"
