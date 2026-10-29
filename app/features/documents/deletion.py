"""Controlled local-only batch deletion with progress reporting."""

from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.adapters.lightrag.errors import KnowledgeEngineNotInstalled, KnowledgeHTTPError, KnowledgeUnavailable
from app.features.ingest import storage
from app.features.ingest.config import IngestSettings, get_ingest_settings
from app.features.knowledge.contracts import KnowledgeGateway
from app.features.knowledge.service import get_knowledge_gateway
from app.platform.paths import DATA_DIR

_MAX_ITEMS = 200
_MAX_JOBS = 50
_LOCK = threading.RLock()
_JOBS: Dict[str, Dict[str, Any]] = {}


class DeleteValidationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _snapshot(job: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(job)


def _publish(job_id: str, **changes: Any) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        job.update(changes)
        job["updated_at"] = _now()


def _trim_jobs() -> None:
    if len(_JOBS) <= _MAX_JOBS:
        return
    completed = [job for job in _JOBS.values() if job.get("status") not in ("queued", "running")]
    for job in sorted(completed, key=lambda item: item.get("updated_at", ""))[: len(_JOBS) - _MAX_JOBS]:
        _JOBS.pop(job["id"], None)


def _safe_app_copy_path(value: str, settings: IngestSettings) -> Optional[Path]:
    if not value:
        return None
    try:
        candidate = storage.resolve_display_path(value).resolve()
    except Exception:
        return None
    roots = (settings.upload_dir.resolve(), (DATA_DIR / "originals").resolve())
    if not any(candidate.is_relative_to(root) for root in roots):
        return None
    return candidate if candidate.is_file() else None


def _delete_knowledge(
    gateway: KnowledgeGateway,
    doc_id: str,
    *,
    delete_file: bool,
) -> Dict[str, Any]:
    if not doc_id:
        return {"ok": False, "message": "Knowledge document id is missing"}
    try:
        response = gateway.delete_document(
            doc_id,
            delete_file=delete_file,
            delete_llm_cache=False,
        )
    except KnowledgeEngineNotInstalled as exc:
        return {"ok": False, "message": str(exc) or "Knowledge engine is not installed"}
    except KnowledgeUnavailable as exc:
        return {"ok": False, "message": f"Knowledge service unavailable: {type(exc).__name__}"}
    except KnowledgeHTTPError as exc:
        if exc.status_code == 404:
            return {"ok": True, "message": "Document was already absent", "already_absent": True}
        return {"ok": False, "message": str(exc)}
    status = str(response.get("status") or "").lower()
    if status in ("busy", "not_allowed"):
        return {"ok": False, "message": str(response.get("message") or status)}
    return {
        "ok": True,
        "message": str(response.get("message") or "Knowledge deletion submitted"),
        "submitted": status == "deletion_started",
    }


def _delete_app_copy(*, file_id: str, settings: IngestSettings) -> Dict[str, Any]:
    if not file_id or not storage.valid_file_id(file_id):
        return {"ok": False, "message": "Local ingestion job id is missing or invalid"}
    result = storage.read_result(settings, file_id)
    if result is None:
        return {"ok": False, "message": "Local ingestion job was not found"}
    archive = _safe_app_copy_path(str(result.get("saved_path") or ""), settings)
    storage.remove_job(settings, file_id)
    if archive is None:
        return {
            "ok": True,
            "message": "App parsed record removed; no safe app archive was found",
            "archive_removed": False,
        }
    try:
        archive.unlink(missing_ok=True)
        return {
            "ok": True,
            "message": "App parsed record and archive removed",
            "archive_removed": True,
        }
    except OSError as exc:
        return {
            "ok": False,
            "message": f"Parsed record removed but app archive deletion failed: {type(exc).__name__}",
            "archive_removed": False,
        }


def _process_item(
    item: Dict[str, Any],
    *,
    delete_knowledge_upload_copy: bool,
    settings: IngestSettings,
    gateway: KnowledgeGateway,
) -> Dict[str, Any]:
    doc_id = str(item.get("doc_id") or "").strip()
    file_id = str(item.get("file_id") or "").strip()
    name = str(item.get("name") or item.get("file_path") or doc_id or file_id)
    steps: List[Dict[str, Any]] = []
    knowledge_ok = True
    local_ok = True
    if doc_id:
        step = _delete_knowledge(
            gateway,
            doc_id,
            delete_file=delete_knowledge_upload_copy,
        )
        steps.append({"target": "knowledge", **step})
        knowledge_ok = bool(step.get("ok"))
    if file_id and knowledge_ok:
        step = _delete_app_copy(file_id=file_id, settings=settings)
        steps.append({"target": "local", **step})
        local_ok = bool(step.get("ok"))
    return {
        "doc_id": doc_id,
        "file_id": file_id,
        "name": name,
        "ok": knowledge_ok and local_ok,
        "pending": any(bool(step.get("submitted")) for step in steps),
        "steps": steps,
        "message": "; ".join(step.get("message", "") for step in steps if step.get("message")),
    }


def _run_job(
    job_id: str,
    items: List[Dict[str, Any]],
    delete_knowledge_upload_copy: bool,
) -> None:
    settings = get_ingest_settings()
    gateway = get_knowledge_gateway()
    _publish(job_id, status="running")
    for index, item in enumerate(items):
        try:
            result = _process_item(
                item,
                delete_knowledge_upload_copy=delete_knowledge_upload_copy,
                settings=settings,
                gateway=gateway,
            )
        except Exception as exc:  # noqa: BLE001
            result = {"doc_id": item.get("doc_id", ""), "file_id": item.get("file_id", ""), "name": item.get("name", ""), "ok": False, "steps": [], "message": f"Unexpected deletion error: {type(exc).__name__}"}
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return
            job["items"].append(result)
            job["completed"] = index + 1
            job["succeeded"] = sum(1 for row in job["items"] if row.get("ok"))
            job["failed"] = sum(1 for row in job["items"] if not row.get("ok"))
            job["pending"] = sum(1 for row in job["items"] if row.get("pending"))
            job["updated_at"] = _now()
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["status"] = "completed"
            if job.get("pending"):
                job["message"] = (
                    f"Deletion accepted: {job['succeeded']} accepted, "
                    f"{job['failed']} failed, {job['pending']} knowledge removals pending"
                )
            else:
                job["message"] = (
                    f"Deletion finished: {job['succeeded']} succeeded, "
                    f"{job['failed']} failed"
                )
            job["updated_at"] = _now()


def _validate_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clean_items: List[Dict[str, Any]] = []
    keys = set()
    for item in items:
        if not isinstance(item, dict):
            raise DeleteValidationError("Every deletion item must be an object")
        doc_id = str(item.get("doc_id") or "").strip()
        file_id = str(item.get("file_id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not doc_id and not file_id:
            raise DeleteValidationError("Every deletion item must identify a knowledge document or app copy")
        if file_id and not storage.valid_file_id(file_id):
            raise DeleteValidationError(f"Invalid app file id: {file_id}")
        key = (doc_id, file_id)
        if key in keys:
            continue
        keys.add(key)
        clean_items.append({
            "doc_id": doc_id,
            "file_id": file_id,
            "name": name,
        })
    return clean_items


def start_deletion(
    items: List[Dict[str, Any]],
    *,
    delete_knowledge_upload_copy: bool = False,
) -> Dict[str, Any]:
    if not items:
        raise DeleteValidationError("At least one item is required")
    if len(items) > _MAX_ITEMS:
        raise DeleteValidationError(f"At most {_MAX_ITEMS} deletion items are allowed")
    clean_items = _validate_items(items)
    job_id = "del_" + uuid4().hex
    job = {
        "id": job_id,
        "status": "queued",
        "delete_knowledge_upload_copy": bool(delete_knowledge_upload_copy),
        "total": len(clean_items),
        "completed": 0,
        "succeeded": 0,
        "failed": 0,
        "pending": 0,
        "items": [],
        "message": "Deletion queued",
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _LOCK:
        _JOBS[job_id] = job
        _trim_jobs()
    threading.Thread(
        target=_run_job,
        args=(job_id, clean_items, bool(delete_knowledge_upload_copy)),
        name=f"document-delete-{job_id}",
        daemon=True,
    ).start()
    return _snapshot(job)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        job = _JOBS.get(job_id)
        return _snapshot(job) if job is not None else None


__all__ = ["DeleteValidationError", "get_job", "start_deletion"]
