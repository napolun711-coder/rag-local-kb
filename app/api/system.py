"""System and page routes."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

from app import __version__
from app.platform.config import get_settings

router = APIRouter()
_NO_CACHE = {"Cache-Control": "no-cache"}


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/web", status_code=307)


@router.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "version": __version__}


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
