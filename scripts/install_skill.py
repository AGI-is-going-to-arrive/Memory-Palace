#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


SKILL_NAME = "memory-palace"
TARGET_MAP = {
    "claude": ".claude/skills",
    "codex": ".codex/skills",
    "gemini": ".gemini/skills",
    "cursor": ".cursor/skills",
    "opencode": ".opencode/skills",
    "agent": ".agent/skills",
    "antigravity": None,
}
UTF8_ENV = {
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUTF8": "1",
}
SKILL_RELATIVE_FILES = [
    Path("SKILL.md"),
    Path("agents/openai.yaml"),
    Path("references/mcp-workflow.md"),
    Path("references/trigger-samples.md"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install the canonical Memory Palace skill into workspace-local or user-local "
            "CLI skill directories, and optionally register MCP bindings that point to this repo."
        ),
    )
    parser.add_argument(
        "--targets",
        default="claude,codex,opencode,cursor,agent",
        help=(
            "Comma-separated targets. Available: claude,codex,gemini,cursor,opencode,agent,"
            "antigravity,all. Default excludes gemini because user-scope install is more reliable there."
        ),
    )
    parser.add_argument(
        "--scope",
        choices=("workspace", "user"),
        default="workspace",
        help="Install into the current workspace root or into the user's home directory.",
    )
    parser.add_argument(
        "--mode",
        choices=("copy", "symlink"),
        default="copy",
        help="Copy files by default; use symlink when you want repo edits to reflect immediately.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing installed skill directory if present.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without changing the filesystem.",
    )
    parser.add_argument(
        "--with-mcp",
        action="store_true",
        help=(
            "Also install or update MCP configuration for the selected targets when this script knows "
            "a stable configuration surface for the chosen scope."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Check whether the selected skill installs already match the canonical source. "
            "When used with --with-mcp, also verify MCP bindings."
        ),
    )
    return parser.parse_args()


def resolve_targets(raw: str) -> list[str]:
    requested = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not requested:
        raise SystemExit("No targets specified.")
    if "all" in requested:
        return list(TARGET_MAP)
    invalid = [item for item in requested if item not in TARGET_MAP]
    if invalid:
        raise SystemExit(f"Unknown targets: {', '.join(invalid)}")
    ordered: list[str] = []
    seen: set[str] = set()
    for item in requested:
        if item not in seen:
            ordered.append(item)
            seen.add(item)
    return ordered


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def project_root() -> Path:
    return workspace_root() / "Memory-Palace"


def repo_wrapper_relative() -> Path:
    return Path("Memory-Palace/scripts/run_memory_palace_mcp_stdio.sh")


def repo_wrapper_absolute() -> Path:
    return project_root() / "scripts" / "run_memory_palace_mcp_stdio.sh"


def source_dir() -> Path:
    source = project_root() / "docs" / "skills" / SKILL_NAME
    required = [source / relative_path for relative_path in SKILL_RELATIVE_FILES]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"Canonical skill source incomplete: {', '.join(missing)}")
    return source


def gemini_variant_file(source: Path) -> Path:
    variant = source / "variants" / "gemini" / "SKILL.md"
    if not variant.is_file():
        raise SystemExit(f"Gemini variant missing: {variant}")
    return variant


def antigravity_workflow_file(project_dir: Path) -> Path:
    variant = (
        project_dir
        / "docs"
        / "skills"
        / SKILL_NAME
        / "variants"
        / "antigravity"
        / "global_workflows"
        / "memory-palace.md"
    )
    if not variant.is_file():
        raise SystemExit(f"Antigravity workflow variant missing: {variant}")
    return variant


def antigravity_destination(base_dir: Path, scope: str) -> Path:
    if scope == "workspace":
        return base_dir / ".agent" / "workflows" / "memory-palace.md"
    return base_dir / ".gemini" / "antigravity" / "global_workflows" / "memory-palace.md"


def ensure_wrapper_script() -> None:
    wrapper = repo_wrapper_absolute()
    if not wrapper.is_file():
        raise SystemExit(f"Missing MCP wrapper script: {wrapper}")


def read_json_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_file(path: Path, payload: dict, *, dry_run: bool) -> None:
    print(f"[json] write -> {path}")
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _normalized(value: object) -> str:
    return str(value).replace("\\", "/")


def _wrapper_binding_ok(command_parts: list[str], *, allow_relative: bool) -> bool:
    normalized_parts = [_normalized(part) for part in command_parts if str(part).strip()]
    joined = " ".join(normalized_parts)
    candidates = {_normalized(repo_wrapper_absolute())}
    if allow_relative:
        candidates.add(_normalized(repo_wrapper_relative()))
    return any(candidate in joined for candidate in candidates)


def _project_claude_server_block() -> dict:
    return {
        "type": "stdio",
        "command": "bash",
        "args": [str(repo_wrapper_relative())],
        "env": dict(UTF8_ENV),
    }


def _user_claude_server_block() -> dict:
    return {
        "type": "stdio",
        "command": "bash",
        "args": [str(repo_wrapper_absolute())],
        "env": dict(UTF8_ENV),
    }


def _gemini_server_block(*, relative: bool) -> dict:
    script_arg = str(repo_wrapper_relative() if relative else repo_wrapper_absolute())
    return {
        "command": "bash",
        "args": [script_arg],
        "description": "Memory Palace MCP for the current repository",
        "timeout": 20000,
        "env": dict(UTF8_ENV),
    }


def _opencode_server_block() -> dict:
    return {
        "command": ["bash", str(repo_wrapper_absolute())],
        "enabled": True,
        "type": "local",
    }


def _codex_server_block_text() -> str:
    script_arg = json.dumps(str(repo_wrapper_absolute()))
    return "\n".join(
        [
            "[mcp_servers.memory-palace]",
            'command = "bash"',
            f"args = [{script_arg}]",
            "startup_timeout_sec = 30.0",
            "",
            "[mcp_servers.memory-palace.env]",
            'PYTHONIOENCODING = "utf-8"',
            'PYTHONUTF8 = "1"',
            "",
        ]
    )


def _strip_codex_memory_palace_block(text: str) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[mcp_servers.memory-palace"):
            skipping = True
            continue
        if skipping and stripped.startswith("[") and not stripped.startswith("[mcp_servers.memory-palace"):
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept).rstrip()


def _workspace_mcp_supported(target_name: str) -> bool:
    return target_name in {"claude", "gemini"}


def _user_mcp_supported(target_name: str) -> bool:
    return target_name in {"claude", "codex", "gemini", "opencode"}


def install_target(
    target_name: str,
    *,
    source: Path,
    base_dir: Path,
    mode: str,
    force: bool,
    dry_run: bool,
) -> None:
    if target_name == "antigravity":
        destination_file = antigravity_destination(
            base_dir, "workspace" if base_dir == workspace_root() else "user"
        )
        print(f"[{target_name}] workflow -> {destination_file}")
        if dry_run:
            return
        workflow_source = antigravity_workflow_file(project_root())
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        legacy_file = destination_file.with_name("acg-memory-palace.md")
        if legacy_file.exists() or legacy_file.is_symlink():
            if legacy_file.is_symlink() or legacy_file.is_file():
                legacy_file.unlink()
            else:
                shutil.rmtree(legacy_file)
        if destination_file.exists() or destination_file.is_symlink():
            if not force:
                raise SystemExit(
                    f"Target already exists: {destination_file} (use --force to replace it)"
                )
            if destination_file.is_symlink() or destination_file.is_file():
                destination_file.unlink()
            else:
                shutil.rmtree(destination_file)
        shutil.copy2(workflow_source, destination_file)
        return

    destination_root = base_dir / TARGET_MAP[target_name]
    destination_dir = destination_root / SKILL_NAME
    action = "symlink" if mode == "symlink" else "copy"
    print(f"[{target_name}] {action} -> {destination_dir}")

    if dry_run:
        return

    destination_root.mkdir(parents=True, exist_ok=True)

    if destination_dir.exists() or destination_dir.is_symlink():
        if not force:
            raise SystemExit(
                f"Target already exists: {destination_dir} (use --force to replace it)"
            )
        if destination_dir.is_symlink() or destination_dir.is_file():
            destination_dir.unlink()
        else:
            shutil.rmtree(destination_dir)

    destination_dir.mkdir(parents=True, exist_ok=True)

    if mode == "symlink":
        if target_name == "gemini":
            skill_target = destination_dir / "SKILL.md"
            skill_target.parent.mkdir(parents=True, exist_ok=True)
            skill_target.symlink_to(gemini_variant_file(source))
            return
        for relative_path in SKILL_RELATIVE_FILES:
            link_path = destination_dir / relative_path
            link_path.parent.mkdir(parents=True, exist_ok=True)
            link_path.symlink_to(source / relative_path)
        return

    if target_name == "gemini":
        shutil.copy2(gemini_variant_file(source), destination_dir / "SKILL.md")
        return

    for relative_path in SKILL_RELATIVE_FILES:
        source_file = source / relative_path
        target_file = destination_dir / relative_path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)


def install_mcp_binding(target_name: str, *, scope: str, dry_run: bool) -> None:
    ensure_wrapper_script()
    if scope == "workspace":
        if not _workspace_mcp_supported(target_name):
            print(
                f"[{target_name}] MCP note -> no stable workspace-local MCP config surface; use --scope user --with-mcp"
            )
            return
        if target_name == "claude":
            config_path = workspace_root() / ".mcp.json"
            payload = read_json_file(config_path)
            payload.setdefault("mcpServers", {})["memory-palace"] = _project_claude_server_block()
            write_json_file(config_path, payload, dry_run=dry_run)
            return
        if target_name == "gemini":
            config_path = workspace_root() / ".gemini" / "settings.json"
            payload = read_json_file(config_path)
            payload.setdefault("mcpServers", {})["memory-palace"] = _gemini_server_block(relative=True)
            write_json_file(config_path, payload, dry_run=dry_run)
            return
        return

    if not _user_mcp_supported(target_name):
        print(f"[{target_name}] MCP note -> no user-scope MCP config managed for this target")
        return

    if target_name == "claude":
        config_path = Path.home() / ".claude.json"
        payload = read_json_file(config_path)
        projects = payload.setdefault("projects", {})
        project_block = projects.setdefault(str(workspace_root()), {})
        project_block.setdefault("mcpServers", {})["memory-palace"] = _user_claude_server_block()
        write_json_file(config_path, payload, dry_run=dry_run)
        return

    if target_name == "codex":
        config_path = Path.home() / ".codex" / "config.toml"
        existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
        trimmed = _strip_codex_memory_palace_block(existing)
        rendered = _codex_server_block_text().rstrip()
        next_text = f"{trimmed}\n\n{rendered}\n" if trimmed else f"{rendered}\n"
        print(f"[toml] write -> {config_path}")
        if dry_run:
            return
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(next_text, encoding="utf-8")
        return

    if target_name == "gemini":
        config_path = Path.home() / ".gemini" / "settings.json"
        payload = read_json_file(config_path)
        payload.setdefault("mcpServers", {})["memory-palace"] = _gemini_server_block(relative=False)
        write_json_file(config_path, payload, dry_run=dry_run)
        return

    if target_name == "opencode":
        config_path = Path.home() / ".config" / "opencode" / "opencode.json"
        payload = read_json_file(config_path)
        if not payload:
            payload["$schema"] = "https://opencode.ai/config.json"
        payload.setdefault("mcp", {})["memory-palace"] = _opencode_server_block()
        write_json_file(config_path, payload, dry_run=dry_run)


def _check_regular_skill_target(target_name: str, *, base_dir: Path, source: Path) -> tuple[bool, str]:
    destination_root = base_dir / TARGET_MAP[target_name]
    destination_dir = destination_root / SKILL_NAME
    if not destination_dir.is_dir():
        return False, f"missing skill dir: {destination_dir}"
    for relative_path in SKILL_RELATIVE_FILES:
        expected = source / relative_path
        actual = destination_dir / relative_path
        if not actual.is_file():
            return False, f"missing file: {actual}"
        if actual.read_bytes() != expected.read_bytes():
            return False, f"mismatch: {actual}"
    return True, str(destination_dir)


def check_skill_target(target_name: str, *, base_dir: Path, source: Path, scope: str) -> tuple[bool, str]:
    if target_name == "antigravity":
        destination_file = antigravity_destination(base_dir, scope)
        expected = antigravity_workflow_file(project_root())
        if destination_file.is_file() and destination_file.read_bytes() == expected.read_bytes():
            return True, str(destination_file)
        return False, f"missing or mismatched workflow: {destination_file}"
    if target_name == "gemini":
        destination_file = base_dir / TARGET_MAP[target_name] / SKILL_NAME / "SKILL.md"
        expected = gemini_variant_file(source)
        if destination_file.is_file() and destination_file.read_bytes() == expected.read_bytes():
            return True, str(destination_file)
        return False, f"missing or mismatched file: {destination_file}"
    return _check_regular_skill_target(target_name, base_dir=base_dir, source=source)


def check_mcp_binding(target_name: str, *, scope: str) -> tuple[bool | None, str]:
    if scope == "workspace":
        if not _workspace_mcp_supported(target_name):
            return None, "workspace scope does not define a stable repo-local MCP config for this target"
        if target_name == "claude":
            config_path = workspace_root() / ".mcp.json"
            payload = read_json_file(config_path)
            server = payload.get("mcpServers", {}).get("memory-palace", {})
            command = [server.get("command", ""), *(server.get("args") or [])]
            ok = server.get("type") == "stdio" and _wrapper_binding_ok(command, allow_relative=True)
            return ok, str(config_path)
        if target_name == "gemini":
            config_path = workspace_root() / ".gemini" / "settings.json"
            payload = read_json_file(config_path)
            server = payload.get("mcpServers", {}).get("memory-palace", {})
            command = [server.get("command", ""), *(server.get("args") or [])]
            ok = _wrapper_binding_ok(command, allow_relative=True)
            return ok, str(config_path)
        return None, "not applicable"

    if not _user_mcp_supported(target_name):
        return None, "no user-scope MCP config is managed for this target"

    if target_name == "claude":
        config_path = Path.home() / ".claude.json"
        payload = read_json_file(config_path)
        project_block = payload.get("projects", {}).get(str(workspace_root()), {})
        server = project_block.get("mcpServers", {}).get("memory-palace", {})
        command = [server.get("command", ""), *(server.get("args") or [])]
        ok = server.get("type") == "stdio" and _wrapper_binding_ok(command, allow_relative=False)
        return ok, str(config_path)

    if target_name == "codex":
        config_path = Path.home() / ".codex" / "config.toml"
        if not config_path.is_file():
            return False, str(config_path)
        if tomllib is None:
            return False, "tomllib unavailable; cannot inspect ~/.codex/config.toml"
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
        server = payload.get("mcp_servers", {}).get("memory-palace", {})
        command = [server.get("command", ""), *(server.get("args") or [])]
        ok = _wrapper_binding_ok(command, allow_relative=False)
        return ok, str(config_path)

    if target_name == "gemini":
        config_path = Path.home() / ".gemini" / "settings.json"
        payload = read_json_file(config_path)
        server = payload.get("mcpServers", {}).get("memory-palace", {})
        command = [server.get("command", ""), *(server.get("args") or [])]
        ok = _wrapper_binding_ok(command, allow_relative=False)
        return ok, str(config_path)

    if target_name == "opencode":
        config_path = Path.home() / ".config" / "opencode" / "opencode.json"
        payload = read_json_file(config_path)
        server = payload.get("mcp", {}).get("memory-palace", {})
        command = list(server.get("command") or [])
        ok = bool(server.get("enabled")) and _wrapper_binding_ok(command, allow_relative=False)
        return ok, str(config_path)

    return None, "not applicable"


def _validate_frontmatter(source: Path) -> None:
    skill_file = source / "SKILL.md"
    text = skill_file.read_text(encoding="utf-8")
    frontmatter = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    if not frontmatter:
        raise SystemExit(f"Missing YAML frontmatter in {skill_file}")
    block = frontmatter.group(1)
    name_match = re.search(r"(?m)^name:\s*(.+?)\s*$", block)
    description_match = re.search(r"(?m)^description:\s*(.+?)\s*$", block)
    if not name_match or name_match.group(1).strip().strip("\"'") != SKILL_NAME:
        raise SystemExit(f"Invalid skill name in {skill_file}")
    if not description_match or not description_match.group(1).strip():
        raise SystemExit(f"Missing description in {skill_file}")


def run_check(*, args: argparse.Namespace, targets: list[str], source: Path, base_dir: Path) -> int:
    _validate_frontmatter(source)
    ok_all = True
    print(f"Source: {source}")
    print(f"Scope: {args.scope} -> {base_dir}")
    for target_name in targets:
        ok, message = check_skill_target(
            target_name,
            base_dir=base_dir,
            source=source,
            scope=args.scope,
        )
        print(f"[{target_name}] SKILL {'OK' if ok else 'FAIL'} - {message}")
        ok_all &= ok
        if args.with_mcp:
            binding_ok, binding_message = check_mcp_binding(target_name, scope=args.scope)
            if binding_ok is None:
                print(f"[{target_name}] MCP NOTE - {binding_message}")
            else:
                print(f"[{target_name}] MCP {'OK' if binding_ok else 'FAIL'} - {binding_message}")
                ok_all &= binding_ok
    return 0 if ok_all else 1


def main() -> None:
    args = parse_args()
    targets = resolve_targets(args.targets)
    source = source_dir()
    ensure_wrapper_script()

    if args.scope == "workspace":
        base_dir = workspace_root()
    else:
        base_dir = Path.home()

    if args.scope == "workspace" and "gemini" in targets:
        print(
            "WARN: workspace-local gemini installs can still be less reliable on some machines because "
            "Gemini may try to read hidden `.gemini/skills/...` paths. Prefer `--scope user` for Gemini "
            "if a local policy blocks hidden paths.",
            file=sys.stderr,
        )

    if args.mode == "symlink" and os.name == "nt":
        raise SystemExit("Symlink mode is not supported by default on Windows. Use --mode copy.")

    if args.check:
        raise SystemExit(run_check(args=args, targets=targets, source=source, base_dir=base_dir))

    print(f"Source: {source}")
    print(f"Scope: {args.scope} -> {base_dir}")

    for target_name in targets:
        install_target(
            target_name,
            source=source,
            base_dir=base_dir,
            mode=args.mode,
            force=args.force,
            dry_run=args.dry_run,
        )
        if args.with_mcp:
            install_mcp_binding(target_name, scope=args.scope, dry_run=args.dry_run)

    print("Dry run complete." if args.dry_run else "Install complete.")


if __name__ == "__main__":
    main()
