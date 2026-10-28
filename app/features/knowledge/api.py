"""Knowledge service status and index compatibility API."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.adapters.lightrag.client import LightRagHttpClient
from app.features.knowledge import index_guard

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class IndexAcceptRequest(BaseModel):
    confirmation: str = Field(default="", max_length=32)


def _require_local(request: Request) -> None:
    host = (request.client.host if request.client else "").strip()
    if host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="This API is local-only")


@router.get("/status")
def knowledge_status() -> Dict[str, Any]:
    payload = LightRagHttpClient().health().to_dict()
    service = payload.get("service")
    if isinstance(service, dict):
        service.pop("engine_identity", None)
    return payload


@router.get(
    "/index-compatibility",
    dependencies=[Depends(_require_local)],
)
def index_compatibility() -> Dict[str, Any]:
    health = LightRagHttpClient().health()
    return index_guard.status_from_service(health.service)


@router.post(
    "/index-compatibility/accept",
    dependencies=[Depends(_require_local)],
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
