"""Local-only read-only document catalog API."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.adapters.lightrag.errors import KnowledgeEngineNotInstalled, KnowledgeHTTPError, KnowledgeUnavailable
from app.features.documents.contracts import DocumentOverview, DocumentView, LocalJobSummary
from app.features.documents.deletion import DeleteValidationError, get_job, start_deletion
from app.features.documents.service import DocumentCatalogService
from app.features.documents import topics


def _require_local(request: Request) -> None:
    host = (request.client.host if request.client else "").strip()
    if host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="This API is local-only")


router = APIRouter(prefix="/api/files", tags=["documents"], dependencies=[Depends(_require_local)])
topics_router = APIRouter(prefix="/api/topics", tags=["topics"], dependencies=[Depends(_require_local)])


class DeleteItem(BaseModel):
    doc_id: str = ""
    file_id: str = ""
    file_path: str = ""
    name: str = ""


class DeleteRequest(BaseModel):
    items: List[DeleteItem] = Field(default_factory=list)
    scope: Literal["kb", "local", "all"] = "kb"
    remove_originals: bool = False
    confirm: bool = False


class TopicCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["project", "topic", "person", "other"] = "topic"
    description: str = Field(default="", max_length=500)


class DocumentTopicRequest(BaseModel):
    file_path: str = Field(min_length=1, max_length=2000)
    topic_id: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=500)
    file_id: str = Field(default="", max_length=200)


def _service() -> DocumentCatalogService:
    return DocumentCatalogService()


def _origin_for_local(job: Optional[LocalJobSummary]) -> Dict[str, Any]:
    if job is None:
        return {"available": False, "kind": "", "kind_label": "", "path": "", "url": "", "reason": "No local ingestion job"}
    url = f"/api/files/origin/{job.file_id}" if job.source_available and job.file_id else ""
    return {
        "available": bool(job.source_available),
        "kind": "source" if job.source_available else "",
        "kind_label": "本机原件" if job.source_available else "",
        "path": job.source_path if job.source_available else "",
        "url": url,
        "download_url": (url + "?download=true") if url else "",
        "reason": "" if job.source_available else "Local source file is missing",
    }


_STATUS_LABELS = {
    "processed": "已完成",
    "processing": "建索引中",
    "parsing": "解析中",
    "analyzing": "分析中",
    "pending": "排队中",
    "preprocessed": "预处理完成",
    "failed": "处理失败",
}
_LOCAL_STATE_LABELS = {
    "review": "待确认",
    "needs_ocr": "需要 OCR",
    "blocked": "未通过质量门",
    "failed": "解析失败",
    "skipped": "已跳过",
    "ingesting": "提交中",
    "done": "已入库",
}


def _document_payload(item: DocumentView) -> Dict[str, Any]:
    payload = item.to_dict()
    status = str(item.status or "").lower()
    payload["status"] = status
    payload["raw_status"] = item.status
    payload["status_label"] = _STATUS_LABELS.get(status, item.status or "—")
    payload["has_local"] = item.local_job is not None
    payload["file_id"] = item.local_job.file_id if item.local_job else ""
    payload["local_state_label"] = _LOCAL_STATE_LABELS.get(item.local_job.state, item.local_job.state) if item.local_job else ""
    payload["error_msg"] = item.error
    payload["origin"] = _origin_for_local(item.local_job)
    payload["can_open"] = bool(payload["origin"].get("available"))
    return payload


def _local_payload(job: LocalJobSummary) -> Dict[str, Any]:
    payload = job.to_dict()
    origin = _origin_for_local(job)
    payload.update({
        "name": job.doc_name or job.filename,
        "file_path": job.source_path,
        "chunks_count": None,
        "local_state_label": _LOCAL_STATE_LABELS.get(job.state, job.state or "—"),
        "local_detail": f"质量门：{job.quality_level or '—'}" if job.quality_level else "",
        "created_at": job.updated_at,
        "origin": origin,
        "can_open": bool(origin.get("available")),
    })
    return payload


def _upstream_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KnowledgeEngineNotInstalled):
        return HTTPException(status_code=503, detail={"error": "engine_not_installed", "message": str(exc)})
    if isinstance(exc, KnowledgeUnavailable):
        return HTTPException(status_code=503, detail={"error": "knowledge_unavailable", "message": type(exc).__name__})
    if isinstance(exc, KnowledgeHTTPError):
        return HTTPException(status_code=502, detail={"error": "knowledge_error", "message": str(exc)})
    return HTTPException(status_code=502, detail={"error": "knowledge_error", "message": type(exc).__name__})


def _overview_payload(view: DocumentOverview) -> Dict[str, Any]:
    counts = {str(key).replace("DocStatus.", "").lower(): int(value) for key, value in view.remote_status_counts.items()}
    counts["all"] = view.remote_total
    return {
        **view.to_dict(),
        "counts": counts,
        "local": {
            "jobs": view.local_total,
            "waiting_review": view.local_pending,
            "failed": view.local_failed,
            "in_knowledge_base": view.local_done,
            "knowledge_files": 0,
        },
        "upstream": {
            "ok": view.upstream_ok,
            "url": "",
            "message": view.upstream_message,
        },
        "pipeline": {"busy": False, "recovery_required": False, "recovery_message": ""},
        "inbox": {"enabled": False, "waiting": 0, "settling": 0, "accepted": 0, "duplicate": 0, "failed": 0, "events": [], "dirs": {}, "hint": ""},
        "folders": [],
    }


@router.get("/overview")
def files_overview() -> Dict[str, Any]:
    try:
        return _overview_payload(_service().overview())
    except Exception as exc:
        raise _upstream_error(exc) from exc


@router.get("/list")
def files_list(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=200),
    status: str = Query(default="all"),
    q: str = Query(default=""),
    topic_id: str = Query(default=""),
) -> Dict[str, Any]:
    status_filter = None if not status or status == "all" else status.upper()
    try:
        result = _service().list_documents(
            page=page,
            page_size=page_size,
            status_filter=status_filter,
            q=q,
            topic_id=topic_id,
        )
    except Exception as exc:
        raise _upstream_error(exc) from exc
    payload = result.to_dict()
    payload["documents"] = [_document_payload(item) for item in result.items]
    payload["local_only"] = [_local_payload(job) for job in result.local_only]
    payload["upstream"] = {
        "ok": result.upstream_ok,
        "message": result.upstream_message,
    }
    if not result.upstream_ok:
        payload["notice"] = (
            f"{result.upstream_message}。当前仅显示本机已有记录，"
            "未与知识库状态核对；服务恢复后请刷新。"
        )
    return payload


@router.get("/detail/{doc_id}")
def files_detail(doc_id: str) -> Dict[str, Any]:
    try:
        item = _service().get_document(doc_id)
    except Exception as exc:
        raise _upstream_error(exc) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"document": _document_payload(item)}


@router.get("/resolve")
def files_resolve(file_path: str = Query(default="")) -> Dict[str, Any]:
    payload = _service().resolve_origin(file_path)
    file_id = str(payload.get("file_id") or "")
    if payload.get("available") and file_id:
        url = f"/api/files/origin/{file_id}"
        payload["url"] = url
        payload["download_url"] = url + "?download=true"
    return payload


_INLINE_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}
_INLINE_TEXT = {".txt", ".md", ".csv", ".json", ".log", ".xml", ".yaml", ".yml"}


@router.get("/origin/{file_id}")
def files_origin(
    file_id: str,
    download: bool = Query(default=False),
) -> FileResponse:
    origin = _service().origin_for_file_id(file_id)
    if origin is None:
        raise HTTPException(status_code=404, detail="Original file not found")
    path = Path(origin["path"])
    suffix = path.suffix.lower()
    inline = False
    if download:
        media_type = "application/octet-stream"
    elif suffix == ".pdf":
        media_type = "application/pdf"
        inline = True
    elif suffix in _INLINE_IMAGES:
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        inline = True
    elif suffix in _INLINE_TEXT:
        media_type = "text/plain; charset=utf-8"
        inline = True
    else:
        # HTML, SVG, Office files, archives, and unknown formats are downloaded
        # rather than rendered under the application's origin.
        media_type = "application/octet-stream"
    response = FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline" if inline else "attachment",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if inline and suffix != ".pdf":
        response.headers["Content-Security-Policy"] = "sandbox"
    return response


@topics_router.get("")
def list_topics() -> Dict[str, Any]:
    return {
        "enabled": topics.enabled(),
        "topics": topics.list_topics(),
    }


@topics_router.post("")
def create_topic(data: TopicCreateRequest) -> Dict[str, Any]:
    try:
        item = topics.create_topic(
            data.name,
            kind=data.kind,
            description=data.description,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "topic": item}


@router.post("/topic")
def set_document_topic(data: DocumentTopicRequest) -> Dict[str, Any]:
    try:
        item = topics.set_document_topic(
            data.file_path,
            data.topic_id,
            name=data.name,
            file_id=data.file_id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "document_topic": item}


@router.delete("/topic")
def clear_document_topic(file_path: str = Query(min_length=1)) -> Dict[str, Any]:
    try:
        cleared = topics.clear_document_topic(file_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, "cleared": bool(cleared)}

@router.post("/delete", status_code=202)
def files_delete(data: DeleteRequest) -> Dict[str, Any]:
    if not data.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required")
    if not data.items:
        raise HTTPException(status_code=400, detail="At least one item is required")
    try:
        job = start_deletion(
            [item.model_dump() for item in data.items],
            scope=data.scope,
            remove_originals=data.remove_originals,
        )
    except DeleteValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "job": job}


@router.get("/delete/{job_id}")
def files_delete_status(job_id: str) -> Dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Deletion job not found")
    return {"job": job}


__all__ = ["router", "topics_router"]
