"""System and page routes."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Tuple

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

from app import __version__
from app.adapters.lightrag.client import LightRagHttpClient
from app.features.settings import service as settings_service
from app.platform.config import get_settings

router = APIRouter()
_NO_CACHE = {"Cache-Control": "no-cache"}


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/web", status_code=307)


@router.get("/health")
def health() -> dict[str, object]:
    targets: Dict[str, str] = {}
    try:
        targets = settings_service.health_targets()
    except Exception:
        targets = {}
    knowledge = LightRagHttpClient().health()
    service = knowledge.service if isinstance(knowledge.service, dict) else {}
    identity = service.get("engine_identity")
    if not isinstance(identity, dict):
        identity = {}
    vector_storage = str(
        identity.get("vector_storage")
        or targets.get("vector_storage")
        or ""
    )
    return {
        "status": "ok",
        "version": __version__,
        "lightrag": {
            "ok": bool(knowledge.reachable and knowledge.engine_installed),
            "state": knowledge.state,
            "detail": knowledge.detail,
        },
        "ollama": _ollama_health(targets),
        "qdrant": _qdrant_health(targets, vector_storage),
    }


def _probe_json(url: str, timeout: float = 1.5) -> Tuple[bool, str, Dict[str, Any]]:
    if not url:
        return False, "not configured", {}
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return False, f"HTTP {response.status}", {}
            payload = json.loads(response.read().decode("utf-8"))
            return True, "", payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, urllib.error.URLError):
        return False, "unreachable", {}


def _ollama_health(targets: Dict[str, str]) -> Dict[str, object]:
    binding = str(targets.get("embedding_binding") or "").lower()
    host = str(targets.get("embedding_host") or "").rstrip("/")
    model = str(targets.get("embedding_model") or "")
    if binding != "ollama" or not host or not model:
        return {
            "ok": False,
            "enabled": False,
            "state": "not_in_use",
            "detail": "当前嵌入服务不使用 Ollama",
        }
    ok, detail, payload = _probe_json(host + "/api/tags")
    names = {
        str(item.get("name") or "")
        for item in payload.get("models", [])
        if isinstance(item, dict)
    }
    available = ok and model in names
    return {
        "ok": available,
        "enabled": True,
        "state": "ready" if available else "unavailable",
        "detail": (
            "指定嵌入模型可用"
            if available
            else ("Ollama 可达但缺少指定模型" if ok else detail)
        ),
    }


def _qdrant_health(
    targets: Dict[str, str],
    vector_storage: str,
) -> Dict[str, object]:
    active = "qdrant" in vector_storage.lower()
    if not active:
        return {
            "ok": False,
            "enabled": False,
            "state": "not_in_use",
            "detail": "当前使用本地向量存储",
            "storage": vector_storage or "local",
        }
    url = str(targets.get("qdrant_url") or "").rstrip("/")
    ok, detail, _ = _probe_json(url + "/healthz" if url else "")
    return {
        "ok": ok,
        "enabled": True,
        "state": "ready" if ok else "unavailable",
        "detail": "Qdrant 可用" if ok else detail,
        "storage": "qdrant",
    }


@router.get("/web")
def web_page() -> FileResponse:
    return FileResponse(
        get_settings().static_dir / "index.html",
        headers=_NO_CACHE,
    )


@router.get("/files")
def files_page() -> FileResponse:
    return FileResponse(
        get_settings().static_dir / "files.html",
        headers=_NO_CACHE,
    )


@router.get("/settings")
def settings_page() -> FileResponse:
    return FileResponse(
        get_settings().static_dir / "settings.html",
        headers=_NO_CACHE,
    )


@router.get("/memory")
def memory_page() -> FileResponse:
    return FileResponse(
        get_settings().static_dir / "memory.html",
        headers=_NO_CACHE,
    )


@router.get("/pair")
def pair_page() -> FileResponse:
    return FileResponse(
        get_settings().static_dir / "pair.html",
        headers=_NO_CACHE,
    )


@router.get("/access_required")
def access_required_page() -> FileResponse:
    return FileResponse(
        get_settings().static_dir / "access_required.html",
        headers=_NO_CACHE,
    )


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(get_settings().static_dir / "app.ico", headers=_NO_CACHE)
