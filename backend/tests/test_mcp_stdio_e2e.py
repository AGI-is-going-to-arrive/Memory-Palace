from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_harness():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "evaluate_memory_palace_mcp_e2e.py"
    spec = importlib.util.spec_from_file_location("evaluate_memory_palace_mcp_e2e", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_live_mcp_stdio_e2e_suite_passes() -> None:
    harness = _load_harness()
    results, stderr_output = harness.run_suite_sync()

    failing = [item for item in results if item.status == "FAIL"]
    assert not failing, [(item.name, item.summary, item.details) for item in failing]
    assert "bound to a different event loop" not in stderr_output
