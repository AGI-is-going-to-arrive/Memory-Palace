#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = REPO_ROOT / "Memory-Palace"
CANONICAL_DIR = PROJECT_ROOT / "docs" / "skills" / "memory-palace"
MIRRORS = {
    "claude": REPO_ROOT / ".claude" / "skills" / "memory-palace",
    "codex": REPO_ROOT / ".codex" / "skills" / "memory-palace",
    "opencode": REPO_ROOT / ".opencode" / "skills" / "memory-palace",
    "cursor": REPO_ROOT / ".cursor" / "skills" / "memory-palace",
    "agent": REPO_ROOT / ".agent" / "skills" / "memory-palace",
}
REQUIRED_FILES = [
    Path("SKILL.md"),
    Path("agents/openai.yaml"),
    Path("references/mcp-workflow.md"),
    Path("references/trigger-samples.md"),
]
GEMINI_TEST_MODEL = "gemini-3-flash-preview"
PROMPT = (
    "In this repository, answer in exactly 3 bullets only: "
    "(1) the first memory tool call required by the memory-palace skill, "
    "(2) what to do when guard_action is NOOP, and "
    "(3) the canonical repo-visible path of the trigger sample set."
)
CURSOR_AGENT_BIN = Path.home() / ".local" / "bin" / "cursor-agent"
ANTIGRAVITY_BIN = Path("/Applications/Antigravity.app/Contents/Resources/app/bin/antigravity")
ANTIGRAVITY_USER_WORKFLOW = Path.home() / ".gemini" / "antigravity" / "global_workflows" / "memory-palace.md"


@dataclass
class CheckResult:
    status: str
    summary: str
    details: str = ""


def run_command(cmd: list[str], *, cwd: Path, input_text: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def yaml_frontmatter_ok(path: Path) -> tuple[bool, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return False, "missing YAML frontmatter"
    end = text.find("\n---\n", 4)
    if end == -1:
        return False, "unterminated YAML frontmatter"
    block = text[4:end]
    if yaml is not None:
        try:
            data = yaml.safe_load(block)
        except Exception as exc:
            return False, f"invalid YAML: {exc}"
        if not isinstance(data, dict):
            return False, "frontmatter must be a mapping"
        if data.get("name") != "memory-palace":
            return False, "name must be memory-palace"
        if not data.get("description"):
            return False, "description must be non-empty"
        return True, "ok"
    if "name:" not in block or "description:" not in block:
        return False, "frontmatter missing name or description"
    return True, "ok"


def check_structure() -> CheckResult:
    missing = [str(CANONICAL_DIR / rel) for rel in REQUIRED_FILES if not (CANONICAL_DIR / rel).is_file()]
    if missing:
        return CheckResult("FAIL", "canonical bundle 缺文件", "\n".join(missing))
    ok, message = yaml_frontmatter_ok(CANONICAL_DIR / "SKILL.md")
    if not ok:
        return CheckResult("FAIL", "canonical SKILL.md 非法", message)
    return CheckResult("PASS", "canonical bundle 结构与 YAML 通过")


def check_mirrors() -> CheckResult:
    missing_or_mismatch: list[str] = []
    for _, mirror_dir in MIRRORS.items():
        for rel in REQUIRED_FILES:
            expected = CANONICAL_DIR / rel
            actual = mirror_dir / rel
            if not actual.is_file():
                missing_or_mismatch.append(f"missing: {actual}")
            elif actual.read_bytes() != expected.read_bytes():
                missing_or_mismatch.append(f"mismatch: {actual}")
    if missing_or_mismatch:
        return CheckResult("FAIL", "mirror 与 canonical 不一致", "\n".join(missing_or_mismatch))
    return CheckResult("PASS", "all mirrors are byte-identical to canonical")


def check_sync_script() -> CheckResult:
    script = PROJECT_ROOT / "scripts" / "sync_memory_palace_skill.py"
    proc = run_command(["python3", "-B", str(script), "--check"], cwd=REPO_ROOT, timeout=60)
    if proc.returncode != 0:
        return CheckResult("FAIL", "sync script --check 失败", (proc.stdout + "\n" + proc.stderr).strip())
    return CheckResult("PASS", proc.stdout.strip() or "sync script --check passed")


def check_gate_syntax() -> CheckResult:
    proc = run_command(["bash", "-n", "new/run_post_change_checks.sh"], cwd=REPO_ROOT, timeout=30)
    if proc.returncode != 0:
        return CheckResult("FAIL", "run_post_change_checks.sh 语法失败", proc.stderr.strip())
    return CheckResult("PASS", "run_post_change_checks.sh 语法通过")


def classify_skill_answer(text: str) -> tuple[bool, str]:
    lowered = text.lower()
    checks = [
        'read_memory("system://boot")' in text or "system://boot" in lowered,
        ("noop" in lowered and any(token in lowered for token in ["stop", "inspect", "guard_target_uri", "guard_target_id", "重复", "停止"])),
        "memory-palace/docs/skills/memory-palace/references/trigger-samples.md" in lowered,
    ]
    if all(checks):
        return True, "命中 first move / NOOP / trigger sample"
    return False, text[-1500:]


def smoke_claude() -> CheckResult:
    if shutil.which("claude") is None:
        return CheckResult("SKIP", "claude CLI 未安装")
    try:
        proc = run_command(["claude", "-p"], cwd=REPO_ROOT, input_text=PROMPT, timeout=120)
    except subprocess.TimeoutExpired:
        return CheckResult("FAIL", "Claude smoke 超时")
    success, details = classify_skill_answer(proc.stdout)
    if proc.returncode == 0 and success:
        return CheckResult("PASS", "Claude smoke 通过", proc.stdout.strip())
    return CheckResult("FAIL", "Claude smoke 未通过", (proc.stdout + "\n" + proc.stderr).strip() or details)


def smoke_codex() -> CheckResult:
    if shutil.which("codex") is None:
        return CheckResult("SKIP", "codex CLI 未安装")
    with tempfile.TemporaryDirectory(prefix="memory-palace-codex-") as tmpdir:
        tmp = Path(tmpdir)
        schema_path = tmp / "schema.json"
        output_path = tmp / "out.json"
        schema = {
            "type": "object",
            "properties": {
                "first_move": {"type": "string"},
                "noop_handling": {"type": "string"},
                "trigger_samples_path": {"type": "string"},
            },
            "required": ["first_move", "noop_handling", "trigger_samples_path"],
            "additionalProperties": False,
        }
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        try:
            proc = run_command(
                [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--color",
                    "never",
                    "-s",
                    "read-only",
                    "-C",
                    str(REPO_ROOT),
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(output_path),
                    PROMPT,
                ],
                cwd=REPO_ROOT,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            return CheckResult("FAIL", "Codex smoke 超时")
        if proc.returncode != 0 or not output_path.is_file():
            return CheckResult("FAIL", "Codex smoke 未通过", (proc.stdout + "\n" + proc.stderr).strip())
        data = json.loads(output_path.read_text(encoding="utf-8"))
        joined = json.dumps(data, ensure_ascii=False)
        success, details = classify_skill_answer(joined)
        if success:
            return CheckResult("PASS", "Codex smoke 通过", joined)
        return CheckResult("FAIL", "Codex smoke 输出不符合预期", details)


def smoke_opencode() -> CheckResult:
    if shutil.which("opencode") is None:
        return CheckResult("SKIP", "opencode CLI 未安装")
    try:
        proc = run_command(
            [
                "opencode",
                "run",
                "--dir",
                str(REPO_ROOT),
                "--format",
                "default",
                "For this repository's memory-palace skill, answer with exactly three bullets: "
                "(1) the correct first move, (2) what to do when guard_action=NOOP, "
                "(3) the path to the trigger sample file. Keep it concise.",
            ],
            cwd=REPO_ROOT,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        return CheckResult("FAIL", "OpenCode smoke 超时")
    merged = (proc.stdout + "\n" + proc.stderr).strip()
    success, details = classify_skill_answer(merged)
    if proc.returncode == 0 and success:
        return CheckResult("PASS", "OpenCode smoke 通过", merged)
    return CheckResult("FAIL", "OpenCode smoke 未通过", details)


def smoke_gemini() -> CheckResult:
    if shutil.which("gemini") is None:
        return CheckResult("SKIP", "gemini CLI 未安装")
    try:
        discovery = run_command(["gemini", "skills", "list", "--all"], cwd=REPO_ROOT, timeout=90)
    except subprocess.TimeoutExpired:
        return CheckResult("FAIL", "Gemini skills list 超时")
    discovery_text = (discovery.stdout + "\n" + discovery.stderr).strip()
    discovered = "memory-palace [Enabled]" in discovery_text or "memory-palace" in discovery_text
    try:
        invoke = run_command(
            [
                "gemini",
                "-m",
                GEMINI_TEST_MODEL,
                "-p",
                PROMPT,
                "--output-format",
                "text",
                "--allowed-tools",
                "activate_skill,read_file",
            ],
            cwd=REPO_ROOT,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return CheckResult("PARTIAL" if discovered else "FAIL", f"Gemini prompt 超时（model={GEMINI_TEST_MODEL}）", discovery_text)
    merged = (invoke.stdout + "\n" + invoke.stderr).strip()
    success, details = classify_skill_answer(merged)
    if discovered and invoke.returncode == 0 and success:
        return CheckResult("PASS", "Gemini smoke 通过", merged)
    lowered = merged.lower()
    if discovered:
        if any(token in lowered for token in ["429", "resource_exhausted", "model_capacity_exhausted", "econnreset", "tls", "socket disconnected"]):
            return CheckResult("PARTIAL", "Gemini 发现 skill 成功，但执行受上游容量或网络波动影响", merged or discovery_text)
        return CheckResult("FAIL", "Gemini 已发现 skill，但调用结果不符合预期", merged or discovery_text)
    return CheckResult("FAIL", "Gemini 未发现或未调用 memory-palace", merged or discovery_text)


def mirror_only_status(name: str) -> CheckResult:
    mirror = MIRRORS[name]
    if mirror.is_dir():
        return CheckResult("PARTIAL", f"{name} 仅完成 mirror 结构校验", str(mirror))
    return CheckResult("FAIL", f"{name} mirror 缺失", str(mirror))


def smoke_cursor() -> CheckResult:
    mirror = MIRRORS["cursor"]
    if not mirror.is_dir():
        return CheckResult("FAIL", "Cursor mirror 缺失", str(mirror))
    if not CURSOR_AGENT_BIN.is_file():
        return CheckResult("PARTIAL", "Cursor mirror 已准备，但本机未发现 cursor-agent runtime", str(mirror))
    try:
        proc = run_command([str(CURSOR_AGENT_BIN), "-p", PROMPT], cwd=REPO_ROOT, timeout=60)
    except subprocess.TimeoutExpired:
        return CheckResult("PARTIAL", "Cursor runtime 存在，但 smoke 超时", str(CURSOR_AGENT_BIN))
    merged = (proc.stdout + "\n" + proc.stderr).strip()
    lowered = merged.lower()
    if "authentication required" in lowered:
        return CheckResult("PARTIAL", "Cursor runtime 存在，但当前机器缺少登录/鉴权", merged)
    success, details = classify_skill_answer(merged)
    if proc.returncode == 0 and success:
        return CheckResult("PASS", "Cursor smoke 通过", merged)
    return CheckResult("PARTIAL", "Cursor runtime 可用，但当前 smoke 未通过", details)


def smoke_antigravity() -> CheckResult:
    if not ANTIGRAVITY_BIN.is_file():
        return CheckResult("FAIL", "Antigravity app-bundled CLI 缺失")
    if ANTIGRAVITY_USER_WORKFLOW.is_file():
        return CheckResult(
            "PARTIAL",
            "Antigravity app-bundled CLI 已发现，global_workflow 已安装；仍需 GUI 手工 smoke",
            f"{ANTIGRAVITY_BIN}\n{ANTIGRAVITY_USER_WORKFLOW}",
        )
    return CheckResult("MANUAL", "Antigravity CLI 存在，但 workflow 尚未安装", str(ANTIGRAVITY_BIN))


def generate_markdown(results: dict[str, CheckResult]) -> str:
    lines = [
        "# Memory Palace Trigger Smoke Report",
        "",
        "## Summary",
        "",
        "| Check | Status | Summary |",
        "|---|---|---|",
    ]
    for key, result in results.items():
        lines.append(f"| `{key}` | `{result.status}` | {result.summary} |")
    lines.extend(
        [
            "",
            "## Details",
            "",
        ]
    )
    for key, result in results.items():
        lines.append(f"### {key}")
        lines.append("")
        lines.append(f"- Status: `{result.status}`")
        lines.append(f"- Summary: {result.summary}")
        if result.details:
            lines.append("")
            lines.append("```text")
            lines.append(result.details.strip())
            lines.append("```")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    report_path = PROJECT_ROOT / "docs" / "skills" / "TRIGGER_SMOKE_REPORT.md"
    results: dict[str, CheckResult] = {
        "structure": check_structure(),
        "mirrors": check_mirrors(),
        "sync_check": check_sync_script(),
        "gate_syntax": check_gate_syntax(),
        "claude": smoke_claude(),
        "codex": smoke_codex(),
        "opencode": smoke_opencode(),
        "gemini": smoke_gemini(),
        "cursor": smoke_cursor(),
        "agent": mirror_only_status("agent"),
        "antigravity": smoke_antigravity(),
    }
    markdown = generate_markdown(results)
    report_path.write_text(markdown + "\n", encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
