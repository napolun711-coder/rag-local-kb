"""Knowledge service status and index compatibility API."""

from __future__ import annotations

from typing import Any, Callable, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.adapters.lightrag.client import LightRagHttpClient
from app.adapters.lightrag.errors import (
    KnowledgeEngineNotInstalled,
    KnowledgeError,
    KnowledgeHTTPError,
    KnowledgeIndexMismatch,
    KnowledgeUnavailable,
)
from app.features.knowledge import index_guard
from app.platform.errors import AppError
from app.features.access.dependencies import require_authorized, require_local

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
graph_router = APIRouter(
    prefix="/api/graph",
    tags=["graph"],
    dependencies=[Depends(require_authorized)],
)


class IndexAcceptRequest(BaseModel):
    confirmation: str = Field(default="", max_length=32)


def _graph_result(call: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        return call()
    except KnowledgeEngineNotInstalled as exc:
        raise AppError(503, "engine_not_installed", str(exc)) from exc
    except KnowledgeUnavailable as exc:
        raise AppError(503, "knowledge_unavailable", str(exc)) from exc
    except KnowledgeIndexMismatch as exc:
        raise AppError(409, "index_rebuild_required", str(exc)) from exc
    except KnowledgeHTTPError as exc:
        raise AppError(502, "knowledge_error", str(exc)) from exc
    except KnowledgeError as exc:
        raise AppError(502, "graph_failed", str(exc)) from exc


@router.get("/status", dependencies=[Depends(require_authorized)])
def knowledge_status() -> Dict[str, Any]:
    payload = LightRagHttpClient().health().to_dict()
    service = payload.get("service")
    if isinstance(service, dict):
        service.pop("engine_identity", None)
    return payload


@router.get(
    "/index-compatibility",
    dependencies=[Depends(require_authorized)],
)
def index_compatibility() -> Dict[str, Any]:
    health = LightRagHttpClient().health()
    return index_guard.status_from_service(health.service)


@router.post(
    "/index-compatibility/accept",
    dependencies=[Depends(require_local)],
)
def accept_index_compatibility(data: IndexAcceptRequest) -> Dict[str, Any]:
    health = LightRagHttpClient().health()
    if not health.engine_installed:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "knowledge_unavailable",
                "message": health.detail or "Knowledge engine is unavailable",
            },
        )
    try:
        result = index_guard.accept_current(health.service, data.confirmation)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_confirmation",
                "message": str(exc),
            },
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "index_guard_write_failed",
                "message": type(exc).__name__,
            },
        ) from exc
    return {"ok": True, **result}


@graph_router.get("/popular")
def graph_popular(
    limit: int = Query(16, ge=1, le=100),
) -> Dict[str, Any]:
    return _graph_result(lambda: LightRagHttpClient().graph_popular(limit=limit))


@graph_router.get("/search")
def graph_search(
    q: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(20, ge=1, le=50),
) -> Dict[str, Any]:
    query = q.strip()
    if not query:
        raise AppError(422, "invalid_query", "Search query cannot be blank.")
    return _graph_result(
        lambda: LightRagHttpClient().graph_search(query, limit=limit)
    )


@graph_router.get("/global")
def graph_global(
    max_nodes: int = Query(400, ge=1, le=1000),
) -> Dict[str, Any]:
    return _graph_result(
        lambda: LightRagHttpClient().graph_global(max_nodes=max_nodes)
    )


@graph_router.get("/subgraph")
def graph_subgraph(
    label: str = Query(..., min_length=1, max_length=128),
    depth: int = Query(2, ge=1, le=3),
    max_nodes: int = Query(60, ge=10, le=200),
) -> Dict[str, Any]:
    center = label.strip()
    if not center:
        raise AppError(422, "invalid_label", "Graph label cannot be blank.")
    return _graph_result(
        lambda: LightRagHttpClient().graph_subgraph(
            center,
            depth=depth,
            max_nodes=max_nodes,
        )
    )
