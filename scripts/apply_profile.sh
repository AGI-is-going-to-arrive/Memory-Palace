#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

platform_input="${1:-macos}"
profile_input="${2:-b}"
target_file="${3:-${PROJECT_ROOT}/.env}"

platform="$(printf '%s' "${platform_input}" | tr '[:upper:]' '[:lower:]')"
profile="$(printf '%s' "${profile_input}" | tr '[:upper:]' '[:lower:]')"

case "${platform}" in
  macos|windows|docker) ;;
  *)
    echo "Unsupported platform: ${platform}. Expected one of: macos | windows | docker" >&2
    exit 2
    ;;
esac

case "${profile}" in
  a|b|c|d) ;;
  *)
    echo "Unsupported profile: ${profile}. Expected one of: a | b | c | d" >&2
    exit 2
    ;;
esac

base_env="${PROJECT_ROOT}/.env.example"
override_env="${PROJECT_ROOT}/deploy/profiles/${platform}/profile-${profile}.env"

set_env_value() {
  local file_path="$1"
  local key="$2"
  local value="$3"
  local tmp_file
  tmp_file="$(mktemp "${file_path##*/}.XXXXXX")"
  awk -v key="${key}" -v value="${value}" '
    BEGIN { replaced = 0 }
    $0 ~ ("^" key "=") {
      if (!replaced) {
        print key "=" value
        replaced = 1
      }
      next
    }
    { print }
    END {
      if (!replaced) {
        print key "=" value
      }
    }
  ' "${file_path}" > "${tmp_file}"
  mv "${tmp_file}" "${file_path}"
}

resolve_windows_db_path() {
  local db_path="${PROJECT_ROOT}/demo.db"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -am "${db_path}"
    return 0
  fi
  case "${db_path}" in
    /mnt/[a-zA-Z]/*)
      printf '%s\n' "${db_path}" | sed -E 's#^/mnt/([a-zA-Z])/#\1:/#'
      return 0
      ;;
    /[a-zA-Z]/*)
      printf '%s\n' "${db_path}" | sed -E 's#^/([a-zA-Z])/#\1:/#'
      return 0
      ;;
  esac
  printf '%s\n' 'C:/memory_palace/demo.db'
}

dedupe_env_keys() {
  local file_path="$1"
  local key value
  while IFS= read -r key; do
    [[ -n "${key}" ]] || continue
    value="$(awk -F= -v key="${key}" '$1 == key { value = substr($0, length($1) + 2) } END { print value }' "${file_path}")"
    set_env_value "${file_path}" "${key}" "${value}"
  done < <(
    awk -F= '/^[A-Z0-9_]+=/{ count[$1]++ } END { for (key in count) if (count[key] > 1) print key }' "${file_path}" | sort
  )
}

if [[ ! -f "${base_env}" ]]; then
  echo "Missing base env template: ${base_env}" >&2
  exit 1
fi

if [[ ! -f "${override_env}" ]]; then
  echo "Missing profile template: ${override_env}" >&2
  exit 1
fi

cp "${base_env}" "${target_file}"
{
  echo
  echo "# -----------------------------------------------------------------------------"
  echo "# Appended profile overrides (${platform}/profile-${profile})"
  echo "# -----------------------------------------------------------------------------"
  cat "${override_env}"
} >> "${target_file}"

if [[ "${platform}" == "macos" ]]; then
  if grep -Eq '^DATABASE_URL=sqlite\+aiosqlite:////Users/<your-user>/memory_palace/agent_memory\.db$' "${target_file}"; then
    db_path="${PROJECT_ROOT}/demo.db"
    set_env_value "${target_file}" "DATABASE_URL" "sqlite+aiosqlite:////${db_path#/}"
    echo "[auto-fill] DATABASE_URL set to ${db_path}"
  fi
elif [[ "${platform}" == "windows" ]]; then
  if grep -Eq '^DATABASE_URL=sqlite\+aiosqlite:///C:/memory_palace/agent_memory\.db$' "${target_file}"; then
    if db_path="$(resolve_windows_db_path)"; then
      set_env_value "${target_file}" "DATABASE_URL" "sqlite+aiosqlite:///${db_path}"
      echo "[auto-fill] DATABASE_URL set to ${db_path}"
    fi
  fi
fi

dedupe_env_keys "${target_file}"

echo "Generated ${target_file} from ${override_env}"
