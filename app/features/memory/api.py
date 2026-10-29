"""Local-only administrative API for memory inspection and correction."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.features.memory import runtime, supervisor, worker as worker_module
from app.features.memory.config import get_memory_settings
from app.features.memory.db import resolve_db_path
from app.features.memory.memories import MemoryConflict, MemoryNotFound
from app.features.access.dependencies import require_authorized, require_local

router = APIRouter(prefix="/api/memory/admin", tags=["memory-admin"])
status_router = APIRouter(prefix="/api/memory", tags=["memory"])
frontend_router = APIRouter(prefix="/api/memory", tags=["memory"])


def _store():
    store = runtime.get_store()
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Memory is disabled or unavailable",
        )
    return store


def _frontend_status() -> Dict[str, Any]:
    settings = get_memory_settings()
    status = runtime.status()
    store = runtime.get_store()
    path = ""
    if store is not None:
        path = str(store.db.path)
    elif settings.memory_enabled:
        path = str(resolve_db_path(settings.memory_db_path))
    job_counts = status.get("jobs") if isinstance(status.get("jobs"), dict) else {}
    jobs = {str(key): int(value or 0) for key, value in job_counts.items()}
    jobs["total"] = sum(jobs.values())
    try:
        worker_running = worker_module.get_worker().running()
    except Exception:
        worker_running = False
    supervisor_status = supervisor.status()
    return {
        **status,
        "jobs": jobs,
        "context_enabled": settings.memory_context_enabled,
        "fast_memory": settings.memory_fast_memory,
        "upstream_history": settings.memory_upstream_history,
        "auto_extract": settings.memory_auto_extract,
        "chat_topic_filter": settings.chat_topic_filter,
        "worker_configured": bool(
            settings.memory_enabled
            and settings.memory_auto_extract
            and settings.memory_worker
        ),
        "worker_running": worker_running,
        "worker": worker_running,
        "path": path,
        "supervisor": supervisor_status,
    }


@status_router.get("/status", dependencies=[Depends(require_authorized)])
def public_memory_status() -> Dict[str, Any]:
    return _frontend_status()


class SupervisorPolicyPatch(BaseModel):
    enabled: Optional[bool] = None
    scan_interval_seconds: Optional[int] = Field(default=None, ge=30, le=86400)
    stale_days: Optional[int] = Field(default=None, ge=1, le=3650)
    min_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_active_memories: Optional[int] = Field(default=None, ge=1, le=10000)


class MemoryPatch(BaseModel):
    content: Optional[str] = Field(default=None, max_length=300)
    subject: Optional[str] = Field(default=None, max_length=40)
    kind: Optional[str] = None
    scope: Optional[str] = None
    topic_id: Optional[str] = None
    importance: Optional[int] = Field(default=None, ge=1, le=5)
    pinned: Optional[bool] = None
    sensitivity: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


class ClearMemoryRequest(BaseModel):
    confirm: str = ""
    purge_backups: bool = False


@router.get("/status", dependencies=[Depends(require_local)])
def memory_status() -> Dict[str, Any]:
    return {"memory": runtime.status(), "supervisor": supervisor.status()}


@router.get("/policy", dependencies=[Depends(require_local)])
def get_policy() -> Dict[str, Any]:
    return supervisor.policy_dict()


@router.patch("/policy", dependencies=[Depends(require_local)])
def patch_policy(payload: SupervisorPolicyPatch) -> Dict[str, Any]:
    changes = payload.model_dump(exclude_unset=True)
    return supervisor.policy_dict(supervisor.update_policy(changes))


@router.post("/scan", dependencies=[Depends(require_local)])
def scan_memory() -> Dict[str, Any]:
    return supervisor.scan()


@router.get("/findings", dependencies=[Depends(require_local)])
def list_findings(
    status: str = Query(default="open"),
    limit: int = Query(default=100, ge=1, le=500),
) -> Dict[str, Any]:
    return {"items": supervisor.findings(status=status, limit=limit)}


@router.post("/findings/{finding_id}/resolve", dependencies=[Depends(require_local)])
def resolve_finding(finding_id: str, status: str = Query(default="resolved")) -> Dict[str, Any]:
    if status not in ("resolved", "ignored"):
        raise HTTPException(status_code=400, detail="status must be resolved or ignored")
    if not supervisor.resolve_finding(finding_id, status=status):
        raise HTTPException(status_code=404, detail="Finding not found")
    return {"ok": True, "status": status}


@router.get("/items", dependencies=[Depends(require_local)])
def list_items(
    status: Optional[str] = Query(default=None),
    kind: Optional[str] = Query(default=None),
    scope: Optional[str] = Query(default=None),
    topic_id: Optional[str] = Query(default=None),
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    rows = _store().memories.list(
        status=status,
        kind=kind,
        scope=scope,
        topic_id=topic_id,
        q=q,
        limit=limit,
        offset=offset,
        include_deleted=False,
    )
    return {"items": rows, "count": len(rows)}


@router.get("/items/{memory_id}", dependencies=[Depends(require_local)])
def get_item(memory_id: str) -> Dict[str, Any]:
    store = _store()
    item = store.memories.get(memory_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {
        "item": item,
        "sources": store.memories.sources(memory_id),
        "events": store.memories.events(memory_id),
    }


@router.patch("/items/{memory_id}", dependencies=[Depends(require_local)])
def patch_item(memory_id: str, payload: MemoryPatch) -> Dict[str, Any]:
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="No fields supplied")
    try:
        return _store().memories.update(memory_id, **values, actor="user")
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc
    except MemoryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/items/{memory_id}/confirm", dependencies=[Depends(require_local)])
def confirm_item(memory_id: str) -> Dict[str, Any]:
    try:
        return _store().memories.confirm(memory_id)
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc


@router.post("/items/{memory_id}/reject", dependencies=[Depends(require_local)])
def reject_item(memory_id: str, reason: str = Query(default="")) -> Dict[str, Any]:
    try:
        return {"ok": _store().memories.reject(memory_id, reason=reason)}
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc


@router.post("/items/{memory_id}/expire", dependencies=[Depends(require_local)])
def expire_item(memory_id: str, reason: str = Query(default="user_review")) -> Dict[str, Any]:
    try:
        return _store().memories.expire(memory_id, reason=reason, actor="user")
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc


@router.post("/items/{memory_id}/restore", dependencies=[Depends(require_local)])
def restore_item(memory_id: str) -> Dict[str, Any]:
    try:
        return _store().memories.restore(memory_id)
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc


@router.delete("/items/{memory_id}", dependencies=[Depends(require_local)])
def delete_item(memory_id: str, purge: bool = Query(default=False)) -> Dict[str, Any]:
    try:
        return {"ok": _store().memories.delete(memory_id, purge=purge)}
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc


@router.get("/items/{memory_id}/sources", dependencies=[Depends(require_local)])
def item_sources(memory_id: str) -> Dict[str, Any]:
    return {"items": _store().memories.sources(memory_id)}


@router.get("/items/{memory_id}/events", dependencies=[Depends(require_local)])
def item_events(memory_id: str) -> Dict[str, Any]:
    return {"items": _store().memories.events(memory_id)}


# ---------------------------------------------------------------------------
# Browser-facing contract (`/api/memory/*`)
# ---------------------------------------------------------------------------


@frontend_router.get("/items", dependencies=[Depends(require_authorized)])
def frontend_list_items(
    status: Optional[str] = Query(default=None),
    kind: Optional[str] = Query(default=None),
    scope: Optional[str] = Query(default=None),
    topic_id: Optional[str] = Query(default=None),
    q: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    store = runtime.get_store()
    if store is None:
        return {
            "enabled": False,
            "items": [],
            "count": 0,
            "limit": limit,
            "offset": offset,
        }
    rows = store.memories.list(
        status=status,
        kind=kind,
        scope=scope,
        topic_id=topic_id,
        q=q,
        limit=limit,
        offset=offset,
        include_deleted=status == "deleted",
    )
    return {
        "enabled": True,
        "items": rows,
        "count": len(rows),
        "limit": limit,
        "offset": offset,
    }


@frontend_router.get("/items/{memory_id}", dependencies=[Depends(require_authorized)])
def frontend_get_item(
    memory_id: str,
    include_deleted: bool = Query(default=True),
) -> Dict[str, Any]:
    store = _store()
    item = store.memories.get(memory_id)
    if item is None or (not include_deleted and item.get("status") == "deleted"):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {
        "enabled": True,
        "memory": item,
        "sources": store.memories.sources(memory_id),
        "events": store.memories.events(memory_id),
    }


@frontend_router.patch("/items/{memory_id}", dependencies=[Depends(require_local)])
def frontend_patch_item(memory_id: str, payload: MemoryPatch) -> Dict[str, Any]:
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="No fields supplied")
    try:
        item = _store().memories.update(memory_id, **values, actor="user")
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc
    except MemoryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "memory": item}


@frontend_router.post("/items/{memory_id}/confirm", dependencies=[Depends(require_local)])
def frontend_confirm_item(memory_id: str) -> Dict[str, Any]:
    try:
        item = _store().memories.confirm(memory_id)
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc
    return {"ok": True, "memory": item}


@frontend_router.post("/items/{memory_id}/reject", dependencies=[Depends(require_local)])
def frontend_reject_item(memory_id: str, reason: str = Query(default="")) -> Dict[str, Any]:
    try:
        ok = _store().memories.reject(memory_id, reason=reason)
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc
    return {"ok": bool(ok)}


@frontend_router.post("/items/{memory_id}/expire", dependencies=[Depends(require_local)])
def frontend_expire_item(
    memory_id: str,
    reason: str = Query(default="user_review"),
) -> Dict[str, Any]:
    try:
        item = _store().memories.expire(memory_id, reason=reason, actor="user")
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc
    return {"ok": True, "memory": item}


@frontend_router.post("/items/{memory_id}/restore", dependencies=[Depends(require_local)])
def frontend_restore_item(memory_id: str) -> Dict[str, Any]:
    try:
        item = _store().memories.restore(memory_id)
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc
    return {"ok": True, "memory": item}


@frontend_router.delete("/items/{memory_id}", dependencies=[Depends(require_local)])
def frontend_delete_item(
    memory_id: str,
    purge: bool = Query(default=False),
) -> Dict[str, Any]:
    try:
        ok = _store().memories.delete(memory_id, purge=purge)
    except MemoryNotFound as exc:
        raise HTTPException(status_code=404, detail="Memory not found") from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"ok": True, "purged": purge}


@frontend_router.get("/items/{memory_id}/sources", dependencies=[Depends(require_authorized)])
def frontend_item_sources(memory_id: str) -> Dict[str, Any]:
    return {"items": _store().memories.sources(memory_id)}


@frontend_router.get("/items/{memory_id}/events", dependencies=[Depends(require_authorized)])
def frontend_item_events(
    memory_id: str,
    limit: int = Query(default=100, ge=1, le=500),
) -> Dict[str, Any]:
    return {"items": _store().memories.events(memory_id, limit=limit)}


@frontend_router.get("/export", dependencies=[Depends(require_authorized)])
def frontend_export() -> Dict[str, Any]:
    return _store().export_memories()


@frontend_router.post("/import", dependencies=[Depends(require_local)])
async def frontend_import(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        if len(payload["items"]) > 10000:
            raise HTTPException(status_code=413, detail="Import contains too many items")
    try:
        return _store().import_memories(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@frontend_router.post("/clear", dependencies=[Depends(require_local)])
def frontend_clear(payload: ClearMemoryRequest) -> Dict[str, Any]:
    if payload.confirm != "CLEAR ALL":
        raise HTTPException(status_code=400, detail="Confirmation must be CLEAR ALL")
    counts = _store().clear_all(purge_backups=payload.purge_backups)
    return {"ok": True, "counts": counts, "purge_backups": payload.purge_backups}


@frontend_router.get("/supervisor/status", dependencies=[Depends(require_authorized)])
def frontend_supervisor_status() -> Dict[str, Any]:
    return {**supervisor.status(), "advisory_only": True}


@frontend_router.get("/supervisor/findings", dependencies=[Depends(require_authorized)])
def frontend_supervisor_findings(
    status: str = Query(default="open"),
    limit: int = Query(default=100, ge=1, le=500),
) -> Dict[str, Any]:
    return {
        "items": supervisor.findings(status=status, limit=limit),
        "advisory_only": True,
    }


@frontend_router.post("/supervisor/scan", dependencies=[Depends(require_local)])
def frontend_supervisor_scan() -> Dict[str, Any]:
    return {**supervisor.scan(), "advisory_only": True}


@frontend_router.post(
    "/supervisor/findings/{finding_id}/resolve",
    dependencies=[Depends(require_local)],
)
def frontend_supervisor_resolve(
    finding_id: str,
    status: str = Query(default="resolved"),
) -> Dict[str, Any]:
    if status not in ("resolved", "ignored"):
        raise HTTPException(status_code=400, detail="status must be resolved or ignored")
    if not supervisor.resolve_finding(finding_id, status=status):
        raise HTTPException(status_code=404, detail="Finding not found")
    return {"ok": True, "status": status, "advisory_only": True}
