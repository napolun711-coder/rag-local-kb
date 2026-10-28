"""Web session and controlled shutdown APIs."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from app.features.session import service

router = APIRouter(tags=["session"])


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


@router.get("/api/app/session")
def get_session(request: Request) -> Dict[str, Any]:
    return service.session_payload(request)


@router.post("/api/app/keepalive")
def keepalive(request: Request) -> Dict[str, Any]:
    return service.keepalive(request)


@router.get("/api/app/control")
def control_token(request: Request) -> Dict[str, Any]:
    try:
        return service.local_control(request)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/api/app/leave")
async def leave(request: Request) -> Dict[str, Any]:
    payload = await _json_body(request)
    try:
        return service.leave(request, payload.get("token"))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/api/app/stop")
async def stop(request: Request) -> Dict[str, Any]:
    payload = await _json_body(request)
    try:
        return service.stop_now(request, payload.get("token"), payload.get("delay", 3))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


__all__ = ["router"]
