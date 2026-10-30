"""HTTP client for the stable LightRAG shell contract."""

from __future__ import annotations

from typing import Any, Dict, Iterator, Mapping, Optional, Sequence

import requests

from app.adapters.lightrag.config import LightRagSettings, get_lightrag_settings
from app.adapters.lightrag.errors import (
    KnowledgeEngineNotInstalled,
    KnowledgeHTTPError,
    KnowledgeIndexMismatch,
    KnowledgeUnavailable,
)
from app.features.knowledge.contracts import KnowledgeGateway, KnowledgeHealth
from app.features.knowledge import index_guard


class LightRagHttpClient(KnowledgeGateway):
    def __init__(self, settings: Optional[LightRagSettings] = None) -> None:
        self.settings = settings or get_lightrag_settings()
        self.session = requests.Session()

    @property
    def timeout(self) -> tuple[float, float]:
        return (self.settings.connect_timeout_seconds, self.settings.timeout_seconds)

    def _url(self, path: str) -> str:
        return self.settings.url + "/" + path.lstrip("/")

    def _raise_for_response(self, response: requests.Response) -> None:
        if response.status_code < 400:
            return
        code = ""
        message = f"LightRAG HTTP {response.status_code}"
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                code = str(error.get("code") or "")
                message = str(error.get("message") or message)
            elif isinstance(error, str):
                message = error
        except Exception:
            pass
        if code == "engine_not_installed":
            raise KnowledgeEngineNotInstalled(message)
        raise KnowledgeHTTPError(response.status_code, message)

    def _assert_index_compatible(self) -> None:
        health = self.health()
        try:
            index_guard.require_compatible(health.service)
        except KnowledgeIndexMismatch:
            raise
        except Exception as exc:
            raise KnowledgeIndexMismatch(
                f"Index compatibility could not be verified: {type(exc).__name__}"
            ) from exc

    def health(self) -> KnowledgeHealth:
        try:
            response = self.session.get(
                self._url("/health"),
                timeout=(self.settings.connect_timeout_seconds, 5.0),
                verify=self.settings.verify_tls,
            )
        except requests.RequestException as exc:
            return KnowledgeHealth(
                configured=True,
                reachable=False,
                engine_installed=False,
                state="unreachable",
                detail=type(exc).__name__,
            )
        if response.status_code != 200:
            return KnowledgeHealth(
                configured=True,
                reachable=True,
                engine_installed=False,
                state="unhealthy",
                detail=f"HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except Exception:
            payload = {}
        engine = bool(payload.get("engine_installed")) if isinstance(payload, dict) else False
        return KnowledgeHealth(
            configured=True,
            reachable=True,
            engine_installed=engine,
            state="ready" if engine else "engine_not_installed",
            detail="" if engine else "The HTTP shell is reachable but no engine is installed.",
            service=payload if isinstance(payload, dict) else {},
        )

    def query(self, question: str, *, mode: str = "mix", **options: Any) -> Dict[str, Any]:
        self._assert_index_compatible()
        body: Dict[str, Any] = {"query": question, "mode": mode, **options}
        try:
            response = self.session.post(
                self._url("/query"),
                json=body,
                timeout=self.timeout,
                verify=self.settings.verify_tls,
            )
        except requests.RequestException as exc:
            raise KnowledgeUnavailable(type(exc).__name__) from exc
        self._raise_for_response(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}

    def stream_query(self, question: str, *, mode: str = "mix", **options: Any) -> Iterator[bytes]:
        self._assert_index_compatible()
        body: Dict[str, Any] = {"query": question, "mode": mode, **options}
        try:
            response = self.session.post(
                self._url("/query/stream"),
                json=body,
                timeout=self.timeout,
                stream=True,
                verify=self.settings.verify_tls,
            )
        except requests.RequestException as exc:
            raise KnowledgeUnavailable(type(exc).__name__) from exc
        self._raise_for_response(response)
        try:
            for chunk in response.iter_content(chunk_size=None):
                if chunk:
                    yield chunk
        finally:
            response.close()

    def upload_document(self, filename: str, content: bytes, content_type: str = "") -> Dict[str, Any]:
        self._assert_index_compatible()
        try:
            response = self.session.post(
                self._url("/documents/upload"),
                files={"file": (filename, content, content_type or "application/octet-stream")},
                timeout=self.timeout,
                verify=self.settings.verify_tls,
            )
        except requests.RequestException as exc:
            raise KnowledgeUnavailable(type(exc).__name__) from exc
        self._raise_for_response(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}

    def insert_text(self, text: str, file_source: str) -> Dict[str, Any]:
        """Submit reviewed text through LightRAG's native text-ingest API."""
        self._assert_index_compatible()
        try:
            response = self.session.post(
                self._url("/documents/text"),
                json={"text": text, "file_source": file_source},
                timeout=self.timeout,
                verify=self.settings.verify_tls,
            )
        except requests.RequestException as exc:
            raise KnowledgeUnavailable(type(exc).__name__) from exc
        self._raise_for_response(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}

    def list_documents(self, **params: Any) -> Dict[str, Any]:
        try:
            response = self.session.get(
                self._url("/documents"),
                params=params,
                timeout=self.timeout,
                verify=self.settings.verify_tls,
            )
        except requests.RequestException as exc:
            raise KnowledgeUnavailable(type(exc).__name__) from exc
        self._raise_for_response(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}

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
        payload: Dict[str, Any] = {
            "page": max(1, int(page or 1)),
            "page_size": max(10, min(200, int(page_size or 50))),
            "sort_field": sort_field,
            "sort_direction": sort_direction,
        }
        if status_filter:
            payload["status_filter"] = status_filter
        if status_filters:
            payload["status_filters"] = list(status_filters)
        try:
            response = self.session.post(
                self._url("/documents/paginated"),
                json=payload,
                timeout=self.timeout,
                verify=self.settings.verify_tls,
            )
        except requests.RequestException as exc:
            raise KnowledgeUnavailable(type(exc).__name__) from exc
        self._raise_for_response(response)
        data = response.json()
        return data if isinstance(data, dict) else {"documents": []}

    def document_status(self, track_id: str) -> Dict[str, Any]:
        try:
            response = self.session.get(
                self._url(f"/documents/track_status/{track_id}"),
                timeout=self.timeout,
                verify=self.settings.verify_tls,
            )
        except requests.RequestException as exc:
            raise KnowledgeUnavailable(type(exc).__name__) from exc
        self._raise_for_response(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}

    def _graph_get(self, path: str, params: Mapping[str, Any]) -> Dict[str, Any]:
        try:
            response = self.session.get(
                self._url(path),
                params=dict(params),
                timeout=self.timeout,
                verify=self.settings.verify_tls,
            )
        except requests.RequestException as exc:
            raise KnowledgeUnavailable(type(exc).__name__) from exc
        self._raise_for_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise KnowledgeHTTPError(502, "Knowledge graph returned invalid JSON") from exc
        return payload if isinstance(payload, dict) else {"data": payload}

    def graph_global(self, *, max_nodes: int = 400) -> Dict[str, Any]:
        return self._graph_get("/graph/global", {"max_nodes": max(1, int(max_nodes))})

    def graph_subgraph(
        self,
        label: str,
        *,
        depth: int = 2,
        max_nodes: int = 60,
    ) -> Dict[str, Any]:
        return self._graph_get(
            "/graph/subgraph",
            {
                "label": str(label).strip(),
                "depth": max(1, min(3, int(depth))),
                "max_nodes": max(10, min(200, int(max_nodes))),
            },
        )

    def graph_popular(self, *, limit: int = 16) -> Dict[str, Any]:
        return self._graph_get(
            "/graph/popular",
            {"limit": max(1, min(100, int(limit)))},
        )

    def graph_search(self, query: str, *, limit: int = 20) -> Dict[str, Any]:
        return self._graph_get(
            "/graph/search",
            {
                "q": str(query).strip(),
                "limit": max(1, min(50, int(limit))),
            },
        )

    def delete_document(
        self,
        document_id: str,
        *,
        delete_file: bool = False,
        delete_llm_cache: bool = False,
    ) -> Dict[str, Any]:
        try:
            response = self.session.delete(
                self._url("/documents/delete_document"),
                json={
                    "doc_ids": [document_id],
                    "delete_file": bool(delete_file),
                    "delete_llm_cache": bool(delete_llm_cache),
                },
                timeout=self.timeout,
                verify=self.settings.verify_tls,
            )
        except requests.RequestException as exc:
            raise KnowledgeUnavailable(type(exc).__name__) from exc
        self._raise_for_response(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}


__all__ = ["LightRagHttpClient"]
