"""Stable HTTP shell that will host a LightRAG engine when installed."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

import requests
import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.platform.ports import publish_runtime_port, select_port
from services.lightrag_shell.config import get_settings

LOGGER = logging.getLogger("ragkb.lightrag_shell")
settings = get_settings()
app = FastAPI(title="LightRAG HTTP Shell", version="0.1.0")


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or "http_error")
        message = str(detail.get("message") or "Request failed")
    else:
        code = "http_error"
        message = str(detail or "Request failed")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": message}},
    )


def _backend(path: str) -> str:
    return settings.backend_url.rstrip("/") + "/" + path.lstrip("/")


def _engine_configured() -> bool:
    return bool(settings.backend_url)


def _not_installed() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "engine_not_installed",
                "message": "The LightRAG engine is not configured or installed.",
            }
        },
    )


def _backend_error(exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "error": {
                "code": "engine_unreachable",
                "message": f"The LightRAG engine is unreachable: {type(exc).__name__}",
            }
        },
    )


def _request_json(method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
    if not _engine_configured():
        raise HTTPException(
            status_code=503,
            detail={"code": "engine_not_installed", "message": "The engine is not installed."},
        )
    try:
        response = requests.request(
            method,
            _backend(path),
            timeout=settings.backend_timeout,
            **kwargs,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "engine_unreachable", "message": type(exc).__name__},
        ) from exc
    try:
        payload = response.json()
    except Exception:
        payload = {"data": response.text}
    return payload if isinstance(payload, dict) else {"data": payload}


@app.get("/health")
def health() -> Dict[str, Any]:
    configured = _engine_configured()
    if not configured:
        return {
            "status": "ok",
            "service": "lightrag-shell",
            "engine_installed": False,
            "backend_configured": False,
            "state": "engine_not_installed",
        }
    try:
        response = requests.get(_backend("/health"), timeout=5)
        reachable = response.status_code == 200
    except requests.RequestException:
        reachable = False
    return {
        "status": "ok" if reachable else "degraded",
        "service": "lightrag-shell",
        "engine_installed": reachable,
        "backend_configured": True,
        "backend_reachable": reachable,
        "state": "ready" if reachable else "engine_unreachable",
    }


@app.post("/query")
async def query(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    return _request_json("POST", "/query", json=payload)


@app.post("/query/stream")
async def query_stream(request: Request):
    if not _engine_configured():
        return _not_installed()
    payload = await request.json()
    upstream = requests.post(
        _backend("/query/stream"),
        json=payload,
        timeout=settings.backend_timeout,
        stream=True,
    )
    if upstream.status_code >= 400:
        upstream.close()
        return _not_installed() if upstream.status_code == 503 else _backend_error(RuntimeError("upstream"))
    return StreamingResponse(upstream.iter_content(chunk_size=None), media_type="application/x-ndjson")


@app.post("/documents/upload")
async def upload_document(file: UploadFile = File(...)) -> Dict[str, Any]:
    if not _engine_configured():
        return _not_installed()
    content = await file.read()
    return _request_json(
        "POST",
        "/documents/upload",
        files={"file": (file.filename or "upload.bin", content, file.content_type or "")},
    )


@app.get("/documents")
def list_documents(request: Request) -> Dict[str, Any]:
    return _request_json("GET", "/documents", params=dict(request.query_params))


@app.post("/documents/paginated")
async def documents_paginated(request: Request) -> Dict[str, Any]:
    payload = await request.json()
    return _request_json("POST", "/documents/paginated", json=payload)


@app.get("/documents/track_status/{track_id}")
def document_status(track_id: str) -> Dict[str, Any]:
    return _request_json("GET", f"/documents/track_status/{track_id}")


@app.delete("/documents/delete_document")
def delete_document(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _request_json("DELETE", "/documents/delete_document", json=payload)


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    port, skipped = select_port(
        settings.host,
        settings.port,
        scan_limit=settings.port_scan_limit,
    )
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    if skipped:
        LOGGER.warning("Skipped unavailable ports: %s", ", ".join(map(str, skipped)))
    target = settings.log_dir / "lightrag-shell.port"
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(f"{port}\n", encoding="ascii")
    os.replace(temporary, target)
    LOGGER.info("Serving LightRAG shell on http://%s:%d", settings.host, port)
    uvicorn.run(app, host=settings.host, port=port)


if __name__ == "__main__":
    run()