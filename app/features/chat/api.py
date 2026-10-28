"""Local-only chat and session APIs compatible with the existing web client."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.adapters.lightrag.errors import KnowledgeEngineNotInstalled, KnowledgeHTTPError, KnowledgeUnavailable
from app.features.chat import sessions
from app.features.chat.contracts import (
    ChatRequest,
    HistorySearchResponse,
    SessionCreateRequest,
    SessionImportRequest,
    SessionPatchRequest,
)
from app.features.chat.service import ChatService
from app.features.memory import chat_bridge


def _require_local(request: Request) -> None:
    host = (request.client.host if request.client else "").strip()
    if host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="This API is local-only")


def _knowledge_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KnowledgeEngineNotInstalled):
        return HTTPException(status_code=503, detail={"error": "engine_not_installed", "message": str(exc)})
    if isinstance(exc, KnowledgeUnavailable):
        return HTTPException(status_code=503, detail={"error": "knowledge_unavailable", "message": str(exc)})
    if isinstance(exc, KnowledgeHTTPError):
        return HTTPException(status_code=502, detail={"error": "knowledge_error", "message": str(exc)})
    return HTTPException(status_code=502, detail={"error": "chat_failed", "message": type(exc).__name__})


def _session_error(exc: Exception) -> HTTPException:
    if isinstance(exc, sessions.SessionsUnavailable):
        return HTTPException(status_code=503, detail={"error": "sessions_unavailable", "message": str(exc)})
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail={"error": "session_not_found", "message": "Session not found"})
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail={"error": "invalid_session", "message": str(exc)})
    return HTTPException(status_code=500, detail={"error": "session_failed", "message": type(exc).__name__})


router = APIRouter(tags=["chat"], dependencies=[Depends(_require_local)])


def _line(event: Dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


@router.post("/chat")
def chat(data: ChatRequest, response: Response) -> Dict[str, Any]:
    try:
        payload, persistence, context_status = ChatService().answer(data)
    except Exception as exc:
        raise _knowledge_error(exc) from exc
    if persistence is not None:
        response.headers["X-Session-Id"] = persistence.session_id
    response.headers["X-Context-Status"] = str(context_status.get("status") or "")
    response.headers["X-Context-Reason"] = str(context_status.get("reason") or "")
    return payload


@router.post("/chat/stream")
def chat_stream(data: ChatRequest):
    try:
        persistence, context_status, source, events = ChatService().stream(data)
    except Exception as exc:
        raise _knowledge_error(exc) from exc

    wrapped = chat_bridge.wrap_stream(
        events,
        persistence,
        source=source,
        context_status=context_status,
        bypass=data.force_bypass,
    )

    def lines() -> Iterator[str]:
        for event in wrapped:
            yield _line(event)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "X-Context-Status": str(context_status.get("status") or ""),
        "X-Context-Reason": str(context_status.get("reason") or ""),
    }
    if persistence is not None:
        headers["X-Session-Id"] = persistence.session_id
    return StreamingResponse(lines(), media_type="application/x-ndjson", headers=headers)


@router.get("/api/sessions/status")
def sessions_status() -> Dict[str, Any]:
    return {"enabled": sessions.enabled()}


@router.get("/api/sessions")
def list_sessions(limit: int = Query(default=200, ge=1, le=500)) -> Dict[str, Any]:
    return sessions.list_sessions(limit=limit)


@router.get("/api/history/search", response_model=HistorySearchResponse)
def search_history(
    q: str = Query(..., min_length=2, max_length=200),
    topic_id: str = Query(default="", max_length=200),
    limit: int = Query(default=10, ge=1, le=20),
) -> Dict[str, Any]:
    """Local-only read-only search across persisted chat history."""
    try:
        return sessions.search_history(q, topic_id=topic_id, limit=limit)
    except Exception as exc:
        raise _session_error(exc) from exc


@router.post("/api/sessions")
def create_session(data: SessionCreateRequest) -> Dict[str, Any]:
    try:
        return sessions.create_session(title=data.title, topic_id=data.topic_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@router.post("/api/sessions/import")
def import_sessions(data: SessionImportRequest) -> Dict[str, Any]:
    try:
        return sessions.import_sessions({"sessions": data.sessions})
    except Exception as exc:
        raise _session_error(exc) from exc


@router.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> Dict[str, Any]:
    try:
        return sessions.get_session(session_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@router.get("/api/sessions/{session_id}/messages")
def get_session_messages(session_id: str, limit: int = Query(default=1000, ge=1, le=5000)) -> Dict[str, Any]:
    try:
        return sessions.list_messages(session_id, limit=limit)
    except Exception as exc:
        raise _session_error(exc) from exc


@router.patch("/api/sessions/{session_id}")
def patch_session(session_id: str, data: SessionPatchRequest) -> Dict[str, Any]:
    try:
        return sessions.update_session(session_id, data.model_dump(exclude_unset=True))
    except Exception as exc:
        raise _session_error(exc) from exc


@router.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> Dict[str, Any]:
    try:
        return sessions.delete_session(session_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@router.delete("/api/sessions/{session_id}/messages")
def clear_session_messages(session_id: str) -> Dict[str, Any]:
    try:
        return sessions.clear_messages(session_id)
    except Exception as exc:
        raise _session_error(exc) from exc


@router.post("/api/sessions/{session_id}/end")
def end_session(session_id: str) -> Dict[str, Any]:
    try:
        return sessions.end_session(session_id)
    except Exception as exc:
        raise _session_error(exc) from exc


__all__ = ["router"]
