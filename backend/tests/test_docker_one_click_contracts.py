from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_env_injection_covers_intent_llm_and_router_fallbacks() -> None:
    shell_text = (PROJECT_ROOT / "scripts" / "docker_one_click.sh").read_text(
        encoding="utf-8"
    )
    ps1_text = (PROJECT_ROOT / "scripts" / "docker_one_click.ps1").read_text(
        encoding="utf-8"
    )

    for literal in (
        "INTENT_LLM_ENABLED",
        "INTENT_LLM_API_BASE",
        "INTENT_LLM_API_KEY",
        "INTENT_LLM_MODEL",
        "--wait",
        "--wait-timeout",
        "RETRIEVAL_EMBEDDING_API_BASE copied from ROUTER_API_BASE",
        "RETRIEVAL_EMBEDDING_API_KEY copied from ROUTER_API_KEY",
        "RETRIEVAL_EMBEDDING_MODEL copied from ROUTER_EMBEDDING_MODEL",
        "RETRIEVAL_RERANKER_API_BASE copied from ROUTER_API_BASE",
        "RETRIEVAL_RERANKER_API_KEY copied from ROUTER_API_KEY",
    ):
        assert literal in shell_text
        assert literal in ps1_text

    assert "wait_for_deployment_ready" in shell_text
    assert "Wait-DeploymentReady" in ps1_text
    assert '--filter "label=com.docker.compose.project=${compose_project_name}"' in shell_text
    assert '--filter "label=com.docker.compose.service=${service}"' in shell_text
    assert "--format '{{.Ports}}'" in shell_text
    assert 'docker port "${container_name}" "${target_port}"' in shell_text
    assert '--filter "label=com.docker.compose.project=$ComposeProjectName"' in ps1_text
    assert '--filter "label=com.docker.compose.service=$Service"' in ps1_text
    assert "--format '{{.Ports}}'" in ps1_text
    assert "docker port $containerName $TargetPort" in ps1_text
    assert 'docker-compose.yml port "${service}" "${target_port}"' in shell_text
    assert "$portArgs += @('-f', 'docker-compose.yml', 'port', $Service, \"$TargetPort\")" in ps1_text
    assert 'upsert_env_value_in_file "${env_file}" "MEMORY_PALACE_FRONTEND_PORT" "${frontend_port}"' in shell_text
    assert 'upsert_env_value_in_file "${env_file}" "MEMORY_PALACE_BACKEND_PORT" "${backend_port}"' in shell_text
    assert "Set-EnvValueInFile -FilePath $envFile -Key 'MEMORY_PALACE_FRONTEND_PORT' -Value \"$FrontendPort\"" in ps1_text
    assert "Set-EnvValueInFile -FilePath $envFile -Key 'MEMORY_PALACE_BACKEND_PORT' -Value \"$BackendPort\"" in ps1_text


def test_compose_waits_for_healthy_sse_service() -> None:
    compose_text = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    backend_block = compose_text.split("\n  backend:\n", 1)[1].split("\n  sse:\n", 1)[0]
    sse_block = compose_text.split("\n  sse:\n", 1)[1].split("\n  frontend:\n", 1)[0]
    frontend_block = compose_text.split("\n  frontend:\n", 1)[1]

    assert "healthcheck:" in backend_block
    assert "http://127.0.0.1:8000/health" in backend_block
    assert "healthcheck:" in sse_block
    assert "http://127.0.0.1:8000/health" in sse_block
    assert "sse:\n        condition: service_healthy" in frontend_block
