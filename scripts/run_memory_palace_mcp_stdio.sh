#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${PROJECT_ROOT}/backend"
VENV_PYTHON="${BACKEND_DIR}/.venv/bin/python"
DB_PATH="${BACKEND_DIR}/memory.db"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "Missing backend virtualenv python: ${VENV_PYTHON}" >&2
  exit 1
fi

cd "${BACKEND_DIR}"

export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:////${DB_PATH#/}}"
export RETRIEVAL_REMOTE_TIMEOUT_SEC="${RETRIEVAL_REMOTE_TIMEOUT_SEC:-1}"

exec "${VENV_PYTHON}" mcp_server.py
