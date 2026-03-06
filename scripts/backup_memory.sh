#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ENV_FILE="${PROJECT_ROOT}/.env"
OUTPUT_DIR="${PROJECT_ROOT}/backups"

usage() {
  cat <<'EOF'
Usage: bash scripts/backup_memory.sh [--env-file <path>] [--output-dir <path>]

Creates a consistent SQLite backup using Python's sqlite3 backup API.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Environment file not found: ${ENV_FILE}" >&2
  exit 1
fi

PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "Python is required for consistent SQLite backup but was not found in PATH." >&2
  exit 1
fi

export MEMORY_PALACE_PROJECT_ROOT="${PROJECT_ROOT}"
export MEMORY_PALACE_BACKUP_ENV_FILE="${ENV_FILE}"
export MEMORY_PALACE_BACKUP_OUTPUT_DIR="${OUTPUT_DIR}"

"${PYTHON_BIN}" - <<'PY'
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def normalize_env_value(value: str) -> str:
    normalized = value.strip()
    if len(normalized) >= 2 and (
        (normalized.startswith("'") and normalized.endswith("'"))
        or (normalized.startswith('"') and normalized.endswith('"'))
    ):
        return normalized[1:-1]
    return normalized


def read_database_url(env_path: Path) -> str:
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "DATABASE_URL":
            database_url = normalize_env_value(value)
            if database_url:
                return database_url
    fail(f"DATABASE_URL is missing in {env_path}")


def resolve_sqlite_path(project_root: Path, database_url: str) -> Path:
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        fail(f"DATABASE_URL must start with '{prefix}'")
    raw_path = database_url[len(prefix):].strip()
    if not raw_path:
        fail("DATABASE_URL does not contain a valid sqlite file path")
    sqlite_path = Path(raw_path)
    if not sqlite_path.is_absolute():
        sqlite_path = project_root / sqlite_path
    return sqlite_path


project_root = Path(os.environ["MEMORY_PALACE_PROJECT_ROOT"])
env_file = Path(os.environ["MEMORY_PALACE_BACKUP_ENV_FILE"])
output_dir = Path(os.environ["MEMORY_PALACE_BACKUP_OUTPUT_DIR"])

database_url = read_database_url(env_file)
sqlite_path = resolve_sqlite_path(project_root, database_url)
if not sqlite_path.exists():
    fail(f"SQLite database file not found: {sqlite_path}")

output_dir.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
dest_file = output_dir / f"memory_palace_backup_{timestamp}.db"

with sqlite3.connect(sqlite_path) as source_conn:
    with sqlite3.connect(dest_file) as target_conn:
        source_conn.backup(target_conn)

print("Backup completed.")
print(f"Source: {sqlite_path}")
print(f"Target: {dest_file}")
PY
