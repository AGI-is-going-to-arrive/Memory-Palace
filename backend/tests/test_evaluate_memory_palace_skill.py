from __future__ import annotations

import importlib.util
import json
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


def test_classify_skill_answer_accepts_repo_visible_trigger_sample_path() -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()

    success, details = evaluate_memory_palace_skill.classify_skill_answer(
        "- first move: `read_memory(\"system://boot\")`\n"
        "- noop handling: stop, inspect `guard_target_uri` / `guard_target_id`\n"
        "- trigger samples: `docs/skills/memory-palace/references/trigger-samples.md`\n"
    )

    assert success is True
    assert "trigger sample" in details


def test_smoke_codex_accepts_output_file_when_cli_times_out(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()

    monkeypatch.setattr(evaluate_memory_palace_skill.shutil, "which", lambda _: "/usr/bin/codex")

    def _fake_runner(cmd, *, cwd, output_path, input_text=None, timeout=120):
        _ = cmd
        _ = cwd
        _ = input_text
        _ = timeout
        output_path.write_text(
            json.dumps(
                {
                    "first_move": 'read_memory("system://boot")',
                    "noop_handling": "stop and inspect guard_target_uri / guard_target_id",
                    "trigger_samples_path": "docs/skills/memory-palace/references/trigger-samples.md",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return evaluate_memory_palace_skill.CommandCapture(
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
        )

    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "_run_command_capture_until_output_file",
        _fake_runner,
    )

    result = evaluate_memory_palace_skill.smoke_codex()

    assert result.status == "PASS"
    assert result.summary == "Codex smoke 通过"


def test_run_gemini_prompt_falls_back_to_flash_preview_on_429(monkeypatch) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    calls: list[str] = []

    def _fake_run_command_capture(cmd, *, cwd, input_text=None, timeout=120):
        _ = cwd
        _ = input_text
        _ = timeout
        model = cmd[2]
        calls.append(model)
        if model == evaluate_memory_palace_skill.GEMINI_TEST_MODEL:
            return evaluate_memory_palace_skill.CommandCapture(
                returncode=1,
                stdout="",
                stderr='{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","message":"No capacity available for model"}}',
                timed_out=False,
                model=model,
            )
        return evaluate_memory_palace_skill.CommandCapture(
            returncode=0,
            stdout="- read_memory(\"system://boot\")\n- stop and inspect guard_target_uri / guard_target_id\n- docs/skills/memory-palace/references/trigger-samples.md\n",
            stderr="",
            timed_out=False,
            model=model,
        )

    monkeypatch.setattr(
        evaluate_memory_palace_skill, "run_command_capture", _fake_run_command_capture
    )

    result = evaluate_memory_palace_skill.run_gemini_prompt("prompt", timeout=30)

    assert calls == [
        evaluate_memory_palace_skill.GEMINI_TEST_MODEL,
        evaluate_memory_palace_skill.GEMINI_FALLBACK_MODEL,
    ]
    assert result.model == evaluate_memory_palace_skill.GEMINI_FALLBACK_MODEL


def test_frontmatter_data_parses_description_without_yaml_module(monkeypatch) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    monkeypatch.setattr(evaluate_memory_palace_skill, "yaml", None)

    payload = evaluate_memory_palace_skill._frontmatter_data(
        evaluate_memory_palace_skill.CANONICAL_DIR / "SKILL.md"
    )

    assert isinstance(payload, dict)
    assert payload.get("name") == "memory-palace"
    assert "Memory Palace durable-memory work" in str(payload.get("description") or "")


def test_run_command_capture_until_output_file_returns_after_json_is_ready(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    output_path = tmp_path / "out.json"

    class _FakeProcess:
        def __init__(self):
            self.pid = 123
            self.returncode = None
            self.calls = 0

        def poll(self):
            return self.returncode

        def communicate(self, input=None, timeout=None):
            _ = input
            self.calls += 1
            if self.calls == 1:
                output_path.write_text('{"ok": true}', encoding="utf-8")
                raise subprocess.TimeoutExpired("codex", timeout, output="", stderr="")
            self.returncode = 0
            return ("", "")

    fake_process = _FakeProcess()
    terminated: list[int] = []

    monkeypatch.setattr(
        evaluate_memory_palace_skill.subprocess,
        "Popen",
        lambda *args, **kwargs: fake_process,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill.os,
        "killpg",
        lambda pid, sig: terminated.append(pid),
    )

    result = evaluate_memory_palace_skill._run_command_capture_until_output_file(
        ["codex", "exec"],
        cwd=tmp_path,
        output_path=output_path,
        timeout=5,
    )

    assert result.timed_out is False
    assert result.returncode == 0
    assert terminated == [123]
