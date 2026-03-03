#!/usr/bin/env python3
"""Phase D spike runner for embedding/sqlite-vec/WAL feasibility checks."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JSON_OUTPUT = (
    BACKEND_ROOT / "tests" / "benchmark" / "phase_d_spike_metrics.json"
)
DEFAULT_MARKDOWN_OUTPUT = (
    BACKEND_ROOT / "tests" / "benchmark" / "benchmark_results_phase_d_spike.md"
)
PROFILE_AB_JSON_ARTIFACT = (
    BACKEND_ROOT / "tests" / "benchmark" / "profile_ab_metrics.json"
)
PROFILE_VEC_ISOLATION_JSON_ARTIFACT_V2 = (
    BACKEND_ROOT / "tests" / "benchmark" / "profile_vec_isolation_metrics_v2.json"
)
PROFILE_VEC_ISOLATION_JSON_ARTIFACT = (
    BACKEND_ROOT / "tests" / "benchmark" / "profile_vec_isolation_metrics.json"
)

_API_DISABLED_BACKENDS = {"hash", "none", "off", "disabled", "false", "0"}
_HOLD11_EMBEDDING_SUCCESS_THRESHOLD = 0.995
_HOLD11_FALLBACK_HASH_RATE_THRESHOLD = 0.01
_HOLD11_DEGRADED_RATE_THRESHOLD = 0.01
_HOLD12_LATENCY_IMPROVEMENT_THRESHOLD = 0.20
_HOLD12_QUALITY_DELTA_THRESHOLD = 0.0
_HOLD13_FAILURE_RATE_THRESHOLD = 0.001
_HOLD13_RETRY_RATE_P95_THRESHOLD = 0.01
_HOLD13_THROUGHPUT_RATIO_THRESHOLD = 1.10
_WAL_PROFILE_DEFAULTS: Dict[str, Dict[str, Union[int, float]]] = {
    "small": {
        "workers": 4,
        "tx_per_worker": 80,
        "timeout_sec": 0.05,
        "min_throughput_ratio": 1.0,
        "max_failure_rate": 0.0,
        "max_retry_rate": 0.01,
        "max_persistence_gap": 0,
    },
    "medium": {
        "workers": 6,
        "tx_per_worker": 120,
        "timeout_sec": 0.08,
        "min_throughput_ratio": 1.0,
        "max_failure_rate": 0.001,
        "max_retry_rate": 0.01,
        "max_persistence_gap": 0,
    },
    "stress": {
        "workers": 8,
        "tx_per_worker": 200,
        "timeout_sec": 0.1,
        "min_throughput_ratio": 1.0,
        "max_failure_rate": 0.003,
        "max_retry_rate": 0.02,
        "max_persistence_gap": 0,
    },
    "business_write_burst": {
        "workers": 8,
        "tx_per_worker": 160,
        "timeout_sec": 0.08,
        "min_throughput_ratio": 1.05,
        "max_failure_rate": 0.002,
        "max_retry_rate": 0.01,
        "max_persistence_gap": 0,
    },
    "business_write_peak": {
        "workers": 12,
        "tx_per_worker": 220,
        "timeout_sec": 0.12,
        "min_throughput_ratio": 1.02,
        "max_failure_rate": 0.005,
        "max_retry_rate": 0.01,
        "max_persistence_gap": 0,
    },
}
_LOCK_RETRY_MAX_ATTEMPTS = 12
_LOCK_RETRY_BASE_DELAY_SEC = 0.001
_LOCK_RETRY_MAX_DELAY_SEC = 0.02


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sanitize_report_path(raw_path: Optional[str]) -> str:
    candidate = str(raw_path or "").strip()
    if not candidate:
        return ""
    try:
        path_obj = Path(candidate).expanduser()
    except Exception:
        return candidate

    if not path_obj.is_absolute():
        return candidate

    try:
        return path_obj.resolve(strict=False).relative_to(BACKEND_ROOT).as_posix()
    except Exception:
        pass
    try:
        return path_obj.resolve(strict=False).relative_to(BACKEND_ROOT.parent).as_posix()
    except Exception:
        pass
    try:
        return "<tmp>/" + path_obj.resolve(strict=False).relative_to(Path("/tmp")).as_posix()
    except Exception:
        pass
    return f"<abs>/{path_obj.name}"


def _normalize_embedding_api_base(base: str) -> str:
    normalized = (base or "").strip().rstrip("/")
    if not normalized:
        return ""
    lowered = normalized.lower()
    if lowered.endswith("/embeddings"):
        return normalized[: -len("/embeddings")]
    return normalized


def _first_env(
    names: Iterable[str], env: Optional[Mapping[str, str]] = None
) -> Tuple[str, str]:
    source_env: Mapping[str, str] = env if env is not None else os.environ
    for name in names:
        raw = source_env.get(name)
        if raw is None:
            continue
        candidate = raw.strip()
        if candidate:
            return candidate, name
    return "", ""


def _embedding_base_candidates(backend: str) -> Sequence[str]:
    backend_value = (backend or "").strip().lower()
    if backend_value == "router":
        return (
            "ROUTER_API_BASE",
            "RETRIEVAL_EMBEDDING_API_BASE",
            "RETRIEVAL_EMBEDDING_BASE",
        )
    if backend_value == "openai":
        return (
            "OPENAI_BASE_URL",
            "OPENAI_API_BASE",
            "RETRIEVAL_EMBEDDING_API_BASE",
            "RETRIEVAL_EMBEDDING_BASE",
        )
    return (
        "RETRIEVAL_EMBEDDING_API_BASE",
        "RETRIEVAL_EMBEDDING_BASE",
        "ROUTER_API_BASE",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    )


def _embedding_key_candidates(backend: str) -> Sequence[str]:
    backend_value = (backend or "").strip().lower()
    if backend_value == "router":
        return (
            "ROUTER_API_KEY",
            "RETRIEVAL_EMBEDDING_API_KEY",
            "RETRIEVAL_EMBEDDING_KEY",
        )
    if backend_value == "openai":
        return (
            "OPENAI_API_KEY",
            "RETRIEVAL_EMBEDDING_API_KEY",
            "RETRIEVAL_EMBEDDING_KEY",
        )
    return (
        "RETRIEVAL_EMBEDDING_API_KEY",
        "RETRIEVAL_EMBEDDING_KEY",
        "ROUTER_API_KEY",
        "OPENAI_API_KEY",
    )


def run_embedding_provider_probe() -> Dict[str, Any]:
    """Evaluate embedding provider routing readiness from environment only."""
    configured_backend = (
        os.getenv("RETRIEVAL_EMBEDDING_BACKEND", "hash").strip().lower() or "hash"
    )
    backend_cases = [configured_backend]
    for backend in ("api", "router", "openai", "hash", "none"):
        if backend not in backend_cases:
            backend_cases.append(backend)

    cases: List[Dict[str, Any]] = []
    for backend in backend_cases:
        raw_base, base_source = _first_env(_embedding_base_candidates(backend))
        _, key_source = _first_env(_embedding_key_candidates(backend))
        resolved_base = _normalize_embedding_api_base(raw_base)
        api_required = backend not in _API_DISABLED_BACKENDS
        if not api_required:
            status = "not_required"
        elif resolved_base:
            status = "ready"
        else:
            status = "missing_api_base"

        case = {
            "backend": backend,
            "api_base_source": base_source or "unset",
            "backend/api_base_source": f"{backend}/{base_source or 'unset'}",
            "api_base": resolved_base,
            "api_base_present": bool(resolved_base),
            "api_key_source": key_source or "unset",
            "api_key_present": bool(key_source),
            "status": status,
        }
        cases.append(case)

    configured_case = next(
        (item for item in cases if item.get("backend") == configured_backend),
        cases[0],
    )
    probe_status = (
        "ok"
        if configured_case["status"] in {"ready", "not_required"}
        else "needs_attention"
    )
    return {
        "status": probe_status,
        "configured_backend": configured_backend,
        "cases": cases,
    }


def run_sqlite_vec_probe(sqlite_vec_extension_path: Optional[str]) -> Dict[str, Any]:
    """Probe sqlite runtime and optional sqlite-vec extension loading."""
    result: Dict[str, Any] = {
        "status": "unknown",
        "sqlite_version": sqlite3.sqlite_version,
        "sqlite_source_id": sqlite3.sqlite_version_info,
        "extension_path_input": sqlite_vec_extension_path or "",
        "extension_path": "",
        "extension_path_exists": False,
        "extension_load_attempted": False,
        "extension_loaded": False,
        "sqlite_vec_readiness": "hold",
        "verification_level": "runtime_only",
        "diag_code": "",
        "checks": [],
        "compile_options": [],
        "errors": [],
    }

    connection: Optional[sqlite3.Connection] = None
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("SELECT 1")
        result["checks"].append({"name": "basic_query", "ok": True})

        compile_options = [
            str(row[0])
            for row in connection.execute("PRAGMA compile_options").fetchall()
            if row and row[0] is not None
        ]
        result["compile_options"] = sorted(set(compile_options))
        result["checks"].append({"name": "compile_options", "ok": True})

        if not sqlite_vec_extension_path:
            result["status"] = "skipped_no_extension_path"
            result["checks"].append({"name": "extension_load", "ok": False, "reason": "path_not_provided"})
            return result

        resolved_extension_file = _resolve_sqlite_extension_file(sqlite_vec_extension_path)
        if resolved_extension_file is None:
            result["status"] = "invalid_extension_path"
            result["diag_code"] = "path_not_found"
            result["errors"].append("invalid_extension_path:path_not_found")
            result["checks"].append({"name": "extension_load", "ok": False, "reason": "path_not_found"})
            return result
        extension_path_obj = resolved_extension_file
        extension_path = str(extension_path_obj)
        result["extension_path"] = extension_path
        result["extension_path_exists"] = True
        if not extension_path_obj.is_file():
            result["status"] = "invalid_extension_path"
            result["diag_code"] = "path_not_file"
            result["errors"].append("invalid_extension_path:path_not_file")
            result["checks"].append({"name": "extension_load", "ok": False, "reason": "path_not_file"})
            return result
        try:
            connection.enable_load_extension(True)
        except (AttributeError, sqlite3.Error) as exc:
            result["status"] = "extension_loading_unavailable"
            result["errors"].append(f"enable_load_extension_failed: {exc}")
            return result

        result["extension_load_attempted"] = True
        try:
            connection.load_extension(extension_path)
            result["extension_loaded"] = True
            result["status"] = "ok"
            result["sqlite_vec_readiness"] = "ready"
            result["verification_level"] = "extension_loaded"
            result["checks"].append({"name": "extension_load", "ok": True})
        except sqlite3.Error as exc:
            result["status"] = "extension_load_failed"
            result["diag_code"] = "load_extension_failed"
            result["errors"].append(f"load_extension_failed: {exc}")
            result["checks"].append({"name": "extension_load", "ok": False})
        finally:
            try:
                connection.enable_load_extension(False)
            except sqlite3.Error:
                # Best-effort cleanup only.
                pass
    except sqlite3.Error as exc:
        result["status"] = "sqlite_runtime_error"
        result["errors"].append(f"sqlite_runtime_error: {exc}")
    finally:
        if connection is not None:
            connection.close()
    return result


def _init_write_probe_db(db_path: Path, journal_mode: str, timeout_sec: float) -> str:
    timeout = max(0.001, float(timeout_sec))
    busy_timeout_ms = max(1, int(timeout * 1000))
    with sqlite3.connect(str(db_path), timeout=timeout) as connection:
        effective_mode = str(
            connection.execute(f"PRAGMA journal_mode={journal_mode.upper()}").fetchone()[0]
        ).lower()
        connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS write_probe (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.commit()
    return effective_mode


def _resolve_wal_probe_config(
    *,
    workers: Optional[int],
    tx_per_worker: Optional[int],
    timeout_sec: Optional[float],
    load_profile: str,
) -> Tuple[str, int, int, float]:
    normalized_profile = (load_profile or "small").strip().lower()
    if normalized_profile not in _WAL_PROFILE_DEFAULTS:
        normalized_profile = "small"

    profile_defaults = _WAL_PROFILE_DEFAULTS[normalized_profile]
    default_workers = int(profile_defaults["workers"])
    default_tx = int(profile_defaults["tx_per_worker"])
    default_timeout = float(profile_defaults["timeout_sec"])
    resolved_workers = max(
        1, int(default_workers if workers is None else workers)
    )
    resolved_tx = max(
        1, int(default_tx if tx_per_worker is None else tx_per_worker)
    )
    resolved_timeout = max(
        0.001, float(default_timeout if timeout_sec is None else timeout_sec)
    )
    return normalized_profile, resolved_workers, resolved_tx, resolved_timeout


def _default_wal_thresholds(load_profile: str) -> Dict[str, Union[int, float]]:
    normalized_profile = (load_profile or "small").strip().lower()
    if normalized_profile not in _WAL_PROFILE_DEFAULTS:
        normalized_profile = "small"
    defaults = _WAL_PROFILE_DEFAULTS[normalized_profile]
    return {
        "min_throughput_ratio": float(defaults["min_throughput_ratio"]),
        "max_failure_rate": float(defaults["max_failure_rate"]),
        "max_retry_rate": float(defaults["max_retry_rate"]),
        "max_persistence_gap": int(defaults["max_persistence_gap"]),
    }


def _resolve_sqlite_extension_file(path_input: str) -> Optional[Path]:
    try:
        raw = Path(path_input).expanduser().resolve(strict=False)
    except OSError:
        return None
    candidates = [raw]
    if raw.suffix == "":
        candidates.extend(Path(str(raw) + suffix) for suffix in (".dylib", ".so", ".dll"))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def _lock_retry_delay_sec(worker_id: int, seq: int, attempt: int) -> float:
    """Deterministic capped exponential backoff with light jitter."""
    base_delay = min(
        _LOCK_RETRY_MAX_DELAY_SEC,
        _LOCK_RETRY_BASE_DELAY_SEC * (2 ** max(0, int(attempt))),
    )
    jitter = 1.0 + ((int(worker_id) + int(seq) + int(attempt)) % 4) * 0.25
    return max(0.0, float(base_delay) * float(jitter))


def _write_probe_worker(
    db_path: Path,
    worker_id: int,
    tx_per_worker: int,
    timeout_sec: float,
) -> Dict[str, int]:
    timeout = max(0.001, float(timeout_sec))
    busy_timeout_ms = max(1, int(timeout * 1000))
    connection = sqlite3.connect(
        str(db_path),
        timeout=timeout,
        check_same_thread=False,
        isolation_level=None,
    )
    connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    success = 0
    failed = 0
    lock_retries = 0
    try:
        for seq in range(int(tx_per_worker)):
            retried_in_current_tx = False
            for attempt in range(_LOCK_RETRY_MAX_ATTEMPTS):
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "INSERT INTO write_probe(worker_id, seq, created_at) VALUES (?, ?, ?)",
                        (int(worker_id), int(seq), _utc_now_iso()),
                    )
                    connection.execute("COMMIT")
                    success += 1
                    break
                except sqlite3.OperationalError as exc:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    message = str(exc).lower()
                    if "locked" in message and attempt < (_LOCK_RETRY_MAX_ATTEMPTS - 1):
                        if not retried_in_current_tx:
                            lock_retries += 1
                            retried_in_current_tx = True
                        time.sleep(_lock_retry_delay_sec(worker_id, seq, attempt))
                        continue
                    failed += 1
                    break
                except sqlite3.Error:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    failed += 1
                    break
    finally:
        connection.close()
    return {
        "success": success,
        "failed": failed,
        "lock_retries": lock_retries,
    }


def _run_journal_mode_probe(
    journal_mode: str,
    workers: int,
    tx_per_worker: int,
    timeout_sec: float,
) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"phase-d-{journal_mode}-") as tmp_dir:
        db_path = Path(tmp_dir) / f"write_probe_{journal_mode}.db"
        effective_mode = _init_write_probe_db(
            db_path=db_path, journal_mode=journal_mode, timeout_sec=timeout_sec
        )
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    _write_probe_worker,
                    db_path,
                    worker_id,
                    tx_per_worker,
                    timeout_sec,
                )
                for worker_id in range(workers)
            ]
            worker_rows = [future.result() for future in futures]
        elapsed_sec = max(1e-9, time.perf_counter() - started)
        successful_tx = sum(int(row["success"]) for row in worker_rows)
        failed_tx = sum(int(row["failed"]) for row in worker_rows)
        lock_retries = sum(int(row["lock_retries"]) for row in worker_rows)
        planned_tx = int(workers) * int(tx_per_worker)
        throughput = successful_tx / elapsed_sec

        with sqlite3.connect(str(db_path)) as connection:
            persisted_rows = int(
                connection.execute("SELECT COUNT(*) FROM write_probe").fetchone()[0]
            )

    return {
        "status": "ok" if failed_tx == 0 else "ok_with_failures",
        "requested_journal_mode": journal_mode.lower(),
        "effective_journal_mode": effective_mode,
        "workers": int(workers),
        "tx_per_worker": int(tx_per_worker),
        "planned_tx": planned_tx,
        "successful_tx": successful_tx,
        "failed_tx": failed_tx,
        "lock_retries": lock_retries,
        "rows_persisted": persisted_rows,
        "elapsed_sec": round(elapsed_sec, 6),
        "throughput_tps": round(float(throughput), 3),
    }


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(item) for item in values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (max(0.0, min(100.0, float(percentile))) / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _aggregate_journal_mode_metrics(samples: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    sample_list = [item for item in samples if isinstance(item, Mapping)]
    if not sample_list:
        return {
            "status": "degraded",
            "requested_journal_mode": "unknown",
            "effective_journal_mode": "unknown",
            "workers": 0,
            "tx_per_worker": 0,
            "planned_tx": 0,
            "successful_tx": 0,
            "failed_tx": 0,
            "lock_retries": 0,
            "rows_persisted": 0,
            "elapsed_sec": 0.0,
            "throughput_tps": 0.0,
            "failure_rate": 0.0,
            "retry_rate": 0.0,
            "persistence_gap": 0,
            "sample_count": 0,
            "throughput_samples_tps": [],
        }

    planned_tx = sum(int(item.get("planned_tx", 0)) for item in sample_list)
    successful_tx = sum(int(item.get("successful_tx", 0)) for item in sample_list)
    failed_tx = sum(int(item.get("failed_tx", 0)) for item in sample_list)
    lock_retries = sum(int(item.get("lock_retries", 0)) for item in sample_list)
    rows_persisted = sum(int(item.get("rows_persisted", 0)) for item in sample_list)
    elapsed_sec = max(
        1e-9, sum(float(item.get("elapsed_sec", 0.0) or 0.0) for item in sample_list)
    )
    throughput = successful_tx / elapsed_sec
    failure_rate = (failed_tx / planned_tx) if planned_tx > 0 else 0.0
    retry_rate = (lock_retries / planned_tx) if planned_tx > 0 else 0.0
    persistence_gap = successful_tx - rows_persisted
    throughput_samples = [
        float(item.get("throughput_tps", 0.0) or 0.0) for item in sample_list
    ]
    mode_statuses = {str(item.get("status", "unknown")) for item in sample_list}
    status = "ok" if mode_statuses == {"ok"} else "ok_with_failures"

    first = sample_list[0]
    return {
        "status": status,
        "requested_journal_mode": str(first.get("requested_journal_mode", "unknown")),
        "effective_journal_mode": str(first.get("effective_journal_mode", "unknown")),
        "workers": int(first.get("workers", 0) or 0),
        "tx_per_worker": int(first.get("tx_per_worker", 0) or 0),
        "planned_tx": int(planned_tx),
        "successful_tx": int(successful_tx),
        "failed_tx": int(failed_tx),
        "lock_retries": int(lock_retries),
        "rows_persisted": int(rows_persisted),
        "elapsed_sec": round(float(elapsed_sec), 6),
        "throughput_tps": round(float(throughput), 3),
        "failure_rate": round(float(failure_rate), 6),
        "retry_rate": round(float(retry_rate), 6),
        "persistence_gap": int(persistence_gap),
        "sample_count": len(sample_list),
        "throughput_samples_tps": [round(item, 3) for item in throughput_samples],
    }


def _build_wal_regression_gate(
    *,
    delete_metrics: Mapping[str, Any],
    wal_metrics: Mapping[str, Any],
    wal_gain: Optional[float],
    min_throughput_ratio: float,
    max_failure_rate: float,
    max_retry_rate: float,
    max_persistence_gap: int,
) -> Dict[str, Any]:
    reasons: List[str] = []
    wal_failure_rate = float(wal_metrics.get("failure_rate", 0.0) or 0.0)
    wal_retry_rate = float(wal_metrics.get("retry_rate", 0.0) or 0.0)
    wal_persistence_gap = int(wal_metrics.get("persistence_gap", 0) or 0)
    delete_persistence_gap = int(delete_metrics.get("persistence_gap", 0) or 0)

    if wal_failure_rate > max_failure_rate:
        reasons.append(
            "wal_failure_rate_exceeded:{actual:.6f}>{expected:.6f}".format(
                actual=wal_failure_rate,
                expected=max_failure_rate,
            )
        )
    if wal_retry_rate > max_retry_rate:
        reasons.append(
            "wal_retry_rate_exceeded:{actual:.6f}>{expected:.6f}".format(
                actual=wal_retry_rate,
                expected=max_retry_rate,
            )
        )
    if wal_persistence_gap > max_persistence_gap:
        reasons.append(
            f"wal_persistence_gap_exceeded:{wal_persistence_gap}>{max_persistence_gap}"
        )
    if delete_persistence_gap > max_persistence_gap:
        reasons.append(
            f"delete_persistence_gap_exceeded:{delete_persistence_gap}>{max_persistence_gap}"
        )
    if wal_gain is None:
        reasons.append("wal_vs_delete_throughput_ratio_unavailable")
    elif float(wal_gain) < float(min_throughput_ratio):
        reasons.append(
            "wal_throughput_ratio_below_threshold:{actual:.3f}<{expected:.3f}".format(
                actual=float(wal_gain),
                expected=float(min_throughput_ratio),
            )
        )

    return {
        "pass": len(reasons) == 0,
        "reasons": reasons,
    }


def _build_wal_threshold_suggestion(
    *,
    load_profile: str,
    wal_metrics: Mapping[str, Any],
    delete_metrics: Mapping[str, Any],
    wal_gain: Optional[float],
    profile_thresholds: Mapping[str, Union[int, float]],
) -> Dict[str, Any]:
    profile_min_ratio = float(profile_thresholds.get("min_throughput_ratio", 1.0))
    profile_max_failure = float(profile_thresholds.get("max_failure_rate", 0.0))
    profile_max_retry = float(profile_thresholds.get("max_retry_rate", 0.01))
    profile_max_gap = int(profile_thresholds.get("max_persistence_gap", 0))
    observed_failure = float(wal_metrics.get("failure_rate", 0.0) or 0.0)
    observed_retry = float(wal_metrics.get("retry_rate", 0.0) or 0.0)
    observed_delete_gap = int(delete_metrics.get("persistence_gap", 0) or 0)
    observed_wal_gap = int(wal_metrics.get("persistence_gap", 0) or 0)
    observed_max_gap = max(0, observed_delete_gap, observed_wal_gap)

    if wal_gain is None or float(wal_gain) <= 0:
        ratio_suggestion = profile_min_ratio
    else:
        ratio_suggestion = max(0.8, min(profile_min_ratio, round(float(wal_gain) * 0.9, 3)))

    return {
        "profile": load_profile,
        "profile_baseline": {
            "min_throughput_ratio": round(profile_min_ratio, 3),
            "max_failure_rate": round(profile_max_failure, 6),
            "max_retry_rate": round(profile_max_retry, 6),
            "max_persistence_gap": profile_max_gap,
        },
        "suggested_stable_thresholds": {
            "min_throughput_ratio": round(ratio_suggestion, 3),
            "max_failure_rate": round(max(profile_max_failure, observed_failure + 0.001), 6),
            "max_retry_rate": round(max(profile_max_retry, observed_retry + 0.001), 6),
            "max_persistence_gap": max(profile_max_gap, observed_max_gap),
        },
        "observed": {
            "wal_vs_delete_throughput_ratio": wal_gain,
            "wal_failure_rate": round(observed_failure, 6),
            "wal_retry_rate": round(observed_retry, 6),
            "wal_persistence_gap": observed_wal_gap,
            "delete_persistence_gap": observed_delete_gap,
        },
        "note": "Thresholds are recommendations for future gate tuning; production default remains HOLD.",
    }


def run_write_lane_wal_probe(
    workers: Optional[int] = None,
    tx_per_worker: Optional[int] = None,
    timeout_sec: Optional[float] = None,
    load_profile: str = "small",
    repeat: int = 1,
    min_throughput_ratio: Optional[float] = None,
    max_failure_rate: Optional[float] = None,
    max_retry_rate: Optional[float] = None,
    max_persistence_gap: Optional[int] = None,
) -> Dict[str, Any]:
    """Run write-lane spike under DELETE and WAL modes with optional profiles."""
    (
        normalized_profile,
        normalized_workers,
        normalized_tx,
        normalized_timeout,
    ) = _resolve_wal_probe_config(
        workers=workers,
        tx_per_worker=tx_per_worker,
        timeout_sec=timeout_sec,
        load_profile=load_profile,
    )
    normalized_repeat = max(1, int(repeat))
    profile_thresholds = _default_wal_thresholds(normalized_profile)
    effective_min_ratio = (
        float(min_throughput_ratio)
        if min_throughput_ratio is not None
        else float(profile_thresholds["min_throughput_ratio"])
    )
    effective_max_failure_rate = max(
        0.0,
        float(
            profile_thresholds["max_failure_rate"]
            if max_failure_rate is None
            else max_failure_rate
        ),
    )
    effective_max_retry_rate = max(
        0.0,
        float(
            profile_thresholds["max_retry_rate"]
            if max_retry_rate is None
            else max_retry_rate
        ),
    )
    effective_max_persistence_gap = max(
        0,
        int(
            profile_thresholds["max_persistence_gap"]
            if max_persistence_gap is None
            else max_persistence_gap
        ),
    )

    delete_samples: List[Dict[str, Any]] = []
    wal_samples: List[Dict[str, Any]] = []
    for _ in range(normalized_repeat):
        delete_samples.append(
            _run_journal_mode_probe(
                journal_mode="delete",
                workers=normalized_workers,
                tx_per_worker=normalized_tx,
                timeout_sec=normalized_timeout,
            )
        )
        wal_samples.append(
            _run_journal_mode_probe(
                journal_mode="wal",
                workers=normalized_workers,
                tx_per_worker=normalized_tx,
                timeout_sec=normalized_timeout,
            )
        )

    delete_metrics = _aggregate_journal_mode_metrics(delete_samples)
    wal_metrics = _aggregate_journal_mode_metrics(wal_samples)

    delete_tp = float(delete_metrics["throughput_tps"])
    wal_tp = float(wal_metrics["throughput_tps"])
    wal_gain = round((wal_tp / delete_tp), 3) if delete_tp > 0 else None

    summary = {
        "delete": {
            "throughput_tps_p50": round(
                _percentile(
                    [float(item.get("throughput_tps", 0.0) or 0.0) for item in delete_samples],
                    50,
                ),
                3,
            ),
            "throughput_tps_p95": round(
                _percentile(
                    [float(item.get("throughput_tps", 0.0) or 0.0) for item in delete_samples],
                    95,
                ),
                3,
            ),
            "failure_rate_max": round(
                max(
                    (
                        (
                            float(item.get("failed_tx", 0) or 0)
                            / max(1.0, float(item.get("planned_tx", 0) or 0))
                        )
                        for item in delete_samples
                    ),
                    default=0.0,
                ),
                6,
            ),
            "retry_rate_p95": round(
                _percentile(
                    [
                        (
                            float(item.get("lock_retries", 0) or 0)
                            / max(1.0, float(item.get("planned_tx", 0) or 0))
                        )
                        for item in delete_samples
                    ],
                    95,
                ),
                6,
            ),
        },
        "wal": {
            "throughput_tps_p50": round(
                _percentile(
                    [float(item.get("throughput_tps", 0.0) or 0.0) for item in wal_samples],
                    50,
                ),
                3,
            ),
            "throughput_tps_p95": round(
                _percentile(
                    [float(item.get("throughput_tps", 0.0) or 0.0) for item in wal_samples],
                    95,
                ),
                3,
            ),
            "failure_rate_max": round(
                max(
                    (
                        (
                            float(item.get("failed_tx", 0) or 0)
                            / max(1.0, float(item.get("planned_tx", 0) or 0))
                        )
                        for item in wal_samples
                    ),
                    default=0.0,
                ),
                6,
            ),
            "retry_rate_p95": round(
                _percentile(
                    [
                        (
                            float(item.get("lock_retries", 0) or 0)
                            / max(1.0, float(item.get("planned_tx", 0) or 0))
                        )
                        for item in wal_samples
                    ],
                    95,
                ),
                6,
            ),
        },
    }
    regression_gate = _build_wal_regression_gate(
        delete_metrics=delete_metrics,
        wal_metrics=wal_metrics,
        wal_gain=wal_gain,
        min_throughput_ratio=effective_min_ratio,
        max_failure_rate=effective_max_failure_rate,
        max_retry_rate=effective_max_retry_rate,
        max_persistence_gap=effective_max_persistence_gap,
    )
    threshold_suggestion = _build_wal_threshold_suggestion(
        load_profile=normalized_profile,
        wal_metrics=wal_metrics,
        delete_metrics=delete_metrics,
        wal_gain=wal_gain,
        profile_thresholds=profile_thresholds,
    )
    status = (
        "ok"
        if delete_metrics["status"] == "ok"
        and wal_metrics["status"] == "ok"
        and bool(regression_gate.get("pass"))
        else "degraded"
    )

    return {
        "status": status,
        "load_profile": normalized_profile,
        "repeat": normalized_repeat,
        "workers": normalized_workers,
        "tx_per_worker": normalized_tx,
        "timeout_sec": normalized_timeout,
        "regression_thresholds": {
            "min_throughput_ratio": round(effective_min_ratio, 3),
            "max_failure_rate": round(effective_max_failure_rate, 6),
            "max_retry_rate": round(effective_max_retry_rate, 6),
            "max_persistence_gap": effective_max_persistence_gap,
        },
        "results": {
            "delete": delete_metrics,
            "wal": wal_metrics,
        },
        "summary": summary,
        "regression_gate": regression_gate,
        "threshold_suggestion": threshold_suggestion,
        "wal_vs_delete_throughput_ratio": wal_gain,
    }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_rate(numerator: float, denominator: float) -> float:
    if denominator <= 0.0:
        return 0.0
    return round(numerator / denominator, 6)


def _load_json_metrics_artifact(path: Path) -> Dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return {"path": str(resolved), "status": "missing", "payload": {}}
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive path
        return {
            "path": str(resolved),
            "status": "invalid_json",
            "error": f"{type(exc).__name__}: {exc}",
            "payload": {},
        }
    if not isinstance(payload, Mapping):
        return {
            "path": str(resolved),
            "status": "invalid_payload",
            "payload": {},
        }
    return {"path": str(resolved), "status": "ok", "payload": dict(payload)}


def _load_profile_ab_metrics_artifact(path: Path) -> Dict[str, Any]:
    return _load_json_metrics_artifact(path)


def _load_vec_isolation_metrics_artifact(
    primary_path: Path,
    fallback_path: Optional[Path] = None,
) -> Dict[str, Any]:
    primary_artifact = _load_json_metrics_artifact(primary_path)
    primary_status = str(primary_artifact.get("status", "missing"))
    if primary_status == "ok":
        primary_artifact["source"] = "primary"
        return primary_artifact
    if primary_status != "missing":
        primary_artifact["source"] = "primary"
        return primary_artifact
    if fallback_path is None:
        primary_artifact["source"] = "primary"
        return primary_artifact

    fallback_artifact = _load_json_metrics_artifact(fallback_path)
    fallback_artifact["source"] = "fallback"
    if str(fallback_artifact.get("status", "missing")) == "ok":
        fallback_artifact["status"] = "ok_fallback"
        fallback_artifact["fallback_from"] = str(primary_path.expanduser().resolve())
    return fallback_artifact


def _extract_profile_rows(
    profile_metrics: Mapping[str, Any], profile_key: str
) -> List[Mapping[str, Any]]:
    profiles = profile_metrics.get("profiles")
    if not isinstance(profiles, Mapping):
        return []
    profile_payload = profiles.get(profile_key)
    if not isinstance(profile_payload, Mapping):
        return []
    rows = profile_payload.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _extract_vec_isolation_rows(vec_isolation_metrics: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    runs = vec_isolation_metrics.get("runs")
    if not isinstance(runs, list):
        return []
    rows: List[Mapping[str, Any]] = []
    for run_payload in runs:
        if not isinstance(run_payload, Mapping):
            continue

        direct_rows = run_payload.get("rows")
        if isinstance(direct_rows, list):
            rows.extend(row for row in direct_rows if isinstance(row, Mapping))

        repeats = run_payload.get("repeats")
        if not isinstance(repeats, list):
            continue
        for repeat_payload in repeats:
            if not isinstance(repeat_payload, Mapping):
                continue
            repeat_rows = repeat_payload.get("rows")
            if not isinstance(repeat_rows, list):
                continue
            rows.extend(row for row in repeat_rows if isinstance(row, Mapping))
    return rows


def _read_optional_float(row: Mapping[str, Any], keys: Sequence[str]) -> Optional[float]:
    for key in keys:
        if key not in row:
            continue
        raw = row.get(key)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return None


def _read_optional_delta(
    row: Mapping[str, Any],
    *,
    delta_key: str,
    baseline_keys: Sequence[str],
    candidate_keys: Sequence[str],
) -> Optional[float]:
    direct_delta = _read_optional_float(row, [delta_key])
    if direct_delta is not None:
        return round(direct_delta, 6)

    baseline = _read_optional_float(row, baseline_keys)
    candidate = _read_optional_float(row, candidate_keys)
    if baseline is None or candidate is None:
        return None
    return round(candidate - baseline, 6)


def _parse_optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _normalize_vec_isolation_row(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    dataset = str(row.get("dataset", "")).strip()
    if not dataset:
        return None

    b_p95 = _read_optional_float(row, ("b_p95", "b_p95_ms", "legacy_p95_ms"))
    c_p95 = _read_optional_float(row, ("c_p95", "c_p95_ms", "vec_candidate_p95_ms"))
    if b_p95 is None or c_p95 is None or b_p95 <= 0.0:
        return None

    latency_ratio = _read_optional_float(row, ("latency_improvement_ratio",))
    if latency_ratio is None:
        latency_ratio = (b_p95 - c_p95) / b_p95

    ndcg_delta = _read_optional_delta(
        row,
        delta_key="ndcg_delta",
        baseline_keys=("b_ndcg_at_10", "legacy_ndcg_at_10"),
        candidate_keys=("c_ndcg_at_10", "vec_candidate_ndcg_at_10"),
    )
    recall_delta = _read_optional_delta(
        row,
        delta_key="recall_delta",
        baseline_keys=("b_recall_at_10", "legacy_recall_at_10"),
        candidate_keys=("c_recall_at_10", "vec_candidate_recall_at_10"),
    )
    if ndcg_delta is None or recall_delta is None:
        return None

    raw_reasons = row.get("c_degrade_reasons")
    if raw_reasons is None:
        raw_reasons = row.get("invalid_reasons")
    if raw_reasons is None:
        raw_reasons = []
    if not isinstance(raw_reasons, list):
        return None
    c_degrade_reasons = [str(item).strip() for item in raw_reasons if str(item).strip()]

    c_valid = _parse_optional_bool(row.get("c_valid"))
    if c_valid is None:
        c_valid = len(c_degrade_reasons) == 0

    return {
        "dataset": dataset,
        "dataset_label": str(row.get("dataset_label") or dataset),
        "b_p95": round(b_p95, 6),
        "c_p95": round(c_p95, 6),
        "latency_improvement_ratio": round(float(latency_ratio), 6),
        "ndcg_delta": round(float(ndcg_delta), 6),
        "recall_delta": round(float(recall_delta), 6),
        "c_degrade_reasons": c_degrade_reasons,
        "c_valid": bool(c_valid),
    }


def _estimate_reason_count(
    *,
    reason: str,
    reason_counts: Mapping[str, Any],
    reason_union: Sequence[str],
    degraded_queries: int,
    total_queries: int,
) -> int:
    if reason in reason_counts:
        return max(0, min(total_queries, _safe_int(reason_counts.get(reason), 0)))
    if reason in reason_union:
        if degraded_queries > 0:
            return max(0, min(total_queries, degraded_queries))
        return total_queries
    return 0


def _build_hold_gate_11_from_profile_metrics(
    profile_metrics: Mapping[str, Any],
) -> Dict[str, Any]:
    rows = _extract_profile_rows(profile_metrics, "profile_cd")
    if not rows:
        return {
            "status": "missing_profile_cd_rows",
            "thresholds": {
                "embedding_success_rate_min": _HOLD11_EMBEDDING_SUCCESS_THRESHOLD,
                "embedding_fallback_hash_rate_max": _HOLD11_FALLBACK_HASH_RATE_THRESHOLD,
                "search_degraded_rate_max": _HOLD11_DEGRADED_RATE_THRESHOLD,
            },
            "overall_pass": False,
        }

    total_queries = 0
    degraded_queries = 0
    embedding_request_failed_queries = 0
    embedding_fallback_hash_queries = 0
    count_source = "estimated_from_degrade_union"

    for row in rows:
        degradation = row.get("degradation")
        if not isinstance(degradation, Mapping):
            continue
        row_queries = max(
            0,
            _safe_int(degradation.get("queries"), _safe_int(row.get("query_count"), 0)),
        )
        row_degraded = max(0, _safe_int(degradation.get("degraded"), 0))
        reason_union = [
            str(item).strip()
            for item in (degradation.get("degrade_reasons") or [])
            if str(item).strip()
        ]
        raw_reason_counts = degradation.get("degrade_reason_counts")
        if isinstance(raw_reason_counts, Mapping):
            row_reason_counts = {
                str(key): _safe_int(value, 0)
                for key, value in raw_reason_counts.items()
                if str(key).strip()
            }
            count_source = "degrade_reason_counts"
        else:
            row_reason_counts = {}

        total_queries += row_queries
        degraded_queries += row_degraded
        embedding_request_failed_queries += _estimate_reason_count(
            reason="embedding_request_failed",
            reason_counts=row_reason_counts,
            reason_union=reason_union,
            degraded_queries=row_degraded,
            total_queries=row_queries,
        )
        embedding_fallback_hash_queries += _estimate_reason_count(
            reason="embedding_fallback_hash",
            reason_counts=row_reason_counts,
            reason_union=reason_union,
            degraded_queries=row_degraded,
            total_queries=row_queries,
        )

    if total_queries <= 0:
        return {
            "status": "invalid_query_count",
            "thresholds": {
                "embedding_success_rate_min": _HOLD11_EMBEDDING_SUCCESS_THRESHOLD,
                "embedding_fallback_hash_rate_max": _HOLD11_FALLBACK_HASH_RATE_THRESHOLD,
                "search_degraded_rate_max": _HOLD11_DEGRADED_RATE_THRESHOLD,
            },
            "overall_pass": False,
        }

    embedding_success_rate = _safe_rate(
        float(total_queries - embedding_request_failed_queries),
        float(total_queries),
    )
    embedding_fallback_hash_rate = _safe_rate(
        float(embedding_fallback_hash_queries),
        float(total_queries),
    )
    search_degraded_rate = _safe_rate(float(degraded_queries), float(total_queries))

    checks = {
        "embedding_success_rate": embedding_success_rate >= _HOLD11_EMBEDDING_SUCCESS_THRESHOLD,
        "embedding_fallback_hash_rate": embedding_fallback_hash_rate <= _HOLD11_FALLBACK_HASH_RATE_THRESHOLD,
        "search_degraded_rate": search_degraded_rate <= _HOLD11_DEGRADED_RATE_THRESHOLD,
    }

    return {
        "status": "ok",
        "query_scope": "profile_cd",
        "query_count": total_queries,
        "degraded_queries": degraded_queries,
        "embedding_request_failed_queries": embedding_request_failed_queries,
        "embedding_fallback_hash_queries": embedding_fallback_hash_queries,
        "embedding_success_rate": embedding_success_rate,
        "embedding_fallback_hash_rate": embedding_fallback_hash_rate,
        "search_degraded_rate": search_degraded_rate,
        "count_source": count_source,
        "thresholds": {
            "embedding_success_rate_min": _HOLD11_EMBEDDING_SUCCESS_THRESHOLD,
            "embedding_fallback_hash_rate_max": _HOLD11_FALLBACK_HASH_RATE_THRESHOLD,
            "search_degraded_rate_max": _HOLD11_DEGRADED_RATE_THRESHOLD,
        },
        "checks": checks,
        "overall_pass": all(checks.values()),
    }


def _build_hold_gate_12_from_vec_isolation_metrics(
    vec_isolation_artifact: Mapping[str, Any],
    sqlite_vec_probe: Mapping[str, Any],
) -> Dict[str, Any]:
    thresholds = {
        "latency_improvement_ratio_min": _HOLD12_LATENCY_IMPROVEMENT_THRESHOLD,
        "ndcg_delta_min": _HOLD12_QUALITY_DELTA_THRESHOLD,
        "recall_delta_min": _HOLD12_QUALITY_DELTA_THRESHOLD,
    }
    sqlite_status = str(sqlite_vec_probe.get("status", ""))
    extension_loaded = bool(sqlite_vec_probe.get("extension_loaded"))
    extension_ready = sqlite_status == "ok" and extension_loaded
    rollback_ready = sqlite_status != "sqlite_runtime_error"

    artifact_status = str(vec_isolation_artifact.get("status", "missing"))
    artifact_source = str(vec_isolation_artifact.get("source", "primary"))
    artifact_path = _sanitize_report_path(str(vec_isolation_artifact.get("path", "")))
    usable_artifact_statuses = {"ok", "ok_fallback"}

    def _fail_closed(status: str) -> Dict[str, Any]:
        checks = {
            "extension_ready": extension_ready,
            "no_new_500_proxy": False,
            "latency_improvement_gate": False,
            "quality_non_regression_gate": False,
            "rollback_ready": rollback_ready,
        }
        return {
            "status": status,
            "vec_isolation_status": artifact_status,
            "vec_isolation_source": artifact_source,
            "vec_isolation_path": artifact_path,
            "thresholds": thresholds,
            "row_count": 0,
            "latency_improvement_ratio_mean": 0.0,
            "invalid_reasons": [],
            "rows": [],
            "checks": checks,
            "overall_pass": False,
        }

    if artifact_status not in usable_artifact_statuses:
        return _fail_closed(f"vec_isolation_artifact_{artifact_status}")

    vec_payload = vec_isolation_artifact.get("payload")
    if not isinstance(vec_payload, Mapping):
        return _fail_closed("invalid_vec_isolation_payload")

    raw_rows = _extract_vec_isolation_rows(vec_payload)
    if not raw_rows:
        return _fail_closed("invalid_vec_isolation_rows")

    rows: List[Dict[str, Any]] = []
    latency_checks: List[bool] = []
    quality_checks: List[bool] = []
    no_new_500_checks: List[bool] = []
    latency_ratios: List[float] = []
    invalid_reason_union: set[str] = set()

    for raw_row in raw_rows:
        normalized_row = _normalize_vec_isolation_row(raw_row)
        if normalized_row is None:
            return _fail_closed("invalid_vec_isolation_row")

        latency_ratio = float(normalized_row["latency_improvement_ratio"])
        ndcg_delta = float(normalized_row["ndcg_delta"])
        recall_delta = float(normalized_row["recall_delta"])
        c_degrade_reasons = list(normalized_row["c_degrade_reasons"])
        c_valid = bool(normalized_row["c_valid"])

        latency_pass = latency_ratio >= _HOLD12_LATENCY_IMPROVEMENT_THRESHOLD
        quality_pass = (
            ndcg_delta >= _HOLD12_QUALITY_DELTA_THRESHOLD
            and recall_delta >= _HOLD12_QUALITY_DELTA_THRESHOLD
        )
        no_new_500_pass = c_valid and len(c_degrade_reasons) == 0

        latency_checks.append(latency_pass)
        quality_checks.append(quality_pass)
        no_new_500_checks.append(no_new_500_pass)
        latency_ratios.append(latency_ratio)
        invalid_reason_union.update(c_degrade_reasons)
        rows.append(
            {
                **normalized_row,
                "latency_pass": latency_pass,
                "quality_pass": quality_pass,
            }
        )

    checks = {
        "extension_ready": extension_ready,
        "no_new_500_proxy": bool(no_new_500_checks) and all(no_new_500_checks),
        "latency_improvement_gate": bool(latency_checks) and all(latency_checks),
        "quality_non_regression_gate": bool(quality_checks) and all(quality_checks),
        "rollback_ready": rollback_ready,
    }

    return {
        "status": "ok",
        "vec_isolation_status": artifact_status,
        "vec_isolation_source": artifact_source,
        "vec_isolation_path": artifact_path,
        "thresholds": thresholds,
        "row_count": len(rows),
        "latency_improvement_ratio_mean": round(
            sum(latency_ratios) / float(len(latency_ratios)), 6
        )
        if latency_ratios
        else 0.0,
        "invalid_reasons": sorted(invalid_reason_union),
        "rows": rows,
        "checks": checks,
        "overall_pass": all(checks.values()),
    }


def _build_hold_gate_13_from_wal_probe(wal_probe: Mapping[str, Any]) -> Dict[str, Any]:
    wal_results = wal_probe.get("results")
    wal_metrics = wal_results.get("wal", {}) if isinstance(wal_results, Mapping) else {}
    wal_summary = wal_probe.get("summary", {}).get("wal", {})
    failed_tx = _safe_int(wal_metrics.get("failed_tx"), 0)
    failure_rate = _safe_float(wal_metrics.get("failure_rate"), 0.0)
    persistence_gap = _safe_int(wal_metrics.get("persistence_gap"), 0)
    retry_rate_p95 = _safe_float(
        wal_summary.get("retry_rate_p95", wal_metrics.get("retry_rate", 0.0)),
        0.0,
    )
    throughput_ratio = _safe_float(wal_probe.get("wal_vs_delete_throughput_ratio"), 0.0)

    checks = {
        "wal_failed_tx": failed_tx == 0,
        "wal_failure_rate": failure_rate <= _HOLD13_FAILURE_RATE_THRESHOLD,
        "persistence_gap": persistence_gap == 0,
        "retry_rate_p95": retry_rate_p95 <= _HOLD13_RETRY_RATE_P95_THRESHOLD,
        "throughput_ratio": throughput_ratio >= _HOLD13_THROUGHPUT_RATIO_THRESHOLD,
    }
    return {
        "status": "ok",
        "thresholds": {
            "wal_failed_tx_eq": 0,
            "wal_failure_rate_max": _HOLD13_FAILURE_RATE_THRESHOLD,
            "persistence_gap_eq": 0,
            "retry_rate_p95_max": _HOLD13_RETRY_RATE_P95_THRESHOLD,
            "throughput_ratio_min": _HOLD13_THROUGHPUT_RATIO_THRESHOLD,
        },
        "wal_failed_tx": failed_tx,
        "wal_failure_rate": failure_rate,
        "persistence_gap": persistence_gap,
        "retry_rate_p95": retry_rate_p95,
        "wal_vs_delete_tps_ratio": throughput_ratio,
        "checks": checks,
        "overall_pass": all(checks.values()),
    }


def _build_hold_gate_snapshot(
    *,
    profile_metrics_artifact: Mapping[str, Any],
    vec_isolation_artifact: Mapping[str, Any],
    sqlite_vec_probe: Mapping[str, Any],
    wal_probe: Mapping[str, Any],
) -> Dict[str, Any]:
    profile_source_status = str(profile_metrics_artifact.get("status", "missing"))
    profile_source_path = _sanitize_report_path(str(profile_metrics_artifact.get("path", "")))
    profile_metrics_payload = profile_metrics_artifact.get("payload")
    profile_metrics = (
        profile_metrics_payload
        if isinstance(profile_metrics_payload, Mapping)
        else {}
    )
    vec_source_status = str(vec_isolation_artifact.get("status", "missing"))
    vec_source_path = _sanitize_report_path(str(vec_isolation_artifact.get("path", "")))
    vec_source = str(vec_isolation_artifact.get("source", "primary"))
    return {
        "generated_at_utc": _utc_now_iso(),
        "source": {
            "profile_metrics_json": profile_source_path,
            "profile_metrics_status": profile_source_status,
            "vec_isolation_metrics_json": vec_source_path,
            "vec_isolation_metrics_status": vec_source_status,
            "vec_isolation_metrics_source": vec_source,
            "sqlite_vec_probe_source": "phase_d_spike_runtime",
            "wal_probe_source": "phase_d_spike_runtime",
        },
        "gate_11": _build_hold_gate_11_from_profile_metrics(profile_metrics),
        "gate_12": _build_hold_gate_12_from_vec_isolation_metrics(
            vec_isolation_artifact, sqlite_vec_probe
        ),
        "gate_13": _build_hold_gate_13_from_wal_probe(wal_probe),
    }


def _derive_risks(
    embedding_probe: Mapping[str, Any],
    sqlite_vec_probe: Mapping[str, Any],
    wal_probe: Mapping[str, Any],
) -> List[str]:
    risks: List[str] = []
    configured_case = embedding_probe.get("configured_case")
    if not isinstance(configured_case, Mapping):
        cases = embedding_probe.get("cases")
        if isinstance(cases, list):
            configured_backend = str(embedding_probe.get("configured_backend", ""))
            configured_case = next(
                (
                    item
                    for item in cases
                    if isinstance(item, Mapping)
                    and str(item.get("backend", "")) == configured_backend
                ),
                None,
            )

    if isinstance(configured_case, Mapping):
        case_status = str(configured_case.get("status", ""))
        backend = str(configured_case.get("backend", ""))
        if case_status == "missing_api_base":
            risks.append(
                f"Embedding backend '{backend}' requires API base but no source is configured."
            )

    sqlite_status = str(sqlite_vec_probe.get("status", ""))
    if sqlite_status == "skipped_no_extension_path":
        risks.append(
            "sqlite-vec extension path not provided; compatibility remains unverified (HOLD)."
        )
    elif sqlite_status == "invalid_extension_path":
        diag_code = str(sqlite_vec_probe.get("diag_code", "")).strip() or "unknown"
        risks.append(
            f"sqlite-vec extension path is invalid ({diag_code}); compatibility remains unverified."
        )
    elif sqlite_status == "extension_load_failed":
        risks.append("sqlite-vec extension failed to load; compatibility is not proven yet.")
    elif sqlite_status == "extension_loading_unavailable":
        risks.append("Current sqlite build cannot enable extension loading.")
    elif sqlite_status == "sqlite_runtime_error":
        risks.append("sqlite runtime probe failed unexpectedly.")

    wal_results = wal_probe.get("results", {})
    if isinstance(wal_results, Mapping):
        for mode in ("delete", "wal"):
            mode_metrics = wal_results.get(mode, {})
            if isinstance(mode_metrics, Mapping) and int(mode_metrics.get("failed_tx", 0)) > 0:
                risks.append(
                    f"Write lane probe in {mode.upper()} mode observed failed transactions."
                )
            if isinstance(mode_metrics, Mapping) and int(mode_metrics.get("persistence_gap", 0)) > 0:
                risks.append(
                    f"Write lane probe in {mode.upper()} mode observed non-zero persistence gap."
                )

    wal_gate = wal_probe.get("regression_gate", {})
    if isinstance(wal_gate, Mapping) and not bool(wal_gate.get("pass", True)):
        reasons = wal_gate.get("reasons", [])
        if isinstance(reasons, list) and reasons:
            risks.append(
                "WAL regression gate failed: " + "; ".join(str(item) for item in reasons)
            )
        else:
            risks.append("WAL regression gate failed without explicit reasons.")

    if not risks:
        risks.append("No blocker detected in current spike scope; keep feature flags default-off.")
    return risks


def _build_go_no_go(
    embedding_probe: Mapping[str, Any],
    sqlite_vec_probe: Mapping[str, Any],
    wal_probe: Mapping[str, Any],
    hold_gate: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    blockers: List[str] = []

    configured_backend = str(embedding_probe.get("configured_backend", "")).strip().lower()
    configured_case = None
    cases = embedding_probe.get("cases")
    if isinstance(cases, list):
        configured_case = next(
            (
                item
                for item in cases
                if isinstance(item, Mapping)
                and str(item.get("backend", "")).strip().lower() == configured_backend
            ),
            None,
        )
    if isinstance(configured_case, Mapping):
        if str(configured_case.get("status", "")) == "missing_api_base":
            blockers.append("embedding_config_missing")

    sqlite_status = str(sqlite_vec_probe.get("status", ""))
    if sqlite_status in {"sqlite_runtime_error"}:
        blockers.append("sqlite_runtime_error")

    wal_results = wal_probe.get("results", {})
    wal_failed = 0
    wal_effective_mode = ""
    if isinstance(wal_results, Mapping):
        wal_failed = int((wal_results.get("wal", {}) or {}).get("failed_tx", 0))
        wal_effective_mode = str(
            (wal_results.get("wal", {}) or {}).get("effective_journal_mode", "")
        ).strip().lower()
    if wal_effective_mode != "wal":
        blockers.append("wal_not_effective")

    wal_gate_present = False
    wal_gate_pass = True
    wal_gate = wal_probe.get("regression_gate", {})
    if isinstance(wal_gate, Mapping) and "pass" in wal_gate:
        wal_gate_present = True
        wal_gate_pass = bool(wal_gate.get("pass", True))
    if wal_gate_present and not wal_gate_pass:
        blockers.append("wal_regression_gate_failed")
    if wal_failed > 0:
        # Hold contract: WAL probe must not report failed transactions.
        blockers.append("wal_failed_transactions")

    if isinstance(hold_gate, Mapping):
        for gate_key in ("gate_11", "gate_12", "gate_13"):
            gate_payload = hold_gate.get(gate_key)
            if not isinstance(gate_payload, Mapping):
                continue
            if bool(gate_payload.get("overall_pass")) is False:
                blockers.append(f"{gate_key}_failed")

    if blockers:
        return {
            "decision": "NO_GO",
            "blockers": sorted(set(blockers)),
            "summary": "Spike produced blockers; keep HOLD and do not promote to default path.",
        }
    return {
        "decision": "GO",
        "blockers": [],
        "summary": "Spike baseline is executable. Continue with default-off feature flags.",
    }


def _default_rollback_points() -> List[str]:
    return [
        "Keep RETRIEVAL_EMBEDDING_BACKEND at hash/none when provider probe is not ready.",
        "Do not configure sqlite-vec extension path in production startup until compatibility is validated.",
        "If WAL write lane degrades consistency, switch journal mode back to DELETE.",
        "If browse read behavior must rollback, restore browse.get_node -> get_memory_by_path(... reinforce_access=True).",
    ]


def _render_phase_d_markdown(report: Mapping[str, Any]) -> str:
    probes = report.get("probes", {})
    embedding_probe = probes.get("embedding_provider", {})
    sqlite_probe = probes.get("sqlite_vec", {})
    wal_probe = probes.get("write_lane_wal", {})
    hold_gate = report.get("hold_gate", {})
    wal_results = wal_probe.get("results", {}) if isinstance(wal_probe, Mapping) else {}
    delete_metrics = wal_results.get("delete", {}) if isinstance(wal_results, Mapping) else {}
    wal_metrics = wal_results.get("wal", {}) if isinstance(wal_results, Mapping) else {}

    lines: List[str] = [
        "# Benchmark Results - phase_d_spike",
        "",
        f"> generated_at_utc: {report.get('generated_at_utc', '')}",
        "",
        "## Scope",
        "",
        f"- phase: {report.get('scope', {}).get('phase', 'D')}",
        "- focus: embedding provider routing / sqlite-vec compatibility / write lane WAL",
        f"- sqlite_vec_readiness: {sqlite_probe.get('sqlite_vec_readiness', 'hold')}",
        f"- wal_load_profile: {wal_probe.get('load_profile', 'small')}",
        f"- wal_repeat: {wal_probe.get('repeat', 1)}",
        "",
        "## Probe Status",
        "",
        "| Probe | Status |",
        "|---|---|",
        f"| embedding_provider | {embedding_probe.get('status', 'unknown')} |",
        f"| sqlite_vec | {sqlite_probe.get('status', 'unknown')} |",
        f"| write_lane_wal | {wal_probe.get('status', 'unknown')} |",
        "",
        "## Embedding Cases",
        "",
        "| Backend | API Base Source | API Base Present | Status |",
        "|---|---|---|---|",
    ]
    embedding_cases = embedding_probe.get("cases", [])
    if isinstance(embedding_cases, list):
        for case in embedding_cases:
            if not isinstance(case, Mapping):
                continue
            lines.append(
                "| {backend} | {source} | {present} | {status} |".format(
                    backend=case.get("backend", ""),
                    source=case.get("api_base_source", "unset"),
                    present="yes" if bool(case.get("api_base_present")) else "no",
                    status=case.get("status", ""),
                )
            )

    lines.extend(
        [
            "",
            "## Write Lane Throughput",
            "",
            "| Mode | Throughput (tx/s) | Success/Planned | Failed | Failure Rate | Retry Rate | Persistence Gap |",
            "|---|---:|---:|---:|---:|---:|---:|",
            "| DELETE | {delete_tp} | {delete_success}/{delete_plan} | {delete_failed} | {delete_failure_rate} | {delete_retry_rate} | {delete_gap} |".format(
                delete_tp=delete_metrics.get("throughput_tps", 0.0),
                delete_success=delete_metrics.get("successful_tx", 0),
                delete_plan=delete_metrics.get("planned_tx", 0),
                delete_failed=delete_metrics.get("failed_tx", 0),
                delete_failure_rate=delete_metrics.get("failure_rate", 0.0),
                delete_retry_rate=delete_metrics.get("retry_rate", 0.0),
                delete_gap=delete_metrics.get("persistence_gap", 0),
            ),
            "| WAL | {wal_tp} | {wal_success}/{wal_plan} | {wal_failed} | {wal_failure_rate} | {wal_retry_rate} | {wal_gap} |".format(
                wal_tp=wal_metrics.get("throughput_tps", 0.0),
                wal_success=wal_metrics.get("successful_tx", 0),
                wal_plan=wal_metrics.get("planned_tx", 0),
                wal_failed=wal_metrics.get("failed_tx", 0),
                wal_failure_rate=wal_metrics.get("failure_rate", 0.0),
                wal_retry_rate=wal_metrics.get("retry_rate", 0.0),
                wal_gap=wal_metrics.get("persistence_gap", 0),
            ),
            f"- wal_vs_delete_throughput_ratio: {wal_probe.get('wal_vs_delete_throughput_ratio', 'n/a')}",
            f"- wal_regression_gate_pass: {wal_probe.get('regression_gate', {}).get('pass', False)}",
            "",
            "## WAL Threshold Suggestion",
            "",
            "- profile_baseline: {baseline}".format(
                baseline=wal_probe.get("threshold_suggestion", {}).get(
                    "profile_baseline", {}
                )
            ),
            "- suggested_stable_thresholds: {suggested}".format(
                suggested=wal_probe.get("threshold_suggestion", {}).get(
                    "suggested_stable_thresholds", {}
                )
            ),
            "",
            "## HOLD Gate Snapshot (#11/#12/#13)",
            "",
            f"- source_profile_metrics: {(hold_gate.get('source') or {}).get('profile_metrics_json', '')}",
            f"- source_profile_metrics_status: {(hold_gate.get('source') or {}).get('profile_metrics_status', 'missing')}",
            f"- source_vec_isolation_metrics: {(hold_gate.get('source') or {}).get('vec_isolation_metrics_json', '')}",
            f"- source_vec_isolation_metrics_status: {(hold_gate.get('source') or {}).get('vec_isolation_metrics_status', 'missing')}",
            f"- source_vec_isolation_metrics_source: {(hold_gate.get('source') or {}).get('vec_isolation_metrics_source', 'primary')}",
            (
                "- #11: embedding_success_rate={success}, embedding_fallback_hash_rate={fallback}, "
                "search_degraded_rate={degraded}, overall_pass={passed}"
            ).format(
                success=(hold_gate.get("gate_11") or {}).get("embedding_success_rate", "n/a"),
                fallback=(hold_gate.get("gate_11") or {}).get("embedding_fallback_hash_rate", "n/a"),
                degraded=(hold_gate.get("gate_11") or {}).get("search_degraded_rate", "n/a"),
                passed=(hold_gate.get("gate_11") or {}).get("overall_pass", False),
            ),
            (
                "- #12: extension_ready={ext}, latency_gate={lat}, quality_gate={quality}, "
                "no_new_500_proxy={no500}, overall_pass={passed}"
            ).format(
                ext=((hold_gate.get("gate_12") or {}).get("checks") or {}).get("extension_ready", False),
                lat=((hold_gate.get("gate_12") or {}).get("checks") or {}).get("latency_improvement_gate", False),
                quality=((hold_gate.get("gate_12") or {}).get("checks") or {}).get("quality_non_regression_gate", False),
                no500=((hold_gate.get("gate_12") or {}).get("checks") or {}).get("no_new_500_proxy", False),
                passed=(hold_gate.get("gate_12") or {}).get("overall_pass", False),
            ),
            (
                "- #13: wal_failed_tx={failed}, wal_failure_rate={failure}, retry_rate_p95={retry}, "
                "persistence_gap={gap}, wal_vs_delete_tps_ratio={ratio}, overall_pass={passed}"
            ).format(
                failed=(hold_gate.get("gate_13") or {}).get("wal_failed_tx", "n/a"),
                failure=(hold_gate.get("gate_13") or {}).get("wal_failure_rate", "n/a"),
                retry=(hold_gate.get("gate_13") or {}).get("retry_rate_p95", "n/a"),
                gap=(hold_gate.get("gate_13") or {}).get("persistence_gap", "n/a"),
                ratio=(hold_gate.get("gate_13") or {}).get("wal_vs_delete_tps_ratio", "n/a"),
                passed=(hold_gate.get("gate_13") or {}).get("overall_pass", False),
            ),
            "",
            "## Go/No-Go",
            "",
            f"- decision: {report.get('go_no_go', {}).get('decision', 'HOLD')}",
            f"- summary: {report.get('go_no_go', {}).get('summary', '')}",
            "",
            "## Risks",
            "",
        ]
    )
    risks = report.get("risks", [])
    if isinstance(risks, list):
        for risk in risks:
            lines.append(f"- {risk}")

    lines.extend(["", "## Rollback Points", ""])
    rollback_points = report.get("rollback_points", [])
    if isinstance(rollback_points, list):
        for point in rollback_points:
            lines.append(f"- {point}")
    lines.append("")
    return "\n".join(lines)


def _write_report_artifacts(
    report: Mapping[str, Any],
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_render_phase_d_markdown(report), encoding="utf-8")


def build_phase_d_report(
    *,
    sqlite_vec_extension_path: Optional[str] = None,
    workers: Optional[int] = None,
    tx_per_worker: Optional[int] = None,
    timeout_sec: Optional[float] = None,
    wal_load_profile: str = "small",
    wal_repeat: int = 1,
    wal_min_throughput_ratio: Optional[float] = None,
    wal_max_failure_rate: Optional[float] = None,
    wal_max_retry_rate: Optional[float] = None,
    wal_max_persistence_gap: Optional[int] = None,
    output_json_path: Union[str, Path] = DEFAULT_JSON_OUTPUT,
    output_markdown_path: Union[str, Path] = DEFAULT_MARKDOWN_OUTPUT,
    write_artifacts: bool = True,
) -> Dict[str, Any]:
    """Build full Phase D spike report and optionally persist JSON/Markdown."""
    embedding_probe = run_embedding_provider_probe()
    sqlite_vec_probe = run_sqlite_vec_probe(sqlite_vec_extension_path)
    wal_probe = run_write_lane_wal_probe(
        workers=workers,
        tx_per_worker=tx_per_worker,
        timeout_sec=timeout_sec,
        load_profile=wal_load_profile,
        repeat=wal_repeat,
        min_throughput_ratio=wal_min_throughput_ratio,
        max_failure_rate=wal_max_failure_rate,
        max_retry_rate=wal_max_retry_rate,
        max_persistence_gap=wal_max_persistence_gap,
    )
    profile_metrics_artifact = _load_profile_ab_metrics_artifact(
        PROFILE_AB_JSON_ARTIFACT
    )
    vec_isolation_artifact = _load_vec_isolation_metrics_artifact(
        PROFILE_VEC_ISOLATION_JSON_ARTIFACT_V2,
        fallback_path=PROFILE_VEC_ISOLATION_JSON_ARTIFACT,
    )
    hold_gate = _build_hold_gate_snapshot(
        profile_metrics_artifact=profile_metrics_artifact,
        vec_isolation_artifact=vec_isolation_artifact,
        sqlite_vec_probe=sqlite_vec_probe,
        wal_probe=wal_probe,
    )

    go_no_go = _build_go_no_go(
        embedding_probe=embedding_probe,
        sqlite_vec_probe=sqlite_vec_probe,
        wal_probe=wal_probe,
        hold_gate=hold_gate,
    )
    risks = _derive_risks(
        embedding_probe=embedding_probe,
        sqlite_vec_probe=sqlite_vec_probe,
        wal_probe=wal_probe,
    )
    sqlite_vec_probe_report = dict(sqlite_vec_probe)
    if "extension_path_input" in sqlite_vec_probe_report:
        sqlite_vec_probe_report["extension_path_input"] = _sanitize_report_path(
            str(sqlite_vec_probe_report.get("extension_path_input", ""))
        )
    if "extension_path" in sqlite_vec_probe_report:
        sqlite_vec_probe_report["extension_path"] = _sanitize_report_path(
            str(sqlite_vec_probe_report.get("extension_path", ""))
        )
    report: Dict[str, Any] = {
        "generated_at_utc": _utc_now_iso(),
        "scope": {
            "phase": "D",
            "description": "Phase D spike for provider/sqlite-vec/write-lane feasibility",
            "wal_probe": {
                "load_profile": wal_probe.get("load_profile", "small"),
                "repeat": wal_probe.get("repeat", 1),
                "workers": wal_probe.get("workers", 0),
                "tx_per_worker": wal_probe.get("tx_per_worker", 0),
                "timeout_sec": wal_probe.get("timeout_sec", 0.0),
                "regression_thresholds": wal_probe.get("regression_thresholds", {}),
            },
            "sqlite_vec_extension_path": _sanitize_report_path(
                sqlite_vec_extension_path or ""
            ),
        },
        "probes": {
            "embedding_provider": embedding_probe,
            "sqlite_vec": sqlite_vec_probe_report,
            "write_lane_wal": wal_probe,
        },
        "hold_gate": hold_gate,
        "go_no_go": go_no_go,
        "risks": risks,
        "rollback_points": _default_rollback_points(),
    }

    if write_artifacts:
        json_path = Path(output_json_path).expanduser().resolve()
        markdown_path = Path(output_markdown_path).expanduser().resolve()
        _write_report_artifacts(report, json_path=json_path, markdown_path=markdown_path)
        report["artifacts"] = {
            "json": str(json_path),
            "markdown": str(markdown_path),
        }
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Phase D spike probes and emit report artifacts."
    )
    parser.add_argument(
        "--sqlite-vec-extension-path",
        type=str,
        default="",
        help="Optional sqlite-vec extension file path for load test.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Concurrent workers for WAL probe. Defaults from --wal-load-profile.",
    )
    parser.add_argument(
        "--tx-per-worker",
        type=int,
        default=None,
        help="Transactions per worker for WAL probe. Defaults from --wal-load-profile.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=None,
        help="SQLite lock timeout in seconds for WAL probe. Defaults from --wal-load-profile.",
    )
    parser.add_argument(
        "--wal-load-profile",
        type=str,
        choices=sorted(_WAL_PROFILE_DEFAULTS.keys()),
        default="small",
        help="WAL probe load profile. Default: small",
    )
    parser.add_argument(
        "--wal-repeat",
        type=int,
        default=1,
        help="How many rounds to repeat per journal mode. Default: 1",
    )
    parser.add_argument(
        "--wal-min-throughput-ratio",
        type=float,
        default=None,
        help="Optional minimum WAL/DELETE throughput ratio threshold.",
    )
    parser.add_argument(
        "--wal-max-failure-rate",
        type=float,
        default=None,
        help="Maximum allowed failed_tx/planned_tx for WAL regression gate. Default from profile.",
    )
    parser.add_argument(
        "--wal-max-retry-rate",
        type=float,
        default=None,
        help="Maximum allowed lock_retries/planned_tx for WAL regression gate. Default from profile.",
    )
    parser.add_argument(
        "--wal-max-persistence-gap",
        type=int,
        default=None,
        help="Maximum allowed successful_tx-rows_persisted for regression gate. Default from profile.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_JSON_OUTPUT,
        help=f"JSON output path. Default: {DEFAULT_JSON_OUTPUT}",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_MARKDOWN_OUTPUT,
        help=f"Markdown output path. Default: {DEFAULT_MARKDOWN_OUTPUT}",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Build report only without writing files.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = build_phase_d_report(
        sqlite_vec_extension_path=args.sqlite_vec_extension_path or None,
        workers=args.workers,
        tx_per_worker=args.tx_per_worker,
        timeout_sec=args.timeout_sec,
        wal_load_profile=args.wal_load_profile,
        wal_repeat=args.wal_repeat,
        wal_min_throughput_ratio=args.wal_min_throughput_ratio,
        wal_max_failure_rate=args.wal_max_failure_rate,
        wal_max_retry_rate=args.wal_max_retry_rate,
        wal_max_persistence_gap=args.wal_max_persistence_gap,
        output_json_path=args.output_json,
        output_markdown_path=args.output_md,
        write_artifacts=not bool(args.no_write),
    )
    print(f"[phase-d] decision: {report['go_no_go']['decision']}")
    if "artifacts" in report:
        print(f"[phase-d] json: {report['artifacts']['json']}")
        print(f"[phase-d] markdown: {report['artifacts']['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
