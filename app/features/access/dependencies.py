"""Shared FastAPI authorization dependencies for local and paired-LAN clients."""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.features.access.service import is_local_request, is_request_authorized


def require_local(request: Request) -> None:
    if not is_local_request(request):
        raise HTTPException(status_code=403, detail="This API is local-only")


def require_authorized(request: Request) -> None:
    if not is_request_authorized(request):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "device_auth_required",
                "message": "This device must be authorized by the local computer",
                "pair_url": "/pair",
            },
        )


__all__ = ["require_local", "require_authorized"]
