#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the canonical Memory Palace skill into workspace-local or user-local CLI skill directories.",
    )
    parser.add_argument(
        "--targets",
        default="claude,codex,opencode,cursor,agent",
        help="Comma-separated targets. Available: claude,codex,gemini,cursor,opencode,agent,antigravity,all. Default excludes gemini because user-scope install is more reliable there.",
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


def source_dir() -> Path:
    source = project_root() / "docs" / "skills" / SKILL_NAME
    required = [
        source / "SKILL.md",
        source / "agents" / "openai.yaml",
        source / "references" / "mcp-workflow.md",
        source / "references" / "trigger-samples.md",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(f"Canonical skill source incomplete: {', '.join(missing)}")
    return source


def gemini_variant_file(source: Path) -> Path:
    variant = source / "variants" / "gemini" / "SKILL.md"
    if not variant.is_file():
        raise SystemExit(f"Gemini variant missing: {variant}")
    return variant


def antigravity_workflow_file(project_root: Path) -> Path:
    variant = (
        project_root
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
        destination_file = antigravity_destination(base_dir, "workspace" if base_dir == workspace_root() else "user")
        print(f"[{target_name}] workflow -> {destination_file}")
        if dry_run:
            return
        workflow_source = antigravity_workflow_file(project_root())
        destination_file = antigravity_destination(base_dir, "workspace" if base_dir == workspace_root() else "user")
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

    if mode == "symlink":
        if target_name == "gemini":
            raise SystemExit("Gemini install currently requires --mode copy because it uses a Gemini-specific SKILL.md variant.")
        destination_dir.symlink_to(source, target_is_directory=True)
    else:
        shutil.copytree(source, destination_dir)
        if target_name == "gemini":
            shutil.copy2(gemini_variant_file(source), destination_dir / "SKILL.md")


def main() -> None:
    args = parse_args()
    targets = resolve_targets(args.targets)
    source = source_dir()

    if args.scope == "workspace":
        base_dir = workspace_root()
    else:
        base_dir = Path.home()

    if args.scope == "workspace" and "gemini" in targets:
        print(
            "WARN: workspace-local gemini installs are less reliable because Gemini may try to read hidden "
            "`.gemini/skills/...` paths that local ignore/policy rules can block. Prefer `--scope user` for gemini.",
            file=sys.stderr,
        )

    if args.mode == "symlink" and os.name == "nt":
        raise SystemExit("Symlink mode is not supported by default on Windows. Use --mode copy.")

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

    print("Dry run complete." if args.dry_run else "Install complete.")


if __name__ == "__main__":
    main()
