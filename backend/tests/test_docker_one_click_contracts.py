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
        "RETRIEVAL_EMBEDDING_API_BASE copied from ROUTER_API_BASE",
        "RETRIEVAL_EMBEDDING_API_KEY copied from ROUTER_API_KEY",
        "RETRIEVAL_EMBEDDING_MODEL copied from ROUTER_EMBEDDING_MODEL",
        "RETRIEVAL_RERANKER_API_BASE copied from ROUTER_API_BASE",
        "RETRIEVAL_RERANKER_API_KEY copied from ROUTER_API_KEY",
    ):
        assert literal in shell_text
        assert literal in ps1_text
