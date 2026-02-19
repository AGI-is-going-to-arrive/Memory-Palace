import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable


EXPECTED_DATASETS = {
    "squad_v2_dev",
    "dailydialog",
    "msmarco_passages",
    "beir_nfcorpus",
    "beir_nq",
    "beir_hotpotqa",
    "beir_fiqa",
}

REQUIRED_ROW_FIELDS = {
    "id",
    "query",
    "relevant_uris_or_doc_ids",
    "language",
    "domain",
    "source_dataset",
    "split",
}


def _count_jsonl(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            yield json.loads(raw)


def _parse_utc_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _assert_row_contract(row: Dict[str, Any], dataset: str) -> None:
    assert set(row) == REQUIRED_ROW_FIELDS
    assert str(row["id"]).strip()
    assert str(row["query"]).strip()
    assert str(row["language"]).strip()
    assert str(row["domain"]).strip()
    assert str(row["split"]).strip()
    assert row["source_dataset"] == dataset

    relevant = row["relevant_uris_or_doc_ids"]
    assert isinstance(relevant, list) and relevant
    cleaned = [str(item).strip() for item in relevant]
    assert all(cleaned)
    assert cleaned == sorted(set(cleaned))


def test_dataset_integrity_skeleton_directories_exist() -> None:
    tests_dir = Path(__file__).resolve().parents[1]
    datasets_dir = tests_dir / "datasets"
    assert (datasets_dir / "raw").exists()
    assert (datasets_dir / "processed").exists()
    assert (datasets_dir / "manifests").exists()


def test_dataset_integrity_skeleton_manifest_files_parse_when_present() -> None:
    tests_dir = Path(__file__).resolve().parents[1]
    manifests_dir = tests_dir / "datasets" / "manifests"
    project_root = manifests_dir.parents[3]
    manifest_paths = sorted(manifests_dir.glob("*.json"))
    assert manifest_paths, "No dataset manifests found. Run dataset pipeline first."

    found_datasets = set()

    for manifest_path in manifest_paths:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        dataset = payload["dataset"]
        found_datasets.add(dataset)
        assert manifest_path.stem == dataset
        assert "dataset" in payload
        assert "source_url" in payload
        assert str(payload["source_url"]).startswith("https://")
        assert payload.get("schema_version") == "v1"
        assert payload.get("status") == "ready"
        assert payload.get("record_count_full", 0) > 0
        assert _parse_utc_iso(str(payload["downloaded_at_utc"])).tzinfo is not None

        raw_files = payload.get("raw_files", [])
        assert isinstance(raw_files, list) and raw_files
        for item in raw_files:
            assert len(str(item.get("sha256", ""))) == 64
            raw_path = project_root / str(item["path"])
            assert raw_path.exists()
            assert int(item["size_bytes"]) == raw_path.stat().st_size

        assert "processed_files" in payload
        full_path = project_root / str(payload["processed_files"]["full"])
        assert full_path.exists()
        full_count = int(payload["record_count_full"])
        assert _count_jsonl(full_path) == full_count
        full_ids = set()
        for row in _iter_jsonl(full_path):
            _assert_row_contract(row, dataset)
            full_ids.add(str(row["id"]))
        assert len(full_ids) == full_count

        sample_files = payload.get("sample_files", {})
        sample_counts = payload.get("sample_counts", {})
        for sample_size in ("100", "200", "500"):
            assert sample_size in sample_files
            sample_path = project_root / str(sample_files[sample_size])
            assert sample_path.exists()
            sample_count = _count_jsonl(sample_path)
            assert sample_count == int(sample_counts[sample_size])
            assert sample_count == min(int(sample_size), full_count)
            assert sample_count > 0
            sample_ids = set()
            for row in _iter_jsonl(sample_path):
                _assert_row_contract(row, dataset)
                sample_ids.add(str(row["id"]))
            assert len(sample_ids) == sample_count
            assert sample_ids.issubset(full_ids)

    assert found_datasets == EXPECTED_DATASETS
