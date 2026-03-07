from pathlib import Path


BENCHMARK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCHMARK_DIR.parents[2]
REPO_ROOT = BENCHMARK_DIR.parents[3]

WORKFLOW_PATH = REPO_ROOT / ".github/workflows/benchmark-gate.yml"
DOCKERIGNORE_PATH = PROJECT_ROOT / ".dockerignore"
BACKEND_DOCKERFILE_PATH = PROJECT_ROOT / "deploy/docker/Dockerfile.backend"
DOCKER_ONE_CLICK_SH_PATH = PROJECT_ROOT / "scripts/docker_one_click.sh"
DOCKER_ONE_CLICK_PS1_PATH = PROJECT_ROOT / "scripts/docker_one_click.ps1"
RUN_POST_CHANGE_CHECKS_PATH = REPO_ROOT / "new/run_post_change_checks.sh"
RUN_PWSH_DOCKER_REAL_TEST_PATH = REPO_ROOT / "new/run_pwsh_docker_real_test.sh"


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


def test_phase7_scripts_reserve_ports_before_parallel_compose_up() -> None:
    shell_text = DOCKER_ONE_CLICK_SH_PATH.read_text(encoding="utf-8")
    ps1_text = DOCKER_ONE_CLICK_PS1_PATH.read_text(encoding="utf-8")
    post_check_text = RUN_POST_CHANGE_CHECKS_PATH.read_text(encoding="utf-8")

    assert "memory-palace-port-locks" in shell_text
    assert "memory-palace-port-locks" in ps1_text
    assert "memory-palace-port-locks" in post_check_text
    assert "try_acquire_path_lock" in shell_text
    assert "Try-AcquirePathLock" in ps1_text
    assert "reserve_exact_port_if_available" in post_check_text


def test_phase7_scripts_use_isolated_env_files_and_checkout_deploy_lock() -> None:
    compose_text = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    shell_text = DOCKER_ONE_CLICK_SH_PATH.read_text(encoding="utf-8")
    ps1_text = DOCKER_ONE_CLICK_PS1_PATH.read_text(encoding="utf-8")
    post_check_text = RUN_POST_CHANGE_CHECKS_PATH.read_text(encoding="utf-8")

    assert "MEMORY_PALACE_DOCKER_ENV_FILE" in compose_text
    assert "memory-palace-docker-env-" in shell_text
    assert "memory-palace-docker-env-" in ps1_text
    assert "memory-palace-deploy-locks" in shell_text
    assert "memory-palace-deploy-locks" in ps1_text
    assert "DEPLOYMENT_LOCK" in shell_text
    assert "$script:DeploymentLockDir" in ps1_text
    assert "another docker_one_click deployment is already running for this checkout" in shell_text
    assert "another docker_one_click deployment is already running for this checkout" in ps1_text
    assert "memory-palace-post-change-checks" in post_check_text
    assert "Another run_post_change_checks.sh process is already active for this workspace." in post_check_text


def test_phase7_windows_equivalent_pwsh_gate_preserves_skip_status() -> None:
    post_check_text = RUN_POST_CHANGE_CHECKS_PATH.read_text(encoding="utf-8")
    pwsh_text = RUN_PWSH_DOCKER_REAL_TEST_PATH.read_text(encoding="utf-8")
    gate_start = post_check_text.index("run_windows_equivalent_pwsh_docker_gate() {")
    gate_end = post_check_text.index("append_review_record()", gate_start)
    gate_text = post_check_text[gate_start:gate_end]

    assert 'pwsh_exit_code' in gate_text
    assert 'status="SKIP"' in gate_text
    assert 'if [[ "${status}" == "FAIL" ]]; then' in gate_text
    assert "docker_run_exit" in pwsh_text


def test_phase7_post_check_exit_trap_is_root_guarded_and_pwsh_temp_json_is_cleaned() -> None:
    post_check_text = RUN_POST_CHANGE_CHECKS_PATH.read_text(encoding="utf-8")
    pwsh_text = RUN_PWSH_DOCKER_REAL_TEST_PATH.read_text(encoding="utf-8")

    assert 'ROOT_BASHPID="${BASHPID:-$$}"' in post_check_text
    assert 'if [[ "${BASHPID:-$$}" != "${ROOT_BASHPID}" ]]; then' in post_check_text
    assert 'if (cd "${REPO_ROOT}" && bash new/run_pwsh_docker_real_test.sh --env-file "${RUNTIME_ENV_FILE}" --output-json "${result_json}"); then' in post_check_text
    assert 'if ! (cd "${REPO_ROOT}" && bash new/run_pwsh_docker_real_test.sh --env-file "${RUNTIME_ENV_FILE}" --output-json "${result_json}"); then' not in post_check_text
    assert 'HOST_RESULT_JSON="${SCRIPT_DIR}/.tmp-pwsh_docker_real_test_result_${RUN_TOKEN}.json"' in pwsh_text
    assert 'CONTAINER_RESULT_JSON="/work/new/.tmp-pwsh_docker_real_test_result_${RUN_TOKEN}.json"' in pwsh_text
    assert '.tmp-pwsh_docker_real_test_result_' in pwsh_text
    assert 'if [[ -f "${HOST_RESULT_JSON}" && "${OUTPUT_JSON}" != "${HOST_RESULT_JSON}" ]]; then' in pwsh_text
    assert 'cp "${HOST_RESULT_JSON}" "${OUTPUT_JSON}" 2>/dev/null || true' in pwsh_text
    assert 'rm -f "${HOST_RESULT_JSON}" 2>/dev/null || true' in pwsh_text
