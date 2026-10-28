"""Upload, parse, quality gate, advice, and human review workflow."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from app.features.ingest import advice as advice_service
from app.features.ingest import parse as parse_service
from app.features.ingest import quality as quality_service
from app.features.ingest.config import IngestSettings, get_ingest_settings
from app.features.ingest import storage
from app.adapters.lightrag.errors import (
    KnowledgeEngineNotInstalled,
    KnowledgeHTTPError,
    KnowledgeIndexMismatch,
    KnowledgeUnavailable,
)
from app.features.knowledge.service import get_knowledge_gateway

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()
_SESSION_ALLOW_ALL = False
CONTENT_MAX_CHARS = 500_000
REVIEW_STATES = ("review", "needs_ocr", "blocked")


class IngestError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _validate(filename: str, size: int, settings: IngestSettings) -> None:
    lowered = str(filename or "").lower()
    if not any(lowered.endswith(ext) for ext in settings.allowed_extensions):
        raise IngestError(
            400,
            "unsupported_file_type",
            f"Unsupported file type: {filename or storage.FALLBACK_FILENAME}",
        )
    if size > settings.max_upload_size:
        raise IngestError(
            413,
            "file_too_large",
            f"File exceeds the upload limit of {settings.max_upload_size} bytes",
        )


def _parse_summary(parsed: parse_service.ParseResult) -> Dict[str, Any]:
    return {
        "ok": bool(parsed.ok),
        "route": parsed.route,
        "route_label": parsed.route_label,
        "pages": int(parsed.pages or 0),
        "images": int(parsed.images or 0),
        "parser": parsed.parser,
        "detail": parsed.detail,
        "truncated": bool(parsed.truncated),
        "char_total": int(parsed.char_total),
        "asset_count": len(parsed.assets),
    }


def _advice_for(
    text: str,
    parsed: parse_service.ParseResult,
    quality: quality_service.QualityReport,
    settings: IngestSettings,
) -> Dict[str, Any]:
    if quality.level != quality_service.LEVEL_PASS:
        return {}
    pending = len(parsed.assets)
    if parsed.route == "image" and pending == 0:
        pending = 1 if parsed.images else 0
    result = advice_service.advise(
        text,
        quality=quality.to_dict(),
        route=parsed.route,
        route_label=parsed.route_label,
        pages=parsed.pages,
        file_name=parsed.parser,
        images_total=int(parsed.images or 0),
        images_pending=pending,
        ocr_used=False,
        min_chars=settings.quality_min_chars,
        max_unocr_images=settings.quality_max_unocr_images,
    )
    return result.to_dict()


def _run_pipeline(settings: IngestSettings, result: Dict[str, Any]) -> Dict[str, Any]:
    source = storage.resolve_display_path(str(result.get("source_path") or ""))
    timeline = list(result.get("timeline") or [])
    timeline.append({"step": "parse", "state": "checking", "at": storage.utc_now(), "detail": ""})
    result["timeline"] = timeline

    parsed = parse_service.parse_file(source, str(result.get("filename") or source.name))
    result["parse"] = _parse_summary(parsed)
    if not parsed.ok:
        result["state"] = "failed"
        result["detail"] = parsed.detail or "Local parsing failed"
        result["quality"] = {}
        result["advice"] = {}
        result["updated_at"] = storage.utc_now()
        timeline.append({"step": "parse", "state": "failed", "at": result["updated_at"], "detail": result["detail"]})
        storage.write_result(settings, result["file_id"], result)
        return result

    storage.write_content(settings, result["file_id"], parsed.text)
    report = quality_service.evaluate(
        parsed.text,
        route=parsed.route,
        pages=parsed.pages,
        options=quality_service.options_from_settings(settings),
        routes=settings.quality_ocr_routes,
    )
    quality = report.to_dict()
    result["quality"] = quality
    result["advice"] = _advice_for(parsed.text, parsed, report, settings)
    result["content_chars"] = int(parsed.char_total)
    result["image_total"] = int(parsed.images or 0)
    result["unocr_images"] = len(parsed.assets) + (1 if parsed.route == "image" and not parsed.assets else 0)
    result["is_image"] = parsed.route == "image"
    result["ocr_enabled"] = False
    result["ocr_external"] = False

    if report.level == quality_service.LEVEL_PASS:
        result["state"] = "review"
        result["detail"] = "Local quality gate passed; ready for explicit knowledge submission."
    elif report.level == quality_service.LEVEL_NEEDS_OCR:
        result["state"] = "needs_ocr"
        result["detail"] = quality_service.describe(report)
    else:
        result["state"] = "blocked"
        result["detail"] = quality_service.describe(report)
    result["updated_at"] = storage.utc_now()
    result["timeline"].append(
        {"step": "quality", "state": result["state"], "at": result["updated_at"], "detail": result["detail"]}
    )
    storage.write_result(settings, result["file_id"], result)
    return result


def process_upload(filename: str, content: bytes, content_type: str = "") -> Dict[str, Any]:
    settings = get_ingest_settings()
    _validate(filename, len(content), settings)
    with _LOCK:
        file_id = storage.new_file_id()
        archived = storage.archive_upload(settings, filename, content)
        source = storage.save_job_source(settings, file_id, filename, content)
        created = storage.utc_now()
        result = {
            "file_id": file_id,
            "filename": filename,
            "doc_name": Path(filename).stem,
            "content_type": content_type,
            "saved_path": storage.display_path(archived),
            "source_path": storage.display_path(source),
            "state": "processing",
            "detail": "Upload accepted",
            "created_at": created,
            "updated_at": created,
            "timeline": [{"step": "upload", "state": "done", "at": created, "detail": ""}],
            "lightrag": {"state": "not_submitted", "track_id": ""},
            "knowledge": {"state": "not_submitted", "track_id": ""},
            "knowledge_gateway_ready": False,
        }
        storage.write_result(settings, file_id, result)
        return _public_status(_run_pipeline(settings, result))


def _can(status: Dict[str, Any]) -> Dict[str, bool]:
    state = str(status.get("state") or "")
    knowledge = status.get("knowledge") if isinstance(status.get("knowledge"), dict) else {}
    knowledge_state = str(knowledge.get("state") or "not_submitted")
    track_id = str(knowledge.get("track_id") or (status.get("lightrag") or {}).get("track_id") or "")
    editable = state in REVIEW_STATES
    return {
        "ingest": state == "review" and knowledge_state not in ("submitted", "ingesting", "done"),
        "force": False,
        "ocr": False,
        "retry": state in ("failed", "blocked", "needs_ocr"),
        "skip": state in REVIEW_STATES,
        "edit": editable,
        "refresh": bool(track_id) and state in ("ingesting", "done"),
    }


def _public_status(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(result)
    payload["can"] = _can(payload)
    knowledge = payload.get("knowledge") if isinstance(payload.get("knowledge"), dict) else {}
    payload["progress"] = dict(knowledge.get("progress") or {})
    payload["ocr"] = {"used": False, "scope": "", "enabled": False}
    payload["knowledge"] = dict(knowledge)
    payload["gateway_ready"] = str(knowledge.get("state") or "") in ("submitted", "ingesting", "done")
    return payload


def status(file_id: str) -> Optional[Dict[str, Any]]:
    settings = get_ingest_settings()
    result = storage.read_result(settings, file_id)
    return _public_status(result) if result else None


def pending_reviews() -> list[Dict[str, Any]]:
    settings = get_ingest_settings()
    items = [
        _public_status(item)
        for item in storage.list_results(settings)
        if str(item.get("state") or "") in ("review", "needs_ocr")
    ]
    items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return items


def active_jobs() -> list[Dict[str, Any]]:
    return []


def session_allow_all() -> bool:
    return _SESSION_ALLOW_ALL


def set_session_allow_all(value: bool) -> Dict[str, Any]:
    global _SESSION_ALLOW_ALL
    _SESSION_ALLOW_ALL = bool(value)
    return {"session_allow_all": _SESSION_ALLOW_ALL}


def confirm_mode() -> str:
    return "always"


def content_for_edit(file_id: str) -> Optional[Dict[str, Any]]:
    settings = get_ingest_settings()
    result = status(file_id)
    if result is None:
        return None
    content = storage.read_content(settings, file_id)
    editable = str(result.get("state") or "") in REVIEW_STATES
    return {
        "content": content,
        "chars": len(content),
        "editable": editable,
        "reason": "" if editable else "This job is not in a reviewable state",
        "edit": {"count": 0, "updated_at": ""},
        "max_chars": CONTENT_MAX_CHARS,
    }


def edit_content(
    file_id: str,
    content: str,
    *,
    ingest_now: bool = False,
    caption: bool = False,
    doc_name: str = "",
) -> Dict[str, Any]:
    settings = get_ingest_settings()
    with _LOCK:
        result = storage.read_result(settings, file_id)
        if result is None:
            return {"ok": False, "http_status": 404, "message": "Ingestion job not found"}
        if str(result.get("state") or "") not in REVIEW_STATES:
            return {"ok": False, "http_status": 409, "message": "This job cannot be edited"}
        text = str(content or "")
        if not text.strip():
            return {"ok": False, "http_status": 400, "message": "Content cannot be empty"}
        if len(text) > CONTENT_MAX_CHARS:
            return {"ok": False, "http_status": 413, "message": "Content is too large"}

        storage.write_content(settings, file_id, text)
        route = str((result.get("parse") or {}).get("route") or "text")
        pages = int((result.get("parse") or {}).get("pages") or 1)
        report = quality_service.evaluate(
            text,
            route=route,
            pages=pages,
            options=quality_service.options_from_settings(settings),
            routes=settings.quality_ocr_routes,
        )
        result["quality"] = report.to_dict()
        result["content_chars"] = int(report.metrics.get("char_total") or 0)
        result["manual_text"] = bool(caption)
        if doc_name.strip():
            result["doc_name"] = doc_name.strip()[:120]
        parsed = parse_service.ParseResult(
            ok=True,
            route=route,
            text=text,
            pages=pages,
            images=int(result.get("image_total") or 0),
        )
        result["advice"] = _advice_for(text, parsed, report, settings)
        result["state"] = "review" if report.passed else ("needs_ocr" if report.needs_ocr else "blocked")
        result["detail"] = "Saved and rechecked; ready for explicit knowledge submission."
        result["knowledge"] = {"state": "not_submitted", "track_id": ""}
        result["lightrag"] = {"state": "not_submitted", "track_id": ""}
        result["updated_at"] = storage.utc_now()
        result["timeline"] = list(result.get("timeline") or []) + [
            {"step": "edit", "state": result["state"], "at": result["updated_at"], "detail": ""}
        ]
        storage.write_result(settings, file_id, result)
        return {
            "ok": True,
            "state": result["state"],
            "message": result["detail"],
            "chars": len(text),
            "edited": True,
            "ingested": False,
            "status": _public_status(result),
            "gateway_ready": False,
        }


def activate(file_id: str, action: str) -> Dict[str, Any]:
    settings = get_ingest_settings()
    action = str(action or "").strip().lower()
    with _LOCK:
        result = storage.read_result(settings, file_id)
        if result is None:
            return {"ok": False, "http_status": 404, "message": "Ingestion job not found"}
        state = str(result.get("state") or "")
        if action == "skip":
            if state not in REVIEW_STATES:
                return {"ok": False, "http_status": 409, "message": "This job cannot be skipped"}
            result["state"] = "skipped"
            result["detail"] = "Skipped by the user; local source and parsed text were retained."
        elif action == "retry":
            if state not in ("failed", "blocked", "needs_ocr"):
                return {"ok": False, "http_status": 409, "message": "This job does not need a retry"}
            return _retry_result(settings, result)
        elif action == "force":
            return {"ok": False, "http_status": 409, "message": "Forced ingestion requires the knowledge gateway"}
        elif action == "ingest":
            return _submit_to_knowledge(settings, result)
        elif action == "ocr":
            return {"ok": False, "http_status": 409, "message": "OCR is not configured in this stage"}
        else:
            return {"ok": False, "http_status": 400, "message": "Unknown action"}
        result["updated_at"] = storage.utc_now()
        storage.write_result(settings, file_id, result)
        return {"ok": True, "state": result["state"], "message": result["detail"], "duplicate_submit": False}


def _knowledge_failure(
    settings: IngestSettings,
    result: Dict[str, Any],
    *,
    code: str,
    message: str,
    http_status: int,
) -> Dict[str, Any]:
    result["state"] = "review"
    result["detail"] = message
    result["knowledge"] = {
        "state": "failed",
        "track_id": "",
        "code": code,
        "message": message,
    }
    result["lightrag"] = {"state": "failed", "track_id": ""}
    result["updated_at"] = storage.utc_now()
    result["timeline"] = list(result.get("timeline") or []) + [
        {"step": "knowledge", "state": "failed", "at": result["updated_at"], "detail": message}
    ]
    storage.write_result(settings, result["file_id"], result)
    return {"ok": False, "http_status": http_status, "code": code, "message": message, "state": "review"}


def _submit_to_knowledge(settings: IngestSettings, result: Dict[str, Any]) -> Dict[str, Any]:
    if str(result.get("state") or "") != "review":
        return {"ok": False, "http_status": 409, "message": "Only reviewed content can be submitted"}
    content = storage.read_content(settings, result["file_id"])
    if not content.strip():
        return {"ok": False, "http_status": 409, "message": "Parsed content is empty"}
    base = str(result.get("doc_name") or Path(result.get("filename") or "document").stem).strip()
    if not base.lower().endswith(".md"):
        base += ".md"
    document_name = base.replace("/", "_").replace("\\", "_")
    result["state"] = "ingesting"
    result["detail"] = "Submitting reviewed content to the knowledge service"
    result["knowledge"] = {"state": "submitting", "document_name": document_name, "track_id": ""}
    result["lightrag"] = {"state": "submitting", "track_id": ""}
    result["updated_at"] = storage.utc_now()
    storage.write_result(settings, result["file_id"], result)
    try:
        response = get_knowledge_gateway().upload_document(
            document_name, content.encode("utf-8"), "text/markdown"
        )
    except KnowledgeIndexMismatch as exc:
        return _knowledge_failure(
            settings,
            result,
            code="index_rebuild_required",
            message=str(exc),
            http_status=409,
        )
    except KnowledgeEngineNotInstalled as exc:
        return _knowledge_failure(settings, result, code="engine_not_installed", message=str(exc) or "The knowledge engine is not installed", http_status=503)
    except KnowledgeUnavailable as exc:
        return _knowledge_failure(settings, result, code="knowledge_unavailable", message=f"Knowledge service unavailable: {type(exc).__name__}", http_status=503)
    except KnowledgeHTTPError as exc:
        return _knowledge_failure(settings, result, code="knowledge_submit_failed", message=str(exc), http_status=502)
    except Exception as exc:
        return _knowledge_failure(settings, result, code="knowledge_submit_failed", message=f"Knowledge submission failed: {type(exc).__name__}", http_status=502)

    track_id = str(response.get("track_id") or response.get("id") or "")
    result["knowledge"] = {
        "state": "submitted",
        "document_name": document_name,
        "track_id": track_id,
        "response": response,
    }
    result["lightrag"] = {"state": "submitted", "track_id": track_id}
    result["state"] = "ingesting" if track_id else "done"
    result["detail"] = "Submitted to the knowledge service; waiting for indexing" if track_id else "Knowledge service accepted the document"
    result["updated_at"] = storage.utc_now()
    result["timeline"] = list(result.get("timeline") or []) + [
        {"step": "knowledge", "state": result["state"], "at": result["updated_at"], "detail": result["detail"]}
    ]
    storage.write_result(settings, result["file_id"], result)
    return {"ok": True, "state": result["state"], "message": result["detail"], "knowledge": result["knowledge"], "duplicate_submit": False}


def refresh_knowledge_status(file_id: str) -> Dict[str, Any]:
    settings = get_ingest_settings()
    with _LOCK:
        result = storage.read_result(settings, file_id)
        if result is None:
            return {"ok": False, "http_status": 404, "message": "Ingestion job not found"}
        knowledge = result.get("knowledge") if isinstance(result.get("knowledge"), dict) else {}
        track_id = str(knowledge.get("track_id") or (result.get("lightrag") or {}).get("track_id") or "")
        if not track_id:
            return {"ok": False, "http_status": 409, "code": "no_track_id", "message": "This job has no knowledge track id"}
        try:
            response = get_knowledge_gateway().document_status(track_id)
        except KnowledgeEngineNotInstalled as exc:
            return {"ok": False, "http_status": 503, "code": "engine_not_installed", "message": str(exc)}
        except KnowledgeUnavailable as exc:
            return {"ok": False, "http_status": 503, "code": "knowledge_unavailable", "message": type(exc).__name__}
        except KnowledgeHTTPError as exc:
            return {"ok": False, "http_status": 502, "code": "knowledge_status_failed", "message": str(exc)}
        documents = response.get("documents") if isinstance(response.get("documents"), list) else []
        states = [str(item.get("status") or "").strip().lower() for item in documents if isinstance(item, dict)]
        total = int(response.get("total_count") or len(documents) or 0)
        done_count = sum(1 for state in states if state in ("processed", "completed", "success"))
        failed_count = sum(1 for state in states if state in ("failed", "error"))
        if total > 0 and done_count >= total:
            remote_state = "done"
        elif total > 0 and failed_count and done_count + failed_count >= total:
            remote_state = "failed"
        else:
            remote_state = "processing"
        progress = {
            "state": remote_state,
            "total": total,
            "processed": done_count,
            "failed": failed_count,
            "processing": max(total - done_count - failed_count, 0),
        }
        if remote_state == "done":
            result["state"] = "done"
            result["detail"] = "Knowledge indexing completed"
            knowledge["state"] = "done"
        elif remote_state == "failed":
            errors = [str(item.get("error_msg") or "") for item in documents if isinstance(item, dict) and str(item.get("status") or "").lower() in ("failed", "error")]
            result["state"] = "review"
            result["detail"] = "Knowledge indexing failed; review before retrying"
            knowledge["state"] = "failed"
            if any(errors):
                knowledge["message"] = errors[0][:500]
        else:
            result["state"] = "ingesting"
            result["detail"] = "Knowledge service is processing the document"
            knowledge["state"] = "ingesting"
        knowledge["last_status"] = response
        knowledge["progress"] = progress
        result["knowledge"] = knowledge
        result["lightrag"] = {"state": knowledge["state"], "track_id": track_id}
        result["updated_at"] = storage.utc_now()
        storage.write_result(settings, result["file_id"], result)
        return {"ok": True, "state": result["state"], "knowledge": knowledge, "response": response}


def _retry_result(settings: IngestSettings, result: Dict[str, Any]) -> Dict[str, Any]:
    result["state"] = "processing"
    result["detail"] = "Retrying local parsing"
    result["updated_at"] = storage.utc_now()
    storage.write_result(settings, result["file_id"], result)
    updated = _run_pipeline(settings, result)
    return {
        "ok": True,
        "state": updated.get("state"),
        "message": updated.get("detail") or "Retry completed",
        "duplicate_submit": False,
    }


__all__ = [
    "IngestError",
    "activate",
    "active_jobs",
    "confirm_mode",
    "content_for_edit",
    "edit_content",
    "pending_reviews",
    "process_upload",
    "session_allow_all",
    "set_session_allow_all",
    "status",
    "refresh_knowledge_status",
]
