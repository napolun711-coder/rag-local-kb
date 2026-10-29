"""LAN access APIs and one-time device pairing."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response

from app.features.access import service

router = APIRouter(tags=["access"])


def _require_local(request: Request) -> None:
    if not service.is_local_request(request):
        raise HTTPException(status_code=403, detail="This operation is local-only")


@router.get("/api/app/access/status")
def access_status(request: Request) -> Dict[str, Any]:
    return service.status_payload(request)


@router.post("/api/app/access/requests")
async def create_access_request(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    try:
        return service.create_request(
            payload.get("device_name") if isinstance(payload, dict) else "",
            request.client.host if request.client else "unknown",
        )
    except service.RequestLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.get("/api/app/access/requests")
def access_requests(request: Request) -> Dict[str, Any]:
    _require_local(request)
    return {"requests": service.list_requests(), "available": True}


@router.post("/api/app/access/requests/{request_id}/approve")
def approve_access_request(request_id: str, request: Request) -> Dict[str, Any]:
    _require_local(request)
    try:
        item = service.approve_request(request_id)
    except service.RequestGone as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, **item}


@router.delete("/api/app/access/requests/{request_id}")
def ignore_access_request(request_id: str, request: Request) -> Dict[str, Any]:
    _require_local(request)
    return {"ok": service.ignore_request(request_id)}


@router.post("/api/app/access/requests/{request_id}/claim")
async def claim_access_request(
    request_id: str, request: Request, response: Response
) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        claimed = service.claim_request(
            request_id,
            str(payload.get("request_secret") or ""),
            request.client.host if request.client else "unknown",
        )
    except service.RequestGone as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except service.InvalidPairingSecret as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not claimed:
        return {"status": "pending"}
    response.set_cookie(
        service.COOKIE_NAME,
        service.cookie_value(claimed["device_id"], claimed["device_token"]),
        max_age=service.COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    service.set_csrf_cookie(response)
    return {"status": "approved", "redirect": "/web", "name": claimed["name"]}


@router.get("/api/app/access/devices")
def access_devices(request: Request) -> Dict[str, Any]:
    _require_local(request)
    return {"devices": service.list_devices(), "available": True}


@router.delete("/api/app/access/devices/{device_id}")
def delete_access_device(device_id: str, request: Request) -> Dict[str, Any]:
    _require_local(request)
    if not service.revoke_device(device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    return {"ok": True}


__all__ = ["router"]
