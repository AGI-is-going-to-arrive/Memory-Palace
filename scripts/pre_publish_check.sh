#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

EXIT_CODE=0
WARNINGS=0

print_section() {
  printf "\n[%s]\n" "$1"
}

fail() {
  echo "FAIL: $*"
  EXIT_CODE=1
}

warn() {
  echo "WARN: $*"
  WARNINGS=$((WARNINGS + 1))
}

pass() {
  echo "PASS: $*"
}

check_local_artifacts() {
  local -a paths=(
    ".env"
    ".env.docker"
    ".venv"
    ".claude"
    "demo.db"
    "snapshots"
    "backend/backend.log"
    "frontend/frontend.log"
    "backend/.pytest_cache"
    "backend/tests/benchmark/.real_profile_cache"
    "frontend/node_modules"
    "frontend/dist"
  )

  local found_any=0
  local path
  for path in "${paths[@]}"; do
    if [[ -e "${path}" ]]; then
      warn "本地文件存在（上传前建议移除或确认未纳入提交）: ${path}"
      found_any=1
    fi
  done

  if [[ "${found_any}" -eq 0 ]]; then
    pass "未发现高风险本地产物目录"
  fi
}

check_tracked_forbidden_paths() {
  local -a paths=(
    ".env"
    ".env.docker"
    ".venv"
    ".claude"
    "demo.db"
    "snapshots"
    "backend/backend.log"
    "frontend/frontend.log"
    "backend/.pytest_cache"
    "backend/tests/benchmark/.real_profile_cache"
    "frontend/node_modules"
    "frontend/dist"
  )

  local hit=0
  local path tracked
  for path in "${paths[@]}"; do
    tracked="$(git ls-files -- "${path}" || true)"
    if [[ -n "${tracked}" ]]; then
      fail "以下敏感/本地产物已被跟踪，请先移出版本库: ${path}"
      hit=1
    fi
  done

  if [[ "${hit}" -eq 0 ]]; then
    pass "敏感本地产物未被跟踪"
  fi
}

collect_existing_tracked_files() {
  local file
  while IFS= read -r -d '' file; do
    if [[ -f "${file}" ]]; then
      printf '%s\0' "${file}"
    fi
  done < <(git ls-files -z)
}

scan_tracked_files() {
  local label="$1"
  local regex="$2"

  local -a hits=()
  while IFS= read -r line; do
    [[ -n "${line}" ]] && hits+=("${line}")
  done < <(
    collect_existing_tracked_files \
      | xargs -0 rg -l -n --no-messages "${regex}" 2>/dev/null \
      | sort -u || true
  )

  if [[ "${#hits[@]}" -gt 0 ]]; then
    fail "${label} 命中以下文件："
    printf '  - %s\n' "${hits[@]}"
  else
    pass "${label} 未命中"
  fi
}

check_env_example_api_keys() {
  if [[ ! -f ".env.example" ]]; then
    fail "缺少 .env.example"
    return
  fi

  local -a hits=()
  while IFS= read -r line; do
    [[ -n "${line}" ]] && hits+=("${line}")
  done < <(rg -n '^[A-Z0-9_]*API_KEY=.+$' .env.example || true)

  if [[ "${#hits[@]}" -gt 0 ]]; then
    fail ".env.example 中发现非空 API_KEY，请改为空值占位"
    printf '  - %s\n' "${hits[@]}"
  else
    pass ".env.example 的 API_KEY 均为空占位"
  fi
}

print_section "1) 本地敏感产物检查"
check_local_artifacts

print_section "2) Git 跟踪状态检查"
check_tracked_forbidden_paths

print_section "3) 密钥模式扫描（仅扫描已跟踪文件）"
scan_tracked_files \
  "密钥/凭证模式" \
  'BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{35}|-----BEGIN PGP PRIVATE KEY BLOCK-----'

CURRENT_USER="$(id -un 2>/dev/null || true)"
if [[ -n "${CURRENT_USER}" ]]; then
  print_section "4) 个人路径泄露扫描（仅扫描已跟踪文件）"
  scan_tracked_files \
    "个人绝对路径（${CURRENT_USER}）" \
    "/Users/${CURRENT_USER}|C:\\\\Users\\\\${CURRENT_USER}|file:///Users/${CURRENT_USER}"
fi

print_section "5) .env.example 占位检查"
check_env_example_api_keys

echo
if [[ "${EXIT_CODE}" -ne 0 ]]; then
  echo "RESULT: FAIL"
  echo "建议先执行：git status --short，并清理上面列出的命中项后再上传。"
  exit "${EXIT_CODE}"
fi

echo "RESULT: PASS"
if [[ "${WARNINGS}" -gt 0 ]]; then
  echo "注意：存在 ${WARNINGS} 个警告项（通常是本地文件存在但未被跟踪）。"
fi
echo "可安全继续执行上传前流程。"
