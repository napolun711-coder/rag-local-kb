"""Upload and local review API."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.features.ingest import service, worker
from app.features.ingest.config import get_ingest_settings

router = APIRouter(tags=["ingest"])


class IngestAction(BaseModel):
    action: str = Field(min_length=1, max_length=32)


class IngestContent(BaseModel):
    content: str = Field(default="", max_length=service.CONTENT_MAX_CHARS)
    ingest: bool = False
    caption: bool = False
    doc_name: str = Field(default="", max_length=120)


class IngestSession(BaseModel):
    allow_all: bool = False


class QueueState(BaseModel):
    paused: bool = False


def _detail(exc: service.IngestError) -> Dict[str, Any]:
    return {"error": exc.code, "message": exc.message}


@router.post("/upload", status_code=202)
async def upload_file(
    file: UploadFile = File(...),
    batch_id: str = Form(default=""),
) -> Dict[str, Any]:
    settings = get_ingest_settings()
    filename = file.filename or "upload.bin"
    content = await file.read(settings.max_upload_size + 1)
    try:
        result = await asyncio.to_thread(
            service.process_upload,
            filename,
            content,
            file.content_type or "",
            batch_id=batch_id,
        )
    except service.IngestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
    return {
        "success": True,
        "filename": filename,
        "message": "File accepted for local parsing and quality review",
        "file_id": result["file_id"],
        "batch_id": result.get("batch_id") or "",
        "state": result["state"],
        "duplicate": bool(result.get("duplicate")),
        "detail": result.get("detail") or "",
        "saved_path": result.get("saved_path") or "",
        "lightrag": result.get("lightrag") or {"state": "not_configured", "track_id": ""},
    }


@router.get("/ingest/status/{file_id}")
def ingest_status(file_id: str) -> Dict[str, Any]:
    payload = service.status(file_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "ingest_not_found", "message": "Ingestion job not found"},
        )
    return payload


@router.get("/ingest/content/{file_id}")
def ingest_content(file_id: str) -> Dict[str, Any]:
    payload = service.content_for_edit(file_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "ingest_not_found", "message": "Ingestion job not found"},
        )
    return payload


@router.post("/ingest/content/{file_id}")
def ingest_content_save(file_id: str, data: IngestContent) -> Dict[str, Any]:
    outcome = service.edit_content(
        file_id,
        data.content,
        ingest_now=bool(data.ingest),
        caption=bool(data.caption),
        doc_name=data.doc_name,
    )
    if not outcome.get("ok"):
        raise HTTPException(
            status_code=int(outcome.get("http_status") or 400),
            detail={"error": "edit_failed", "message": outcome.get("message") or "Edit failed"},
        )
    return {"success": True, "action": "edit", **outcome}


@router.get("/ingest/pending")
def ingest_pending() -> Dict[str, Any]:
    skipped, skipped_total = service.skipped_jobs()
    return {
        "pending": service.pending_reviews(),
        "actionable": service.actionable_jobs(),
        "skipped": skipped,
        "skipped_total": skipped_total,
        "active": service.active_jobs(),
        "confirm": {
            "mode": service.confirm_mode(),
            "session_allow_all": service.session_allow_all(),
            "restore_hours": service.SKIPPED_RESTORE_HOURS,
        },
    }


@router.get("/ingest/queue/status")
def ingest_queue_status() -> Dict[str, Any]:
    return {"ok": True, **worker.status()}


@router.post("/ingest/queue")
def ingest_queue_state(data: QueueState) -> Dict[str, Any]:
    return {"ok": True, **worker.set_paused(data.paused)}


@router.get("/ingest/batch/{batch_id}")
def ingest_batch(batch_id: str) -> Dict[str, Any]:
    payload = service.batch_status(batch_id)
    if payload is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_batch_id", "message": "Invalid ingestion batch id"},
        )
    return payload


@router.post("/ingest/knowledge-status/{file_id}")
def refresh_knowledge_status(file_id: str) -> Dict[str, Any]:
    outcome = service.refresh_knowledge_status(file_id)
    if not outcome.get("ok"):
        raise HTTPException(
            status_code=int(outcome.get("http_status") or 400),
            detail={
                "error": outcome.get("code") or "knowledge_status_failed",
                "message": outcome.get("message") or "Status refresh failed",
            },
        )
    return {"success": True, **outcome}


@router.post("/ingest/session")
def ingest_session(data: IngestSession) -> Dict[str, Any]:
    return {"success": True, **service.set_session_allow_all(data.allow_all)}


@router.post("/ingest/action/{file_id}")
def ingest_action(file_id: str, data: IngestAction) -> Dict[str, Any]:
    outcome = service.activate(file_id, data.action)
    if not outcome.get("ok"):
        raise HTTPException(
            status_code=int(outcome.get("http_status") or 400),
            detail={"error": outcome.get("code") or "action_failed", "message": outcome.get("message") or "Action failed"},
        )
    return {"success": True, "action": data.action, **outcome}


__all__ = ["router"]