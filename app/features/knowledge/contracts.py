"""Stable contracts between application features and a knowledge gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional, Protocol, Sequence


@dataclass(frozen=True)
class KnowledgeHealth:
    configured: bool
    reachable: bool
    engine_installed: bool
    state: str
    detail: str = ""
    service: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "configured": self.configured,
            "reachable": self.reachable,
            "engine_installed": self.engine_installed,
            "state": self.state,
            "detail": self.detail,
            "service": dict(self.service),
        }


class KnowledgeGateway(Protocol):
    def health(self) -> KnowledgeHealth:
        ...

    def query(self, question: str, *, mode: str = "mix", **options: Any) -> Dict[str, Any]:
        ...

    def stream_query(
        self, question: str, *, mode: str = "mix", **options: Any
    ) -> Iterator[bytes]:
        ...

    def upload_document(
        self, filename: str, content: bytes, content_type: str = ""
    ) -> Dict[str, Any]:
        ...

    def insert_text(self, text: str, file_source: str) -> Dict[str, Any]:
        ...

    def list_documents(self, **params: Any) -> Dict[str, Any]:
        ...

    def list_documents_paginated(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        status_filter: Optional[str] = None,
        status_filters: Optional[Sequence[str]] = None,
        sort_field: str = "updated_at",
        sort_direction: str = "desc",
    ) -> Dict[str, Any]:
        ...

    def document_status(self, track_id: str) -> Dict[str, Any]:
        ...

    def graph_global(self, *, max_nodes: int = 400) -> Dict[str, Any]:
        ...

    def graph_subgraph(
        self,
        label: str,
        *,
        depth: int = 2,
        max_nodes: int = 60,
    ) -> Dict[str, Any]:
        ...

    def graph_popular(self, *, limit: int = 16) -> Dict[str, Any]:
        ...

    def graph_search(self, query: str, *, limit: int = 20) -> Dict[str, Any]:
        ...

    def delete_document(
        self,
        document_id: str,
        *,
        delete_file: bool = False,
        delete_llm_cache: bool = False,
    ) -> Dict[str, Any]:
        ...


__all__ = ["KnowledgeGateway", "KnowledgeHealth"]
