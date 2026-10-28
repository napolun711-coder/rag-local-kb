"""Narrow adapter between document views and memory-owned topic tags."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.features.memory import runtime
from app.features.memory.store import document_key


def enabled() -> bool:
    return runtime.get_store() is not None


def list_topics() -> List[Dict[str, Any]]:
    store = runtime.get_store()
    if store is None:
        return []
    return store.list_topics(status="active", limit=500)


def all_topics() -> List[Dict[str, Any]]:
    store = runtime.get_store()
    if store is None:
        return []
    return store.list_topics(status="", limit=500)


def topic_maps() -> tuple[Dict[str, str], Dict[str, str]]:
    """Return document-key → topic-id and topic-id → name."""
    store = runtime.get_store()
    if store is None:
        return {}, {}
    names = {
        str(item.get("id") or ""): str(item.get("name") or "")
        for item in store.list_topics(status="", limit=500)
    }
    return store.document_topic_map(), names


def topic_for(
    file_path: str,
    mapping: Dict[str, str],
    names: Optional[Dict[str, str]] = None,
) -> tuple[str, str]:
    key = document_key(file_path)
    topic_id = mapping.get(key, "") if key else ""
    return topic_id, (names or {}).get(topic_id, "")


def set_document_topic(
    file_path: str,
    topic_id: str,
    *,
    name: str = "",
    file_id: str = "",
) -> Dict[str, Any]:
    store = runtime.get_store()
    if store is None:
        raise RuntimeError("Topic tags require the memory store to be enabled")
    return store.set_document_topic(
        file_path,
        topic_id,
        name=name,
        file_id=file_id,
    )


def clear_document_topic(file_path: str) -> bool:
    store = runtime.get_store()
    if store is None:
        raise RuntimeError("Topic tags require the memory store to be enabled")
    return store.clear_document_topic(file_path)


def create_topic(name: str, *, kind: str = "topic", description: str = "") -> Dict[str, Any]:
    store = runtime.get_store()
    if store is None:
        raise RuntimeError("Topics require the memory store to be enabled")
    return store.create_topic(name=name, kind=kind, description=description)


__all__ = [
    "all_topics",
    "clear_document_topic",
    "create_topic",
    "enabled",
    "list_topics",
    "set_document_topic",
    "topic_for",
    "topic_maps",
]
