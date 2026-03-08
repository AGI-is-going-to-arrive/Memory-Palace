from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_harness():
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "evaluate_memory_palace_skill.py"
    spec = importlib.util.spec_from_file_location("evaluate_memory_palace_skill", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_gate_syntax_skips_when_post_check_script_is_absent(monkeypatch, tmp_path: Path) -> None:
    harness = _load_harness()
    monkeypatch.setattr(harness, "REPO_ROOT", tmp_path)

    result = harness.check_gate_syntax()

    assert result.status == "SKIP"
    assert "缺失" in result.summary
    assert "run_post_change_checks.sh" in result.details


def test_gate_syntax_passes_when_post_check_script_exists(monkeypatch, tmp_path: Path) -> None:
    harness = _load_harness()
    gate_script = tmp_path / "new" / "run_post_change_checks.sh"
    gate_script.parent.mkdir(parents=True, exist_ok=True)
    gate_script.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho ok\n", encoding="utf-8")
    monkeypatch.setattr(harness, "REPO_ROOT", tmp_path)

    result = harness.check_gate_syntax()

    assert result.status == "PASS"
