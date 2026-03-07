import hashlib
import json
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Optional, Sequence

from filelock import FileLock, Timeout


_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
_RATE_LIMIT_STATE_LOCK_TIMEOUT_SECONDS = 1.0


def _normalize_extension(extension: str) -> str:
    value = str(extension or "").strip().lower()
    if not value:
        return ""
    if not value.startswith("."):
        value = f".{value}"
    return value


def _normalize_allowed_extensions(extensions: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for extension in extensions:
        value = _normalize_extension(extension)
        if value and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def _normalize_allowed_roots(roots: Sequence[str | Path]) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for root in roots:
        raw = str(root or "").strip()
        if not raw:
            continue
        try:
            resolved = Path(raw).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved not in normalized:
            normalized.append(resolved)
    return tuple(normalized)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in _TRUTHY_ENV_VALUES


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return max(minimum, default)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_csv(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    values: list[str] = []
    for part in str(raw or "").split(","):
        value = part.strip()
        if value:
            values.append(value)
    return values


@dataclass(frozen=True)
class ExternalImportGuardConfig:
    enabled: bool = False
    allowed_roots: tuple[Path, ...] = ()
    allowed_exts: tuple[str, ...] = (".md", ".txt", ".json")
    max_total_bytes: int = 5 * 1024 * 1024
    max_files: int = 200
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 10
    rate_limit_state_file: Optional[Path] = None
    require_shared_rate_limit: bool = False

    @classmethod
    def from_env(cls) -> "ExternalImportGuardConfig":
        return cls(
            enabled=_env_bool("EXTERNAL_IMPORT_ENABLED", False),
            allowed_roots=_normalize_allowed_roots(
                _env_csv("EXTERNAL_IMPORT_ALLOWED_ROOTS")
            ),
            allowed_exts=_normalize_allowed_extensions(
                _env_csv("EXTERNAL_IMPORT_ALLOWED_EXTS", ".md,.txt,.json")
            ),
            max_total_bytes=_env_int(
                "EXTERNAL_IMPORT_MAX_TOTAL_BYTES", 5 * 1024 * 1024, minimum=1
            ),
            max_files=_env_int("EXTERNAL_IMPORT_MAX_FILES", 200, minimum=1),
            rate_limit_window_seconds=_env_int(
                "EXTERNAL_IMPORT_RATE_LIMIT_WINDOW_SECONDS", 60, minimum=1
            ),
            rate_limit_max_requests=_env_int(
                "EXTERNAL_IMPORT_RATE_LIMIT_MAX_REQUESTS", 10, minimum=1
            ),
            rate_limit_state_file=(
                Path(state_file).expanduser().resolve(strict=False)
                if (state_file := str(os.getenv("EXTERNAL_IMPORT_RATE_LIMIT_STATE_FILE") or "").strip())
                else None
            ),
            require_shared_rate_limit=_env_bool(
                "EXTERNAL_IMPORT_REQUIRE_SHARED_RATE_LIMIT", False
            ),
        )


class ExternalImportGuard:
    def __init__(
        self,
        config: ExternalImportGuardConfig | None = None,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config or ExternalImportGuardConfig.from_env()
        self._clock = clock or time.time
        self._rate_limit_buckets: Dict[str, Deque[float]] = {}
        self._rate_limit_guard = threading.Lock()

    def policy_snapshot(self) -> Dict[str, Any]:
        roots = sorted(str(item) for item in self._config.allowed_roots)
        roots_payload = "|".join(roots)
        roots_fingerprint = (
            hashlib.sha256(roots_payload.encode("utf-8", errors="ignore")).hexdigest()
            if roots_payload
            else ""
        )
        return {
            "enabled": bool(self._config.enabled),
            "allowed_roots_count": len(roots),
            "allowed_roots_fingerprint": roots_fingerprint,
            "allowed_exts": list(self._config.allowed_exts),
            "max_total_bytes": int(self._config.max_total_bytes),
            "max_files": int(self._config.max_files),
            "rate_limit_window_seconds": int(self._config.rate_limit_window_seconds),
            "rate_limit_max_requests": int(self._config.rate_limit_max_requests),
            "rate_limit_storage": (
                "state_file"
                if self._config.rate_limit_state_file is not None
                else "process_memory"
            ),
            "require_shared_rate_limit": bool(
                self._config.require_shared_rate_limit
            ),
        }

    def validate_batch(
        self,
        *,
        file_paths: Sequence[str | Path],
        actor_id: str,
        session_id: str | None = None,
    ) -> Dict[str, Any]:
        requested = [str(path) for path in file_paths]
        actor = str(actor_id or "").strip()
        session = str(session_id or "").strip() or None
        result: Dict[str, Any] = {
            "ok": False,
            "reason": "rejected",
            "actor_id": actor,
            "session_id": session,
            "allowed_files": [],
            "rejected_files": [],
            "requested_file_count": len(requested),
            "file_count": 0,
            "max_files": int(self._config.max_files),
            "total_bytes": 0,
            "max_total_bytes": int(self._config.max_total_bytes),
            "retry_after_seconds": 0,
            "rate_limit_storage": (
                "state_file"
                if self._config.rate_limit_state_file is not None
                else "process_memory"
            ),
            "require_shared_rate_limit": bool(
                self._config.require_shared_rate_limit
            ),
            "policy": self.policy_snapshot(),
        }

        if not self._config.enabled:
            result["reason"] = "external_import_disabled"
            return result
        if not self._config.allowed_roots:
            result["reason"] = "allowed_roots_not_configured"
            return result
        if not self._config.allowed_exts:
            result["reason"] = "allowed_exts_not_configured"
            return result
        if not actor:
            result["reason"] = "actor_id_required"
            return result
        if (
            self._config.require_shared_rate_limit
            and self._config.rate_limit_state_file is None
        ):
            result["reason"] = "rate_limit_shared_state_required"
            result["config_errors"] = [
                (
                    "EXTERNAL_IMPORT_RATE_LIMIT_STATE_FILE is required when "
                    "EXTERNAL_IMPORT_REQUIRE_SHARED_RATE_LIMIT=true"
                )
            ]
            return result

        rate_limit_state = self._check_and_record_rate_limit(
            actor_id=actor,
            session_id=session,
        )
        result["rate_limit"] = rate_limit_state
        if not rate_limit_state.get("allowed", False):
            result["reason"] = str(rate_limit_state.get("reason") or "rate_limited")
            result["retry_after_seconds"] = int(
                rate_limit_state.get("retry_after_seconds") or 0
            )
            state_error = str(rate_limit_state.get("state_error") or "").strip()
            if state_error:
                result["rate_limit_state_error"] = state_error
            return result

        if not requested:
            result["reason"] = "no_files_provided"
            return result

        if len(requested) > self._config.max_files:
            result["reason"] = "max_files_exceeded"
            result["rejected_files"] = [
                {
                    "path": raw_path,
                    "reason": "max_files_exceeded",
                    "detail": (
                        f"requested={len(requested)} exceeds max_files={self._config.max_files}"
                    ),
                }
                for raw_path in requested
            ]
            return result

        allowed_files: list[Dict[str, Any]] = []
        rejected_files: list[Dict[str, Any]] = []
        total_bytes = 0
        for raw_path in requested:
            inspected = self._inspect_candidate(raw_path)
            if inspected.get("ok"):
                file_info = inspected["file"]
                total_bytes += int(file_info["size_bytes"])
                allowed_files.append(file_info)
                continue
            rejected_files.append(
                {
                    "path": raw_path,
                    "reason": inspected.get("reason", "rejected"),
                    "detail": inspected.get("detail", ""),
                }
            )

        result["allowed_files"] = allowed_files
        result["rejected_files"] = rejected_files
        result["file_count"] = len(allowed_files)
        result["total_bytes"] = int(total_bytes)

        if rejected_files:
            result["reason"] = "file_validation_failed"
            return result

        if total_bytes > self._config.max_total_bytes:
            overflow = total_bytes - self._config.max_total_bytes
            result["reason"] = "max_total_bytes_exceeded"
            result["rejected_files"] = [
                {
                    "path": "<batch>",
                    "reason": "max_total_bytes_exceeded",
                    "detail": (
                        f"total_bytes={total_bytes} exceeds "
                        f"max_total_bytes={self._config.max_total_bytes} by {overflow}"
                    ),
                }
            ]
            return result

        result["ok"] = True
        result["reason"] = "ok"
        return result

    def _inspect_candidate(self, raw_path: str) -> Dict[str, Any]:
        requested = str(raw_path or "").strip()
        if not requested:
            return {
                "ok": False,
                "reason": "invalid_path",
                "detail": "path is empty",
            }

        candidate = Path(requested).expanduser()
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return {
                "ok": False,
                "reason": "path_resolve_failed",
                "detail": "path cannot be resolved",
            }

        if not self._is_within_allowed_roots(resolved):
            return {
                "ok": False,
                "reason": "path_not_allowed",
                "detail": "resolved path is outside allowed roots",
            }

        extension = resolved.suffix.lower()
        if extension not in self._config.allowed_exts:
            return {
                "ok": False,
                "reason": "extension_not_allowed",
                "detail": f"extension {extension!r} is not allowed",
            }

        if not resolved.exists():
            return {
                "ok": False,
                "reason": "file_not_found",
                "detail": "file does not exist",
            }

        if not resolved.is_file():
            return {
                "ok": False,
                "reason": "not_a_file",
                "detail": "path is not a regular file",
            }

        try:
            size_bytes = int(resolved.stat().st_size)
        except OSError:
            return {
                "ok": False,
                "reason": "stat_failed",
                "detail": "failed to read file size",
            }

        return {
            "ok": True,
            "file": {
                "path": requested,
                "resolved_path": str(resolved),
                "extension": extension,
                "size_bytes": size_bytes,
            },
        }

    def _is_within_allowed_roots(self, resolved_path: Path) -> bool:
        for root in self._config.allowed_roots:
            try:
                resolved_path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _check_and_record_rate_limit(
        self,
        *,
        actor_id: str,
        session_id: str | None,
    ) -> Dict[str, Any]:
        now = float(self._clock())
        window_seconds = float(self._config.rate_limit_window_seconds)
        max_requests = int(self._config.rate_limit_max_requests)
        keys = self._rate_limit_keys(actor_id=actor_id, session_id=session_id)
        with self._rate_limit_guard:
            state_file = self._config.rate_limit_state_file
            if state_file is not None:
                return self._check_and_record_rate_limit_with_state_file(
                    keys=keys,
                    now=now,
                    window_seconds=window_seconds,
                    max_requests=max_requests,
                    state_file=state_file,
                )
            buckets: Dict[str, Deque[float]] = {}
            for key in keys:
                bucket = self._rate_limit_buckets.setdefault(key, deque())
                buckets[key] = bucket
                rate_limit_state = self._evaluate_rate_limit_bucket(
                    bucket=bucket,
                    key=key,
                    now=now,
                    window_seconds=window_seconds,
                    max_requests=max_requests,
                    keys=keys,
                )
                if not rate_limit_state.get("allowed", False):
                    return rate_limit_state

            for bucket in buckets.values():
                bucket.append(now)

            primary_key = keys[0]
            primary_bucket = buckets[primary_key]
            remaining = max_requests - len(primary_bucket)
            return {
                "allowed": True,
                "reason": "ok",
                "key": primary_key,
                "keys": list(keys),
                "scope": self._rate_limit_scope_from_key(primary_key),
                "window_seconds": int(window_seconds),
                "max_requests": max_requests,
                "remaining": max(0, int(remaining)),
                "retry_after_seconds": 0,
            }

    @staticmethod
    def _rate_limit_keys(*, actor_id: str, session_id: str | None) -> tuple[str, ...]:
        actor_key = f"{actor_id}::*"
        keys = [actor_key]
        if session_id:
            session_key = f"{actor_id}::{session_id}"
            if session_key not in keys:
                keys.append(session_key)
        return tuple(keys)

    @staticmethod
    def _rate_limit_scope_from_key(key: str) -> str:
        if key.endswith("::*"):
            return "actor"
        return "session"

    def _check_and_record_rate_limit_with_state_file(
        self,
        *,
        keys: Sequence[str],
        now: float,
        window_seconds: float,
        max_requests: int,
        state_file: Path,
    ) -> Dict[str, Any]:
        lock_file = Path(f"{state_file}.lock")
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(
                str(lock_file), timeout=_RATE_LIMIT_STATE_LOCK_TIMEOUT_SECONDS
            ):
                payload, load_error = self._load_rate_limit_state_payload(state_file)
                if load_error:
                    return self._rate_limit_state_unavailable(
                        key=keys[0],
                        keys=keys,
                        window_seconds=window_seconds,
                        max_requests=max_requests,
                        state_error=load_error,
                    )

                buckets: Dict[str, Deque[float]] = {}
                for key in keys:
                    bucket_values, bucket_error = self._extract_bucket_from_payload(
                        payload=payload,
                        key=key,
                    )
                    if bucket_error:
                        return self._rate_limit_state_unavailable(
                            key=key,
                            keys=keys,
                            window_seconds=window_seconds,
                            max_requests=max_requests,
                            state_error=bucket_error,
                        )

                    bucket = deque(bucket_values)
                    rate_limit_state = self._evaluate_rate_limit_bucket(
                        bucket=bucket,
                        key=key,
                        now=now,
                        window_seconds=window_seconds,
                        max_requests=max_requests,
                        keys=keys,
                    )
                    if not rate_limit_state.get("allowed", False):
                        return rate_limit_state
                    buckets[key] = bucket

                self._prune_rate_limit_state_payload(
                    payload=payload,
                    now=now,
                    window_seconds=window_seconds,
                    protected_keys=set(keys),
                )
                for key, bucket in buckets.items():
                    bucket.append(now)
                    payload[key] = list(bucket)
                save_error = self._write_rate_limit_state_payload(
                    state_file=state_file,
                    payload=payload,
                )
                if save_error:
                    return self._rate_limit_state_unavailable(
                        key=keys[0],
                        keys=keys,
                        window_seconds=window_seconds,
                        max_requests=max_requests,
                        state_error=save_error,
                    )

                for key, bucket in buckets.items():
                    self._rate_limit_buckets[key] = deque(bucket)

                primary_key = keys[0]
                primary_bucket = buckets[primary_key]
                remaining = max_requests - len(primary_bucket)
                return {
                    "allowed": True,
                    "reason": "ok",
                    "key": primary_key,
                    "keys": list(keys),
                    "scope": self._rate_limit_scope_from_key(primary_key),
                    "window_seconds": int(window_seconds),
                    "max_requests": max_requests,
                    "remaining": max(0, int(remaining)),
                    "retry_after_seconds": 0,
                }
        except Timeout:
            return self._rate_limit_state_unavailable(
                key=keys[0],
                keys=keys,
                window_seconds=window_seconds,
                max_requests=max_requests,
                state_error="state_lock_timeout",
            )
        except OSError:
            return self._rate_limit_state_unavailable(
                key=keys[0],
                keys=keys,
                window_seconds=window_seconds,
                max_requests=max_requests,
                state_error="state_io_error",
            )

    def _evaluate_rate_limit_bucket(
        self,
        *,
        bucket: Deque[float],
        key: str,
        now: float,
        window_seconds: float,
        max_requests: int,
        keys: Sequence[str],
    ) -> Dict[str, Any]:
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if bucket and not math.isfinite(float(bucket[0])):
            return self._rate_limit_state_unavailable(
                key=key,
                keys=keys,
                window_seconds=window_seconds,
                max_requests=max_requests,
                state_error="invalid_bucket_timestamp",
            )

        if len(bucket) >= max_requests:
            retry_raw = window_seconds - (now - float(bucket[0]))
            if not math.isfinite(retry_raw):
                return self._rate_limit_state_unavailable(
                    key=key,
                    keys=keys,
                    window_seconds=window_seconds,
                    max_requests=max_requests,
                    state_error="invalid_retry_after",
                )
            retry_after = max(1, int(math.ceil(retry_raw)))
            return {
                "allowed": False,
                "reason": "rate_limited",
                "key": key,
                "keys": list(keys),
                "scope": self._rate_limit_scope_from_key(key),
                "window_seconds": int(window_seconds),
                "max_requests": max_requests,
                "remaining": 0,
                "retry_after_seconds": retry_after,
            }

        return {
            "allowed": True,
            "reason": "ok",
            "key": key,
            "keys": list(keys),
            "scope": self._rate_limit_scope_from_key(key),
            "window_seconds": int(window_seconds),
            "max_requests": max_requests,
            "remaining": max(0, int(max_requests - len(bucket))),
            "retry_after_seconds": 0,
        }

    @staticmethod
    def _rate_limit_state_unavailable(
        *,
        key: str,
        keys: Sequence[str],
        window_seconds: float,
        max_requests: int,
        state_error: str,
    ) -> Dict[str, Any]:
        return {
            "allowed": False,
            "reason": "rate_limit_state_unavailable",
            "key": key,
            "keys": list(keys),
            "scope": ExternalImportGuard._rate_limit_scope_from_key(key),
            "window_seconds": int(window_seconds),
            "max_requests": max_requests,
            "remaining": 0,
            "retry_after_seconds": 0,
            "state_error": state_error,
        }

    @staticmethod
    def _load_rate_limit_state_payload(
        state_file: Path,
    ) -> tuple[Dict[str, Any], Optional[str]]:
        if not state_file.exists():
            return {}, None
        if not state_file.is_file():
            return {}, "state_file_not_regular_file"
        try:
            payload = json.loads(state_file.read_text(encoding="utf-8"))
        except OSError:
            return {}, "state_file_read_failed"
        except json.JSONDecodeError:
            return {}, "state_file_invalid_json"
        if not isinstance(payload, dict):
            return {}, "state_file_invalid_payload"
        return payload, None

    @staticmethod
    def _extract_bucket_from_payload(
        *, payload: Dict[str, Any], key: str
    ) -> tuple[list[float], Optional[str]]:
        values = payload.get(key)
        if values is None:
            return [], None
        if not isinstance(values, list):
            return [], "state_bucket_invalid_type"
        parsed: list[float] = []
        for item in values:
            try:
                timestamp = float(item)
            except (TypeError, ValueError):
                return [], "state_bucket_invalid_timestamp"
            if not math.isfinite(timestamp) or timestamp < 0:
                return [], "state_bucket_invalid_timestamp"
            parsed.append(timestamp)
        return parsed, None

    @staticmethod
    def _write_rate_limit_state_payload(
        *, state_file: Path, payload: Dict[str, Any]
    ) -> Optional[str]:
        tmp_file = state_file.with_name(
            f"{state_file.name}.tmp.{os.getpid()}.{threading.get_ident()}"
        )
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_file.replace(state_file)
            return None
        except OSError:
            return "state_file_write_failed"
        finally:
            try:
                if tmp_file.exists():
                    tmp_file.unlink()
            except OSError:
                pass

    @staticmethod
    def _prune_rate_limit_state_payload(
        *,
        payload: Dict[str, Any],
        now: float,
        window_seconds: float,
        protected_keys: set[str],
    ) -> None:
        cutoff = float(now) - float(window_seconds)
        for key in list(payload.keys()):
            if key in protected_keys:
                continue
            values = payload.get(key)
            if not isinstance(key, str) or not isinstance(values, list):
                payload.pop(key, None)
                continue

            cleaned: list[float] = []
            valid = True
            for item in values:
                try:
                    timestamp = float(item)
                except (TypeError, ValueError):
                    valid = False
                    break
                if not math.isfinite(timestamp) or timestamp < 0:
                    valid = False
                    break
                if timestamp > cutoff:
                    cleaned.append(timestamp)

            if not valid or not cleaned:
                payload.pop(key, None)
                continue
            payload[key] = cleaned
