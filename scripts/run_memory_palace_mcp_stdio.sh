#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${PROJECT_ROOT}/backend"
VENV_PYTHON="${BACKEND_DIR}/.venv/bin/python"
ENV_FILE="${PROJECT_ROOT}/.env"
DOCKER_ENV_FILE="${PROJECT_ROOT}/.env.docker"
DEFAULT_DB_PATH="${PROJECT_ROOT}/demo.db"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "Missing backend virtualenv python: ${VENV_PYTHON}" >&2
  exit 1
fi

cd "${BACKEND_DIR}"

# Reuse the repo's configured DATABASE_URL when .env exists so MCP clients and
# the Dashboard/API keep talking to the same SQLite file. Fall back to demo.db
# only for a minimal no-.env local boot.
if [[ -z "${DATABASE_URL:-}" && ! -f "${ENV_FILE}" ]]; then
  if [[ -f "${DOCKER_ENV_FILE}" ]]; then
    echo "Refusing to fall back to demo.db while ${DOCKER_ENV_FILE} exists." >&2
    echo "The repo-local stdio wrapper does not reuse Docker's /app/data database path." >&2
    echo "Create a local .env for the SQLite file you want, or connect your client to the Docker /sse endpoint instead." >&2
    exit 1
  fi
  export DATABASE_URL="sqlite+aiosqlite:////${DEFAULT_DB_PATH#/}"
fi
export RETRIEVAL_REMOTE_TIMEOUT_SEC="${RETRIEVAL_REMOTE_TIMEOUT_SEC:-1}"

exec "${VENV_PYTHON}" mcp_server.py
