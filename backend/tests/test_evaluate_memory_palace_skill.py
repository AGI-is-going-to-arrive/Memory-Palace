from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
import sys


def _load_skill_eval_module():
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "evaluate_memory_palace_skill.py"
    spec = importlib.util.spec_from_file_location("evaluate_memory_palace_skill", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_check_gate_syntax_skips_when_post_check_script_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    monkeypatch.setattr(evaluate_memory_palace_skill, "REPO_ROOT", tmp_path)

    result = evaluate_memory_palace_skill.check_gate_syntax()

    assert result.status == "SKIP"
    assert "缺失" in result.summary
    assert str(tmp_path / "new" / "run_post_change_checks.sh") in result.details
    assert str(tmp_path.parent / "new" / "run_post_change_checks.sh") in result.details


def test_check_gate_syntax_validates_first_existing_post_check_script(
    monkeypatch,
    tmp_path: Path,
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    gate_script = tmp_path.parent / "new" / "run_post_change_checks.sh"
    gate_script.parent.mkdir(parents=True, exist_ok=True)
    gate_script.write_text("echo ok\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def _fake_run_command(
        cmd: list[str],
        *,
        cwd: Path,
        input_text=None,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        captured["input_text"] = input_text
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(evaluate_memory_palace_skill, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(evaluate_memory_palace_skill, "run_command", _fake_run_command)

    result = evaluate_memory_palace_skill.check_gate_syntax()

    assert result.status == "PASS"
    assert captured["cmd"] == ["bash", "-n", str(gate_script)]
    assert captured["cwd"] == tmp_path
    assert captured["timeout"] == 30
