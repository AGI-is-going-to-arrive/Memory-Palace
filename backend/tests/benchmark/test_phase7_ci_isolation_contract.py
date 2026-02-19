from pathlib import Path


BENCHMARK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCHMARK_DIR.parents[2]
REPO_ROOT = BENCHMARK_DIR.parents[3]

WORKFLOW_PATH = REPO_ROOT / ".github/workflows/benchmark-gate.yml"
DOCKERIGNORE_PATH = PROJECT_ROOT / ".dockerignore"
BACKEND_DOCKERFILE_PATH = PROJECT_ROOT / "deploy/docker/Dockerfile.backend"


def _load_nonempty_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        lines.append(text)
    return lines


def test_phase7_benchmark_workflow_has_tiered_pr_nightly_weekly_gates() -> None:
    assert WORKFLOW_PATH.exists(), "missing benchmark workflow gate file"
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "benchmark-pr:" in text
    assert "benchmark-nightly:" in text
    assert "benchmark-weekly:" in text
    assert 'cron: "0 3 * * *"' in text
    assert 'cron: "0 4 * * 0"' in text
    assert "tests/benchmark/test_search_memory_contract_regression.py" in text
    assert "pytest tests/benchmark -q" in text


def test_phase7_dockerignore_excludes_test_and_doc_assets_from_images() -> None:
    assert DOCKERIGNORE_PATH.exists(), "missing project .dockerignore"
    lines = _load_nonempty_lines(DOCKERIGNORE_PATH)

    assert "backend/tests/" in lines
    assert "docs/" in lines
    assert "snapshots/" in lines


def test_phase7_backend_dockerfile_relies_on_backend_copy_with_dockerignore_guard() -> None:
    assert BACKEND_DOCKERFILE_PATH.exists(), "missing backend Dockerfile"
    text = BACKEND_DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert "COPY backend /app/backend" in text
    assert "COPY . /app" not in text
    assert "backend/tests" not in text
