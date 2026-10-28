"""Read-only document catalog application service."""

from __future__ import annotations

from dataclasses import replace
from math import ceil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.adapters.lightrag.errors import (
    KnowledgeEngineNotInstalled,
    KnowledgeHTTPError,
    KnowledgeUnavailable,
)
from app.features.documents.catalog import build_local_index, merge_documents
from app.features.documents.contracts import DocumentListResult, DocumentOverview, DocumentView, LocalJobSummary
from app.features.documents import topics
from app.features.ingest import storage
from app.features.ingest.config import IngestSettings, get_ingest_settings
from app.features.knowledge.contracts import KnowledgeGateway
from app.features.knowledge.service import get_knowledge_gateway
from app.platform.paths import BASE_DIR, KNOWLEDGE_DIR

LOCAL_PENDING_STATES = frozenset({"review", "needs_ocr", "blocked"})
MAX_DETAIL_PAGES = 20


class DocumentCatalogService:
    def __init__(
        self,
        gateway: Optional[KnowledgeGateway] = None,
        settings: Optional[IngestSettings] = None,
    ) -> None:
        self.gateway = gateway or get_knowledge_gateway()
        self.settings = settings or get_ingest_settings()

    def _local_results(self) -> List[Dict[str, Any]]:
        return storage.list_results(self.settings)

    @staticmethod
    def _upstream_message(exc: Exception) -> str:
        if isinstance(exc, KnowledgeEngineNotInstalled):
            return "知识引擎未安装或未启用"
        if isinstance(exc, KnowledgeUnavailable):
            return "知识服务不可达"
        if isinstance(exc, KnowledgeHTTPError):
            return f"知识服务返回 HTTP {exc.status_code}"
        return "知识服务暂时不可用"

    @staticmethod
    def _can_degrade(exc: Exception) -> bool:
        if isinstance(exc, (KnowledgeEngineNotInstalled, KnowledgeUnavailable)):
            return True
        return isinstance(exc, KnowledgeHTTPError) and exc.status_code >= 500

    def _degraded_list(
        self,
        *,
        page: int,
        page_size: int,
        query: str,
        wanted_topic: str,
        message: str,
    ) -> DocumentListResult:
        local_results = self._local_results()
        index = build_local_index(local_results)
        topic_map, topic_names = topics.topic_maps()
        jobs = [
            self._with_job_topic(item, topic_map, topic_names)
            for item in index.local_only()
        ]
        jobs = [
            item for item in jobs
            if self._job_matches(item, query, wanted_topic)
        ]
        return DocumentListResult(
            items=(),
            page=page,
            page_size=page_size,
            total_count=0,
            total_pages=1,
            has_next=False,
            has_prev=False,
            status_counts={},
            local_only=tuple(jobs),
            local_only_total=len(jobs),
            upstream_ok=False,
            upstream_message=message,
        )

    def list_documents(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        status_filter: Optional[str] = None,
        status_filters: Optional[Sequence[str]] = None,
        sort_field: str = "updated_at",
        sort_direction: str = "desc",
        q: str = "",
        topic_id: str = "",
    ) -> DocumentListResult:
        safe_page = max(1, int(page or 1))
        safe_size = max(10, min(200, int(page_size or 50)))
        query = str(q or "").strip().casefold()
        wanted_topic = str(topic_id or "").strip()
        try:
            response = self.gateway.list_documents_paginated(
                page=safe_page,
                page_size=safe_size,
                status_filter=status_filter,
                status_filters=list(status_filters or []),
                sort_field=sort_field,
                sort_direction=sort_direction,
            )
        except Exception as exc:
            if not self._can_degrade(exc):
                raise
            return self._degraded_list(
                page=safe_page,
                page_size=safe_size,
                query=query,
                wanted_topic=wanted_topic,
                message=self._upstream_message(exc),
            )
        documents = response.get("documents") if isinstance(response.get("documents"), list) else []
        pagination = response.get("pagination") if isinstance(response.get("pagination"), dict) else {}
        status_counts = response.get("status_counts") if isinstance(response.get("status_counts"), dict) else {}
        local_results = self._local_results()
        index = build_local_index(local_results)
        items = merge_documents(documents, index)
        local_only = index.local_only()
        topic_map, topic_names = topics.topic_maps()
        items = tuple(self._with_document_topic(item, topic_map, topic_names) for item in items)
        local_only = tuple(self._with_job_topic(item, topic_map, topic_names) for item in local_only)

        if query or wanted_topic:
            all_documents: List[DocumentView] = []
            all_index = build_local_index(local_results)
            current_page = 1
            first_page = True
            try:
                while current_page <= MAX_DETAIL_PAGES:
                    remote = self.gateway.list_documents_paginated(
                        page=current_page,
                        page_size=200,
                        status_filter=status_filter,
                        status_filters=list(status_filters or []),
                        sort_field=sort_field,
                        sort_direction=sort_direction,
                    )
                    remote_docs = (
                        remote.get("documents")
                        if isinstance(remote.get("documents"), list)
                        else []
                    )
                    all_documents.extend(merge_documents(remote_docs, all_index))
                    remote_pagination = (
                        remote.get("pagination")
                        if isinstance(remote.get("pagination"), dict)
                        else {}
                    )
                    if first_page:
                        status_counts = (
                            remote.get("status_counts")
                            if isinstance(remote.get("status_counts"), dict)
                            else status_counts
                        )
                        first_page = False
                    if not bool(remote_pagination.get("has_next")):
                        break
                    current_page += 1
            except Exception as exc:
                if not self._can_degrade(exc):
                    raise
                return self._degraded_list(
                    page=safe_page,
                    page_size=safe_size,
                    query=query,
                    wanted_topic=wanted_topic,
                    message=self._upstream_message(exc),
                )
            all_documents = [
                self._with_document_topic(item, topic_map, topic_names)
                for item in all_documents
            ]
            all_documents = [
                item for item in all_documents
                if self._document_matches(item, query, wanted_topic)
            ]
            filtered_local = [
                self._with_job_topic(item, topic_map, topic_names)
                for item in all_index.local_only()
            ]
            filtered_local = [
                item for item in filtered_local
                if self._job_matches(item, query, wanted_topic)
            ]
            total = len(all_documents)
            total_pages = max(1, ceil(total / safe_size))
            start = (safe_page - 1) * safe_size
            items = tuple(all_documents[start:start + safe_size])
            local_only = tuple(filtered_local)
            return DocumentListResult(
                items=items,
                page=safe_page,
                page_size=safe_size,
                total_count=total,
                total_pages=total_pages,
                has_next=safe_page < total_pages,
                has_prev=safe_page > 1,
                status_counts={str(key): int(value) for key, value in status_counts.items()},
                local_only=local_only,
                local_only_total=len(local_only),
            )

        return DocumentListResult(
            items=items,
            page=int(pagination.get("page") or safe_page),
            page_size=int(pagination.get("page_size") or safe_size),
            total_count=int(pagination.get("total_count") or len(items)),
            total_pages=int(pagination.get("total_pages") or 1),
            has_next=bool(pagination.get("has_next")),
            has_prev=bool(pagination.get("has_prev")),
            status_counts={str(key): int(value) for key, value in status_counts.items()},
            local_only=local_only,
            local_only_total=len(local_only),
        )

    def _with_document_topic(
        self,
        item: DocumentView,
        mapping: Dict[str, str],
        names: Dict[str, str],
    ) -> DocumentView:
        candidates = [item.file_path, item.name]
        if item.local_job is not None:
            candidates.extend(
                [
                    item.local_job.doc_name,
                    item.local_job.filename,
                    item.local_job.saved_path,
                    item.local_job.source_path,
                ]
            )
        for value in candidates:
            tid, name = topics.topic_for(value, mapping, names)
            if tid:
                return replace(item, topic_id=tid, topic_name=name)
        return replace(item, topic_id="", topic_name="")

    def _with_job_topic(
        self,
        item: LocalJobSummary,
        mapping: Dict[str, str],
        names: Dict[str, str],
    ) -> LocalJobSummary:
        for value in (
            item.doc_name,
            item.filename,
            item.saved_path,
            item.source_path,
        ):
            tid, name = topics.topic_for(value, mapping, names)
            if tid:
                return replace(item, topic_id=tid, topic_name=name)
        return replace(item, topic_id="", topic_name="")

    @staticmethod
    def _document_matches(item: DocumentView, query: str, topic_id: str) -> bool:
        if topic_id and item.topic_id != topic_id:
            return False
        if not query:
            return True
        local = item.local_job
        values = [
            item.name,
            item.file_path,
            item.content_summary,
            local.doc_name if local else "",
            local.filename if local else "",
        ]
        return query in " ".join(str(value or "") for value in values).casefold()

    @staticmethod
    def _job_matches(item: LocalJobSummary, query: str, topic_id: str) -> bool:
        if topic_id and item.topic_id != topic_id:
            return False
        if not query:
            return True
        values = [item.doc_name, item.filename, item.source_path, item.saved_path]
        return query in " ".join(str(value or "") for value in values).casefold()

    def get_document(self, doc_id: str) -> Optional[DocumentView]:
        target = str(doc_id or "").strip()
        if not target:
            return None
        page = 1
        while page <= MAX_DETAIL_PAGES:
            result = self.list_documents(page=page, page_size=200)
            for item in result.items:
                if item.doc_id == target:
                    return item
            if not result.has_next:
                break
            page += 1
        return None

    def overview(self) -> DocumentOverview:
        first_page = self.list_documents(page=1, page_size=10)
        local_results = self._local_results()
        local_pending = sum(
            1 for result in local_results if str(result.get("state") or "") in LOCAL_PENDING_STATES
        )
        local_failed = sum(
            1 for result in local_results if str(result.get("state") or "") == "failed"
        )
        local_done = sum(
            1 for result in local_results if str(result.get("state") or "") == "done"
        )
        return DocumentOverview(
            remote_total=first_page.total_count,
            remote_status_counts=first_page.status_counts,
            local_total=len(local_results),
            local_pending=local_pending,
            local_failed=local_failed,
            local_done=local_done,
            local_only_total=first_page.local_only_total,
            upstream_ok=first_page.upstream_ok,
            upstream_message=first_page.upstream_message,
        )

    def _allowed_roots(self) -> Tuple[Path, ...]:
        return (
            self.settings.upload_dir.resolve(),
            self.settings.parsed_dir.resolve(),
            KNOWLEDGE_DIR.resolve(),
        )

    def _safe_existing_path(self, value: str) -> Optional[Path]:
        if not value:
            return None
        try:
            candidate = storage.resolve_display_path(value).resolve()
        except Exception:
            return None
        if not candidate.is_file():
            return None
        if not any(candidate.is_relative_to(root) for root in self._allowed_roots()):
            return None
        return candidate

    def resolve_origin(self, file_path: str) -> Dict[str, Any]:
        index = build_local_index(self._local_results())
        job, reason = index.match({"file_path": file_path})
        if job is None:
            return {
                "available": False,
                "kind": "",
                "kind_label": "",
                "path": "",
                "url": "",
                "reason": "No local ingestion job matches this document",
            }
        for kind, value in (("source", job.source_path), ("archive", job.saved_path)):
            candidate = self._safe_existing_path(value)
            if candidate is not None:
                return {
                    "available": True,
                    "kind": kind,
                    "kind_label": "本机原件" if kind == "source" else "上传留档",
                    "path": storage.display_path(candidate),
                    "url": "",
                    "reason": "",
                    "match_reason": reason,
                    "file_id": job.file_id,
                }
        return {
            "available": False,
            "kind": "",
            "kind_label": "",
            "path": "",
            "url": "",
            "reason": "Local file is missing or outside the allowed data directories",
            "match_reason": reason,
            "file_id": job.file_id,
        }

    def origin_for_file_id(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Resolve an uploaded source or archive without accepting a client path."""
        result = storage.read_result(self.settings, file_id)
        if result is None:
            return None
        for kind, value in (
            ("source", str(result.get("source_path") or "")),
            ("archive", str(result.get("saved_path") or "")),
        ):
            candidate = self._safe_existing_path(value)
            if candidate is not None:
                return {
                    "path": candidate,
                    "kind": kind,
                    "kind_label": "本机原件" if kind == "source" else "上传留档",
                    "filename": candidate.name,
                    "file_id": str(result.get("file_id") or file_id),
                }
        return None


__all__ = ["DocumentCatalogService"]
