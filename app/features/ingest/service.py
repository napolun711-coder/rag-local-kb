"""Upload, parse, quality gate, advice, and human review workflow."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.features.ingest import advice as advice_service
from app.features.ingest import parse as parse_service
from app.features.ingest import quality as quality_service
from app.features.ingest.config import IngestSettings, get_ingest_settings
from app.features.ingest import storage
from app.features.ocr.config import get_ocr_settings
from app.features.ocr import service as ocr_service
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
EDITABLE_STATES = REVIEW_STATES + ("skipped",)
ACTIONABLE_STATES = ("blocked", "failed")
ACTIVE_STATES = ("processing", "parsing", "checking", "ocr", "ingesting")
PENDING_STATES = ("review", "needs_ocr")
SKIPPED_RESTORE_HOURS = 24
RESTORE_LIMIT = 20
STALE_ACTIVE_MINUTES = 120
STALE_INGEST_MINUTES = 60
CAPTION_MARKER = "\n\n<!-- ragkb:manual-caption -->\n"


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
    *,
    ocr_used: bool = False,
    images_pending: Optional[int] = None,
) -> Dict[str, Any]:
    if quality.level != quality_service.LEVEL_PASS:
        return {}
    pending = len(parsed.assets) if images_pending is None else max(0, int(images_pending))
    if parsed.route == "image" and pending == 0 and images_pending is None:
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
        ocr_used=ocr_used,
        min_chars=settings.quality_min_chars,
        max_unocr_images=settings.quality_max_unocr_images,
    )
    return result.to_dict()


def _ocr_candidate(parsed: parse_service.ParseResult, settings: IngestSettings) -> bool:
    cfg = get_ocr_settings()
    if not cfg.configured or not parsed.assets:
        return False
    if parsed.route == "image":
        return True
    return bool(cfg.image_ocr)


def _apply_ocr(
    parsed: parse_service.ParseResult,
    settings: IngestSettings,
) -> Dict[str, Any]:
    cfg = get_ocr_settings()
    if not cfg.configured:
        return {"used": False, "attempted": False, "reason": "OCR 当前关闭"}
    if not _ocr_candidate(parsed, settings):
        reason = "文档没有可识别图片" if not parsed.assets else "内嵌图片 OCR 已关闭"
        return {"used": False, "attempted": False, "reason": reason}
    ocr = ocr_service.recognize_assets(parsed.assets, settings=cfg)
    text = str(ocr.pop("text", "") or "").strip()
    if text:
        combined = (parsed.text.rstrip() + "\n\n" + text).strip()
        limit = settings.parse_max_chars
        if limit > 0 and len(combined) > limit:
            combined = combined[:limit]
            parsed.truncated = True
        parsed.text = combined
    return {
        **ocr,
        "attempted": True,
        "mode": cfg.mode,
        "reason": "" if ocr.get("used") else "本机 OCR 未识别出文字",
    }


def _is_image_job(result: Dict[str, Any]) -> bool:
    parsed = result.get("parse") if isinstance(result.get("parse"), dict) else {}
    return bool(result.get("is_image")) or str(parsed.get("route") or "") == "image"


def _short_doc_id(file_id: str) -> str:
    value = str(file_id or "")
    if not storage.valid_file_id(value):
        return ""
    return value.rsplit("-", 1)[-1][:4]


def _doc_name_stem(result: Dict[str, Any]) -> str:
    raw = str(result.get("doc_name") or Path(str(result.get("filename") or "document")).stem or "document")
    stem = raw.strip()
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    suffix = "_" + _short_doc_id(str(result.get("file_id") or ""))
    if _is_image_job(result) and suffix != "_" and stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    stem = stem.replace("/", "_").replace("\\", "_").strip()
    return (stem or "document")[:120]


def _doc_id_for(result: Dict[str, Any]) -> str:
    return _short_doc_id(str(result.get("file_id") or "")) if _is_image_job(result) else ""


def _document_name(result: Dict[str, Any]) -> str:
    stem = _doc_name_stem(result)
    short_id = _doc_id_for(result)
    return f"{stem}_{short_id}.md" if short_id else f"{stem}.md"


def _extract_caption(content: str) -> str:
    text = str(content or "")
    index = text.find(CAPTION_MARKER)
    return text[index + len(CAPTION_MARKER):].strip() if index >= 0 else ""


def _compose_caption(content: str, caption: str) -> str:
    base = str(content or "")
    block = CAPTION_MARKER + str(caption or "").strip()
    index = base.find(CAPTION_MARKER)
    if index >= 0:
        return base[:index].rstrip() + block
    if base.strip():
        return base.rstrip() + block
    return str(caption or "").strip()


def _cutoff(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours or 1)))


def _updated_after(value: Any, cutoff: datetime) -> bool:
    try:
        moment = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment >= cutoff


def _older_than(value: Any, minutes: int) -> bool:
    try:
        moment = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment <= datetime.now(timezone.utc) - timedelta(minutes=max(1, int(minutes)))


def _restore_items(
    states: Tuple[str, ...],
    *,
    limit: int = RESTORE_LIMIT,
) -> List[Dict[str, Any]]:
    settings = get_ingest_settings()
    items = [
        _public_status(item)
        for item in storage.list_results(settings)
        if str(item.get("state") or "") in states
    ]
    items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return items[:limit]


def _run_pipeline(
    settings: IngestSettings,
    result: Dict[str, Any],
    *,
    force_ocr: bool = False,
    allow_session_auto: bool = True,
) -> Dict[str, Any]:
    source = storage.resolve_display_path(str(result.get("source_path") or ""))
    timeline = list(result.get("timeline") or [])
    timeline.append({"step": "parse", "state": "checking", "at": storage.utc_now(), "detail": ""})
    result["timeline"] = timeline

    parsed = parse_service.parse_file(source, str(result.get("filename") or source.name))
    if not parsed.ok:
        result["parse"] = _parse_summary(parsed)
        result["state"] = "failed"
        result["detail"] = parsed.detail or "Local parsing failed"
        result["quality"] = {}
        result["advice"] = {}
        result["updated_at"] = storage.utc_now()
        timeline.append({"step": "parse", "state": "failed", "at": result["updated_at"], "detail": result["detail"]})
        storage.write_result(settings, result["file_id"], result)
        return result

    ocr_cfg = get_ocr_settings()
    ocr_runtime = ocr_service.status(ocr_cfg)
    ocr_available = bool(ocr_runtime.get("enabled"))
    ocr_meta: Dict[str, Any] = {
        "used": False,
        "attempted": False,
        "enabled": ocr_available,
        "provider": "local",
        "external": False,
    }
    if force_ocr or (ocr_available and ocr_cfg.mode == "auto"):
        ocr_meta.update(_apply_ocr(parsed, settings))
        if ocr_meta.get("attempted"):
            timeline.append(
                {
                    "step": "ocr",
                    "state": "done" if ocr_meta.get("used") else "failed",
                    "at": storage.utc_now(),
                    "detail": str(ocr_meta.get("reason") or ""),
                }
            )
    result["ocr"] = ocr_meta
    result["ocr_enabled"] = ocr_available
    result["ocr_external"] = False
    result["parse"] = _parse_summary(parsed)
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
    embedded_pending = 0
    if parsed.route == "image":
        embedded_pending = int(parsed.images or 1) if parsed.assets else 0
    elif ocr_cfg.image_ocr:
        embedded_pending = len(parsed.assets)
    if ocr_meta.get("attempted"):
        embedded_pending = int(ocr_meta.get("failed") or 0)
    result["advice"] = _advice_for(
        parsed.text,
        parsed,
        report,
        settings,
        ocr_used=bool(ocr_meta.get("used")),
        images_pending=embedded_pending,
    )
    result["content_chars"] = int(parsed.char_total)
    result["image_total"] = int(parsed.images or 0)
    result["unocr_images"] = embedded_pending
    result["is_image"] = parsed.route == "image"

    if report.level == quality_service.LEVEL_PASS:
        result["state"] = "review"
        result["detail"] = "已通过本地质量校验，等待你确认入库"
        if allow_session_auto and _SESSION_ALLOW_ALL:
            result["detail"] = "已通过本地质量校验，按本次运行设置直接提交知识库"
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
    if allow_session_auto and _SESSION_ALLOW_ALL and result["state"] == "review":
        _submit_to_knowledge(settings, result)
    return result


def process_upload(
    filename: str,
    content: bytes,
    content_type: str = "",
    *,
    allow_session_auto: bool = True,
) -> Dict[str, Any]:
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
            "detail": "已接收，正在本地解析",
            "created_at": created,
            "updated_at": created,
            "timeline": [{"step": "upload", "state": "done", "at": created, "detail": ""}],
            "lightrag": {"state": "not_submitted", "track_id": ""},
            "knowledge": {"state": "not_submitted", "track_id": ""},
            "knowledge_gateway_ready": False,
        }
        storage.write_result(settings, file_id, result)
        return _public_status(
            _run_pipeline(settings, result, allow_session_auto=allow_session_auto)
        )


def _can(status: Dict[str, Any]) -> Dict[str, bool]:
    state = str(status.get("state") or "")
    knowledge = status.get("knowledge") if isinstance(status.get("knowledge"), dict) else {}
    knowledge_state = str(knowledge.get("state") or "not_submitted")
    track_id = str(knowledge.get("track_id") or (status.get("lightrag") or {}).get("track_id") or "")
    editable = state in EDITABLE_STATES
    forceable = state in ("blocked", "needs_ocr") and knowledge_state not in ("submitted", "ingesting", "done")
    return {
        "ingest": state == "review" and knowledge_state not in ("submitted", "ingesting", "done"),
        "force": forceable,
        "ocr": (
            state in ("needs_ocr", "blocked")
            and int(status.get("image_total") or 0) > 0
            and bool(ocr_service.status(get_ocr_settings()).get("enabled"))
        ),
        "retry": state in ("failed", "blocked", "needs_ocr"),
        "resume": state == "skipped",
        "skip": state in REVIEW_STATES,
        "edit": editable,
        "refresh": bool(track_id) and state in ("ingesting", "done"),
    }


def _public_status(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(result)
    knowledge = payload.get("knowledge") if isinstance(payload.get("knowledge"), dict) else {}
    payload["progress"] = dict(knowledge.get("progress") or {})
    ocr = payload.get("ocr") if isinstance(payload.get("ocr"), dict) else {}
    payload["ocr"] = {
        **ocr,
        "used": bool(ocr.get("used")),
        "scope": "local" if ocr.get("used") else "",
        "enabled": bool(ocr_service.status(get_ocr_settings()).get("enabled")),
        "external": False,
    }
    payload["can"] = _can(payload)
    payload["knowledge"] = dict(knowledge)
    lightrag = payload.get("lightrag") if isinstance(payload.get("lightrag"), dict) else {}
    payload["knowledge_error"] = str(
        knowledge.get("message")
        or lightrag.get("error")
        or (knowledge.get("last_status") or {}).get("message")
        or ""
    )
    payload["gateway_ready"] = str(knowledge.get("state") or "") in ("submitted", "ingesting", "done")
    return payload


def status(file_id: str) -> Optional[Dict[str, Any]]:
    settings = get_ingest_settings()
    result = storage.read_result(settings, file_id)
    return _public_status(result) if result else None


def pending_reviews() -> List[Dict[str, Any]]:
    """Jobs waiting for an explicit ingest/skip decision."""
    return _restore_items(PENDING_STATES)


def actionable_jobs() -> List[Dict[str, Any]]:
    """Jobs that need a retry or a content correction."""
    return _restore_items(ACTIONABLE_STATES)


def _recover_stale_active() -> None:
    """A process crash during local parsing would otherwise leave a job at 'processing' forever."""
    settings = get_ingest_settings()
    cutoff = _cutoff(max(1, STALE_ACTIVE_MINUTES // 60))
    for item in storage.list_results(settings):
        state = str(item.get("state") or "")
        if state not in ("processing", "parsing", "checking", "ocr"):
            continue
        if _updated_after(item.get("updated_at"), cutoff):
            continue
        item["state"] = "failed"
        item["detail"] = "处理中断，请重试"
        item["knowledge"] = {"state": "not_submitted", "track_id": ""}
        item["lightrag"] = {"state": "not_submitted", "track_id": ""}
        item["updated_at"] = storage.utc_now()
        item["timeline"] = list(item.get("timeline") or []) + [{
            "step": "recover",
            "state": "failed",
            "at": item["updated_at"],
            "detail": "Stale active job recovered after restart",
        }]
        storage.write_result(settings, item["file_id"], item)


def active_jobs() -> List[Dict[str, Any]]:
    """Jobs still being parsed or submitted to the knowledge service."""
    _recover_stale_active()
    return _restore_items(ACTIVE_STATES)


def skipped_jobs(*, hours: int = SKIPPED_RESTORE_HOURS) -> Tuple[List[Dict[str, Any]], int]:
    """Recently skipped jobs for chat restore, plus the total skipped count."""
    settings = get_ingest_settings()
    cutoff = _cutoff(hours)
    all_items = [
        _public_status(item)
        for item in storage.list_results(settings)
        if str(item.get("state") or "") == "skipped"
    ]
    all_items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    recent = [
        item for item in all_items
        if _updated_after(item.get("updated_at"), cutoff)
    ]
    return recent[:RESTORE_LIMIT], len(all_items)


def session_allow_all() -> bool:
    return _SESSION_ALLOW_ALL


def _release_review_jobs() -> None:
    settings = get_ingest_settings()
    for item in storage.list_results(settings):
        if str(item.get("state") or "") != "review":
            continue
        with _LOCK:
            fresh = storage.read_result(settings, str(item.get("file_id") or ""))
            if fresh is None or str(fresh.get("state") or "") != "review":
                continue
            _submit_to_knowledge(settings, fresh)


def set_session_allow_all(value: bool) -> Dict[str, Any]:
    global _SESSION_ALLOW_ALL
    _SESSION_ALLOW_ALL = bool(value)
    released = 0
    if _SESSION_ALLOW_ALL:
        settings = get_ingest_settings()
        released = sum(
            1 for item in storage.list_results(settings)
            if str(item.get("state") or "") == "review"
        )
        if released:
            threading.Thread(
                target=_release_review_jobs,
                name="ingest-release-reviews",
                daemon=True,
            ).start()
    return {
        "session_allow_all": _SESSION_ALLOW_ALL,
        "allow_all": _SESSION_ALLOW_ALL,
        "released": released,
    }


def confirm_mode() -> str:
    return "always"


def content_for_edit(file_id: str) -> Optional[Dict[str, Any]]:
    settings = get_ingest_settings()
    raw = storage.read_result(settings, file_id)
    if raw is None:
        return None
    result = _public_status(raw)
    content = storage.read_content(settings, file_id)
    state = str(result.get("state") or "")
    editable = state in EDITABLE_STATES
    edit = result.get("edit") if isinstance(result.get("edit"), dict) else {}
    return {
        "file_id": file_id,
        "name": str(result.get("filename") or result.get("doc_name") or file_id),
        "doc_name": str(result.get("doc_name") or ""),
        "doc_name_stem": _doc_name_stem(result),
        "doc_id": _doc_id_for(result),
        "content": content,
        "chars": len(content),
        "body_chars": len(content),
        "caption_text": str(result.get("caption_text") or _extract_caption(content)),
        "manual_text": bool(result.get("manual_text")),
        "is_image": _is_image_job(result),
        "editable": editable,
        "reason": "" if editable else "当前状态不能修改正文",
        "edit": {
            "count": int(edit.get("count") or 0),
            "updated_at": str(edit.get("updated_at") or ""),
            "backup": str(edit.get("backup") or ""),
        },
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
        if str(result.get("state") or "") not in EDITABLE_STATES:
            return {"ok": False, "http_status": 409, "message": "This job cannot be edited"}
        text = str(content or "")
        if not text.strip():
            return {"ok": False, "http_status": 400, "message": "Content cannot be empty"}
        if len(text) > CONTENT_MAX_CHARS:
            return {"ok": False, "http_status": 413, "message": "Content is too large"}

        previous = storage.read_content(settings, file_id)
        if caption:
            # 纯图片的第一段描述就是全文；再次保存描述时应整体替换，而不是把旧描述当正文再追加。
            if (previous and CAPTION_MARKER not in previous
                    and bool(result.get("manual_text")) and _is_image_job(result)):
                composed = text
            else:
                composed = _compose_caption(previous, text)
        else:
            composed = text
        if previous and previous != composed:
            backup = storage.content_path(settings, file_id).with_name("content.before-edit.md")
            backup.write_text(previous, encoding="utf-8")
            edit = result.get("edit") if isinstance(result.get("edit"), dict) else {}
            result["edit"] = {
                "count": int(edit.get("count") or 0) + 1,
                "updated_at": storage.utc_now(),
                "backup": backup.name,
            }
        storage.write_content(settings, file_id, composed)
        route = str((result.get("parse") or {}).get("route") or "text")
        pages = int((result.get("parse") or {}).get("pages") or 1)
        report = quality_service.evaluate(
            composed,
            route=route,
            pages=pages,
            options=quality_service.options_from_settings(settings),
            routes=settings.quality_ocr_routes,
        )
        result["quality"] = report.to_dict()
        result["content_chars"] = int(report.metrics.get("char_total") or 0)
        result["manual_text"] = bool(caption) or CAPTION_MARKER in composed
        result["caption_text"] = (
            str(text).strip() if caption
            else (_extract_caption(composed) if result["manual_text"] else "")
        )
        if doc_name.strip():
            result["doc_name"] = doc_name.strip()[:120]
        parsed = parse_service.ParseResult(
            ok=True,
            route=route,
            text=composed,
            pages=pages,
            images=int(result.get("image_total") or 0),
        )
        result["advice"] = _advice_for(composed, parsed, report, settings)
        result["state"] = "review" if report.passed else ("needs_ocr" if report.needs_ocr else "blocked")
        result["detail"] = "已保存并重新质检，等待你确认入库"
        result["knowledge"] = {"state": "not_submitted", "track_id": ""}
        result["lightrag"] = {"state": "not_submitted", "track_id": ""}
        result["updated_at"] = storage.utc_now()
        result["timeline"] = list(result.get("timeline") or []) + [
            {"step": "edit", "state": result["state"], "at": result["updated_at"], "detail": ""}
        ]
        storage.write_result(settings, file_id, result)

        ingested = False
        ingest_error = ""
        if result["state"] == "review" and ingest_now:
            submitted = _submit_to_knowledge(settings, result)
            ingested = bool(submitted.get("ok"))
            if not ingested:
                ingest_error = str(submitted.get("message") or "知识库提交失败")
                result["detail"] = f"正文已保存，但入库失败：{ingest_error}"
                result["updated_at"] = storage.utc_now()
                storage.write_result(settings, file_id, result)
        elif ingest_now:
            ingest_error = "质量校验未通过，未提交知识库"

        return {
            "ok": True,
            "saved": True,
            "state": result["state"],
            "message": result["detail"],
            "chars": len(composed),
            "edited": True,
            "ingested": ingested,
            "ingest_error": ingest_error,
            "status": _public_status(result),
            "gateway_ready": str((result.get("knowledge") or {}).get("state") or "") in (
                "submitted",
                "ingesting",
                "done",
            ),
        }


def activate(file_id: str, action: str) -> Dict[str, Any]:
    settings = get_ingest_settings()
    action = str(action or "").strip().lower()
    with _LOCK:
        result = storage.read_result(settings, file_id)
        if result is None:
            return {"ok": False, "http_status": 404, "message": "Ingestion job not found"}
        state = str(result.get("state") or "")
        knowledge = result.get("knowledge") if isinstance(result.get("knowledge"), dict) else {}
        knowledge_state = str(knowledge.get("state") or "")
        if action == "skip":
            if state not in REVIEW_STATES:
                return {
                    "ok": False,
                    "http_status": 409,
                    "message": "只有待确认的文件可以跳过",
                    "status": _public_status(result),
                }
            result["skipped_from"] = state
            result["state"] = "skipped"
            result["detail"] = "已跳过：本机解析结果与原件保留，可稍后继续入库"
        elif action == "retry":
            if state not in ("failed", "blocked", "needs_ocr"):
                return {
                    "ok": False,
                    "http_status": 409,
                    "message": "这份文件当前不需要重试",
                    "status": _public_status(result),
                }
            return _retry_result(settings, result)
        elif action == "resume":
            if state != "skipped":
                return {
                    "ok": False,
                    "http_status": 409,
                    "message": "只有已跳过的文件可以继续入库",
                    "status": _public_status(result),
                }
            if not storage.read_content(settings, file_id).strip():
                return {
                    "ok": False,
                    "http_status": 409,
                    "message": "解析正文为空，不能入库",
                    "status": _public_status(result),
                }
            result["state"] = "review"
            result["detail"] = "正在重新提交跳过的内容"
            result["updated_at"] = storage.utc_now()
            result["timeline"] = list(result.get("timeline") or []) + [{
                "step": "resume",
                "state": "done",
                "at": result["updated_at"],
                "detail": "用户继续入库已跳过的文件",
            }]
            return _submit_to_knowledge(settings, result)
        elif action == "force":
            if state not in ("blocked", "needs_ocr"):
                return {
                    "ok": False,
                    "http_status": 409,
                    "message": "只有未通过质检的内容才需要人工放行",
                    "status": _public_status(result),
                }
            if knowledge_state in ("submitting", "submitted", "ingesting", "done"):
                return {
                    "ok": True,
                    "state": state,
                    "message": "入库已在进行中",
                    "duplicate_submit": True,
                    "status": _public_status(result),
                }
            if not storage.read_content(settings, file_id).strip():
                return {
                    "ok": False,
                    "http_status": 409,
                    "message": "解析正文为空，不能入库",
                    "status": _public_status(result),
                }
            result["forced"] = True
            result["state"] = "review"
            result["detail"] = "已按你的选择人工放行，正在提交知识库"
            result["updated_at"] = storage.utc_now()
            result["timeline"] = list(result.get("timeline") or []) + [{
                "step": "force",
                "state": "done",
                "at": result["updated_at"],
                "detail": "用户确认跳过质量门结论，人工放行入库",
            }]
            return _submit_to_knowledge(settings, result)
        elif action == "ingest":
            if state == "done" or knowledge_state == "done":
                return {
                    "ok": True,
                    "state": state,
                    "message": "这份资料已经入库",
                    "duplicate_submit": True,
                    "status": _public_status(result),
                }
            if state == "ingesting" or knowledge_state in ("submitting", "submitted", "ingesting"):
                return {
                    "ok": True,
                    "state": state,
                    "message": "入库已在进行中",
                    "duplicate_submit": True,
                    "status": _public_status(result),
                }
            return _submit_to_knowledge(settings, result)
        elif action == "ocr":
            if state not in ("needs_ocr", "blocked"):
                return {
                    "ok": False,
                    "http_status": 409,
                    "message": "这份文件当前不需要 OCR",
                    "status": _public_status(result),
                }
            return _ocr_result(settings, result)
        else:
            return {"ok": False, "http_status": 400, "message": "Unknown action"}
        result["updated_at"] = storage.utc_now()
        storage.write_result(settings, file_id, result)
        return {
            "ok": True,
            "state": result["state"],
            "message": result["detail"],
            "duplicate_submit": False,
            "status": _public_status(result),
        }


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
    return {
        "ok": False,
        "http_status": http_status,
        "code": code,
        "message": message,
        "state": "review",
        "status": _public_status(result),
    }


def _submit_to_knowledge(settings: IngestSettings, result: Dict[str, Any]) -> Dict[str, Any]:
    if str(result.get("state") or "") != "review":
        return {
            "ok": False,
            "http_status": 409,
            "message": "只有待确认的内容可以提交知识库",
            "status": _public_status(result),
        }
    content = storage.read_content(settings, result["file_id"])
    if not content.strip():
        return {
            "ok": False,
            "http_status": 409,
            "message": "解析正文为空，不能入库",
            "status": _public_status(result),
        }
    document_name = _document_name(result)
    submitted_at = storage.utc_now()
    result["state"] = "ingesting"
    result["detail"] = "正在提交知识库"
    result["submitted_at"] = submitted_at
    result["knowledge"] = {
        "state": "submitting",
        "document_name": document_name,
        "track_id": "",
        "submitted_at": submitted_at,
    }
    result["lightrag"] = {"state": "submitting", "track_id": ""}
    result["updated_at"] = submitted_at
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
        return _knowledge_failure(
            settings,
            result,
            code="engine_not_installed",
            message=str(exc) or "知识库引擎未安装或未启用",
            http_status=503,
        )
    except KnowledgeUnavailable as exc:
        return _knowledge_failure(
            settings,
            result,
            code="knowledge_unavailable",
            message=f"知识库服务不可用（{type(exc).__name__}）",
            http_status=503,
        )
    except KnowledgeHTTPError as exc:
        return _knowledge_failure(
            settings,
            result,
            code="knowledge_submit_failed",
            message=f"知识库提交失败：{exc}",
            http_status=502,
        )
    except Exception as exc:
        return _knowledge_failure(
            settings,
            result,
            code="knowledge_submit_failed",
            message=f"知识库提交失败（{type(exc).__name__}）",
            http_status=502,
        )

    track_id = str(response.get("track_id") or response.get("id") or "")
    result["knowledge"] = {
        "state": "submitted",
        "document_name": document_name,
        "track_id": track_id,
        "submitted_at": submitted_at,
        "response": response,
    }
    result["lightrag"] = {"state": "submitted", "track_id": track_id}
    result["state"] = "ingesting" if track_id else "done"
    result["detail"] = "已提交知识库，正在解析并建立索引" if track_id else "知识库已接收文档"
    result["updated_at"] = storage.utc_now()
    result["timeline"] = list(result.get("timeline") or []) + [
        {"step": "knowledge", "state": result["state"], "at": result["updated_at"], "detail": result["detail"]}
    ]
    storage.write_result(settings, result["file_id"], result)
    return {
        "ok": True,
        "state": result["state"],
        "message": result["detail"],
        "knowledge": result["knowledge"],
        "duplicate_submit": False,
        "status": _public_status(result),
    }


def refresh_knowledge_status(file_id: str) -> Dict[str, Any]:
    settings = get_ingest_settings()
    with _LOCK:
        result = storage.read_result(settings, file_id)
        if result is None:
            return {"ok": False, "http_status": 404, "message": "Ingestion job not found"}
        knowledge = result.get("knowledge") if isinstance(result.get("knowledge"), dict) else {}
        track_id = str(knowledge.get("track_id") or (result.get("lightrag") or {}).get("track_id") or "")
        if not track_id:
            return {
                "ok": False,
                "http_status": 409,
                "code": "no_track_id",
                "message": "这份文件还没有知识库跟踪号",
                "status": _public_status(result),
            }
        try:
            response = get_knowledge_gateway().document_status(track_id)
        except KnowledgeEngineNotInstalled as exc:
            return {
                "ok": False,
                "http_status": 503,
                "code": "engine_not_installed",
                "message": str(exc) or "知识库引擎未安装或未启用",
                "status": _public_status(result),
            }
        except KnowledgeUnavailable as exc:
            return {
                "ok": False,
                "http_status": 503,
                "code": "knowledge_unavailable",
                "message": f"知识库服务不可用（{type(exc).__name__}）",
                "status": _public_status(result),
            }
        except KnowledgeHTTPError as exc:
            return {
                "ok": False,
                "http_status": 502,
                "code": "knowledge_status_failed",
                "message": f"知识库状态查询失败：{exc}",
                "status": _public_status(result),
            }
        documents = response.get("documents") if isinstance(response.get("documents"), list) else []
        states = [str(item.get("status") or "").strip().lower() for item in documents if isinstance(item, dict)]
        total = int(response.get("total_count") or len(documents) or 0)
        done_count = sum(1 for state in states if state in ("processed", "completed", "success"))
        failed_count = sum(1 for state in states if state in ("failed", "error"))
        submitted_at = (
            knowledge.get("submitted_at")
            or result.get("submitted_at")
            or result.get("created_at")
        )
        if total > 0 and done_count >= total:
            remote_state = "done"
        elif total > 0 and failed_count and done_count + failed_count >= total:
            remote_state = "failed"
        elif total == 0 and _older_than(submitted_at, STALE_INGEST_MINUTES):
            remote_state = "missing"
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
            result["detail"] = "知识库索引已完成"
            knowledge["state"] = "done"
            knowledge.pop("message", None)
        elif remote_state == "failed":
            errors = [str(item.get("error_msg") or "") for item in documents if isinstance(item, dict) and str(item.get("status") or "").lower() in ("failed", "error")]
            result["state"] = "review"
            result["detail"] = "知识库索引失败，请查看原因后重试"
            knowledge["state"] = "failed"
            if any(errors):
                knowledge["message"] = errors[0][:500]
        elif remote_state == "missing":
            result["state"] = "review"
            result["detail"] = "知识库没有返回这份文档的解析记录，请重新提交或删除"
            knowledge["state"] = "failed"
            knowledge["code"] = "track_not_found"
            knowledge["message"] = "知识库未返回该跟踪号的文档（可能未成功受理）"
        else:
            result["state"] = "ingesting"
            result["detail"] = "知识库正在解析并建立索引"
            knowledge["state"] = "ingesting"
        knowledge["last_status"] = response
        knowledge["progress"] = progress
        result["knowledge"] = knowledge
        result["lightrag"] = {"state": knowledge["state"], "track_id": track_id}
        result["updated_at"] = storage.utc_now()
        storage.write_result(settings, result["file_id"], result)
        return {
            "ok": True,
            "state": result["state"],
            "knowledge": knowledge,
            "response": response,
            "status": _public_status(result),
        }


def _retry_result(settings: IngestSettings, result: Dict[str, Any]) -> Dict[str, Any]:
    result["state"] = "processing"
    result["detail"] = "正在重新解析"
    result["updated_at"] = storage.utc_now()
    storage.write_result(settings, result["file_id"], result)
    updated = _run_pipeline(settings, result)
    return {
        "ok": True,
        "state": updated.get("state"),
        "message": updated.get("detail") or "重试完成",
        "duplicate_submit": False,
        "status": _public_status(updated),
    }


def _ocr_result(settings: IngestSettings, result: Dict[str, Any]) -> Dict[str, Any]:
    if not ocr_service.status(get_ocr_settings()).get("enabled"):
        return {
            "ok": False,
            "http_status": 409,
            "code": "ocr_unavailable",
            "message": "本机 OCR 未启用或当前不可用",
            "status": _public_status(result),
        }
    result["state"] = "processing"
    result["detail"] = "正在执行本地 OCR"
    result["updated_at"] = storage.utc_now()
    storage.write_result(settings, result["file_id"], result)
    updated = _run_pipeline(settings, result, force_ocr=True)
    ocr = updated.get("ocr") if isinstance(updated.get("ocr"), dict) else {}
    if not ocr.get("used"):
        return {
            "ok": False,
            "http_status": 409,
            "code": "ocr_no_text",
            "message": ocr.get("reason") or "本机 OCR 未识别出文字",
            "status": _public_status(updated),
        }
    return {
        "ok": True,
        "state": updated.get("state"),
        "message": updated.get("detail") or "本地 OCR 已完成",
        "ocr": ocr,
        "status": _public_status(updated),
        "duplicate_submit": False,
    }


__all__ = [
    "EDITABLE_STATES",
    "IngestError",
    "RESTORE_LIMIT",
    "SKIPPED_RESTORE_HOURS",
    "activate",
    "actionable_jobs",
    "active_jobs",
    "confirm_mode",
    "content_for_edit",
    "edit_content",
    "pending_reviews",
    "process_upload",
    "session_allow_all",
    "set_session_allow_all",
    "skipped_jobs",
    "status",
    "refresh_knowledge_status",
]
