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


def test_repo_local_stdio_wrapper_prefers_repo_env_before_fallback_db() -> None:
    project_root = Path(__file__).resolve().parents[2]
    wrapper_text = (
        project_root / "scripts" / "run_memory_palace_mcp_stdio.sh"
    ).read_text(encoding="utf-8")

    assert 'ENV_FILE="${PROJECT_ROOT}/.env"' in wrapper_text
    assert 'DOCKER_ENV_FILE="${PROJECT_ROOT}/.env.docker"' in wrapper_text
    assert 'DEFAULT_DB_PATH="${PROJECT_ROOT}/demo.db"' in wrapper_text
    assert 'if [[ -z "${DATABASE_URL:-}" && ! -f "${ENV_FILE}" ]]; then' in wrapper_text
    assert 'if [[ -f "${DOCKER_ENV_FILE}" ]]; then' in wrapper_text
    assert "connect your client to the Docker /sse endpoint instead." in wrapper_text
    assert 'export DATABASE_URL="sqlite+aiosqlite:////${DEFAULT_DB_PATH#/}"' in wrapper_text


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


def test_smoke_gemini_live_suite_returns_partial_when_db_path_is_unavailable(
    monkeypatch,
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    monkeypatch.setattr(evaluate_memory_palace_skill, "SKIP_GEMINI_LIVE", False)
    monkeypatch.setattr(evaluate_memory_palace_skill.shutil, "which", lambda _: "/usr/bin/gemini")
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "_extract_gemini_memory_palace_db_path",
        lambda: None,
    )

    result = evaluate_memory_palace_skill.smoke_gemini_live_suite()

    assert result.status == "PARTIAL"
    assert "数据库路径" in result.summary


def test_smoke_gemini_live_suite_accepts_verified_create_after_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    monkeypatch.setattr(evaluate_memory_palace_skill, "SKIP_GEMINI_LIVE", False)
    monkeypatch.setattr(evaluate_memory_palace_skill.shutil, "which", lambda _: "/usr/bin/gemini")
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "_extract_gemini_memory_palace_db_path",
        lambda: tmp_path / "demo.db",
    )
    monkeypatch.setattr(evaluate_memory_palace_skill.time, "time", lambda: 1234)

    note_uri = "notes://gemini_suite_1234"
    unique_token = "gemini_suite_1234_nonce"
    updated_content = (
        "Unique token gemini_suite_1234_nonce. This note records one preference only: "
        "user prefers concise answers. Updated once."
    )
    responses = iter(
        [
            evaluate_memory_palace_skill.CommandCapture(
                returncode=-9,
                stdout="",
                stderr="",
                timed_out=True,
                model=evaluate_memory_palace_skill.GEMINI_TEST_MODEL,
            ),
            evaluate_memory_palace_skill.CommandCapture(
                returncode=0,
                stdout=f"SUCCESS {note_uri}",
                stderr="",
                timed_out=False,
                model=evaluate_memory_palace_skill.GEMINI_TEST_MODEL,
            ),
            evaluate_memory_palace_skill.CommandCapture(
                returncode=0,
                stdout=f"BLOCKED {note_uri}",
                stderr="",
                timed_out=False,
                model=evaluate_memory_palace_skill.GEMINI_TEST_MODEL,
            ),
        ]
    )

    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "run_gemini_prompt",
        lambda prompt, timeout: next(responses),
    )
    expected_substrings: list[str | None] = []
    rows = iter(
        [
            {"content": f"Unique token {unique_token}. This note records one preference only: user prefers concise answers."},
            {"content": updated_content},
        ]
    )

    def _fake_wait_for_memory(db_path, uri, expected_substring=None, retries=5):
        _ = db_path
        _ = uri
        _ = retries
        expected_substrings.append(expected_substring)
        return next(rows)

    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "_wait_for_memory",
        _fake_wait_for_memory,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "_memory_exists",
        lambda db_path, uri: False,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "_find_latest_gemini_chat",
        lambda marker: (
            tmp_path / "chat.json",
            {
                "messages": [
                    {
                        "toolCalls": [
                            {
                                "name": "create_memory",
                                "result": [
                                    {
                                        "functionResponse": {
                                            "response": {
                                                "output": json.dumps(
                                                    {
                                                        "guard_action": "NOOP",
                                                        "guard_target_uri": note_uri,
                                                    }
                                                )
                                            }
                                        }
                                    }
                                ],
                            },
                            {"name": "read_memory"},
                        ]
                    }
                ]
            },
        ),
    )

    result = evaluate_memory_palace_skill.smoke_gemini_live_suite()

    assert result.status == "PASS"
    assert "写入/更新/守卫链路通过" in result.summary
    assert expected_substrings == [unique_token, updated_content]


def test_smoke_gemini_live_suite_accepts_create_verified_via_update_row(
    monkeypatch,
    tmp_path: Path,
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    monkeypatch.setattr(evaluate_memory_palace_skill, "SKIP_GEMINI_LIVE", False)
    monkeypatch.setattr(evaluate_memory_palace_skill.shutil, "which", lambda _: "/usr/bin/gemini")
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "_extract_gemini_memory_palace_db_path",
        lambda: tmp_path / "demo.db",
    )
    monkeypatch.setattr(evaluate_memory_palace_skill.time, "time", lambda: 1234)

    note_uri = "notes://gemini_suite_1234"
    unique_token = "gemini_suite_1234_nonce"
    updated_content = (
        "Unique token gemini_suite_1234_nonce. This note records one preference only: "
        "user prefers concise answers. Updated once."
    )
    responses = iter(
        [
            evaluate_memory_palace_skill.CommandCapture(
                returncode=0,
                stdout=f"SUCCESS {note_uri}",
                stderr="",
                timed_out=False,
                model=evaluate_memory_palace_skill.GEMINI_TEST_MODEL,
            ),
            evaluate_memory_palace_skill.CommandCapture(
                returncode=0,
                stdout=f"SUCCESS {note_uri}",
                stderr="",
                timed_out=False,
                model=evaluate_memory_palace_skill.GEMINI_TEST_MODEL,
            ),
            evaluate_memory_palace_skill.CommandCapture(
                returncode=0,
                stdout=f"BLOCKED {note_uri}",
                stderr="",
                timed_out=False,
                model=evaluate_memory_palace_skill.GEMINI_TEST_MODEL,
            ),
        ]
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "run_gemini_prompt",
        lambda prompt, timeout: next(responses),
    )
    rows = iter(
        [
            None,
            {"content": updated_content},
        ]
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "_wait_for_memory",
        lambda db_path, uri, expected_substring=None, retries=5: next(rows),
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "_memory_exists",
        lambda db_path, uri: False,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "_find_latest_gemini_chat",
        lambda marker: (
            tmp_path / "chat.json",
            {
                "messages": [
                    {
                        "toolCalls": [
                            {
                                "name": "create_memory",
                                "result": [
                                    {
                                        "functionResponse": {
                                            "response": {
                                                "output": json.dumps(
                                                    {
                                                        "guard_action": "NOOP",
                                                        "guard_target_uri": note_uri,
                                                    }
                                                )
                                            }
                                        }
                                    }
                                ],
                            },
                            {"name": "read_memory"},
                        ]
                    }
                ]
            },
        ),
    )

    result = evaluate_memory_palace_skill.smoke_gemini_live_suite()

    assert result.status == "PASS"
    assert "create_verified_via_update=True" in result.details


def test_extract_gemini_memory_palace_db_path_falls_back_to_repo_db_when_wrapper_is_bound(
    monkeypatch,
    tmp_path: Path,
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    repo_root = tmp_path / "Memory-Palace"
    workspace_settings = repo_root / ".gemini" / "settings.json"
    workspace_settings.parent.mkdir(parents=True, exist_ok=True)
    workspace_settings.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "memory-palace": {
                        "command": "bash",
                        "args": [str(evaluate_memory_palace_skill.WRAPPER_RELATIVE)],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(evaluate_memory_palace_skill, "REPO_ROOT", repo_root)

    db_path = evaluate_memory_palace_skill._extract_gemini_memory_palace_db_path()

    assert db_path == evaluate_memory_palace_skill._sqlite_path_from_url(
        evaluate_memory_palace_skill.EXPECTED_DB_URI
    )


def test_smoke_cursor_reports_authentication_required_as_partial(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    cursor_mirror = tmp_path / ".cursor" / "skills" / "memory-palace"
    cursor_mirror.mkdir(parents=True)
    cursor_bin = tmp_path / "cursor-agent"
    cursor_bin.write_text("", encoding="utf-8")

    monkeypatch.setitem(evaluate_memory_palace_skill.MIRRORS, "cursor", cursor_mirror)
    monkeypatch.setattr(evaluate_memory_palace_skill, "CURSOR_AGENT_BIN", cursor_bin)
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "_mirror_contract_issues",
        lambda name: [],
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "run_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["cursor-agent", "-p"],
            1,
            "",
            "Authentication required. Please sign in.",
        ),
    )

    result = evaluate_memory_palace_skill.smoke_cursor()

    assert result.status == "PARTIAL"
    assert "IDE Host 兼容检查" in result.summary
    assert "登录/鉴权" in result.summary


def test_mirror_only_status_requires_matching_agent_assets(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    agent_mirror = tmp_path / ".agent" / "skills" / "memory-palace"
    agent_mirror.mkdir(parents=True)

    monkeypatch.setitem(evaluate_memory_palace_skill.MIRRORS, "agent", agent_mirror)

    result = evaluate_memory_palace_skill.mirror_only_status("agent")

    assert result.status == "FAIL"
    assert "canonical" in result.summary
    assert "missing file" in result.details


def test_mirror_only_status_reports_partial_when_agent_assets_match_canonical(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    agent_mirror = tmp_path / ".agent" / "skills" / "memory-palace"
    for relative_path in evaluate_memory_palace_skill.REQUIRED_FILES:
        target = agent_mirror / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            (evaluate_memory_palace_skill.CANONICAL_DIR / relative_path).read_bytes()
        )

    monkeypatch.setitem(evaluate_memory_palace_skill.MIRRORS, "agent", agent_mirror)

    result = evaluate_memory_palace_skill.mirror_only_status("agent")

    assert result.status == "PARTIAL"
    assert "兼容投影已对齐 canonical" in result.summary
    assert "静态兼容检查" in result.summary
    assert str(agent_mirror) in result.details


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


def test_run_command_capture_force_kills_when_graceful_shutdown_hangs(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()

    class _FakeProcess:
        def __init__(self):
            self.pid = 456
            self.returncode = None
            self.calls = 0

        def poll(self):
            return self.returncode

        def communicate(self, input=None, timeout=None):
            _ = input
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("cmd", timeout, output="partial-out", stderr="partial-err")
            if self.calls == 2:
                raise subprocess.TimeoutExpired("cmd", timeout, output="still-out", stderr="still-err")
            self.returncode = -9
            return ("final-out", "final-err")

    fake_process = _FakeProcess()
    killed: list[tuple[int, int]] = []
    terminated: list[int] = []

    monkeypatch.setattr(
        evaluate_memory_palace_skill.subprocess,
        "Popen",
        lambda *args, **kwargs: fake_process,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "_terminate_process_tree",
        lambda process: terminated.append(process.pid),
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill.os,
        "killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )

    result = evaluate_memory_palace_skill.run_command_capture(
        ["dummy"],
        cwd=tmp_path,
        timeout=1,
    )

    assert result.timed_out is True
    assert result.returncode == -9
    assert terminated == [456]
    assert killed == [(456, evaluate_memory_palace_skill.signal.SIGKILL)]
    assert result.stdout == "final-out"
    assert result.stderr == "final-err"


def test_extract_gemini_db_path_accepts_repo_wrapper_binding(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    settings_path = tmp_path / ".gemini" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "memory-palace": {
                        "command": "bash",
                        "args": ["scripts/run_memory_palace_mcp_stdio.sh"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    expected_db = tmp_path / "demo.db"

    monkeypatch.setattr(evaluate_memory_palace_skill, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "EXPECTED_DB_URI",
        f"sqlite+aiosqlite:///{expected_db}",
    )

    resolved = evaluate_memory_palace_skill._extract_gemini_memory_palace_db_path()

    assert resolved is not None
    assert resolved.as_posix().endswith(expected_db.as_posix())


def test_extract_gemini_db_path_accepts_user_scope_absolute_wrapper(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    home_dir = tmp_path / "home"
    settings_path = home_dir / ".gemini" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "memory-palace": {
                        "command": "bash",
                        "args": [str(evaluate_memory_palace_skill.WRAPPER_ABSOLUTE)],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    expected_db = tmp_path / "user-demo.db"

    monkeypatch.setattr(evaluate_memory_palace_skill.Path, "home", lambda: home_dir)
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "EXPECTED_DB_URI",
        f"sqlite+aiosqlite:///{expected_db}",
    )

    resolved = evaluate_memory_palace_skill._extract_gemini_memory_palace_db_path()

    assert resolved is not None
    assert resolved.as_posix().endswith(expected_db.as_posix())


def test_sqlite_path_from_url_strips_query_and_memory_targets() -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()

    assert evaluate_memory_palace_skill._sqlite_path_from_url(
        "sqlite+aiosqlite:////tmp/demo.db?mode=rwc#fragment"
    ) == Path("/tmp/demo.db")
    assert evaluate_memory_palace_skill._sqlite_path_from_url(
        "sqlite+aiosqlite:///:memory:"
    ) is None


def test_smoke_antigravity_requires_rule_and_reference_anchors(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    antigravity_bin = tmp_path / "antigravity"
    antigravity_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    workflow_path = tmp_path / "memory-palace.md"
    workflow_path.write_text(
        "---\n"
        "description: test\n"
        "---\n\n"
        "# /memory-palace\n\n"
        "- Repo-local workflow reference: docs/skills/memory-palace/references/mcp-workflow.md\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(evaluate_memory_palace_skill, "ANTIGRAVITY_BIN", antigravity_bin)
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_USER_WORKFLOW",
        workflow_path,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_WORKFLOW_SOURCE",
        workflow_path,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_WORKSPACE_WORKFLOW",
        tmp_path / "missing-workspace-workflow.md",
    )

    result = evaluate_memory_palace_skill.smoke_antigravity()

    assert result.status == "FAIL"
    assert "IDE Host 兼容检查失败" in result.summary
    assert "规则来源或 repo-local 引用契约不完整" in result.summary
    assert "AGENTS.md" in result.details
    assert "GEMINI.md" in result.details
    assert "trigger-samples.md" in result.details


def test_smoke_antigravity_accepts_agents_and_gemini_rule_hints(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    antigravity_bin = tmp_path / "app" / "bin" / "antigravity"
    antigravity_bin.parent.mkdir(parents=True, exist_ok=True)
    antigravity_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    bundle_main = tmp_path / "app" / "out" / "main.js"
    bundle_main.parent.mkdir(parents=True, exist_ok=True)
    bundle_main.write_text("agents.md gemini.md", encoding="utf-8")
    workflow_path = tmp_path / "memory-palace.md"
    workflow_path.write_text(
        (evaluate_memory_palace_skill.CANONICAL_DIR / "variants" / "antigravity" / "global_workflows" / "memory-palace.md").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(evaluate_memory_palace_skill, "ANTIGRAVITY_BIN", antigravity_bin)
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_USER_WORKFLOW",
        workflow_path,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_WORKFLOW_SOURCE",
        workflow_path,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_WORKSPACE_WORKFLOW",
        tmp_path / "missing-workspace-workflow.md",
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "REPO_LOCAL_AGENTS",
        tmp_path / "AGENTS.md",
    )
    evaluate_memory_palace_skill.REPO_LOCAL_AGENTS.write_text("repo rules", encoding="utf-8")

    result = evaluate_memory_palace_skill.smoke_antigravity()

    assert result.status == "PARTIAL"
    assert "IDE Host 兼容检查通过静态契约" in result.summary
    assert "AGENTS.md + GEMINI.md" in result.summary
    assert str(workflow_path) in result.details
    assert str(evaluate_memory_palace_skill.REPO_LOCAL_AGENTS) in result.details
    assert "workflow declares AGENTS.md/GEMINI.md compatibility" in result.details


def test_smoke_antigravity_accepts_workspace_workflow_when_user_scope_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    antigravity_bin = tmp_path / "app" / "bin" / "antigravity"
    antigravity_bin.parent.mkdir(parents=True, exist_ok=True)
    antigravity_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    bundle_main = tmp_path / "app" / "out" / "main.js"
    bundle_main.parent.mkdir(parents=True, exist_ok=True)
    bundle_main.write_text("agents.md gemini.md", encoding="utf-8")
    workflow_path = tmp_path / ".agent" / "workflows" / "memory-palace.md"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_workflow = (
        evaluate_memory_palace_skill.CANONICAL_DIR
        / "variants"
        / "antigravity"
        / "global_workflows"
        / "memory-palace.md"
    )
    workflow_path.write_text(canonical_workflow.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(evaluate_memory_palace_skill, "ANTIGRAVITY_BIN", antigravity_bin)
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_USER_WORKFLOW",
        tmp_path / "missing-user-workflow.md",
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_WORKSPACE_WORKFLOW",
        workflow_path,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_WORKFLOW_SOURCE",
        canonical_workflow,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "REPO_LOCAL_AGENTS",
        tmp_path / "AGENTS.md",
    )
    evaluate_memory_palace_skill.REPO_LOCAL_AGENTS.write_text("repo rules", encoding="utf-8")

    result = evaluate_memory_palace_skill.smoke_antigravity()

    assert result.status == "PARTIAL"
    assert str(workflow_path) in result.details


def test_smoke_antigravity_prefers_workspace_workflow_over_stale_user_workflow(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    antigravity_bin = tmp_path / "app" / "bin" / "antigravity"
    antigravity_bin.parent.mkdir(parents=True, exist_ok=True)
    antigravity_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    canonical_workflow = (
        evaluate_memory_palace_skill.CANONICAL_DIR
        / "variants"
        / "antigravity"
        / "global_workflows"
        / "memory-palace.md"
    )
    workspace_workflow = tmp_path / ".agent" / "workflows" / "memory-palace.md"
    workspace_workflow.parent.mkdir(parents=True, exist_ok=True)
    workspace_workflow.write_text(canonical_workflow.read_text(encoding="utf-8"), encoding="utf-8")
    user_workflow = tmp_path / ".gemini" / "antigravity" / "global_workflows" / "memory-palace.md"
    user_workflow.parent.mkdir(parents=True, exist_ok=True)
    user_workflow.write_text("stale workflow", encoding="utf-8")

    monkeypatch.setattr(evaluate_memory_palace_skill, "ANTIGRAVITY_BIN", antigravity_bin)
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_USER_WORKFLOW",
        user_workflow,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_WORKSPACE_WORKFLOW",
        workspace_workflow,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_WORKFLOW_SOURCE",
        canonical_workflow,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "REPO_LOCAL_AGENTS",
        tmp_path / "AGENTS.md",
    )
    evaluate_memory_palace_skill.REPO_LOCAL_AGENTS.write_text("repo rules", encoding="utf-8")

    result = evaluate_memory_palace_skill.smoke_antigravity()

    assert result.status == "PARTIAL"
    assert str(workspace_workflow) in result.details
    assert str(user_workflow) not in result.details


def test_smoke_antigravity_fails_when_repo_agents_file_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    antigravity_bin = tmp_path / "app" / "bin" / "antigravity"
    antigravity_bin.parent.mkdir(parents=True, exist_ok=True)
    antigravity_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    workflow_path = tmp_path / "memory-palace.md"
    canonical_workflow = (
        evaluate_memory_palace_skill.CANONICAL_DIR
        / "variants"
        / "antigravity"
        / "global_workflows"
        / "memory-palace.md"
    )
    workflow_path.write_text(canonical_workflow.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setattr(evaluate_memory_palace_skill, "ANTIGRAVITY_BIN", antigravity_bin)
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_USER_WORKFLOW",
        workflow_path,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_WORKSPACE_WORKFLOW",
        tmp_path / "missing-workspace-workflow.md",
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_WORKFLOW_SOURCE",
        canonical_workflow,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "REPO_LOCAL_AGENTS",
        tmp_path / "missing-AGENTS.md",
    )

    result = evaluate_memory_palace_skill.smoke_antigravity()

    assert result.status == "FAIL"
    assert "IDE Host 兼容检查失败" in result.summary
    assert "仓库根 AGENTS.md 缺失" in result.summary


def test_smoke_antigravity_fails_when_installed_workflow_drifts_from_canonical(
    monkeypatch, tmp_path: Path
) -> None:
    evaluate_memory_palace_skill = _load_skill_eval_module()
    antigravity_bin = tmp_path / "antigravity"
    antigravity_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    workflow_path = tmp_path / "memory-palace.md"
    canonical_workflow = tmp_path / "canonical-memory-palace.md"
    workflow_path.write_text("workflow A\n", encoding="utf-8")
    canonical_workflow.write_text("workflow B\n", encoding="utf-8")

    monkeypatch.setattr(evaluate_memory_palace_skill, "ANTIGRAVITY_BIN", antigravity_bin)
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_USER_WORKFLOW",
        workflow_path,
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_WORKSPACE_WORKFLOW",
        tmp_path / "missing-workspace-workflow.md",
    )
    monkeypatch.setattr(
        evaluate_memory_palace_skill,
        "ANTIGRAVITY_WORKFLOW_SOURCE",
        canonical_workflow,
    )

    result = evaluate_memory_palace_skill.smoke_antigravity()

    assert result.status == "FAIL"
    assert "IDE Host 兼容检查失败" in result.summary
    assert "与 canonical 不一致" in result.summary
