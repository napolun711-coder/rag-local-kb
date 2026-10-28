"""Read-only knowledge service status API."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app.adapters.lightrag.client import LightRagHttpClient

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/status")
def knowledge_status() -> Dict[str, Any]:
    return LightRagHttpClient().health().to_dict()