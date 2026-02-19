import json
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


def _count_jsonl_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _iter_jsonl_rows(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            yield json.loads(raw)


def _resolve_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return project_root / path


def _assert_row_minimal_contract(row: Dict[str, Any], dataset: str) -> None:
    assert set(row) == REQUIRED_ROW_FIELDS
    assert str(row["id"]).strip()
    assert str(row["query"]).strip()
    assert row["source_dataset"] == dataset
    relevant = row["relevant_uris_or_doc_ids"]
    assert isinstance(relevant, list) and len(relevant) > 0


def test_small_gate_public_datasets_profiles() -> None:
    manifests_dir = Path(__file__).resolve().parents[1] / "datasets" / "manifests"
    project_root = manifests_dir.parents[3]
    manifest_paths = sorted(manifests_dir.glob("*.json"))
    assert manifest_paths, "No dataset manifest found. Run dataset pipeline first."
    found = set()

    for manifest_path in manifest_paths:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        dataset = payload["dataset"]
        found.add(dataset)
        full_count = int(payload["record_count_full"])

        samples = payload.get("sample_files", {})
        sample_counts = payload.get("sample_counts", {})
        small = samples.get("100")
        assert isinstance(small, str) and small
        sample_path = _resolve_path(project_root, small)

        expected = min(100, full_count)
        assert int(sample_counts["100"]) == expected
        assert _count_jsonl_rows(sample_path) == expected
        ids = set()
        for row in _iter_jsonl_rows(sample_path):
            _assert_row_minimal_contract(row, dataset)
            ids.add(str(row["id"]))
        assert len(ids) == expected

    assert found == EXPECTED_DATASETS


def test_full_gate_public_datasets_profiles() -> None:
    manifests_dir = Path(__file__).resolve().parents[1] / "datasets" / "manifests"
    project_root = manifests_dir.parents[3]
    manifest_paths = sorted(manifests_dir.glob("*.json"))
    assert manifest_paths, "No dataset manifest found. Run dataset pipeline first."
    found = set()

    for manifest_path in manifest_paths:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        dataset = payload["dataset"]
        found.add(dataset)
        full_count = int(payload["record_count_full"])
        samples = payload.get("sample_files", {})
        sample_counts = payload.get("sample_counts", {})
        full = samples.get("500")
        assert isinstance(full, str) and full
        sample_path = _resolve_path(project_root, full)
        expected = min(500, full_count)
        assert int(sample_counts["500"]) == expected
        assert _count_jsonl_rows(sample_path) == expected
        ids_500 = set()
        for row in _iter_jsonl_rows(sample_path):
            _assert_row_minimal_contract(row, dataset)
            ids_500.add(str(row["id"]))
        assert len(ids_500) == expected

        small_path = _resolve_path(project_root, samples["100"])
        ids_100 = {str(row["id"]) for row in _iter_jsonl_rows(small_path)}
        assert ids_100.issubset(ids_500)

    assert found == EXPECTED_DATASETS
