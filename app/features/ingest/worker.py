"""Durable local ingestion worker.

Uploads are persisted as `queued` result files and processed by a bounded local
worker pool. A restart requeues jobs that were mid-parse. No external queue
service is required for this single-user application.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.features.ingest import service, storage

LOGGER = logging.getLogger(__name__)

_LOCK = threading.RLock()
_STOP = threading.Event()
_STARTED = False
_PAUSED = False
_THREADS: List[threading.Thread] = []
_IN_FLIGHT: set[str] = set()
_RECOVERED = 0

_PROCESSING_STATES = ("processing", "parsing", "checking", "ocr")


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(high, value))


def worker_count() -> int:
    return _int_env("RAGKB_INGEST_WORKERS", 2, 1, 8)


def max_attempts() -> int:
    return _int_env("RAGKB_INGEST_MAX_ATTEMPTS", 3, 1, 10)


def retry_backoff_seconds() -> int:
    return _int_env("RAGKB_INGEST_RETRY_BACKOFF_SECONDS", 5, 1, 300)


def _moment(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _due(result: Dict[str, Any]) -> bool:
    moment = _moment(result.get("next_attempt_at"))
    return moment is None or moment <= datetime.now(timezone.utc)


def recover() -> int:
    """Requeue jobs that were being parsed when the previous process stopped."""
    global _RECOVERED
    settings = service.get_ingest_settings()
    recovered = 0
    now = storage.utc_now()
    for item in storage.list_results(settings):
        state = str(item.get("state") or "")
        file_id = str(item.get("file_id") or "")
        if not file_id:
            continue
        if state == "queued" and int(item.get("attempts") or 0) >= max_attempts():
            item["state"] = "failed"
            item["detail"] = "超过最大重试次数"
            item["updated_at"] = now
            storage.write_result(settings, file_id, item)
            continue
        if state not in _PROCESSING_STATES:
            continue
        item["state"] = "queued"
        item["detail"] = "服务重启后重新排队"
        item["next_attempt_at"] = ""
        item["updated_at"] = now
        item["timeline"] = list(item.get("timeline") or []) + [{
            "step": "recover",
            "state": "queued",
            "at": now,
            "detail": "Previous parsing process stopped",
        }]
        storage.write_result(settings, file_id, item)
        recovered += 1
    _RECOVERED = recovered
    return recovered


def _claim_one() -> Optional[Dict[str, Any]]:
    if _PAUSED:
        return None
    settings = service.get_ingest_settings()
    with _LOCK:
        candidates = [
            item for item in storage.list_results(settings)
            if str(item.get("state") or "") == "queued"
            and int(item.get("attempts") or 0) < max_attempts()
            and _due(item)
            and str(item.get("file_id") or "") not in _IN_FLIGHT
        ]
        candidates.sort(key=lambda item: str(item.get("queued_at") or item.get("created_at") or ""))
        for candidate in candidates:
            file_id = str(candidate.get("file_id") or "")
            fresh = storage.read_result(settings, file_id)
            if fresh is None or str(fresh.get("state") or "") != "queued":
                continue
            attempts = int(fresh.get("attempts") or 0) + 1
            now = storage.utc_now()
            fresh["state"] = "processing"
            fresh["detail"] = "正在本地解析"
            fresh["attempts"] = attempts
            fresh["started_at"] = now
            fresh["updated_at"] = now
            fresh["timeline"] = list(fresh.get("timeline") or []) + [{
                "step": "queue",
                "state": "processing",
                "at": now,
                "detail": f"Worker attempt {attempts}/{max_attempts()}",
            }]
            storage.write_result(settings, file_id, fresh)
            _IN_FLIGHT.add(file_id)
            return fresh
    return None


def _handle_exception(settings, file_id: str, exc: Exception) -> None:
    with _LOCK:
        result = storage.read_result(settings, file_id)
        if result is None:
            return
        attempts = int(result.get("attempts") or 1)
        limit = max_attempts()
        now = storage.utc_now()
        if attempts >= limit:
            result["state"] = "failed"
            result["detail"] = f"处理异常（已重试 {attempts} 次）：{type(exc).__name__}"
            result["next_attempt_at"] = ""
        else:
            delay = retry_backoff_seconds() * (2 ** max(0, attempts - 1))
            next_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            result["state"] = "queued"
            result["detail"] = f"处理异常，{delay} 秒后重试（{attempts}/{limit}）"
            result["next_attempt_at"] = next_at.isoformat()
        result["updated_at"] = now
        result["timeline"] = list(result.get("timeline") or []) + [{
            "step": "queue",
            "state": result["state"],
            "at": now,
            "detail": type(exc).__name__,
        }]
        storage.write_result(settings, file_id, result)


def process_claimed(result: Dict[str, Any]) -> None:
    file_id = str(result.get("file_id") or "")
    settings = service.get_ingest_settings()
    try:
        service._run_pipeline(
            settings,
            result,
            allow_session_auto=bool(result.get("allow_session_auto", True)),
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Ingestion job failed: %s", file_id)
        _handle_exception(settings, file_id, exc)
    finally:
        with _LOCK:
            _IN_FLIGHT.discard(file_id)


def run_once() -> bool:
    """Claim and process one queued job synchronously (used by tests)."""
    claimed = _claim_one()
    if claimed is None:
        return False
    process_claimed(claimed)
    return True


def _worker_loop() -> None:
    while not _STOP.is_set():
        if _PAUSED:
            _STOP.wait(0.5)
            continue
        try:
            claimed = _claim_one()
        except Exception:
            LOGGER.exception("Ingestion worker claim failed")
            _STOP.wait(1.0)
            continue
        if claimed is None:
            _STOP.wait(0.5)
            continue
        process_claimed(claimed)


def start() -> None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STOP.clear()
        try:
            recover()
        except Exception:
            LOGGER.exception("Ingestion queue recovery failed; starting worker anyway")
        _THREADS[:] = [
            threading.Thread(
                target=_worker_loop,
                name=f"ragkb-ingest-{index + 1}",
                daemon=True,
            )
            for index in range(worker_count())
        ]
        _STARTED = True
        for thread in _THREADS:
            thread.start()


def stop() -> None:
    global _STARTED
    _STOP.set()
    for thread in list(_THREADS):
        if thread.is_alive():
            thread.join(timeout=2)
    with _LOCK:
        _THREADS.clear()
        _IN_FLIGHT.clear()
        _STARTED = False


def set_paused(value: bool) -> Dict[str, Any]:
    global _PAUSED
    _PAUSED = bool(value)
    return status()


def is_paused() -> bool:
    return _PAUSED


def status() -> Dict[str, Any]:
    settings = service.get_ingest_settings()
    queued = 0
    processing = 0
    for item in storage.list_results(settings):
        state = str(item.get("state") or "")
        if state == "queued":
            queued += 1
        elif state in _PROCESSING_STATES:
            processing += 1
    with _LOCK:
        return {
            "paused": _PAUSED,
            "workers": worker_count(),
            "max_attempts": max_attempts(),
            "queued": queued,
            "processing": processing,
            "in_flight": len(_IN_FLIGHT),
            "recovered": _RECOVERED,
        }


__all__ = [
    "is_paused",
    "max_attempts",
    "process_claimed",
    "recover",
    "run_once",
    "set_paused",
    "start",
    "status",
    "stop",
    "worker_count",
]
