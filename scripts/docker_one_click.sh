#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

profile="b"
no_build=0
frontend_port="${MEMORY_PALACE_FRONTEND_PORT:-${NOCTURNE_FRONTEND_PORT:-3000}}"
backend_port="${MEMORY_PALACE_BACKEND_PORT:-${NOCTURNE_BACKEND_PORT:-18000}}"
auto_port=1

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/docker_one_click.sh [--profile a|b|c|d] [--frontend-port <port>] [--backend-port <port>] [--no-auto-port] [--no-build]
USAGE
}

is_positive_int() {
  [[ "$1" =~ ^[0-9]+$ ]] && [[ "$1" -ge 1 ]] && [[ "$1" -le 65535 ]]
}

port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  (echo >"/dev/tcp/127.0.0.1/${port}") >/dev/null 2>&1
}

find_free_port() {
  local start_port="$1"
  local max_scan="${2:-200}"
  local current="$start_port"
  local i

  for ((i = 0; i <= max_scan; i++)); do
    if [[ "${current}" -gt 65535 ]]; then
      break
    fi
    if ! port_in_use "${current}"; then
      echo "${current}"
      return 0
    fi
    current=$((current + 1))
  done

  echo "" >&2
  return 1
}

resolve_data_volume() {
  local explicit_volume="${MEMORY_PALACE_DATA_VOLUME:-${NOCTURNE_DATA_VOLUME:-}}"
  if [[ -n "${explicit_volume}" ]]; then
    echo "${explicit_volume}"
    return 0
  fi

  if docker volume inspect memory_palace_data >/dev/null 2>&1; then
    echo "memory_palace_data"
    return 0
  fi

  local project_slug
  project_slug="$(
    basename "${PROJECT_ROOT}" \
      | tr '[:upper:]' '[:lower:]' \
      | sed -E 's/[^a-z0-9]+/_/g; s/^_+//; s/_+$//'
  )"
  local legacy_candidates=(
    "${project_slug}_nocturne_data"
    "${project_slug}_nocturne_memory_data"
    "nocturne_data"
    "nocturne_memory_data"
  )
  local candidate
  for candidate in "${legacy_candidates[@]}"; do
    if ! docker volume inspect "${candidate}" >/dev/null 2>&1; then
      continue
    fi

    if [[ "${candidate}" == "${project_slug}"_* ]]; then
      echo "[compat] detected project-scoped legacy docker volume '${candidate}'; reusing it for data continuity." >&2
      echo "${candidate}"
      return 0
    fi

    local owner_label
    owner_label="$(
      docker volume inspect "${candidate}" \
        --format '{{ index .Labels "com.docker.compose.project" }}' 2>/dev/null || true
    )"
    if [[ -n "${owner_label}" && "${owner_label}" == "${project_slug}" ]]; then
      echo "[compat] detected legacy docker volume '${candidate}' owned by compose project '${owner_label}'; reusing it for data continuity." >&2
      echo "${candidate}"
      return 0
    fi

    echo "[compat] found legacy-like volume '${candidate}' but skipped auto-reuse (owner label mismatch). Set MEMORY_PALACE_DATA_VOLUME explicitly if this is the expected volume." >&2
  done

  echo "memory_palace_data"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      shift
      if [[ $# -eq 0 ]]; then
        echo "Missing value for --profile" >&2
        exit 2
      fi
      profile="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
      ;;
    --frontend-port)
      shift
      if [[ $# -eq 0 ]]; then
        echo "Missing value for --frontend-port" >&2
        exit 2
      fi
      frontend_port="$1"
      ;;
    --backend-port)
      shift
      if [[ $# -eq 0 ]]; then
        echo "Missing value for --backend-port" >&2
        exit 2
      fi
      backend_port="$1"
      ;;
    --no-auto-port)
      auto_port=0
      ;;
    --no-build)
      no_build=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
  shift
done

case "${profile}" in
  a|b|c|d) ;;
  *)
    echo "Unsupported profile: ${profile}. Expected one of: a | b | c | d" >&2
    exit 2
    ;;
esac

if ! is_positive_int "${frontend_port}"; then
  echo "Invalid --frontend-port: ${frontend_port}" >&2
  exit 2
fi

if ! is_positive_int "${backend_port}"; then
  echo "Invalid --backend-port: ${backend_port}" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed or not in PATH" >&2
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  compose_cmd=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  compose_cmd=(docker-compose)
else
  echo "Neither 'docker compose' nor 'docker-compose' is available" >&2
  exit 1
fi

bash "${SCRIPT_DIR}/apply_profile.sh" docker "${profile}" "${PROJECT_ROOT}/.env.docker"

cd "${PROJECT_ROOT}"

# Force recreate to avoid stale network attachment causing frontend->backend 502.
"${compose_cmd[@]}" -f docker-compose.yml down --remove-orphans >/dev/null 2>&1 || true

if [[ ${auto_port} -eq 1 ]]; then
  if ! resolved_frontend_port="$(find_free_port "${frontend_port}")"; then
    echo "Failed to auto-resolve free frontend port near ${frontend_port}. Try --no-auto-port with explicit values." >&2
    exit 1
  fi
  if ! resolved_backend_port="$(find_free_port "${backend_port}")"; then
    echo "Failed to auto-resolve free backend port near ${backend_port}. Try --no-auto-port with explicit values." >&2
    exit 1
  fi

  if [[ "${resolved_frontend_port}" != "${frontend_port}" ]]; then
    echo "[port-adjust] frontend ${frontend_port} is occupied, switched to ${resolved_frontend_port}"
  fi
  if [[ "${resolved_backend_port}" != "${backend_port}" ]]; then
    echo "[port-adjust] backend ${backend_port} is occupied, switched to ${resolved_backend_port}"
  fi
  if [[ "${resolved_frontend_port}" == "${resolved_backend_port}" ]]; then
    next_backend_port="$((resolved_backend_port + 1))"
    resolved_backend_port="$(find_free_port "${next_backend_port}")"
    if [[ -z "${resolved_backend_port}" ]]; then
      echo "Failed to resolve distinct frontend/backend ports." >&2
      exit 1
    fi
    echo "[port-adjust] backend reassigned to avoid collision with frontend: ${resolved_backend_port}"
  fi

  frontend_port="${resolved_frontend_port}"
  backend_port="${resolved_backend_port}"
fi

data_volume="$(resolve_data_volume)"

if [[ ${no_build} -eq 1 ]]; then
  MEMORY_PALACE_FRONTEND_PORT="${frontend_port}" \
  MEMORY_PALACE_BACKEND_PORT="${backend_port}" \
  MEMORY_PALACE_DATA_VOLUME="${data_volume}" \
  NOCTURNE_FRONTEND_PORT="${frontend_port}" \
  NOCTURNE_BACKEND_PORT="${backend_port}" \
  NOCTURNE_DATA_VOLUME="${data_volume}" \
  "${compose_cmd[@]}" -f docker-compose.yml up -d --force-recreate --remove-orphans
else
  MEMORY_PALACE_FRONTEND_PORT="${frontend_port}" \
  MEMORY_PALACE_BACKEND_PORT="${backend_port}" \
  MEMORY_PALACE_DATA_VOLUME="${data_volume}" \
  NOCTURNE_FRONTEND_PORT="${frontend_port}" \
  NOCTURNE_BACKEND_PORT="${backend_port}" \
  NOCTURNE_DATA_VOLUME="${data_volume}" \
  "${compose_cmd[@]}" -f docker-compose.yml up -d --build --force-recreate --remove-orphans
fi

echo ""
echo "Memory Palace is starting with docker profile ${profile}."
echo "Frontend: http://localhost:${frontend_port}"
echo "Backend API: http://localhost:${backend_port}"
echo "Health: http://localhost:${backend_port}/health"
