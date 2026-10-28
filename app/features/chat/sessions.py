"""Application service for server-side chat sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.features.memory import runtime


class SessionsUnavailable(RuntimeError):
    pass


def _store():
    store = runtime.get_store()
    if store is None:
        raise SessionsUnavailable("Memory-backed sessions are disabled or unavailable")
    return store


def _ms(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def session_view(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "title": str(item.get("title") or "新对话"),
        "topic_id": item.get("topic_id"),
        "message_count": int(item.get("message_count") or 0),
        "status": str(item.get("status") or "active"),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "created_ts": _ms(item.get("created_at")),
        "updated_ts": _ms(item.get("updated_at")),
    }


def message_view(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    return {
        "id": str(item.get("id") or ""),
        "session_id": str(item.get("session_id") or ""),
        "role": "user" if str(item.get("role") or "") == "user" else "ai",
        "text": str(item.get("content") or ""),
        "status": str(item.get("status") or "complete"),
        "ts": _ms(item.get("created_at")),
        "created_at": str(item.get("created_at") or ""),
        "references": item.get("references") if isinstance(item.get("references"), list) else [],
        "generation": meta.get("generation"),
        "retry_upstream": bool(meta.get("retry_upstream")),
        "upstream_retry": bool(meta.get("upstream_retry")),
    }


def enabled() -> bool:
    return runtime.get_store() is not None


def list_sessions(limit: int = 200) -> Dict[str, Any]:
    store = runtime.get_store()
    if store is None:
        return {"enabled": False, "sessions": []}
    rows = store.list_sessions(limit=max(1, min(500, int(limit or 200))))
    return {"enabled": True, "sessions": [session_view(item) for item in rows]}


def create_session(*, title: str = "", topic_id: str = "") -> Dict[str, Any]:
    item = _store().create_session(title=title, topic_id=topic_id or None)
    return {"enabled": True, "session": session_view(item)}


def get_session(session_id: str) -> Dict[str, Any]:
    item = _store().get_session(session_id)
    if item is None:
        raise KeyError(session_id)
    return {"enabled": True, "session": session_view(item)}


def list_messages(session_id: str, *, limit: int = 1000) -> Dict[str, Any]:
    rows = _store().list_messages(session_id, limit=max(1, min(5000, int(limit or 1000))))
    return {"enabled": True, "messages": [message_view(item) for item in rows]}


def search_history(
    query: str,
    *,
    topic_id: str = "",
    limit: int = 10,
) -> Dict[str, Any]:
    """只读检索已完成的历史消息；未启用持久化时正常降级为空结果。"""
    store = runtime.get_store()
    if store is None:
        return {"enabled": False, "count": 0, "items": []}
    rows = store.search_history(
        query,
        topic_id=topic_id,
        limit=max(1, min(20, int(limit or 10))),
    )
    return {"enabled": True, "count": len(rows), "items": rows}


def update_session(session_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    item = _store().update_session(
        session_id,
        title=patch.get("title"),
        topic_id=patch.get("topic_id"),
        status=patch.get("status"),
    )
    if item is None:
        raise KeyError(session_id)
    return {"enabled": True, "session": session_view(item)}


def delete_session(session_id: str) -> Dict[str, Any]:
    ok = _store().delete_session(session_id)
    if not ok:
        raise KeyError(session_id)
    return {"enabled": True, "ok": True}


def clear_messages(session_id: str) -> Dict[str, Any]:
    count = _store().clear_session_messages(session_id)
    return {"enabled": True, "ok": True, "deleted": int(count or 0)}


def end_session(session_id: str) -> Dict[str, Any]:
    item = _store().update_session(session_id, status="archived")
    if item is None:
        raise KeyError(session_id)
    return {"enabled": True, "ok": True, "session": session_view(item)}


def import_sessions(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = _store().import_sessions(payload)
    return {"enabled": True, **result}


__all__ = [
    "SessionsUnavailable",
    "clear_messages",
    "create_session",
    "delete_session",
    "enabled",
    "end_session",
    "get_session",
    "import_sessions",
    "list_messages",
    "list_sessions",
    "message_view",
    "search_history",
    "session_view",
    "update_session",
]
