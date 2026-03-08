import importlib.util
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
