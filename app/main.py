"""Application composition root."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import system
from app.features.chat import api as chat_api
from app.features.documents import api as documents_api
from app.features.ingest import api as ingest_api
from app.features.knowledge import api as knowledge_api
from app.features.memory import api as memory_api
from app.features.settings import api as settings_api
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
    application.mount(
        "/static",
        RevalidatingStaticFiles(directory=str(settings.static_dir)),
        name="static",
    )
    application.include_router(system.router)
    application.include_router(chat_api.router)
    application.include_router(documents_api.router)
    application.include_router(documents_api.topics_router)
    application.include_router(ingest_api.router)
    application.include_router(memory_api.router)
    application.include_router(memory_api.status_router)
    application.include_router(memory_api.frontend_router)
    application.include_router(knowledge_api.router)
    application.include_router(settings_api.router)
    register_exception_handlers(application)
    return application


app = create_app()
