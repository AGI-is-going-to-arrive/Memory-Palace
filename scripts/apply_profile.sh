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
    echo "DATABASE_URL=sqlite+aiosqlite:////${db_path#/}" >> "${target_file}"
    echo "[auto-fill] DATABASE_URL set to ${db_path}"
  fi
fi

echo "Generated ${target_file} from ${override_env}"
