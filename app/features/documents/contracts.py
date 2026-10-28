"""Typed read-only views for knowledge documents and local ingest jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class LocalJobSummary:
    file_id: str
    filename: str
    doc_name: str
    state: str
    quality_level: str
    quality_score: Optional[int]
    source_path: str
    saved_path: str
    track_id: str
    knowledge_state: str
    updated_at: str
    source_available: bool
    document_ids: Tuple[str, ...] = ()
    topic_id: str = ""
    topic_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "doc_name": self.doc_name,
            "state": self.state,
            "quality_level": self.quality_level,
            "quality_score": self.quality_score,
            "source_path": self.source_path,
            "saved_path": self.saved_path,
            "track_id": self.track_id,
            "knowledge_state": self.knowledge_state,
            "updated_at": self.updated_at,
            "source_available": self.source_available,
            "document_ids": list(self.document_ids),
            "topic_id": self.topic_id,
            "topic_name": self.topic_name,
        }


@dataclass(frozen=True)
class DocumentView:
    doc_id: str
    name: str
    file_path: str
    status: str
    chunks_count: Optional[int]
    track_id: str
    created_at: str
    updated_at: str
    content_summary: str = ""
    error: str = ""
    local_job: Optional[LocalJobSummary] = None
    local_source_available: bool = False
    match_reason: str = ""
    topic_id: str = ""
    topic_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "name": self.name,
            "file_path": self.file_path,
            "status": self.status,
            "chunks_count": self.chunks_count,
            "track_id": self.track_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "content_summary": self.content_summary,
            "error": self.error,
            "local_job": self.local_job.to_dict() if self.local_job else None,
            "local_source_available": self.local_source_available,
            "match_reason": self.match_reason,
            "topic_id": self.topic_id,
            "topic_name": self.topic_name,
        }


@dataclass(frozen=True)
class DocumentListResult:
    items: Tuple[DocumentView, ...]
    page: int
    page_size: int
    total_count: int
    total_pages: int
    has_next: bool
    has_prev: bool
    status_counts: Dict[str, int] = field(default_factory=dict)
    local_only: Tuple[LocalJobSummary, ...] = ()
    local_only_total: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "pagination": {
                "page": self.page,
                "page_size": self.page_size,
                "total_count": self.total_count,
                "total_pages": self.total_pages,
                "has_next": self.has_next,
                "has_prev": self.has_prev,
            },
            "status_counts": dict(self.status_counts),
            "local_only": [item.to_dict() for item in self.local_only],
            "local_only_total": self.local_only_total,
        }


@dataclass(frozen=True)
class DocumentOverview:
    remote_total: int
    remote_status_counts: Dict[str, int]
    local_total: int
    local_pending: int
    local_failed: int
    local_done: int
    local_only_total: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "remote_total": self.remote_total,
            "remote_status_counts": dict(self.remote_status_counts),
            "local_total": self.local_total,
            "local_pending": self.local_pending,
            "local_failed": self.local_failed,
            "local_done": self.local_done,
            "local_only_total": self.local_only_total,
        }


__all__ = [
    "DocumentListResult",
    "DocumentOverview",
    "DocumentView",
    "LocalJobSummary",
]
