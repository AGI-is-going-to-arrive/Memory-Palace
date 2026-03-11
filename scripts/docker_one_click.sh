#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

profile="b"
no_build=0
frontend_port="${MEMORY_PALACE_FRONTEND_PORT:-${NOCTURNE_FRONTEND_PORT:-3000}}"
backend_port="${MEMORY_PALACE_BACKEND_PORT:-${NOCTURNE_BACKEND_PORT:-18000}}"
auto_port=1
allow_runtime_env_injection=0
port_probe_fallback_warned=0
FRONTEND_PORT_LOCK=""
BACKEND_PORT_LOCK=""
DEPLOYMENT_LOCK=""
TEMP_DOCKER_ENV_FILE=""
TEMP_DOCKER_ENV_FILE_AUTO=0
compose_project_name=""

default_compose_project_name() {
  local project_slug path_checksum
  project_slug="$(
    basename "${PROJECT_ROOT}" \
      | tr '[:upper:]' '[:lower:]' \
      | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//'
  )"
  if [[ -z "${project_slug}" ]]; then
    project_slug="memory-palace"
  fi
  path_checksum="$(
    printf '%s' "${PROJECT_ROOT}" | cksum | awk '{print $1}'
  )"
  echo "${project_slug}-${path_checksum}"
}

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/docker_one_click.sh [--profile a|b|c|d] [--frontend-port <port>] [--backend-port <port>] [--no-auto-port] [--no-build] [--allow-runtime-env-injection]
USAGE
}

is_positive_int() {
  [[ "$1" =~ ^[0-9]+$ ]] && [[ "$1" -ge 1 ]] && [[ "$1" -le 65535 ]]
}

try_acquire_path_lock() {
  local target_path="$1"
  local lock_dir="${target_path}.lockdir"
  local owner_file="${lock_dir}/owner_pid"
  local owner_pid=""

  mkdir -p "$(dirname "${target_path}")" >/dev/null 2>&1 || true

  if mkdir "${lock_dir}" 2>/dev/null; then
    printf '%s\n' "${BASHPID:-$$}" > "${owner_file}" 2>/dev/null || true
    echo "${lock_dir}"
    return 0
  fi

  if [[ -f "${owner_file}" ]]; then
    owner_pid="$(cat "${owner_file}" 2>/dev/null || true)"
  fi
  if [[ -n "${owner_pid}" ]] && ! kill -0 "${owner_pid}" 2>/dev/null; then
    rm -rf "${lock_dir}" >/dev/null 2>&1 || true
    if mkdir "${lock_dir}" 2>/dev/null; then
      printf '%s\n' "${BASHPID:-$$}" > "${owner_file}" 2>/dev/null || true
      echo "${lock_dir}"
      return 0
    fi
  fi

  return 1
}

release_path_lock() {
  local lock_dir="$1"
  if [[ -n "${lock_dir}" ]]; then
    rm -rf "${lock_dir}" >/dev/null 2>&1 || true
  fi
}

port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  if command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "${port}" >/dev/null 2>&1
    return $?
  fi
  if [[ "${port_probe_fallback_warned}" -eq 0 ]]; then
    echo "[port-probe] neither lsof nor nc is available; fail-closed port probing is enabled." >&2
    port_probe_fallback_warned=1
  fi
  return 0
}

find_free_port() {
  local start_port="$1"
  local max_scan="${2:-200}"
  local exclude_port="${3:-}"
  local current="$start_port"
  local i
  local lock_dir=""

  for ((i = 0; i <= max_scan; i++)); do
    if [[ "${current}" -gt 65535 ]]; then
      break
    fi
    if [[ -n "${exclude_port}" && "${current}" -eq "${exclude_port}" ]]; then
      current=$((current + 1))
      continue
    fi
    if ! port_in_use "${current}"; then
      lock_dir="$(try_acquire_path_lock "${TMPDIR:-/tmp}/memory-palace-port-locks/port-${current}" || true)"
      if [[ -n "${lock_dir}" ]]; then
        if ! port_in_use "${current}"; then
          printf '%s %s\n' "${current}" "${lock_dir}"
          return 0
        fi
        release_path_lock "${lock_dir}"
      fi
    fi
    current=$((current + 1))
  done

  echo "" >&2
  return 1
}

wait_for_deployment_ready() {
  local attempts="${1:-30}"
  local sleep_seconds="${2:-2}"
  local probe_frontend_port="${3:-${frontend_port}}"
  local probe_backend_port="${4:-${backend_port}}"
  local backend_url="http://127.0.0.1:${probe_backend_port}/health"
  local frontend_url="http://127.0.0.1:${probe_frontend_port}/"
  local sse_url="http://127.0.0.1:${probe_frontend_port}/sse"
  local sse_status=""
  local attempt=0

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl -fsS "${backend_url}" >/dev/null 2>&1 && \
       curl -fsS "${frontend_url}" >/dev/null 2>&1; then
      sse_status="$(curl -sS -o /dev/null -w '%{http_code}' "${sse_url}" || true)"
      if [[ "${sse_status}" == "200" || "${sse_status}" == "401" ]]; then
        return 0
      fi
    fi
    sleep "${sleep_seconds}"
  done

  return 1
}

compose_project_has_any_container() {
  local service="$1"
  docker ps -a \
    --filter "label=com.docker.compose.project=${compose_project_name}" \
    --filter "label=com.docker.compose.service=${service}" \
    --format '{{.Names}}' \
    | head -n 1 \
    | grep -q .
}

resolve_published_port_from_compose() {
  local service="$1"
  local target_port="$2"
  local fallback_port="$3"
  local attempts="${4:-10}"
  local attempt=1
  local container_name=""
  local port_output=""
  local ports_output=""
  local ports_regex=""

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    ports_output="$(
      docker ps \
        --filter "label=com.docker.compose.project=${compose_project_name}" \
        --filter "label=com.docker.compose.service=${service}" \
        --format '{{.Ports}}' \
        | head -n 1
    )"
    ports_output="${ports_output//$'\r'/}"
    ports_regex=":([0-9]+)->${target_port}/tcp"
    if [[ "${ports_output}" =~ ${ports_regex} ]]; then
      echo "${BASH_REMATCH[1]}"
      return 0
    fi

    container_name="$(
      docker ps \
        --filter "label=com.docker.compose.project=${compose_project_name}" \
        --filter "label=com.docker.compose.service=${service}" \
        --format '{{.Names}}' \
        | head -n 1
    )"
    if [[ -n "${container_name}" ]]; then
      port_output="$(docker port "${container_name}" "${target_port}" 2>/dev/null | head -n 1 || true)"
      port_output="${port_output//$'\r'/}"
      if [[ "${port_output}" =~ :([0-9]+)$ ]]; then
        echo "${BASH_REMATCH[1]}"
        return 0
      fi
    fi

    if port_output="$(
      COMPOSE_PROJECT_NAME="${compose_project_name}" \
        "${compose_cmd[@]}" "${compose_env_file_args[@]}" -f docker-compose.yml port "${service}" "${target_port}" 2>/dev/null \
        | head -n 1
    )"; then
      port_output="${port_output//$'\r'/}"
      if [[ "${port_output}" =~ :([0-9]+)$ ]]; then
        echo "${BASH_REMATCH[1]}"
        return 0
      fi
    fi

    if [[ "${attempt}" -lt "${attempts}" ]]; then
      sleep 1
    fi
  done

  echo "${fallback_port}"
}

cleanup_runtime_state() {
  release_path_lock "${FRONTEND_PORT_LOCK}"
  release_path_lock "${BACKEND_PORT_LOCK}"
  release_path_lock "${DEPLOYMENT_LOCK}"
  if [[ "${TEMP_DOCKER_ENV_FILE_AUTO}" -eq 1 && -n "${TEMP_DOCKER_ENV_FILE}" ]]; then
    rm -f "${TEMP_DOCKER_ENV_FILE}" >/dev/null 2>&1 || true
  fi
}

trap cleanup_runtime_state EXIT

resolve_data_volume() {
  local explicit_volume="${MEMORY_PALACE_DATA_VOLUME:-${NOCTURNE_DATA_VOLUME:-}}"
  if [[ -n "${explicit_volume}" ]]; then
    echo "${explicit_volume}"
    return 0
  fi

  local project_name="${compose_project_name:-${COMPOSE_PROJECT_NAME:-$(default_compose_project_name)}}"
  local default_volume="${project_name}_data"
  if docker volume inspect "${default_volume}" >/dev/null 2>&1; then
    echo "${default_volume}"
    return 0
  fi

  local legacy_candidates=(
    "memory_palace_data"
    "nocturne_data"
    "nocturne_memory_data"
  )
  local candidate
  for candidate in "${legacy_candidates[@]}"; do
    if ! docker volume inspect "${candidate}" >/dev/null 2>&1; then
      continue
    fi

    echo "[compat] found legacy shared docker volume '${candidate}', but defaulting to isolated volume '${default_volume}'. Set MEMORY_PALACE_DATA_VOLUME=${candidate} to reuse old data intentionally." >&2
    break
  done

  echo "${default_volume}"
}

resolve_snapshots_volume() {
  local explicit_volume="${MEMORY_PALACE_SNAPSHOTS_VOLUME:-${NOCTURNE_SNAPSHOTS_VOLUME:-}}"
  if [[ -n "${explicit_volume}" ]]; then
    echo "${explicit_volume}"
    return 0
  fi

  local project_name="${compose_project_name:-${COMPOSE_PROJECT_NAME:-$(default_compose_project_name)}}"
  local default_volume="${project_name}_snapshots"
  if docker volume inspect "${default_volume}" >/dev/null 2>&1; then
    echo "${default_volume}"
    return 0
  fi

  local legacy_candidates=(
    "memory_palace_snapshots"
    "nocturne_snapshots"
  )
  local candidate
  for candidate in "${legacy_candidates[@]}"; do
    if ! docker volume inspect "${candidate}" >/dev/null 2>&1; then
      continue
    fi

    echo "[compat] found legacy shared docker volume '${candidate}', but defaulting to isolated volume '${default_volume}'. Set MEMORY_PALACE_SNAPSHOTS_VOLUME=${candidate} to reuse old snapshots intentionally." >&2
    break
  done

  echo "${default_volume}"
}

get_env_value_from_file() {
  local env_file="$1"
  local key="$2"
  local raw
  raw="$(awk -F= -v target="${key}" '$1==target {print substr($0, index($0, "=") + 1)}' "${env_file}" | tail -n 1)"
  echo "${raw%$'\r'}"
}

upsert_env_value_in_file() {
  local env_file="$1"
  local key="$2"
  local value="$3"
  local tmp_file
  tmp_file="$(mktemp "/tmp/mp-env-upsert-XXXXXX")"
  awk -v target="${key}" -v replacement="${value}" '
    BEGIN { updated=0 }
    $0 ~ ("^" target "=") {
      if (updated == 0) {
        print target "=" replacement
        updated=1
      }
      next
    }
    { print }
    END {
      if (updated == 0) {
        print target "=" replacement
      }
    }
  ' "${env_file}" > "${tmp_file}"
  mv "${tmp_file}" "${env_file}"
}

apply_profile_runtime_overrides() {
  local env_file="$1"
  local selected_profile="${2:-}"
  local override_keys=(
    "ROUTER_API_BASE"
    "ROUTER_API_KEY"
    "ROUTER_EMBEDDING_MODEL"
    "RETRIEVAL_EMBEDDING_BACKEND"
    "RETRIEVAL_EMBEDDING_API_BASE"
    "RETRIEVAL_EMBEDDING_API_KEY"
    "RETRIEVAL_EMBEDDING_MODEL"
    "RETRIEVAL_RERANKER_API_BASE"
    "RETRIEVAL_RERANKER_API_KEY"
    "RETRIEVAL_RERANKER_MODEL"
    "WRITE_GUARD_LLM_ENABLED"
    "WRITE_GUARD_LLM_API_BASE"
    "WRITE_GUARD_LLM_API_KEY"
    "WRITE_GUARD_LLM_MODEL"
    "COMPACT_GIST_LLM_ENABLED"
    "COMPACT_GIST_LLM_API_BASE"
    "COMPACT_GIST_LLM_API_KEY"
    "COMPACT_GIST_LLM_MODEL"
    "INTENT_LLM_ENABLED"
    "INTENT_LLM_API_BASE"
    "INTENT_LLM_API_KEY"
    "INTENT_LLM_MODEL"
    "MCP_API_KEY"
    "MCP_API_KEY_ALLOW_INSECURE_LOCAL"
  )
  local key
  for key in "${override_keys[@]}"; do
    local override_value="${!key:-}"
    if [[ -n "${override_value}" ]]; then
      upsert_env_value_in_file "${env_file}" "${key}" "${override_value}"
      echo "[override] ${key} applied to ${env_file}"
    fi
  done

  if [[ "${selected_profile}" == "c" || "${selected_profile}" == "d" ]]; then
    upsert_env_value_in_file "${env_file}" "RETRIEVAL_EMBEDDING_BACKEND" "api"
    echo "[override] RETRIEVAL_EMBEDDING_BACKEND=api forced for local profile ${selected_profile} runtime injection."

    if [[ -z "${RETRIEVAL_EMBEDDING_API_BASE:-}" && -n "${ROUTER_API_BASE:-}" ]]; then
      upsert_env_value_in_file "${env_file}" "RETRIEVAL_EMBEDDING_API_BASE" "${ROUTER_API_BASE}"
      echo "[override] RETRIEVAL_EMBEDDING_API_BASE copied from ROUTER_API_BASE for local profile ${selected_profile} runtime injection."
    fi
    if [[ -z "${RETRIEVAL_EMBEDDING_API_KEY:-}" && -n "${ROUTER_API_KEY:-}" ]]; then
      upsert_env_value_in_file "${env_file}" "RETRIEVAL_EMBEDDING_API_KEY" "${ROUTER_API_KEY}"
      echo "[override] RETRIEVAL_EMBEDDING_API_KEY copied from ROUTER_API_KEY for local profile ${selected_profile} runtime injection."
    fi
    if [[ -z "${RETRIEVAL_EMBEDDING_MODEL:-}" && -n "${ROUTER_EMBEDDING_MODEL:-}" ]]; then
      upsert_env_value_in_file "${env_file}" "RETRIEVAL_EMBEDDING_MODEL" "${ROUTER_EMBEDDING_MODEL}"
      echo "[override] RETRIEVAL_EMBEDDING_MODEL copied from ROUTER_EMBEDDING_MODEL for local profile ${selected_profile} runtime injection."
    fi
    if [[ -z "${RETRIEVAL_RERANKER_API_BASE:-}" && -n "${ROUTER_API_BASE:-}" ]]; then
      upsert_env_value_in_file "${env_file}" "RETRIEVAL_RERANKER_API_BASE" "${ROUTER_API_BASE}"
      echo "[override] RETRIEVAL_RERANKER_API_BASE copied from ROUTER_API_BASE for local profile ${selected_profile} runtime injection."
    fi
    if [[ -z "${RETRIEVAL_RERANKER_API_KEY:-}" && -n "${ROUTER_API_KEY:-}" ]]; then
      upsert_env_value_in_file "${env_file}" "RETRIEVAL_RERANKER_API_KEY" "${ROUTER_API_KEY}"
      echo "[override] RETRIEVAL_RERANKER_API_KEY copied from ROUTER_API_KEY for local profile ${selected_profile} runtime injection."
    fi
  fi
}

is_truthy() {
  case "${1:-}" in
    1|true|yes|on|enabled|TRUE|YES|ON|ENABLED)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

value_has_unresolved_placeholder() {
  local value="$1"
  [[ "${value}" == *"replace-with-your-key"* ]] \
    || [[ "${value}" == *"<your-router-host>"* ]] \
    || [[ "${value}" == *"host.docker.internal:PORT"* ]] \
    || [[ "${value}" =~ :PORT(/|$) ]]
}

assert_profile_external_settings_ready() {
  local env_file="$1"
  local selected_profile="$2"
  local found=0

  if [[ "${selected_profile}" != "c" && "${selected_profile}" != "d" ]]; then
    return 0
  fi

  local embedding_backend
  embedding_backend="$(get_env_value_from_file "${env_file}" "RETRIEVAL_EMBEDDING_BACKEND" | tr '[:upper:]' '[:lower:]')"
  local reranker_enabled_raw
  reranker_enabled_raw="$(get_env_value_from_file "${env_file}" "RETRIEVAL_RERANKER_ENABLED" | tr '[:upper:]' '[:lower:]')"

  local required_keys=()
  case "${embedding_backend}" in
    router)
      required_keys+=("ROUTER_API_BASE" "ROUTER_API_KEY")
      ;;
    api|openai)
      required_keys+=("RETRIEVAL_EMBEDDING_API_BASE" "RETRIEVAL_EMBEDDING_API_KEY")
      ;;
    hash|none|"")
      ;;
    *)
      required_keys+=("RETRIEVAL_EMBEDDING_API_BASE" "RETRIEVAL_EMBEDDING_API_KEY")
      ;;
  esac
  if is_truthy "${reranker_enabled_raw}"; then
    required_keys+=("RETRIEVAL_RERANKER_API_BASE" "RETRIEVAL_RERANKER_API_KEY")
  fi

  local key
  for key in "${required_keys[@]}"; do
    local value
    value="$(get_env_value_from_file "${env_file}" "${key}")"
    if [[ -z "${value}" ]]; then
      echo "[profile-check] Missing required value for ${key} (${selected_profile})" >&2
      found=1
      continue
    fi
    if value_has_unresolved_placeholder "${value}"; then
      echo "[profile-check] Unresolved placeholder for ${key}: ${value}" >&2
      found=1
    fi
  done

  if [[ "${found}" -eq 1 ]]; then
    echo "[profile-check] Profile ${selected_profile} has unresolved external settings in ${env_file}." >&2
    return 1
  fi
  return 0
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
    --allow-runtime-env-injection)
      allow_runtime_env_injection=1
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

DEPLOYMENT_LOCK="$(
  try_acquire_path_lock "${TMPDIR:-/tmp}/memory-palace-deploy-locks/$(default_compose_project_name)" || true
)"
if [[ -z "${DEPLOYMENT_LOCK}" ]]; then
  echo "[deploy-lock] another docker_one_click deployment is already running for this checkout; wait for it to finish before retrying." >&2
  exit 1
fi

env_file="${MEMORY_PALACE_DOCKER_ENV_FILE:-}"
if [[ -z "${env_file}" ]]; then
  env_file="$(mktemp "${TMPDIR:-/tmp}/memory-palace-docker-env-${profile}-XXXXXX")"
  TEMP_DOCKER_ENV_FILE="${env_file}"
  TEMP_DOCKER_ENV_FILE_AUTO=1
fi
chmod 600 "${env_file}" >/dev/null 2>&1 || true
export MEMORY_PALACE_DOCKER_ENV_FILE="${env_file}"
echo "[env-file] using ${env_file}"
bash "${SCRIPT_DIR}/apply_profile.sh" docker "${profile}" "${env_file}"
if [[ "${allow_runtime_env_injection}" -eq 1 ]]; then
  apply_profile_runtime_overrides "${env_file}" "${profile}"
else
  echo "[override] runtime env injection disabled by default; pass --allow-runtime-env-injection to opt in."
fi
assert_profile_external_settings_ready "${env_file}" "${profile}"

cd "${PROJECT_ROOT}"

if [[ ${auto_port} -eq 1 ]]; then
  if ! frontend_reservation="$(find_free_port "${frontend_port}")"; then
    echo "Failed to auto-resolve free frontend port near ${frontend_port}. Try --no-auto-port with explicit values." >&2
    exit 1
  fi
  read -r resolved_frontend_port FRONTEND_PORT_LOCK <<< "${frontend_reservation}"
  if ! backend_reservation="$(find_free_port "${backend_port}" 200 "${resolved_frontend_port}")"; then
    echo "Failed to auto-resolve free backend port near ${backend_port}. Try --no-auto-port with explicit values." >&2
    exit 1
  fi
  read -r resolved_backend_port BACKEND_PORT_LOCK <<< "${backend_reservation}"

  if [[ "${resolved_frontend_port}" != "${frontend_port}" ]]; then
    echo "[port-adjust] frontend ${frontend_port} is occupied, switched to ${resolved_frontend_port}"
  fi
  if [[ "${resolved_backend_port}" != "${backend_port}" ]]; then
    echo "[port-adjust] backend ${backend_port} is occupied, switched to ${resolved_backend_port}"
  fi

  frontend_port="${resolved_frontend_port}"
  backend_port="${resolved_backend_port}"
fi

data_volume="$(resolve_data_volume)"
snapshots_volume="$(resolve_snapshots_volume)"
upsert_env_value_in_file "${env_file}" "MEMORY_PALACE_FRONTEND_PORT" "${frontend_port}"
upsert_env_value_in_file "${env_file}" "MEMORY_PALACE_BACKEND_PORT" "${backend_port}"
upsert_env_value_in_file "${env_file}" "MEMORY_PALACE_DATA_VOLUME" "${data_volume}"
upsert_env_value_in_file "${env_file}" "MEMORY_PALACE_SNAPSHOTS_VOLUME" "${snapshots_volume}"
upsert_env_value_in_file "${env_file}" "NOCTURNE_FRONTEND_PORT" "${frontend_port}"
upsert_env_value_in_file "${env_file}" "NOCTURNE_BACKEND_PORT" "${backend_port}"
upsert_env_value_in_file "${env_file}" "NOCTURNE_DATA_VOLUME" "${data_volume}"
upsert_env_value_in_file "${env_file}" "NOCTURNE_SNAPSHOTS_VOLUME" "${snapshots_volume}"
planned_frontend_port="$(get_env_value_from_file "${env_file}" "MEMORY_PALACE_FRONTEND_PORT")"
planned_backend_port="$(get_env_value_from_file "${env_file}" "MEMORY_PALACE_BACKEND_PORT")"
if [[ -z "${planned_frontend_port}" ]]; then
  planned_frontend_port="${frontend_port}"
fi
if [[ -z "${planned_backend_port}" ]]; then
  planned_backend_port="${backend_port}"
fi
compose_project_name="${COMPOSE_PROJECT_NAME:-$(default_compose_project_name)}"
compose_env_file_args=()
if [[ -n "${env_file}" ]]; then
  compose_env_file_args=(--env-file "${env_file}")
fi

# Force recreate to avoid stale network attachment causing frontend->backend 502.
if ! COMPOSE_PROJECT_NAME="${compose_project_name}" "${compose_cmd[@]}" "${compose_env_file_args[@]}" -f docker-compose.yml down --remove-orphans >/dev/null 2>&1; then
  echo "[compose-down] pre-cleanup failed; aborting to match fail-closed deployment behavior." >&2
  exit 1
fi

if [[ ${no_build} -eq 1 ]]; then
  if ! COMPOSE_PROJECT_NAME="${compose_project_name}" \
    MEMORY_PALACE_FRONTEND_PORT="${frontend_port}" \
    MEMORY_PALACE_BACKEND_PORT="${backend_port}" \
    MEMORY_PALACE_DATA_VOLUME="${data_volume}" \
    MEMORY_PALACE_SNAPSHOTS_VOLUME="${snapshots_volume}" \
    NOCTURNE_FRONTEND_PORT="${frontend_port}" \
    NOCTURNE_BACKEND_PORT="${backend_port}" \
    NOCTURNE_DATA_VOLUME="${data_volume}" \
    NOCTURNE_SNAPSHOTS_VOLUME="${snapshots_volume}" \
    "${compose_cmd[@]}" "${compose_env_file_args[@]}" -f docker-compose.yml up -d --wait --wait-timeout 120 --force-recreate --remove-orphans; then
    if ! compose_project_has_any_container backend \
      && ! compose_project_has_any_container sse \
      && ! compose_project_has_any_container frontend; then
      echo "[compose-up] docker compose failed before creating any service container; skipping readiness probe." >&2
      exit 1
    fi
    echo "[compose-up] docker compose returned non-zero; probing backend/frontend/sse readiness..." >&2
    probe_frontend_port="$(resolve_published_port_from_compose frontend 8080 "${planned_frontend_port}")"
    probe_backend_port="$(resolve_published_port_from_compose backend 8000 "${planned_backend_port}")"
    if ! wait_for_deployment_ready 30 2 "${probe_frontend_port}" "${probe_backend_port}"; then
      exit 1
    fi
    echo "[compose-up] services became ready after compose reported failure; continuing." >&2
  fi
else
  if ! COMPOSE_PROJECT_NAME="${compose_project_name}" \
    MEMORY_PALACE_FRONTEND_PORT="${frontend_port}" \
    MEMORY_PALACE_BACKEND_PORT="${backend_port}" \
    MEMORY_PALACE_DATA_VOLUME="${data_volume}" \
    MEMORY_PALACE_SNAPSHOTS_VOLUME="${snapshots_volume}" \
    NOCTURNE_FRONTEND_PORT="${frontend_port}" \
    NOCTURNE_BACKEND_PORT="${backend_port}" \
    NOCTURNE_DATA_VOLUME="${data_volume}" \
    NOCTURNE_SNAPSHOTS_VOLUME="${snapshots_volume}" \
    "${compose_cmd[@]}" "${compose_env_file_args[@]}" -f docker-compose.yml up -d --build --wait --wait-timeout 120 --force-recreate --remove-orphans; then
    if ! compose_project_has_any_container backend \
      && ! compose_project_has_any_container sse \
      && ! compose_project_has_any_container frontend; then
      echo "[compose-up] docker compose failed before creating any service container; skipping readiness probe." >&2
      exit 1
    fi
    echo "[compose-up] docker compose returned non-zero; probing backend/frontend/sse readiness..." >&2
    probe_frontend_port="$(resolve_published_port_from_compose frontend 8080 "${planned_frontend_port}")"
    probe_backend_port="$(resolve_published_port_from_compose backend 8000 "${planned_backend_port}")"
    if ! wait_for_deployment_ready 30 2 "${probe_frontend_port}" "${probe_backend_port}"; then
      exit 1
    fi
    echo "[compose-up] services became ready after compose reported failure; continuing." >&2
  fi
fi

reported_frontend_port="$(resolve_published_port_from_compose frontend 8080 "${planned_frontend_port}")"
reported_backend_port="$(resolve_published_port_from_compose backend 8000 "${planned_backend_port}")"

echo ""
echo "Memory Palace is starting with docker profile ${profile}."
echo "Frontend: http://localhost:${reported_frontend_port}"
echo "Backend API: http://localhost:${reported_backend_port}"
echo "SSE Endpoint: http://localhost:${reported_frontend_port}/sse"
echo "Health: http://localhost:${reported_backend_port}/health"
echo "Compose project: ${compose_project_name}"
