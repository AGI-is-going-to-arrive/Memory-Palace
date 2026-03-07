#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DIR = REPO_ROOT / "docs" / "skills" / "memory-palace"
BUNDLE_MIRROR_DIRS = [
    REPO_ROOT / ".claude" / "skills" / "memory-palace",
    REPO_ROOT / ".codex" / "skills" / "memory-palace",
    REPO_ROOT / ".opencode" / "skills" / "memory-palace",
    REPO_ROOT / ".agent" / "skills" / "memory-palace",
    REPO_ROOT / ".cursor" / "skills" / "memory-palace",
]
GEMINI_VARIANT_FILE = CANONICAL_DIR / "variants" / "gemini" / "SKILL.md"
GEMINI_WORKSPACE_DIR = REPO_ROOT / ".gemini" / "skills" / "memory-palace"
BUNDLE_RELATIVE_FILES = [
    Path("SKILL.md"),
    Path("references/mcp-workflow.md"),
    Path("references/trigger-samples.md"),
    Path("agents/openai.yaml"),
]


def ensure_canonical_exists() -> None:
    missing = [path for path in BUNDLE_RELATIVE_FILES if not (CANONICAL_DIR / path).is_file()]
    if not GEMINI_VARIANT_FILE.is_file():
        missing.append(Path("variants/gemini/SKILL.md"))
    if missing:
        joined = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Canonical skill is incomplete: {joined}")


def frontmatter_is_valid(skill_file: Path) -> tuple[bool, str]:
    if not skill_file.is_file():
        return False, "missing SKILL.md"
    text = skill_file.read_text(encoding="utf-8")
    frontmatter = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    if not frontmatter:
        return False, "missing YAML frontmatter"
    block = frontmatter.group(1)
    name_match = re.search(r"(?m)^name:\s*(.+?)\s*$", block)
    description_match = re.search(r"(?m)^description:\s*(.+?)\s*$", block)
    if not name_match:
        return False, "missing name"
    if name_match.group(1).strip().strip("\"'") != "memory-palace":
        return False, "name must be memory-palace"
    if not description_match:
        return False, "missing description"
    if not description_match.group(1).strip():
        return False, "description must be non-empty"
    return True, "ok"


def files_match(left: Path, right: Path) -> bool:
    return left.is_file() and right.is_file() and left.read_bytes() == right.read_bytes()


def sync_file(relative_path: Path, target_dir: Path) -> None:
    source = CANONICAL_DIR / relative_path
    target = target_dir / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def run_check() -> int:
    ensure_canonical_exists()
    ok, message = frontmatter_is_valid(CANONICAL_DIR / "SKILL.md")
    if not ok:
        print(f"canonical SKILL.md invalid: {message}", file=sys.stderr)
        return 1

    drift: list[str] = []
    for mirror_dir in BUNDLE_MIRROR_DIRS:
        if not mirror_dir.is_dir():
            drift.append(f"missing mirror directory: {mirror_dir.relative_to(REPO_ROOT)}")
            continue
        for relative_path in BUNDLE_RELATIVE_FILES:
            source = CANONICAL_DIR / relative_path
            target = mirror_dir / relative_path
            if not files_match(source, target):
                drift.append(f"{mirror_dir.relative_to(REPO_ROOT)}/{relative_path}")
        expected = {mirror_dir / relative_path for relative_path in BUNDLE_RELATIVE_FILES}
        actual = {path for path in mirror_dir.rglob("*") if path.is_file()}
        for extra_path in sorted(actual - expected):
            drift.append(f"unexpected extra file: {extra_path.relative_to(REPO_ROOT)}")

    gemini_skill_file = GEMINI_WORKSPACE_DIR / "SKILL.md"
    if not gemini_skill_file.is_file():
        drift.append(f"missing mirror file: {gemini_skill_file.relative_to(REPO_ROOT)}")
    elif not files_match(GEMINI_VARIANT_FILE, gemini_skill_file):
        drift.append(f"mismatch: {gemini_skill_file.relative_to(REPO_ROOT)}")
    if GEMINI_WORKSPACE_DIR.is_dir():
        actual = {path for path in GEMINI_WORKSPACE_DIR.rglob("*") if path.is_file()}
        expected = {gemini_skill_file}
        for extra_path in sorted(actual - expected):
            drift.append(f"unexpected extra file: {extra_path.relative_to(REPO_ROOT)}")

    if drift:
        print("Drift detected:")
        for item in drift:
            print(f"- {item}")
        return 1
    print("All memory-palace skill mirrors are in sync.")
    return 0


def run_sync() -> int:
    ensure_canonical_exists()
    ok, message = frontmatter_is_valid(CANONICAL_DIR / "SKILL.md")
    if not ok:
        print(f"canonical SKILL.md invalid: {message}", file=sys.stderr)
        return 1
    for mirror_dir in BUNDLE_MIRROR_DIRS:
        if mirror_dir.exists():
            shutil.rmtree(mirror_dir)
        for relative_path in BUNDLE_RELATIVE_FILES:
            sync_file(relative_path, mirror_dir)
    if GEMINI_WORKSPACE_DIR.exists():
        shutil.rmtree(GEMINI_WORKSPACE_DIR)
    GEMINI_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(GEMINI_VARIANT_FILE, GEMINI_WORKSPACE_DIR / "SKILL.md")
    print("Synced memory-palace skill mirrors.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync the canonical Memory Palace skill into workspace mirrors."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check whether mirrors match the canonical skill.",
    )
    args = parser.parse_args()

    try:
        return run_check() if args.check else run_sync()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
