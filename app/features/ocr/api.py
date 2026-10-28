"""Local OCR diagnostics API."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from app.features.access.service import is_local_request
from app.features.ocr import service

router = APIRouter(prefix="/api/ocr", tags=["ocr"])


def _require_local(request: Request) -> None:
    if not is_local_request(request):
        raise HTTPException(status_code=403, detail="This API is local-only")


@router.get("/status", dependencies=[Depends(_require_local)])
def ocr_status() -> Dict[str, Any]:
    return service.status()


@router.post("/test", dependencies=[Depends(_require_local)])
def test_ocr() -> Dict[str, Any]:
    return service.test_connection()


__all__ = ["router"]
