"""Local-only inbox scan API."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.features.access.service import is_local_request
from app.features.inbox.config import get_inbox_settings
from app.features.inbox import service
from app.features.session.service import token_matches

router = APIRouter(prefix="/api/inbox", tags=["inbox"])
files_router = APIRouter(prefix="/api/files", tags=["inbox"])


class ScanRequest(BaseModel):
    token: str = Field(default="", max_length=128)
    reason: str = Field(default="manual", max_length=64)


class OpenFolderRequest(BaseModel):
    token: str = Field(default="", max_length=128)
    target: Literal["inbox"] = "inbox"


def _require_local(request: Request) -> None:
    if not is_local_request(request):
        raise HTTPException(status_code=403, detail="This API is local-only")


@router.get("/status", dependencies=[Depends(_require_local)])
def inbox_status() -> Dict[str, Any]:
    return service.status()


@router.post("/scan", dependencies=[Depends(_require_local)])
def scan_inbox(data: ScanRequest) -> Dict[str, Any]:
    if not token_matches(data.token):
        raise HTTPException(status_code=403, detail="Invalid control token")
    result = service.scan_once(reason=data.reason or "manual")
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("message") or "Scan failed")
    return result


@files_router.post("/open-folder", dependencies=[Depends(_require_local)])
def open_inbox_folder(data: OpenFolderRequest) -> Dict[str, Any]:
    if not token_matches(data.token):
        raise HTTPException(status_code=403, detail="Invalid control token")
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="Folder opening is available on Windows only")
    path = get_inbox_settings().directory
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Inbox folder does not exist")
    subprocess.Popen(  # noqa: S603 - fixed executable and validated configured path
        ["explorer.exe", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "message": "已打开收件箱文件夹"}


__all__ = ["files_router", "router"]
