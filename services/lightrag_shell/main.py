"""Stable HTTP shell that will host a LightRAG engine when installed."""

from __future__ import annotations

import logging
import hashlib
import os
from pathlib import Path
from typing import Any, Dict

import requests
import uvicorn
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
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


def _error_message(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail") or error.get("code")
            if message:
                return str(message)[:800]
        if isinstance(error, str) and error:
            return error[:800]
        for key in ("message", "detail", "data"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:800]
    try:
        text = response.text.strip()
    except Exception:
        text = ""
    return text[:800] if text else "engine request failed"


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
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "engine_unreachable", "message": type(exc).__name__},
        ) from exc
    if response.status_code >= 400:
        message = _error_message(response)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "engine_request_failed",
                "message": f"HTTP {response.status_code}: {message}",
            },
        )
    try:
        payload = response.json()
    except Exception:
        payload = {"data": response.text}
    return payload if isinstance(payload, dict) else {"data": payload}


def _list_items(payload: Dict[str, Any]) -> list[Any]:
    items = payload.get("items")
    if isinstance(items, list):
        return items
    data = payload.get("data")
    return data if isinstance(data, list) else []


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
    engine_identity: Dict[str, Any] = {}
    if reachable:
        try:
            engine_health = response.json()
            configuration = engine_health.get("configuration")
            if isinstance(configuration, dict):
                working_directory = str(engine_health.get("working_directory") or "")
                index_key = ""
                if working_directory:
                    normalized = os.path.normcase(
                        str(Path(working_directory).resolve(strict=False))
                    )
                    index_key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                engine_identity = {
                    "index_key": index_key,
                    "embedding_binding": configuration.get("embedding_binding"),
                    "embedding_model": configuration.get("embedding_model"),
                    "embedding_dim": configuration.get("embedding_dim"),
                    "embedding_binding_host": configuration.get("embedding_binding_host"),
                    "vector_storage": configuration.get("vector_storage"),
                }
        except (TypeError, ValueError):
            engine_identity = {}
    return {
        "status": "ok" if reachable else "degraded",
        "service": "lightrag-shell",
        "engine_installed": reachable,
        "backend_configured": True,
        "backend_reachable": reachable,
        "state": "ready" if reachable else "engine_unreachable",
        "engine_identity": engine_identity,
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
        message = _error_message(upstream)
        status_code = upstream.status_code
        upstream.close()
        if status_code == 503:
            return _not_installed()
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "query_failed",
                    "message": f"HTTP {status_code}: {message}",
                }
            },
        )
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


@app.post("/documents/text")
async def insert_text(request: Request) -> Dict[str, Any]:
    if not _engine_configured():
        return _not_installed()
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid text payload")
    return _request_json("POST", "/documents/text", json=payload)


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


@app.get("/graph/popular")
def graph_popular(limit: int = Query(16, ge=1, le=100)) -> Dict[str, Any]:
    payload = _request_json(
        "GET",
        "/graph/label/popular",
        params={"limit": limit},
    )
    return {"items": _list_items(payload)}


@app.get("/graph/search")
def graph_search(
    q: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(20, ge=1, le=50),
) -> Dict[str, Any]:
    query = q.strip()
    if not query:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_query", "message": "Search query cannot be blank."},
        )
    payload = _request_json(
        "GET",
        "/graph/label/search",
        params={"q": query, "limit": limit},
    )
    return {"items": _list_items(payload)}


@app.get("/graph/global")
def graph_global(
    max_nodes: int = Query(400, ge=1, le=1000),
) -> Dict[str, Any]:
    return _request_json(
        "GET",
        "/graphs",
        params={"label": "*", "max_depth": 3, "max_nodes": max_nodes},
    )


@app.get("/graph/subgraph")
def graph_subgraph(
    label: str = Query(..., min_length=1, max_length=128),
    depth: int = Query(2, ge=1, le=3),
    max_nodes: int = Query(60, ge=10, le=200),
) -> Dict[str, Any]:
    center = label.strip()
    if not center:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_label", "message": "Graph label cannot be blank."},
        )
    return _request_json(
        "GET",
        "/graphs",
        params={"label": center, "max_depth": depth, "max_nodes": max_nodes},
    )


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
