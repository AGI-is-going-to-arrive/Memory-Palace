import json
from pathlib import Path
from typing import Any, Dict


BENCHMARK_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = BENCHMARK_DIR.parent
DATASETS_DIR = TESTS_DIR / "datasets"
THRESHOLDS_V1_PATH = BENCHMARK_DIR / "thresholds_v1.json"
BASELINE_MANIFEST_PATH = BENCHMARK_DIR / "baseline_manifest.md"


def load_thresholds_v1() -> Dict[str, Any]:
    return json.loads(THRESHOLDS_V1_PATH.read_text(encoding="utf-8"))

