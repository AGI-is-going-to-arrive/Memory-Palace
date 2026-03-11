import importlib.util
import json
import sys
from pathlib import Path


def _load_skill_eval_module():
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "evaluate_memory_palace_skill.py"
    spec = importlib.util.spec_from_file_location("evaluate_memory_palace_skill", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_install_skill_module():
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "install_skill.py"
    spec = importlib.util.spec_from_file_location("install_skill", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_sync_skill_module():
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "sync_memory_palace_skill.py"
    spec = importlib.util.spec_from_file_location("sync_memory_palace_skill", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _canonical_skill_text(body: str = "Canonical skill body.\n") -> str:
    return (
        "---\n"
        "name: memory-palace\n"
        "description: Canonical memory palace skill.\n"
        "---\n"
        f"{body}"
    )


def test_check_gate_syntax_skips_when_workspace_gate_missing(monkeypatch, tmp_path):
    module = _load_skill_eval_module()
    repo_root = tmp_path / "Memory-Palace"
    repo_root.mkdir()
    monkeypatch.setattr(module, "REPO_ROOT", repo_root)

    result = module.check_gate_syntax()

    assert result.status == "SKIP"
    assert "run_post_change_checks.sh" in result.summary
    assert "缺失" in result.summary


def test_check_gate_syntax_accepts_parent_workspace_gate(monkeypatch, tmp_path):
    module = _load_skill_eval_module()
    repo_root = tmp_path / "Memory-Palace"
    repo_root.mkdir()
    workspace_gate = tmp_path / "new" / "run_post_change_checks.sh"
    workspace_gate.parent.mkdir()
    workspace_gate.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho ok\n", encoding="utf-8")

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)

    result = module.check_gate_syntax()

    assert result.status == "PASS"
    assert "语法通过" in result.summary


def test_install_skill_reports_invalid_json_with_actionable_message(tmp_path):
    module = _load_install_skill_module()
    broken = tmp_path / "settings.json"
    broken.write_text('{"mcpServers": ', encoding="utf-8")

    try:
        module.read_json_file(broken)
        raise AssertionError("expected SystemExit for invalid JSON")
    except SystemExit as exc:
        message = str(exc)

    assert str(broken) in message
    assert "Invalid JSON" in message
    assert "line" in message
    assert "column" in message


def test_install_skill_write_json_file_creates_backup_before_overwrite(tmp_path):
    module = _load_install_skill_module()
    config_path = tmp_path / "settings.json"
    original_payload = {"mcpServers": {"old": {"command": "bash"}}}
    config_path.write_text(json.dumps(original_payload), encoding="utf-8")

    module.write_json_file(
        config_path,
        {"mcpServers": {"memory-palace": {"command": "bash"}}},
        dry_run=False,
    )

    backup_path = config_path.with_name("settings.json.bak")
    assert backup_path.is_file()
    assert json.loads(backup_path.read_text(encoding="utf-8")) == original_payload


def test_install_skill_write_text_file_creates_backup_before_overwrite(tmp_path):
    module = _load_install_skill_module()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[mcp_servers.old]\ncommand = "bash"\n', encoding="utf-8")

    module.write_text_file(
        config_path,
        '[mcp_servers.memory-palace]\ncommand = "bash"\n',
        dry_run=False,
    )

    backup_path = config_path.with_name("config.toml.bak")
    assert backup_path.is_file()
    assert '[mcp_servers.old]' in backup_path.read_text(encoding="utf-8")


def test_sync_skill_overwrites_required_files_without_deleting_extra_files(
    monkeypatch, tmp_path
):
    module = _load_sync_skill_module()

    repo_root = tmp_path / "Memory-Palace"
    canonical_dir = repo_root / "docs" / "skills" / "memory-palace"
    (canonical_dir / "references").mkdir(parents=True, exist_ok=True)
    (canonical_dir / "agents").mkdir(parents=True, exist_ok=True)
    (canonical_dir / "variants" / "gemini").mkdir(parents=True, exist_ok=True)
    (canonical_dir / "SKILL.md").write_text(
        _canonical_skill_text("canonical-skill\n"), encoding="utf-8"
    )
    (canonical_dir / "references" / "mcp-workflow.md").write_text(
        "canonical-workflow\n", encoding="utf-8"
    )
    (canonical_dir / "references" / "trigger-samples.md").write_text(
        "canonical-trigger\n", encoding="utf-8"
    )
    (canonical_dir / "agents" / "openai.yaml").write_text(
        "canonical-openai\n", encoding="utf-8"
    )
    (canonical_dir / "variants" / "gemini" / "SKILL.md").write_text(
        _canonical_skill_text("canonical-gemini\n"), encoding="utf-8"
    )

    mirror_dir = repo_root / ".codex" / "skills" / "memory-palace"
    mirror_dir.mkdir(parents=True, exist_ok=True)
    (mirror_dir / "SKILL.md").write_text(
        _canonical_skill_text("old-skill\n"), encoding="utf-8"
    )
    (mirror_dir / "legacy-note.md").write_text("keep-me\n", encoding="utf-8")

    gemini_dir = repo_root / ".gemini" / "skills" / "memory-palace"
    gemini_dir.mkdir(parents=True, exist_ok=True)
    (gemini_dir / "SKILL.md").write_text(
        _canonical_skill_text("old-gemini\n"), encoding="utf-8"
    )
    (gemini_dir / "legacy-note.md").write_text("keep-me\n", encoding="utf-8")

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(module, "CANONICAL_DIR", canonical_dir)
    monkeypatch.setattr(module, "GEMINI_VARIANT_FILE", canonical_dir / "variants" / "gemini" / "SKILL.md")
    monkeypatch.setattr(module, "BUNDLE_MIRROR_DIRS", [mirror_dir])
    monkeypatch.setattr(module, "GEMINI_WORKSPACE_DIR", gemini_dir)

    assert module.run_sync() == 0
    assert (mirror_dir / "SKILL.md").read_text(encoding="utf-8") == _canonical_skill_text(
        "canonical-skill\n"
    )
    assert (mirror_dir / "legacy-note.md").read_text(encoding="utf-8") == "keep-me\n"
    assert (gemini_dir / "SKILL.md").read_text(encoding="utf-8") == _canonical_skill_text(
        "canonical-gemini\n"
    )
    assert (gemini_dir / "legacy-note.md").read_text(encoding="utf-8") == "keep-me\n"


def test_sync_skill_check_ignores_legacy_extra_files_when_required_files_match(
    monkeypatch, tmp_path
):
    module = _load_sync_skill_module()

    repo_root = tmp_path / "Memory-Palace"
    canonical_dir = repo_root / "docs" / "skills" / "memory-palace"
    (canonical_dir / "references").mkdir(parents=True, exist_ok=True)
    (canonical_dir / "agents").mkdir(parents=True, exist_ok=True)
    (canonical_dir / "variants" / "gemini").mkdir(parents=True, exist_ok=True)
    for relative_path, content in {
        Path("SKILL.md"): _canonical_skill_text("canonical-skill\n"),
        Path("references/mcp-workflow.md"): "canonical-workflow\n",
        Path("references/trigger-samples.md"): "canonical-trigger\n",
        Path("agents/openai.yaml"): "canonical-openai\n",
        Path("variants/gemini/SKILL.md"): _canonical_skill_text("canonical-gemini\n"),
    }.items():
        path = canonical_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    mirror_dir = repo_root / ".claude" / "skills" / "memory-palace"
    mirror_dir.mkdir(parents=True, exist_ok=True)
    for relative_path, content in {
        Path("SKILL.md"): _canonical_skill_text("canonical-skill\n"),
        Path("references/mcp-workflow.md"): "canonical-workflow\n",
        Path("references/trigger-samples.md"): "canonical-trigger\n",
        Path("agents/openai.yaml"): "canonical-openai\n",
        Path("legacy-note.md"): "keep-me\n",
    }.items():
        path = mirror_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    gemini_dir = repo_root / ".gemini" / "skills" / "memory-palace"
    gemini_dir.mkdir(parents=True, exist_ok=True)
    (gemini_dir / "SKILL.md").write_text(
        _canonical_skill_text("canonical-gemini\n"), encoding="utf-8"
    )
    (gemini_dir / "legacy-note.md").write_text("keep-me\n", encoding="utf-8")

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(module, "CANONICAL_DIR", canonical_dir)
    monkeypatch.setattr(module, "GEMINI_VARIANT_FILE", canonical_dir / "variants" / "gemini" / "SKILL.md")
    monkeypatch.setattr(module, "BUNDLE_MIRROR_DIRS", [mirror_dir])
    monkeypatch.setattr(module, "GEMINI_WORKSPACE_DIR", gemini_dir)

    assert module.run_check() == 0
