#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "Memory-Palace" / "backend"
REPORT_PATH = REPO_ROOT / "Memory-Palace" / "docs" / "skills" / "MCP_LIVE_E2E_REPORT.md"
EXPECTED_TOOLS = {
    "read_memory",
    "create_memory",
    "update_memory",
    "delete_memory",
    "add_alias",
    "search_memory",
    "compact_context",
    "rebuild_index",
    "index_status",
}


@dataclass
class StepResult:
    name: str
    status: str
    summary: str
    details: str = ""


def _text_of(call_result: Any) -> str:
    return "\n".join(getattr(item, "text", str(item)) for item in call_result.content)


def _maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return text


def _result(status: str, name: str, summary: str, details: str = "") -> StepResult:
    return StepResult(name=name, status=status, summary=summary, details=details)


async def run_suite() -> tuple[list[StepResult], str]:
    temp_root = Path(tempfile.mkdtemp(prefix="memory-palace-live-mcp-"))
    db_path = temp_root / "memory_palace_live.db"
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
            "VALID_DOMAINS": "core,notes,system",
            "CORE_MEMORY_URIS": "core://pref_concise",
            "SEARCH_DEFAULT_MODE": "keyword",
            "RETRIEVAL_EMBEDDING_BACKEND": "none",
            "RETRIEVAL_RERANKER_ENABLED": "false",
            "WRITE_GUARD_LLM_ENABLED": "false",
            "INTENT_LLM_ENABLED": "false",
            "COMPACT_GIST_LLM_ENABLED": "false",
        }
    )

    server = StdioServerParameters(
        command="/bin/zsh",
        args=["-lc", "source .venv/bin/activate && python mcp_server.py"],
        cwd=str(BACKEND_ROOT),
        env=env,
    )

    results: list[StepResult] = []
    stderr_path = temp_root / "mcp_server.stderr.log"
    with stderr_path.open("w+", encoding="utf-8") as errlog:
        async with stdio_client(server, errlog=errlog) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                tools = await session.list_tools()
                discovered = {tool.name for tool in tools.tools}
                if discovered == EXPECTED_TOOLS:
                    results.append(_result("PASS", "tool_inventory", "stdio MCP 暴露 9 个工具"))
                else:
                    results.append(
                        _result(
                            "FAIL",
                            "tool_inventory",
                            "工具集合不匹配",
                            f"expected={sorted(EXPECTED_TOOLS)} actual={sorted(discovered)}",
                        )
                    )

                boot_initial = _text_of(await session.call_tool("read_memory", {"uri": "system://boot"}))
                if "Core Memories" in boot_initial and "Loaded: 0/1" in boot_initial:
                    results.append(_result("PASS", "boot_empty", "首次 boot 在空库中按设计返回空核心记忆"))
                else:
                    results.append(_result("FAIL", "boot_empty", "首次 boot 返回异常", boot_initial))

                create_raw = _maybe_json(
                    _text_of(
                        await session.call_tool(
                            "create_memory",
                            {
                                "parent_uri": "core://",
                                "content": "user likes concise answers",
                                "priority": 1,
                                "title": "pref_concise",
                                "disclosure": "when answering",
                            },
                        )
                    )
                )
                if isinstance(create_raw, dict) and create_raw.get("created") and create_raw.get("guard_action") == "ADD":
                    results.append(_result("PASS", "create_memory", "create_memory 成功并返回 guard_action=ADD"))
                else:
                    results.append(_result("FAIL", "create_memory", "create_memory 未按设计成功", json.dumps(create_raw, ensure_ascii=False)))

                duplicate_raw = _maybe_json(
                    _text_of(
                        await session.call_tool(
                            "create_memory",
                            {
                                "parent_uri": "core://",
                                "content": "user likes concise answers",
                                "priority": 1,
                                "title": "pref_concise_dup",
                                "disclosure": "when answering",
                            },
                        )
                    )
                )
                if (
                    isinstance(duplicate_raw, dict)
                    and duplicate_raw.get("created") is False
                    and duplicate_raw.get("guard_action") in {"NOOP", "UPDATE"}
                    and duplicate_raw.get("guard_target_uri") == "core://pref_concise"
                ):
                    results.append(
                        _result(
                            "PASS",
                            "write_guard_block",
                            f"重复写入被 {duplicate_raw.get('guard_action')} 正确拦截",
                        )
                    )
                else:
                    results.append(_result("FAIL", "write_guard_block", "重复写入未被按设计拦截", json.dumps(duplicate_raw, ensure_ascii=False)))

                search_raw = _maybe_json(_text_of(await session.call_tool("search_memory", {"query": "concise answers"})))
                if (
                    isinstance(search_raw, dict)
                    and search_raw.get("ok") is True
                    and search_raw.get("degraded") is False
                    and any(item.get("uri") == "core://pref_concise" for item in search_raw.get("results", []))
                ):
                    results.append(_result("PASS", "search_memory", "search_memory 返回预期记忆且未降级"))
                else:
                    results.append(_result("FAIL", "search_memory", "search_memory 结果异常", json.dumps(search_raw, ensure_ascii=False)))

                read_core = _text_of(await session.call_tool("read_memory", {"uri": "core://pref_concise"}))
                if "user likes concise answers" in read_core:
                    results.append(_result("PASS", "read_memory", "read_memory 能读取刚创建的记忆"))
                else:
                    results.append(_result("FAIL", "read_memory", "read_memory 未返回预期内容", read_core))

                update_raw = _maybe_json(
                    _text_of(
                        await session.call_tool(
                            "update_memory",
                            {"uri": "core://pref_concise", "old_string": "concise", "new_string": "short"},
                        )
                    )
                )
                if isinstance(update_raw, dict) and update_raw.get("updated") and update_raw.get("guard_action") == "ADD":
                    results.append(_result("PASS", "update_memory", "update_memory patch 模式成功"))
                else:
                    results.append(_result("FAIL", "update_memory", "update_memory 结果异常", json.dumps(update_raw, ensure_ascii=False)))

                alias_raw = _text_of(await session.call_tool("add_alias", {"new_uri": "notes://pref_alias", "target_uri": "core://pref_concise"}))
                alias_read = _text_of(await session.call_tool("read_memory", {"uri": "notes://pref_alias"}))
                if "Success: Alias" in alias_raw and "MEMORY: notes://pref_alias" in alias_read:
                    results.append(_result("PASS", "add_alias", "add_alias 成功，alias 可读"))
                else:
                    results.append(_result("FAIL", "add_alias", "add_alias 行为异常", alias_raw + "\n\n" + alias_read))

                delete_alias = _text_of(await session.call_tool("delete_memory", {"uri": "notes://pref_alias"}))
                read_after_delete = _text_of(await session.call_tool("read_memory", {"uri": "core://pref_concise"}))
                if "Success: Memory 'notes://pref_alias' deleted." in delete_alias and "user likes short answers" in read_after_delete:
                    results.append(_result("PASS", "delete_alias", "删除 alias 后原始 core 路径仍保留"))
                else:
                    results.append(_result("FAIL", "delete_alias", "delete_memory/alias 行为不符合设计", delete_alias + "\n\n" + read_after_delete))

                compact_raw = _maybe_json(_text_of(await session.call_tool("compact_context", {"reason": "force test", "force": True, "max_lines": 6})))
                if isinstance(compact_raw, dict) and compact_raw.get("ok") is True:
                    results.append(_result("PASS", "compact_context", "compact_context 可正常返回"))
                else:
                    results.append(_result("FAIL", "compact_context", "compact_context 返回异常", json.dumps(compact_raw, ensure_ascii=False)))

                index_raw = _maybe_json(_text_of(await session.call_tool("index_status", {})))
                if isinstance(index_raw, dict) and index_raw.get("ok") is True and "runtime" in index_raw:
                    results.append(_result("PASS", "index_status", "index_status 返回 runtime 状态"))
                else:
                    results.append(_result("FAIL", "index_status", "index_status 返回异常", json.dumps(index_raw, ensure_ascii=False)))

                rebuild_raw = _maybe_json(_text_of(await session.call_tool("rebuild_index", {"wait": True, "reason": "live_e2e"})))
                job = rebuild_raw.get("wait_result", {}).get("job", {}) if isinstance(rebuild_raw, dict) else {}
                if isinstance(rebuild_raw, dict) and rebuild_raw.get("ok") is True and job.get("status") == "succeeded":
                    results.append(_result("PASS", "rebuild_index", "rebuild_index(wait=true) 任务成功"))
                else:
                    results.append(_result("FAIL", "rebuild_index", "rebuild_index 返回异常", json.dumps(rebuild_raw, ensure_ascii=False)))

                boot_after = _text_of(await session.call_tool("read_memory", {"uri": "system://boot"}))
                if "Loaded: 1/1" in boot_after and "user likes short answers" in boot_after:
                    results.append(_result("PASS", "boot_after_write", "boot 在写入后能加载 core memory"))
                else:
                    results.append(_result("FAIL", "boot_after_write", "boot 在写入后未按设计加载 core memory", boot_after))

        errlog.seek(0)
        stderr_output = errlog.read()
    if "bound to a different event loop" in stderr_output or "Task exception was never retrieved" in stderr_output:
        results.append(_result("FAIL", "runtime_worker", "runtime index worker 仍存在跨 event loop 异常", stderr_output))
    else:
        results.append(_result("PASS", "runtime_worker", "未发现跨 event loop worker 异常"))

    return results, stderr_output


def build_markdown(results: list[StepResult], stderr_output: str) -> str:
    lines = [
        "# Memory Palace Live MCP E2E Report",
        "",
        "## Summary",
        "",
        "| Check | Status | Summary |",
        "|---|---|---|",
    ]
    for item in results:
        lines.append(f"| `{item.name}` | `{item.status}` | {item.summary} |")

    lines.extend(["", "## Details", ""])
    for item in results:
        lines.append(f"### {item.name}")
        lines.append("")
        lines.append(f"- Status: `{item.status}`")
        lines.append(f"- Summary: {item.summary}")
        if item.details:
            lines.extend(["", "```text", item.details.strip(), "```"])
        lines.append("")

    if stderr_output.strip():
        lines.extend(["## MCP Server stderr", "", "```text", stderr_output.strip(), "```", ""])
    return "\n".join(lines)


def run_suite_sync() -> tuple[list[StepResult], str]:
    return asyncio.run(run_suite())


def main() -> int:
    results, stderr_output = run_suite_sync()
    REPORT_PATH.write_text(build_markdown(results, stderr_output) + "\n", encoding="utf-8")
    failed = [item for item in results if item.status == "FAIL"]
    print(REPORT_PATH)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
