import importlib.util
from pathlib import Path


BENCHMARK_DIR = Path(__file__).resolve().parent

EXPECTED_PHASE0_2_PATHS = {
    "baseline_manifest.md",
    "thresholds_v1.json",
    "helpers/prepare_public_datasets.py",
    "test_phase0_baseline_manifest.py",
    "test_dataset_integrity.py",
    "test_benchmark_public_datasets_profiles.py",
}


def _import_module(module_path: Path) -> object:
    module_name = f"_phase1_collect_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phase1_collection_contract_required_files_exist() -> None:
    for relative in EXPECTED_PHASE0_2_PATHS:
        path = BENCHMARK_DIR / relative
        assert path.exists(), f"missing required phase0-2 asset: {relative}"


def test_phase1_collection_contract_test_modules_importable() -> None:
    test_files = sorted(BENCHMARK_DIR.glob("test_*.py"))
    assert test_files, "no benchmark test files found"

    for test_file in test_files:
        module = _import_module(test_file)
        test_names = [
            name for name, value in vars(module).items() if name.startswith("test_") and callable(value)
        ]
        assert test_names, f"{test_file.name} does not define test functions"
