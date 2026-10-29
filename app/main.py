"""Application composition root."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import system
from app.features.access import api as access_api
from app.features.access import service as access_service
from app.features.chat import api as chat_api
from app.features.documents import api as documents_api
from app.features.ingest import api as ingest_api
from app.features.inbox import api as inbox_api
from app.features.knowledge import api as knowledge_api
from app.features.memory import api as memory_api
from app.features.ocr import api as ocr_api
from app.features.session import api as session_api
from app.features.settings import api as settings_api
from app.features.tts import api as tts_api
from app.platform.config import get_settings
from app.platform.errors import register_exception_handlers
from app.platform.logging import setup_logging
from app.platform.paths import ensure_runtime_dirs

LOGGER = logging.getLogger("ragkb")


class RevalidatingStaticFiles(StaticFiles):
    """Serve static files with revalidation instead of long-lived caching."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        from app.features.inbox import service as inbox_service

        inbox_service.start_background()
    except Exception:
        LOGGER.exception("Inbox scanner startup failed (ignored)")
    try:
        from app.adapters.lightrag.client import LightRagHttpClient
        from app.features.knowledge import index_guard

        health = LightRagHttpClient().health()
        status = index_guard.status_from_service(
            health.service,
            create_baseline=health.engine_installed,
        )
        if status.get("blocking"):
            LOGGER.warning("Index compatibility requires attention: %s", status.get("state"))
    except Exception:
        LOGGER.exception("Index compatibility baseline check failed (ignored)")
    memory_worker = None
    try:
        from app.features.memory import extract, worker

        memory_worker = worker.get_worker()
        extract.register_extraction_handler(memory_worker)
        memory_worker.start()
    except Exception:
        LOGGER.exception("Memory worker startup failed (ignored)")
    try:
        from app.features.memory import supervisor

        supervisor.start_background()
    except Exception:
        LOGGER.exception("Memory supervisor startup failed (ignored)")
    try:
        yield
    finally:
        try:
            from app.features.inbox import service as inbox_service

            inbox_service.stop_background()
        except Exception:
            LOGGER.exception("Inbox scanner shutdown failed (ignored)")
        try:
            if memory_worker is not None:
                memory_worker.stop()
        except Exception:
            LOGGER.exception("Memory worker shutdown failed (ignored)")
        try:
            from app.features.memory import supervisor

            supervisor.stop_background()
        except Exception:
            LOGGER.exception("Memory supervisor shutdown failed (ignored)")


def create_app() -> FastAPI:
    settings = get_settings()
    ensure_runtime_dirs(
        (settings.data_dir, settings.knowledge_dir, settings.log_dir)
    )
    logger = setup_logging(settings.log_level, settings.log_dir)
    logger.info("Application starting: %s", settings.to_log_dict())

    application = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)

    @application.middleware("http")
    async def enforce_lan_access(request, call_next):
        path = request.url.path
        public = (
            path in {"/pair", "/access_required", "/favicon.ico", "/health",
                     "/api/app/access/status"}
            or path.startswith("/static/")
            or (request.method == "POST" and path == "/api/app/access/requests")
            or (
                request.method == "POST"
                and path.startswith("/api/app/access/requests/")
                and path.endswith("/claim")
            )
        )
        if public or access_service.is_request_authorized(request):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": {
                        "error": "device_auth_required",
                        "message": "This device must be authorized by the local computer",
                        "pair_url": "/pair",
                    }
                },
            )
        return RedirectResponse("/pair", status_code=307)

    application.mount(
        "/static",
        RevalidatingStaticFiles(directory=str(settings.static_dir)),
        name="static",
    )
    application.include_router(system.router)
    application.include_router(access_api.router)
    application.include_router(session_api.router)
    application.include_router(chat_api.router)
    application.include_router(documents_api.router)
    application.include_router(documents_api.topics_router)
    application.include_router(ingest_api.router)
    application.include_router(inbox_api.router)
    application.include_router(inbox_api.files_router)
    application.include_router(memory_api.router)
    application.include_router(memory_api.status_router)
    application.include_router(memory_api.frontend_router)
    application.include_router(ocr_api.router)
    application.include_router(tts_api.router)
    application.include_router(knowledge_api.router)
    application.include_router(knowledge_api.graph_router)
    application.include_router(settings_api.router)
    register_exception_handlers(application)
    return application


app = create_app()
